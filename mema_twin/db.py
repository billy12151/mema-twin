"""twin 自有 sqlite：canonical 枚举表、pending 治理表、prompt 版本表、证据指针索引表。

偏好记忆本体不在这里——存 mema（经 HTTP MCP 读写）；twin 只管类型归一、
待裁长尾、编译产物和证据指针索引（twin_evidence 只存 mema 记忆 id 与维度
标签，不存正文；设计文档 mema-avatar-design-2026-09-02.md D2/D3 + 实施方案 M1.3）。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
from pathlib import Path

from . import taxonomy

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
  first_seen_memory_id TEXT,
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
    """canonical code 会进文件路径（prompts/<ws>/<code>/）：与 server 侧 workspace
    守卫同款规则（对抗 review#1：canonicalize 是自定义 code 的唯一入口）。"""
    v = (value or "").strip()
    if not v or "/" in v or "\\" in v or ".." in v or "\x00" in v or len(v) > 64:
        raise ValueError(f"unsafe code segment: {value!r}")
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
    return conn


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


def append_alias(conn: sqlite3.Connection, kind: str, code: str, alias: str) -> list[str]:
    a = alias.strip()
    if not a:
        return json.loads((conn.execute(
            "SELECT aliases FROM twin_types WHERE type_kind=? AND code=?", (kind, code)
        ).fetchone() or {"aliases": "[]"})["aliases"] or "[]")
    # 对抗 review#7：同一别名不允许挂到第二个 canonical——否则归一结果由行序决定，
    # 用户后一次治理会静默推翻前一次
    for row in conn.execute(
            "SELECT code, aliases FROM twin_types WHERE type_kind=? AND status='active'", (kind,)
    ).fetchall():
        if row["code"] != code and a in json.loads(row["aliases"] or "[]"):
            raise ValueError(f"别名 {a!r} 已属于 {kind}/{row['code']}，不能同时映射到 {kind}/{code}")
    row = conn.execute(
        "SELECT aliases FROM twin_types WHERE type_kind=? AND code=?", (kind, code)
    ).fetchone()
    if not row:
        raise ValueError(f"unknown canonical: {kind}/{code}")
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


def upsert_pending(conn: sqlite3.Connection, kind: str, raw_value: str,
                   memory_id: str | None = None) -> int:
    """UNIQUE(type_kind, raw_value) 覆盖全部状态：reject 后同值再现必须复活为
    pending（hit_count 续增），否则 INSERT 撞约束让 IntegrityError 逸出工具边界。"""
    cur = conn.execute(
        "INSERT INTO twin_pending_values(type_kind, raw_value, first_seen_memory_id, created_at)"
        " VALUES(?,?,?,?)"
        " ON CONFLICT(type_kind, raw_value) DO UPDATE SET"
        " status='pending', resolved_code=NULL, hit_count=hit_count+1",
        (kind, raw_value, memory_id, now_iso()),
    )
    conn.commit()
    return int(cur.lastrowid)


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


def evidence_stats(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT work_type, COUNT(*) AS n FROM twin_evidence"
        " WHERE status='uncompiled' AND work_type IS NOT NULL"
        " GROUP BY work_type",
    ).fetchall()
    return {r["work_type"]: int(r["n"]) for r in rows}


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


def backfill_evidence_codes(conn: sqlite3.Connection, kind: str,
                            raw_value: str, code: str) -> int:
    """resolve 裁定后回填搁浅证据（对抗 review#3）：pending 维度写入的行 code 列
    为 NULL，对 compile/scan 不可见——按 raw 对账回填，否则创始证据静默丢失。"""
    cur = conn.execute(
        f"UPDATE twin_evidence SET {kind}=?"
        f" WHERE {kind} IS NULL AND {kind}_raw=?",
        (code, raw_value),
    )
    conn.commit()
    return cur.rowcount
