"""本地 console（`mema-twin console`）的读面：twin.sqlite3 只读视图。

自 mema 仓 memory_arbiter/twin_view.py 迁入（v0.5.0 拆分方案 §3.6），按拆分
红利做了三处架构简化，SQL 语义与区块级降级纪律逐条保留：

- 库路径不再探测：``locate_twin_db``/find_spec 全套删除，直接用本包
  ``db.db_path()``（default 形态 = MEMA_TWIN_DB_PATH 或包根 twin.sqlite3）；
  多租户（USERS_DIR）形态 db.db_path() 抛 TenantError，由 HTTP 层归一为
  multi_tenant_unsupported——console 仅支持 default 形态（方案拍板）；
- 偏好本体自持（#1604）：evidence SELECT 带 content/tags/source_ref，
  证据抽屉直接展示本地正文——mema 联查（get_memories_by_ids /
  memory_missing / 跳记忆页链接）整条删除；
- import 状态判定（方案 §3.6 R2 P2-4 口径）：twin_meta 无 ``import_done``
  标记 且存在 content 为空的 alive 行（uncompiled/compiled）→ 迁移横幅
  数据（overview.import）。不用「evidence 全部 content 为空」——users/2
  类库有永久拿不到正文的 dangling 行会永久误报。

不迁移的 spawn 件（TwinRunner/runner_env/get_runner/看门狗/reaper）：写面
已改进程内直调 ``server._twin_impl``（见 console_http.py）。
"""
from __future__ import annotations

import json
import sqlite3
import urllib.parse
from pathlib import Path
from typing import Any, Callable

_IMPORT_DONE_KEY = "import_done"

# alive = 还参与画像生命周期的证据（void 已作废，不参与迁移判定）
_ALIVE_STATUSES = ("uncompiled", "compiled")


class TwinStore:
    """twin.sqlite3 只读视图：短连接、窄 SELECT、区块级降级（绝不 500）。

    旧版 twin 缺表/缺列只让对应区块降级（twin_schema_mismatch），健康区块
    照常返回；打不开（库文件损坏/-wal/-shm 拷贝不齐）→ twin_db_unreadable。
    mema 侧 R2 实测钉死的红线原样保留：**不做 immutable=1 兜底**——WAL 未
    checkpoint 的健康库在 immutable 下读出 no such table，会被误诊成 schema
    异常，反向有害。
    """

    def __init__(self, db_path: str) -> None:
        self.path = str(db_path)

    def _connect(self) -> sqlite3.Connection:
        # ro URI 必须绝对路径（mema R1 P2-10）；quote 保留 '/'（safe 默认含它）。
        p = Path(self.path).resolve()
        uri = "file:" + urllib.parse.quote(str(p)) + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def pending_count(self) -> int | None:
        """badge/横幅顺带查询；任何失败返回 None（不阻塞页面）。"""
        conn: sqlite3.Connection | None = None
        try:
            conn = self._connect()
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM twin_pending_values WHERE status='pending'"
            ).fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return None
        finally:
            if conn is not None:
                conn.close()

    def overview(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": True,
            "versions": [],
            "evidence_stats": [],
            "pending_pending": 0,
            "stale_keys": [],
            "audit": None,
            "import": None,
            "degraded": [],
        }
        try:
            conn = self._connect()
        except sqlite3.Error as exc:
            return {"ok": False, "error": "twin_db_unreadable", "reason": str(exc)}
        try:
            def run(section: str, fn: Callable[[sqlite3.Connection], None]) -> None:
                try:
                    fn(conn)
                except sqlite3.Error:
                    out["degraded"].append(section)

            def versions(c: sqlite3.Connection) -> None:
                rows = c.execute(
                    "SELECT work_type, version, evidence_count, created_at, activated_at,"
                    " length(prompt_md) AS size_chars"
                    " FROM twin_prompt_versions WHERE status='active' ORDER BY work_type"
                ).fetchall()
                out["versions"] = [dict(r) for r in rows]

            def evidence_stats(c: sqlite3.Connection) -> None:
                rows = c.execute(
                    "SELECT work_type, status, COUNT(*) AS n FROM twin_evidence"
                    " GROUP BY work_type, status"
                ).fetchall()
                out["evidence_stats"] = [dict(r) for r in rows]

            def pending(c: sqlite3.Connection) -> None:
                row = c.execute(
                    "SELECT COUNT(*) AS n FROM twin_pending_values WHERE status='pending'"
                ).fetchone()
                out["pending_pending"] = int(row[0]) if row else 0

            def stale(c: sqlite3.Connection) -> None:
                rows = c.execute(
                    "SELECT key FROM twin_meta"
                    " WHERE key LIKE 'persona_stale:%' OR key LIKE 'nightly_reject:%'"
                ).fetchall()
                out["stale_keys"] = [str(r[0]) for r in rows]

            def audit(c: sqlite3.Connection) -> None:
                row = c.execute(
                    "SELECT COUNT(*) AS total, SUM(ok) AS ok_n, MAX(ts) AS last_ts"
                    " FROM twin_audit"
                ).fetchone()
                # 空表 SUM(ok) 为 NULL → int(x or 0) 归一（mema R2 P2-10）。
                out["audit"] = {
                    "total": int(row[0]) if row else 0,
                    "ok_n": int(row[1] or 0) if row else 0,
                    "last_ts": row[2],
                }

            def import_state(c: sqlite3.Connection) -> None:
                """迁移横幅数据（§3.6）：import_done 标记 + 空 content alive 计数。

                标记在（含任意值）→ 迁移完成（或明确做过），不再提示——users/2
                类 dangling 行不得永久误报；标记不在且 alive 行有空 content →
                pending=true 带计数。twin_meta/evidence 任一缺表走区块降级。"""
                done = c.execute(
                    "SELECT 1 FROM twin_meta WHERE key = :k", {"k": _IMPORT_DONE_KEY}
                ).fetchone()
                if done is not None:
                    out["import"] = {"pending": False, "empty_content": 0}
                    return
                ph = ",".join(f"'{s}'" for s in _ALIVE_STATUSES)
                row = c.execute(
                    "SELECT COUNT(*) AS n FROM twin_evidence"
                    f" WHERE status IN ({ph}) AND (content IS NULL OR content = '')"
                ).fetchone()
                out["import"] = {
                    "pending": True,
                    "empty_content": int(row[0]) if row else 0,
                }

            run("versions", versions)
            run("evidence_stats", evidence_stats)
            run("pending", pending)
            run("stale_keys", stale)
            run("audit", audit)
            run("import", import_state)
        finally:
            conn.close()
        return out

    def _list_or_mismatch(self, section: str, fn: Callable[[sqlite3.Connection], list[dict[str, Any]]]) -> dict[str, Any]:
        try:
            conn = self._connect()
        except sqlite3.Error as exc:
            return {"ok": False, "error": "twin_db_unreadable", "reason": str(exc)}
        try:
            return {"ok": True, "items": fn(conn)}
        except sqlite3.Error:
            return {"ok": False, "error": "twin_schema_mismatch", "section": section}
        finally:
            conn.close()

    def evidence(
        self, status: str | None = None, work_type: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> dict[str, Any]:
        """证据列表（分页+过滤）。v0.5.0：SELECT 自带 content/tags/source_ref
        ——正文本地自持，抽屉直接展示，无 mema 联查。"""
        def query(c: sqlite3.Connection) -> list[dict[str, Any]]:
            where = " WHERE (:status IS NULL OR status = :status)" \
                    " AND (:work_type IS NULL OR work_type = :work_type)"
            params = {"status": status, "work_type": work_type}
            total = c.execute(
                "SELECT COUNT(*) AS n FROM twin_evidence" + where, params
            ).fetchone()
            rows = c.execute(
                "SELECT id, memory_id, work_type, audience, purpose, work_type_raw,"
                " audience_raw, purpose_raw, subject, content, tags, source_ref,"
                " status, compiled_version, created_at"
                " FROM twin_evidence" + where +
                " ORDER BY id DESC LIMIT :limit OFFSET :offset",
                {**params, "limit": int(limit), "offset": int(offset)},
            ).fetchall()
            items = [dict(r) for r in rows]
            items.append({"__total__": int(total[0]) if total else 0})
            return items
        result = self._list_or_mismatch("evidence", query)
        if result.get("ok"):
            marker = result["items"].pop()
            result["total"] = marker.get("__total__", 0)
        return result

    def pending(self, status: str = "pending", limit: int = 100) -> dict[str, Any]:
        # 与 twin db.list_pending 同序（db.py list_pending）：hit_count DESC, id。
        def query(c: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = c.execute(
                "SELECT id, type_kind, raw_value, hit_count, status, resolved_code, created_at"
                " FROM twin_pending_values WHERE status = :status"
                " ORDER BY hit_count DESC, id LIMIT :limit",
                {"status": str(status), "limit": int(limit)},
            ).fetchall()
            return [dict(r) for r in rows]
        return self._list_or_mismatch("pending", query)

    def types(self, type_kind: str) -> dict[str, Any]:
        def query(c: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = c.execute(
                "SELECT type_kind, code, label_zh, label_en, domain, aliases, is_custom"
                " FROM twin_types WHERE status='active' AND type_kind = :kind ORDER BY code",
                {"kind": str(type_kind)},
            ).fetchall()
            items: list[dict[str, Any]] = []
            for r in rows:
                item = dict(r)
                try:
                    item["aliases"] = json.loads(item.get("aliases") or "[]")
                except ValueError:
                    item["aliases"] = []
                if not isinstance(item["aliases"], list):
                    item["aliases"] = []
                items.append(item)
            return items
        return self._list_or_mismatch("types", query)

    def audit(self, limit: int = 50) -> dict[str, Any]:
        def query(c: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = c.execute(
                "SELECT id, ts, action, ok, error, ms, client, task_id, step_id"
                " FROM twin_audit ORDER BY id DESC LIMIT :limit",
                {"limit": int(limit)},
            ).fetchall()
            return [dict(r) for r in rows]
        return self._list_or_mismatch("audit", query)

    def persona(self, work_type: str) -> dict[str, Any]:
        """某个产出类型的 active 画像（owner 拍板「按产出规则展示」：目录卡
        点开看内容——prompt_md 是该类型全部偏好的人话编译产物，即「给这类
        工作时分身知道什么」的直接答案）。"""
        try:
            conn = self._connect()
        except sqlite3.Error as exc:
            return {"ok": False, "error": "twin_db_unreadable", "reason": str(exc)}
        try:
            row = conn.execute(
                "SELECT work_type, version, prompt_md, evidence_count, created_at, activated_at"
                " FROM twin_prompt_versions WHERE status='active' AND work_type = :wt",
                {"wt": str(work_type)},
            ).fetchone()
        except sqlite3.Error:
            return {"ok": False, "error": "twin_schema_mismatch", "section": "persona"}
        finally:
            conn.close()
        if row is None:
            return {"ok": False, "error": "not_found", "work_type": str(work_type)}
        return {"ok": True, **dict(row)}
