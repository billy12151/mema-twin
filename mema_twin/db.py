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
  workspace TEXT NOT NULL,
  work_type TEXT NOT NULL,
  version INTEGER NOT NULL,
  prompt_md TEXT NOT NULL,
  source_memory_ids TEXT NOT NULL DEFAULT '[]',
  model TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  evidence_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  activated_at TEXT,
  UNIQUE(workspace, work_type, version)
);
CREATE TABLE IF NOT EXISTS twin_evidence(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workspace TEXT NOT NULL,
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
  UNIQUE(workspace, memory_id)
);
CREATE INDEX IF NOT EXISTS idx_twin_evidence_lookup
  ON twin_evidence(workspace, work_type, status);
"""


def db_path() -> Path:
    return Path(os.environ.get("MEMA_TWIN_DB_PATH") or PROJECT_ROOT / "twin.sqlite3")


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    _seed_types(conn)
    return conn


def _seed_types(conn: sqlite3.Connection) -> None:
    # INSERT OR IGNORE 而非 upsert：治理追加的别名/自建 canonical 不能被内置数据覆盖
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
    row = conn.execute(
        "SELECT aliases FROM twin_types WHERE type_kind=? AND code=?", (kind, code)
    ).fetchone()
    if not row:
        raise ValueError(f"unknown canonical: {kind}/{code}")
    aliases = json.loads(row["aliases"] or "[]")
    a = alias.strip()
    if a and a not in aliases:
        aliases.append(a)
        conn.execute(
            "UPDATE twin_types SET aliases=? WHERE type_kind=? AND code=?",
            (json.dumps(aliases, ensure_ascii=False), kind, code),
        )
        conn.commit()
    return aliases


def add_canonical(conn: sqlite3.Connection, kind: str, code: str, zh: str,
                  en: str = "", domain: str = "", aliases: list[str] | None = None) -> None:
    code = (code or "").strip()
    zh = (zh or "").strip()
    if not code or not zh:
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


def set_pending_first_seen(conn: sqlite3.Connection, pending_ids: list[int],
                           memory_id: int) -> None:
    """write 落库拿到 mema id 后回填（normalize 时 id 尚不存在）。"""
    if not pending_ids:
        return
    ph = ",".join("?" for _ in pending_ids)
    conn.execute(
        f"UPDATE twin_pending_values SET first_seen_memory_id=? WHERE id IN ({ph})",
        (str(memory_id), *pending_ids),
    )
    conn.commit()


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

def record_evidence(conn: sqlite3.Connection, workspace: str, memory_id: int,
                    dims: dict, subject: str = "") -> None:
    """write 成功后登记指针。dims 为 normalize 三维结果 dict；pending 维度
    code 列存 NULL、raw 列存原始值（pending 裁定后可靠对账回填）。"""
    def _code(kind: str) -> str | None:
        d = dims.get(kind) or {}
        return d.get("code") if d.get("ok") else None

    conn.execute(
        "INSERT OR IGNORE INTO twin_evidence"
        "(workspace, memory_id, work_type, audience, purpose,"
        " work_type_raw, audience_raw, purpose_raw, subject, created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (workspace, int(memory_id), _code("work_type"), _code("audience"),
         _code("purpose"),
         str((dims.get("work_type") or {}).get("raw") or ""),
         str((dims.get("audience") or {}).get("raw") or ""),
         str((dims.get("purpose") or {}).get("raw") or ""),
         subject or "", now_iso()),
    )
    conn.commit()


def uncompiled_evidence(conn: sqlite3.Connection, workspace: str,
                        work_type: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM twin_evidence"
        " WHERE workspace=? AND work_type=? AND status='uncompiled'"
        " ORDER BY id",
        (workspace, work_type),
    ).fetchall()
    return [dict(r) for r in rows]


def evidence_stats(conn: sqlite3.Connection, workspace: str) -> dict[str, int]:
    rows = conn.execute(
        "SELECT work_type, COUNT(*) AS n FROM twin_evidence"
        " WHERE workspace=? AND status='uncompiled' AND work_type IS NOT NULL"
        " GROUP BY work_type",
        (workspace,),
    ).fetchall()
    return {r["work_type"]: int(r["n"]) for r in rows}


def mark_compiled(conn: sqlite3.Connection, workspace: str,
                  memory_ids: list[int], version: int) -> int:
    if not memory_ids:
        return 0
    ph = ",".join("?" for _ in memory_ids)
    cur = conn.execute(
        f"UPDATE twin_evidence SET status='compiled', compiled_version=?"
        f" WHERE workspace=? AND status='uncompiled' AND memory_id IN ({ph})",
        (version, workspace, *[int(i) for i in memory_ids]),
    )
    conn.commit()
    return cur.rowcount
