"""twin 自有 sqlite：canonical 枚举表、pending 治理表、prompt 版本表。

偏好记忆本体不在这里——存 mema（经 HTTP MCP 读写）；twin 只管类型归一、
待裁长尾和编译产物（设计文档 mema-avatar-design-2026-09-02.md D2/D3）。
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
    row = conn.execute(
        "SELECT id FROM twin_pending_values WHERE type_kind=? AND raw_value=? AND status='pending'",
        (kind, raw_value),
    ).fetchone()
    if row:
        conn.execute("UPDATE twin_pending_values SET hit_count=hit_count+1 WHERE id=?", (row["id"],))
        conn.commit()
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO twin_pending_values(type_kind, raw_value, first_seen_memory_id, created_at)"
        " VALUES(?,?,?,?)",
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
