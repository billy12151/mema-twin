"""action 级审计日志（v0.4.6）：每次 twin 调用落一行，回应 2026-10-21
「agent 困在流程」调查暴露的观测盲区——HTTP 200 分不清业务成败，服务端
不留调用记录时，事后只能靠 DB 状态推断，截图里的第 2 轮 step_update 到底
传了什么永远无法证实。

设计口径：
- 独立表 twin_audit + 环形 DELETE（v0.4.6 首版用 twin_meta 单列 JSON 环形
  缓冲，对抗评审实测推翻：满环时每次调用全量 json.loads+dumps 重写 2.8MB
  blob=62~156ms/次、写放大 ~112KB/次、跨进程读-改-写最坏丢 48%、脏 JSON
  一次 append 静默抹掉全部历史。换表后单行 INSERT ~0.1ms，sqlite 事务
  自带跨进程串行，读-改-写从根上消失）。
- 只读旁路：写日志失败绝不击穿工具响应（含 DB 只读/磁盘满/库锁定）。
  审计是观测件不是功能件，record 内任何异常静默吞掉。
- 不记正文：只记 action/ok/error/耗时/client/task_id/step_id——偏好正文
  与交付稿全文绝不进日志（隐私与体积；client+action+task_id+ts 已是
  行为指纹，不放大）。
- 关闸：MEMA_TWIN_AUDIT=0 关闭审计记录（不影响 stall advisory，后者有
  独立开关 MEMA_TWIN_STALL_ADVISORY）。
- 行数+字节双帽：环形 DELETE 取先到者（防超长 action/client 把单库撑爆）。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3

from . import db, tenant

# 行数帽（环形 DELETE 保留最近 N 行）：观测价值在近期窗口
_MAX_ROWS = 20000
# 字节帽（静态投影用）：单行最坏字节估算——action/error/client 三段各
# _FIELD_MAX + ts 32 + 两个 id 各 20 + 结构开销 ≈ 400B。字节帽的目的不是
# 精确定额，而是防「全字段顶格 + 行数帽同时打满」把库撑爆。
_MAX_BYTES = 8 * 1024 * 1024
_ROW_BYTES_MAX = 400
# 单字段截断帽（与 server 侧错误码长度对齐）
_FIELD_MAX = 64
# task_id/step_id 记录帽：超出 2^63-1 的值不可能存在（SQLite INTEGER 上限），
# 脏/巨值记 None 而非原样入列（防单行体积被构造性撑爆）
_ID_MAX = 2**63 - 1

_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS twin_audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  action TEXT NOT NULL DEFAULT '',
  ok INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  ms INTEGER NOT NULL DEFAULT 0,
  client TEXT,
  task_id INTEGER,
  step_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_twin_audit_ts ON twin_audit(ts);
CREATE INDEX IF NOT EXISTS idx_twin_audit_action ON twin_audit(action, ts);
"""

_audit_ready: set[str] = set()  # 已建表的 db 路径（同 flow._schema_ready 模式）
_enabled: bool | None = None  # 进程级缓存（env 热切换需求低：运维改 env 后
# 本就要重启 launchd 服务才改配置，热切换是伪需求——但 advisory 关闸独立）


def enabled() -> bool:
    """审计记录是否开启（公开查询面：status 观测键与测试断言用）。"""
    return _audit_enabled()


def _audit_enabled() -> bool:
    global _enabled
    if _enabled is None:
        _enabled = (os.environ.get("MEMA_TWIN_AUDIT") or "").strip().lower() not in (
            "0", "false", "off", "no")
    return _enabled


def _reset_enabled_cache() -> None:
    """测试专用：env 变更后重置探测缓存。"""
    global _enabled
    _enabled = None


def _ensure_audit_schema(conn: sqlite3.Connection, path: str) -> None:
    if path in _audit_ready:
        return
    conn.executescript(_AUDIT_SCHEMA)
    _migrate_json_buffer(conn)
    _audit_ready.add(path)


def _migrate_json_buffer(conn: sqlite3.Connection) -> None:
    """一次性迁移（幂等）：v0.4.6 首版把行存在 twin_meta.audit_log（JSON 数组），
    换表后灌进 twin_audit 再删键。迁移失败不击穿（观测件自愈优先），旧键留着
    不影响新表读写。"""
    try:
        row = conn.execute("SELECT value FROM twin_meta WHERE key=?",
                           ("audit_log",)).fetchone()
        if row is None:
            return
        try:
            rows = json.loads(row["value"] or "[]")
        except (ValueError, TypeError):
            rows = None  # 脏值：直接弃键（旧缓冲已是坏证据，不值得抢救）
        if rows is not None and isinstance(rows, list):
            for r in rows:
                if not isinstance(r, dict):
                    continue
                conn.execute(
                    "INSERT INTO twin_audit(ts, action, ok, error, ms, client,"
                    " task_id, step_id) VALUES(?,?,?,?,?,?,?,?)",
                    (_as_iso(r.get("ts")), _field(r.get("action")),
                     1 if r.get("ok") else 0, _field(r.get("error")),
                     _as_int(r.get("ms"), 10**9) or 0, _field(r.get("client")),
                     _as_id(r.get("task_id")), _as_id(r.get("step_id"))))
        conn.execute("DELETE FROM twin_meta WHERE key=?", ("audit_log",))
        conn.commit()
    except sqlite3.Error:
        return  # 迁移是尽力而为：失败则旧键仍在、新表为空，两者互不阻塞


def record(action: str, ok: bool, error: str | None, elapsed_ms: int,
           data: dict | None, client: str | None = None) -> None:
    """落一行审计。任何失败静默返回（观测件不击穿工具面）。
    client 由 server 入口显式传入（identity.effective_client 的结果）——
    仅读 data.client 会在 http 多宿主模式下恒为 null（评审 P2-3）。"""
    if not _audit_enabled():
        return
    try:
        data = data or {}
        task_id = _as_id(data.get("task_id"))
        step_id = _as_id(data.get("step_id"))
        conn = db.connect()
        try:
            _ensure_audit_schema(conn, str(db.db_path()))
            if task_id is None and step_id is not None:
                # step_update 等只传 step_id 的动作：反查 task_id 让空转打卡
                # 能按任务聚合（复审冒烟发现 6 次空转全记 task_id=None，
                # 事后无法回答「哪个任务在空转」——审计的核心用途）。
                row = conn.execute(
                    "SELECT task_id FROM twin_plan_steps WHERE id=?", (step_id,)
                ).fetchone()
                if row is not None:
                    task_id = _as_id(row["task_id"])
            conn.execute(
                "INSERT INTO twin_audit(ts, action, ok, error, ms, client,"
                " task_id, step_id) VALUES(?,?,?,?,?,?,?,?)",
                (db.now_iso(), _field(action), 1 if ok else 0,
                 _field(error), _as_int(elapsed_ms, 10**9) or 0,
                 _field(client), task_id, step_id))
            _trim(conn)
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 —— 审计旁路：任何异常都不许影响工具响应
        return


def _trim(conn: sqlite3.Connection) -> None:
    """环形截断：仅当行数超帽时才删（每次调用无脑删会让成本随 ring 增长）。
    删法用 id 阈值（`id <= MAX(id) - keep`）而非 NOT IN 子查询——前者走
    主键索引 SEARCH，后者全表扫 + bloom filter，复审实测满环时 6.93ms vs
    0.005ms（1400x）。保留最近 _MAX_ROWS 与 _MAX_BYTES//_ROW_BYTES_MAX 中的
    小值（字节帽用静态投影防「全字段顶格 + 行数打满」把库撑爆）。"""
    keep = min(_MAX_ROWS, max(1, _MAX_BYTES // _ROW_BYTES_MAX))
    total = int(conn.execute("SELECT COUNT(*) AS c FROM twin_audit"
                             ).fetchone()["c"])
    if total <= keep:
        return
    conn.execute(
        "DELETE FROM twin_audit WHERE id <="
        " (SELECT COALESCE(MAX(id), 0) - ? FROM twin_audit)", (keep,))


def recent(limit: int = 50, action: str | None = None) -> list[dict]:
    """最近审计行（新的在前）。供 status/task_get 观测面读取与测试断言。
    表不存在（全新库首调）返回 []——读取面软失败，不炸调用方。"""
    if not _audit_enabled():
        return []
    try:
        conn = db.connect()
        try:
            _ensure_audit_schema(conn, str(db.db_path()))
            sql = ("SELECT id, ts, action, ok, error, ms, client, task_id, step_id"
                   " FROM twin_audit")
            params: tuple = ()
            if action:
                sql += " WHERE action=?"
                params = (action,)
            sql += " ORDER BY id DESC LIMIT ?"
            try:
                n = max(1, min(int(limit or 50), 500))
            except (TypeError, ValueError):
                n = 50
            rows = conn.execute(sql, (*params, n)).fetchall()
        finally:
            conn.close()
        return [{"id": int(r["id"]), "ts": r["ts"], "action": r["action"],
                 "ok": bool(r["ok"]), "error": r["error"], "ms": int(r["ms"]),
                 "client": r["client"], "task_id": r["task_id"],
                 "step_id": r["step_id"]} for r in rows]
    except sqlite3.Error:
        return []


def stats() -> dict:
    """审计摘要（v0.4.6）：总数/失败/近 24h/top 动作/enabled。
    现用现算；审计关闭时返回 {"enabled": False}（不查库）。"""
    if not _audit_enabled():
        return {"enabled": False}
    try:
        conn = db.connect()
        try:
            _ensure_audit_schema(conn, str(db.db_path()))
            total = int(conn.execute(
                "SELECT COUNT(*) AS c FROM twin_audit").fetchone()["c"])
            failed = int(conn.execute(
                "SELECT COUNT(*) AS c FROM twin_audit WHERE ok=0").fetchone()["c"])
            unreachable = int(conn.execute(
                "SELECT COUNT(*) AS c FROM twin_audit"
                " WHERE ok=0 AND error='mema_unreachable'").fetchone()["c"])
            top = [{"action": r["action"], "count": int(r["c"])} for r in conn.execute(
                "SELECT action, COUNT(*) AS c FROM twin_audit"
                " GROUP BY action ORDER BY c DESC, action LIMIT 10")]
        finally:
            conn.close()
    except sqlite3.Error:
        return {"enabled": True}
    out: dict = {"enabled": True, "total": total, "failed": failed,
                 "unreachable": unreachable}
    if failed:
        out["fail_rate"] = round(failed / total, 4)
    if top:
        out["top_actions"] = top
    return out


def recent_24h() -> int:
    """近 24h 调用数（status 用；按 ts 索引范围扫，不全表解析）。"""
    if not _audit_enabled():
        return 0
    cutoff = (_dt.datetime.now(_dt.timezone.utc)
              - _dt.timedelta(hours=24)).replace(microsecond=0).isoformat()
    try:
        conn = db.connect()
        try:
            _ensure_audit_schema(conn, str(db.db_path()))
            return int(conn.execute(
                "SELECT COUNT(*) AS c FROM twin_audit WHERE ts >= ?",
                (cutoff,)).fetchone()["c"])
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


def clear() -> int:
    """清审计日志（全局）。返回删除行数。测试与运维手动清理用。"""
    if not _audit_enabled():
        return 0
    try:
        conn = db.connect()
        try:
            _ensure_audit_schema(conn, str(db.db_path()))
            cur = conn.execute("DELETE FROM twin_audit")
            conn.commit()
            return int(cur.rowcount or 0)
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


# ---- 字段整形（防构造性体积撑爆与脏值）----

def _field(value) -> str:
    return str(value or "")[:_FIELD_MAX]


def _as_int(value, cap: int) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if -cap <= value <= cap else None
    if isinstance(value, str):
        try:
            n = int(value.strip())
        except ValueError:
            return None
        return n if -cap <= n <= cap else None
    return None


def _as_id(value) -> int | None:
    return _as_int(value, _ID_MAX)


def _as_iso(value) -> str:
    """迁移兜底：旧 JSON 行的 ts 原样透传（TEXT 列无格式约束），空值补
    一个可排序的占位（1970）——旧行时间不可考但不丢行。"""
    return str(value or "")[:32] or "1970-01-01T00:00:00+00:00"


# ---- 挂墙 advisory：in_progress 超长无推进（v0.4.6）----
# 2026-10-21 案例：任务 #95 步骤 159 从 10:04:59 起 in_progress 挂住，agent
# 连续 4 轮空转「宣告要补图→只探索不产出→再宣告」。围栏（单一 in_progress 门）
# 挡住了绕行尝试却不出声。此处把墙变指路牌：在 twin 成功响应上附出口指引。
#
# 措辞纪律（红队 P2-1）：① 不用指令式"任选"，按「先自救再放弃」排序；
# ② task_close 降为最后手段并带前提；③ 不放 warnings 键（那是真故障通道，
# 观测提示混进去会让宿主分诊失灵）——server 侧挂独立 stall_advisory 键。

_STALL_MINUTES_DEFAULT = 30
_ADVISORY_COOLDOWN_MINUTES = 30
_ADVISORY_KEY_PREFIX = "stall_advisory_shown:"

_advisory_cooldown: dict[str, str] = {}  # 进程内抑制（跨进程由 twin_meta 键兜底）


def advisory_enabled() -> bool:
    """advisory 独立开关（与审计解耦：可以只留 advisory 关审计）。"""
    raw = (os.environ.get("MEMA_TWIN_STALL_ADVISORY") or "").strip().lower()
    return raw not in ("0", "false", "off", "no")


def stall_guidance(task_id, step_id=None) -> str | None:
    """该任务若有 in_progress 超过阈值的步骤，返回出口指引一句；否则 None。
    只读查询、失败静默（advisory 旁路）。同 (task_id, step_id) 有冷却窗
    （进程内 dict + twin_meta 键双兜底），不反复刷同一条。

    step_id 反查（复审 P2-1）：事故里的空转动作正是 step_update，其入参只有
    step_id/status——没有 task_id。只认 task_id 会让指路牌挂不到最需要的那面
    墙上（step_update/todo/plan_revise 全收不到）。故 task_id 缺省时按 step_id
    反查其所属任务（一次索引查），并把范围收窄到该步骤本身。"""
    if not advisory_enabled():
        return None
    tid = _as_id(task_id)
    sid = _as_id(step_id)
    if not tid and sid:
        try:
            conn = db.connect()
            try:
                _ensure_audit_schema(conn, str(db.db_path()))
                row = conn.execute(
                    "SELECT task_id FROM twin_plan_steps WHERE id=?", (sid,)
                ).fetchone()
            finally:
                conn.close()
            if row is not None:
                tid = _as_id(row["task_id"])
        except sqlite3.Error:
            return None
    if not tid:
        return None
    try:
        minutes = _stall_minutes()
        conn = db.connect()
        try:
            _ensure_audit_schema(conn, str(db.db_path()))
            if sid:
                rows = conn.execute(
                    "SELECT id, title, decided_at FROM twin_plan_steps"
                    " WHERE task_id=? AND status='in_progress' AND id=?"
                    " ORDER BY seq, id", (tid, sid)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, title, decided_at FROM twin_plan_steps"
                    " WHERE task_id=? AND status='in_progress' ORDER BY seq, id",
                    (tid,)).fetchall()
        finally:
            conn.close()
        stale = [r for r in rows if _older_than_minutes(r["decided_at"], minutes)]
        if not stale:
            return None
        r = stale[0]
        key = (f"{tenant.current_tenant() or ''}"
               f"{_ADVISORY_KEY_PREFIX}{tid}:{int(r['id'])}")
        if not _cooldown_ok(key, r["decided_at"]):
            return None
        extra = ""
        if len(stale) > 1:
            extra = (f"（该任务另有 {len(stale) - 1} 个步骤同样超时无推进，"
                     f"一并处理）")
        return (f"步骤 #{r['id']}「{_flat(r['title'])}」已 in_progress 超过"
                f"{minutes} 分钟无推进记录。{extra}"
                f"若只是正常推进中的长步骤，忽略本条。若确实卡住："
                f"① 先重试或换路径，失败就 step_update(failed, reflection=…) "
                f"记下原因与对策；② 计划本身要改走 plan_revise；"
                f"③ 只有在确认这个任务已无价值时才 task_close。")
    except Exception:  # noqa: BLE001 —— advisory 旁路
        return None


def _cooldown_ok(key: str, decided_at) -> bool:
    """冷却窗：同一 (task_id, step_id) 每 _ADVISORY_COOLDOWN_MINUTES 分钟
    最多提示一次。进程内 dict 先查（零成本），未命中再查 twin_meta 键
    （跨进程/重启后仍有效），通过则盖章。任一步失败都放行（advisory 不拦）。"""
    now = db.now_iso()
    last = _advisory_cooldown.get(key)
    if last and not _older_than_minutes(last, _ADVISORY_COOLDOWN_MINUTES):
        return False
    try:
        conn = db.connect()
        try:
            _ensure_audit_schema(conn, str(db.db_path()))
            row = conn.execute("SELECT value FROM twin_meta WHERE key=?",
                               (key,)).fetchone()
            if row is not None and row["value"]:
                if not _older_than_minutes(row["value"],
                                           _ADVISORY_COOLDOWN_MINUTES):
                    return False
            conn.execute(
                "INSERT INTO twin_meta(key, value) VALUES(?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, now))
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass  # 盖章失败放行（最坏多提示一次）
    _advisory_cooldown[key] = now
    return True


def _stall_minutes() -> int:
    raw = (os.environ.get("MEMA_TWIN_STALL_MINUTES") or "").strip()
    if not raw:
        return _STALL_MINUTES_DEFAULT
    try:
        n = int(raw)
    except ValueError:
        return _STALL_MINUTES_DEFAULT
    return max(1, min(n, 24 * 60))


def _older_than_minutes(iso_ts, minutes: int) -> bool:
    if not iso_ts:
        return False  # 无 decided_at 视为刚开始，不误报
    try:
        ts = _dt.datetime.fromisoformat(str(iso_ts))
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_dt.timezone.utc)
    age = _dt.datetime.now(_dt.timezone.utc) - ts
    return age.total_seconds() > minutes * 60


def _flat(text) -> str:
    return " ".join(str(text or "").split())[:40]
