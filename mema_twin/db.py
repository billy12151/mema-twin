"""twin 自有 sqlite：canonical 枚举表、pending 治理表、prompt 版本表、证据指针索引表。

偏好记忆本体不在这里——存 mema（经 HTTP MCP 读写）；twin 只管类型归一、
待裁长尾、编译产物和证据指针索引（twin_evidence 只存 mema 记忆 id 与维度
标签，不存正文；设计文档 mema-avatar-design-2026-09-02.md D2/D3 + 实施方案 M1.3）。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sqlite3
from pathlib import Path

from . import taxonomy

_CODE_RE = re.compile(r"^[A-Za-z0-9_-]+$")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS twin_types(
  type_kind TEXT NOT NULL,
  code TEXT NOT NULL,
  label_zh TEXT NOT NULL,
  label_en TEXT NOT NULL DEFAULT '',
  domain TEXT NOT NULL DEFAULT '',
  aliases TEXT NOT NULL DEFAULT '[]',
  is_custom INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  PRIMARY KEY(type_kind, code)
);
CREATE TABLE IF NOT EXISTS twin_pending_values(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type_kind TEXT NOT NULL,
  raw_value TEXT NOT NULL,
  hit_count INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'pending',
  resolved_code TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(type_kind, raw_value)
);
CREATE TABLE IF NOT EXISTS twin_prompt_versions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  work_type TEXT NOT NULL,
  version INTEGER NOT NULL,
  prompt_md TEXT NOT NULL,
  source_memory_ids TEXT NOT NULL DEFAULT '[]',
  model TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  evidence_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  activated_at TEXT,
  UNIQUE(work_type, version)
);
CREATE TABLE IF NOT EXISTS twin_evidence(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id INTEGER NOT NULL,
  work_type TEXT,
  audience TEXT,
  purpose TEXT,
  work_type_raw TEXT NOT NULL DEFAULT '',
  audience_raw TEXT NOT NULL DEFAULT '',
  purpose_raw TEXT NOT NULL DEFAULT '',
  subject TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'uncompiled',
  compiled_version INTEGER,
  created_at TEXT NOT NULL,
  UNIQUE(memory_id)
);
CREATE INDEX IF NOT EXISTS idx_twin_evidence_lookup
  ON twin_evidence(work_type, status);
"""


def db_path() -> Path:
    return Path(os.environ.get("MEMA_TWIN_DB_PATH") or PROJECT_ROOT / "twin.sqlite3")


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def validate_code_segment(value: str) -> str:
    """canonical code 会进文件路径（prompts/<code>/）、meta 键与 hint 内嵌的
    调用示例：白名单字符集（轮2 P3-1/P3-2）。aud- 前缀保留给受众画像伪类型
    （v0.3.6 AR-1）：真类型不许伪装成画像。"""
    v = (value or "").strip()
    if not v or len(v) > 64 or not _CODE_RE.match(v):
        raise ValueError(f"unsafe code segment: {value!r}（仅允许字母/数字/下划线/连字符）")
    if v.startswith("aud-"):
        raise ValueError(f"保留前缀：{value!r}（aud- 专属受众画像伪类型，不可作普通 code）")
    return v


# WAL 是库文件级持久属性，每个路径设一次即可；每次连接都 PRAGMA journal_mode
# 会与并发写事务抢锁直接 BUSY（对抗 review#6），不走 busy_timeout。
_wal_ready: set[str] = set()


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    key = str(path)
    if key not in _wal_ready:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass  # 与并发写锁冲突：WAL 多数情况已就位，下次连接再补
        _wal_ready.add(key)
    conn.executescript(_SCHEMA)
    _seed_types(conn)
    _migrate(conn, key)
    return conn


# 每路径只跑一次（同 _wal_ready 模式；测试切 MEMA_TWIN_DB_PATH 各自独立生效）
_migrated: set[str] = set()


def _migrate(conn: sqlite3.Connection, key: str) -> None:
    """v0.3.8 幂等迁移（评审 P1-1：播种是 INSERT-only，只改内置枚举不动库则
    生产库静默失效）：
    ① 删 3 行内置 other 种子（仅 is_custom=0——防误删用户同名自建码）；
    ② audience/self 行摘除别名「私人」（仅移除该项，不整行覆写——保留治理追加）；
    ③ twin_pending_values 删旧列 first_seen_memory_id（v0.3.8 票据无 mema id：
      归一门打回发生在写入之前）。
    """
    if key in _migrated:
        return
    conn.execute("DELETE FROM twin_types WHERE code='other' AND is_custom=0")
    row = conn.execute(
        "SELECT aliases FROM twin_types WHERE type_kind='audience' AND code='self'"
    ).fetchone()
    if row is not None:
        try:
            aliases = json.loads(row["aliases"] or "[]")
        except (ValueError, TypeError):
            aliases = []
        if isinstance(aliases, list) and "私人" in aliases:
            aliases = [a for a in aliases if a != "私人"]
            conn.execute(
                "UPDATE twin_types SET aliases=? WHERE type_kind='audience' AND code='self'",
                (json.dumps(aliases, ensure_ascii=False),),
            )
    cols = [r["name"] for r in conn.execute(
        "PRAGMA table_info(twin_pending_values)")]
    if "first_seen_memory_id" in cols:
        try:
            conn.execute("ALTER TABLE twin_pending_values DROP COLUMN first_seen_memory_id")
        except sqlite3.Error:
            pass  # sqlite <3.35 不支持 DROP COLUMN：列留空无碍（仅写入侧已收窄）
    conn.commit()
    _migrated.add(key)


def _seed_types(conn: sqlite3.Connection) -> None:
    # 空表才播种（对抗 review#6：53 条 INSERT+commit 每连接执行放大写锁竞争）；
    # INSERT OR IGNORE 而非 upsert：治理追加的别名/自建 canonical 不能被内置数据覆盖
    n = conn.execute("SELECT COUNT(*) AS c FROM twin_types").fetchone()["c"]
    if n:
        return
    ts = now_iso()
    for kind in taxonomy.KINDS:
        for t in taxonomy.all_types(kind):
            conn.execute(
                "INSERT OR IGNORE INTO twin_types"
                "(type_kind, code, label_zh, label_en, domain, aliases, is_custom, status, created_at)"
                " VALUES(?,?,?,?,?,?,0,'active',?)",
                (kind, t.code, t.zh, t.en, t.domain,
                 json.dumps(list(t.aliases), ensure_ascii=False), ts),
            )
    conn.commit()


def _rows_with_aliases(conn: sqlite3.Connection, kind: str, where: str) -> list[dict]:
    rows = conn.execute(
        f"SELECT * FROM twin_types WHERE type_kind=? AND {where}", (kind,)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["aliases"] = json.loads(d.get("aliases") or "[]")
        out.append(d)
    return out


def type_rows(conn: sqlite3.Connection, kind: str) -> list[dict]:
    """全部 active 类型行（含内置行——治理 map 会往内置行追别名）。"""
    return _rows_with_aliases(conn, kind, "status='active'")


def custom_types(conn: sqlite3.Connection, kind: str) -> list[dict]:
    return _rows_with_aliases(conn, kind, "is_custom=1 AND status='active'")


def _ensure_type_row(conn: sqlite3.Connection, kind: str, code: str) -> sqlite3.Row:
    """取类型行；行缺失但属内置枚举时补播该行（轮2 P2-3：播种是表非空即跳过，
    后续版本给 taxonomy.py 加新内置码后，既有库不会有它的行——治理 map 到
    该码会炸 unknown canonical，且动态清单/匹配面口径分裂）。"""
    row = conn.execute(
        "SELECT * FROM twin_types WHERE type_kind=? AND code=?", (kind, code)
    ).fetchone()
    if row is not None:
        return row
    t = taxonomy.by_code(kind, code)
    if t is None:
        raise ValueError(f"unknown canonical: {kind}/{code}")
    conn.execute(
        "INSERT OR IGNORE INTO twin_types"
        "(type_kind, code, label_zh, label_en, domain, aliases, is_custom, status, created_at)"
        " VALUES(?,?,?,?,?,?,0,'active',?)",
        (kind, t.code, t.zh, t.en, t.domain,
         json.dumps(list(t.aliases), ensure_ascii=False), now_iso()),
    )
    conn.commit()
    return conn.execute(
        "SELECT * FROM twin_types WHERE type_kind=? AND code=?", (kind, code)
    ).fetchone()


def append_alias(conn: sqlite3.Connection, kind: str, code: str, alias: str) -> list[str]:
    a = alias.strip()
    if not a:
        return json.loads((_ensure_type_row(conn, kind, code))["aliases"] or "[]")
    # 对抗 review#7：同一别名不允许挂到第二个 canonical——否则归一结果由行序决定，
    # 用户后一次治理会静默推翻前一次
    for row in conn.execute(
            "SELECT code, aliases FROM twin_types WHERE type_kind=? AND status='active'", (kind,)
    ).fetchall():
        if row["code"] != code and a in json.loads(row["aliases"] or "[]"):
            raise ValueError(f"别名 {a!r} 已属于 {kind}/{row['code']}，不能同时映射到 {kind}/{code}")
    row = _ensure_type_row(conn, kind, code)
    aliases = json.loads(row["aliases"] or "[]")
    if a not in aliases:
        aliases.append(a)
        conn.execute(
            "UPDATE twin_types SET aliases=? WHERE type_kind=? AND code=?",
            (json.dumps(aliases, ensure_ascii=False), kind, code),
        )
        conn.commit()
    return aliases


def add_canonical(conn: sqlite3.Connection, kind: str, code: str, zh: str,
                  en: str = "", domain: str = "", aliases: list[str] | None = None) -> None:
    code = validate_code_segment(code)
    zh = (zh or "").strip()
    if not zh:
        raise ValueError("canonicalize 需要 code 与 zh")
    if taxonomy.by_code(kind, code) or any(r["code"] == code for r in custom_types(conn, kind)):
        raise ValueError(f"code 已存在: {kind}/{code}")
    conn.execute(
        "INSERT INTO twin_types(type_kind, code, label_zh, label_en, domain, aliases, is_custom, status, created_at)"
        " VALUES(?,?,?,?,?,?,1,'active',?)",
        (kind, code, zh, en, domain, json.dumps(aliases or [], ensure_ascii=False), now_iso()),
    )
    conn.commit()


def upsert_pending(conn: sqlite3.Connection, kind: str, raw_value: str) -> int:
    """归一门裁定义务票据（v0.3.8）：打回时创建/续接。UNIQUE(type_kind, raw_value)
    覆盖全部状态：reject 后同值再现必须复活为 pending（hit_count 续增），否则
    INSERT 撞约束让 IntegrityError 逸出工具边界。hit_count 口径 = 打回次数。
    返回值回查而非 lastrowid：冲突走 DO UPDATE 路径时 lastrowid 不可靠（新连接
    上是 0），同值重试会拿到脏票据 id。"""
    conn.execute(
        "INSERT INTO twin_pending_values(type_kind, raw_value, created_at)"
        " VALUES(?,?,?)"
        " ON CONFLICT(type_kind, raw_value) DO UPDATE SET"
        " status='pending', resolved_code=NULL, hit_count=hit_count+1",
        (kind, raw_value, now_iso()),
    )
    row = conn.execute(
        "SELECT id FROM twin_pending_values WHERE type_kind=? AND raw_value=?",
        (kind, raw_value),
    ).fetchone()
    conn.commit()
    return int(row["id"])


def list_pending(conn: sqlite3.Connection, status: str = "pending") -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM twin_pending_values WHERE status=? ORDER BY hit_count DESC, id", (status,)
    ).fetchall()
    return [dict(r) for r in rows]


def set_pending(conn: sqlite3.Connection, pending_id: int, status: str,
                resolved_code: str | None) -> None:
    conn.execute(
        "UPDATE twin_pending_values SET status=?, resolved_code=? WHERE id=?",
        (status, resolved_code, pending_id),
    )
    conn.commit()


# ---- twin_evidence：偏好指针索引（M1.3，本体在 mema，此处只存指针）----

def record_evidence(conn: sqlite3.Connection, memory_id: int,
                    dims: dict, subject: str = "") -> None:
    """write 成功后登记指针。dims 为 normalize 三维结果 dict；pending 维度
    code 列存 NULL、raw 列存原始值（pending 裁定后可靠对账回填）。"""
    def _code(kind: str) -> str | None:
        d = dims.get(kind) or {}
        return d.get("code") if d.get("ok") else None

    conn.execute(
        "INSERT OR IGNORE INTO twin_evidence"
        "(memory_id, work_type, audience, purpose,"
        " work_type_raw, audience_raw, purpose_raw, subject, created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (int(memory_id), _code("work_type"), _code("audience"),
         _code("purpose"),
         str((dims.get("work_type") or {}).get("raw") or ""),
         str((dims.get("audience") or {}).get("raw") or ""),
         str((dims.get("purpose") or {}).get("raw") or ""),
         subject or "", now_iso()),
    )
    conn.commit()


def uncompiled_evidence(conn: sqlite3.Connection, work_type: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM twin_evidence"
        " WHERE work_type=? AND status='uncompiled'"
        " ORDER BY id",
        (work_type,),
    ).fetchall()
    return [dict(r) for r in rows]


def alive_evidence(conn: sqlite3.Connection, work_type: str) -> list[dict]:
    """该 work_type 的全部在世证据（v0.3.7 全量投影）：uncompiled + compiled，
    排除 void。compile 素材取数与 submit 验证门 G3 期望集的同一数据源——
    两处若各查各的会出现「素材来自 A、门期望来自 B」的集合分叉（评审 P2-2）。"""
    rows = conn.execute(
        "SELECT * FROM twin_evidence"
        " WHERE work_type=? AND status IN ('uncompiled','compiled')"
        " ORDER BY id",
        (work_type,),
    ).fetchall()
    return [dict(r) for r in rows]


def voided_evidence(conn: sqlite3.Connection, work_type: str) -> list[dict]:
    """该 work_type 已作废的证据行（素材包「已作废条款」节用）：只取指针信息，
    不读 mema 正文——作废条款的剔除按 `<!-- src -->` 溯源对位旧版参考即可。"""
    rows = conn.execute(
        "SELECT memory_id, subject, compiled_version FROM twin_evidence"
        " WHERE work_type=? AND status='void'"
        " ORDER BY id",
        (work_type,),
    ).fetchall()
    return [dict(r) for r in rows]


def voided_audience_evidence(conn: sqlite3.Connection, audience: str) -> list[dict]:
    """某受众已作废的证据行（画像素材包「已作废条款」节用，评审轮1 P1-2）：按
    audience 查——受众相关行（跨类型行 + aud- 受众级行）的 work_type 各不相同，
    按 work_type 查画像侧永远落空。"""
    rows = conn.execute(
        "SELECT memory_id, subject, compiled_version, work_type FROM twin_evidence"
        " WHERE audience=? AND status='void'"
        " ORDER BY id",
        (audience,),
    ).fetchall()
    return [dict(r) for r in rows]


def evidence_stats(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT work_type, COUNT(*) AS n FROM twin_evidence"
        " WHERE status='uncompiled' AND work_type IS NOT NULL"
        " AND work_type NOT LIKE 'aud-%'"  # aud- 是受众画像证据行，不属于任何类型的编译队列
        " GROUP BY work_type",
    ).fetchall()
    return {r["work_type"]: int(r["n"]) for r in rows}


def audience_evidence(conn: sqlite3.Connection, audience: str,
                      exclude_work_type: str | None = None) -> list[dict]:
    """某受众的全部证据（v0.3.6）：含跨类型行（work_type=真类型）与受众级行
    （work_type=aud-{audience}），不分 compiled——画像是全量投影，compiled 状态
    属于类型编译生命周期（AR-2）。exclude_work_type 用于注入雏形去重（该类型的
    行已在 persona_supplement 里）；aud-{audience} 行永远包含（AR-3）。
    v0.3.7：void 行排除——作废条款不进画像投影，受众证据计数随之变化触发
    audience_stale 重抽象。"""
    rows = conn.execute(
        "SELECT * FROM twin_evidence WHERE audience=?"
        " AND status != 'void'"
        " AND (work_type LIKE 'aud-%' OR work_type IS NOT ?)"
        " ORDER BY id",
        (audience, exclude_work_type),
    ).fetchall()
    return [dict(r) for r in rows]


def void_evidence(conn: sqlite3.Connection, memory_id: int) -> dict | None:
    """作废一条证据（v0.3.7 冲突裁定「新替旧/撤销新写的」执行机制）：行级
    status='void'（保留 compiled_version 痕迹供 stale 判定与作废条款节溯源），
    全链路（compile 全量集合/增补/画像投影/统计）按 status 过滤天然排除。
    单向不可逆、幂等（重复 void 返回带 already_void 标记，不再重复触发 stale）；
    返回作废前行（无此行返回 None）。"""
    row = conn.execute(
        "SELECT * FROM twin_evidence WHERE memory_id=?", (int(memory_id),)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    if d["status"] != "void":
        conn.execute(
            "UPDATE twin_evidence SET status='void' WHERE memory_id=?", (int(memory_id),)
        )
        conn.commit()
        d["already_void"] = False
    else:
        d["already_void"] = True
    return d


def mark_compiled(conn: sqlite3.Connection,
                  memory_ids: list[int], version: int, work_type: str) -> int:
    """对抗 review#2：必须限定 work_type——否则 submit 带错 id 会把别的类型的
    未编译证据永久吞掉（compiled_version 还指向错误版本）。"""
    if not memory_ids:
        return 0
    ph = ",".join("?" for _ in memory_ids)
    cur = conn.execute(
        f"UPDATE twin_evidence SET status='compiled', compiled_version=?"
        f" WHERE work_type=? AND status='uncompiled' AND memory_id IN ({ph})",
        (version, work_type, *[int(i) for i in memory_ids]),
    )
    conn.commit()
    return cur.rowcount
