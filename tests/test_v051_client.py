"""v0.5.1：偏好证据行落 client（调用方宿主标识）+ 历史行按审计回填。

背景：pref_actions 早就调了 identity.effective_client(data)，但返回值只喂审计，
证据行没有归因；页面「写入方」因此无值可显示（全部落到「未记录」）。
本文件锁定三件事：
  a) 新库/旧库都能拿到 client 列（DDL + 幂等 ALTER）；
  b) 写入路径（add_local_evidence / record_evidence）把 client 落库；
  c) 迁移期回填：±1s 窗口、只认 ok=1、同窗口多 client 宁缺勿错、幂等。
"""
import sqlite3

from mema_twin import db

GOOD_DIMS = {
    "work_type": {"ok": True, "kind": "work_type", "raw": "周报", "code": "work_report",
                  "label_zh": "工作汇报", "matched_by": "exact_or_alias"},
    "audience": {"ok": True, "kind": "audience", "raw": "高层", "code": "leadership",
                 "label_zh": "高层与决策层", "matched_by": "exact_or_alias"},
    "purpose": {"ok": True, "kind": "purpose", "raw": "同步", "code": "sync_info",
                "label_zh": "信息同步与知会", "matched_by": "exact_or_alias"},
}

LEGACY_EVIDENCE = """
CREATE TABLE twin_evidence(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id INTEGER NOT NULL,
  work_type TEXT, audience TEXT, purpose TEXT,
  work_type_raw TEXT NOT NULL DEFAULT '',
  audience_raw TEXT NOT NULL DEFAULT '',
  purpose_raw TEXT NOT NULL DEFAULT '',
  subject TEXT NOT NULL DEFAULT '',
  content TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '[]',
  source_ref TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'uncompiled',
  compiled_version INTEGER,
  created_at TEXT NOT NULL,
  UNIQUE(memory_id)
);
"""
LEGACY_AUDIT = """
CREATE TABLE twin_audit(
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
"""


def _legacy_db(path):
    """造一个 v0.5.0 形态的库：twin_evidence 无 client 列（真实旧库形态）。"""
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_EVIDENCE + LEGACY_AUDIT)
    conn.execute(
        "INSERT INTO twin_evidence(memory_id,work_type,subject,content,created_at)"
        " VALUES(7,'work_report','历史偏好','历史正文','2026-11-01T10:00:00+00:00')"
    )
    conn.commit()
    conn.close()


def _audit(path, ts, client, ok=1, action="write"):
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO twin_audit(ts,action,ok,client) VALUES(?,?,?,?)",
        (ts, action, ok, client),
    )
    conn.commit()
    conn.close()


def _client_of(conn, memory_id=7):
    return conn.execute(
        "SELECT client FROM twin_evidence WHERE memory_id=?", (memory_id,)
    ).fetchone()["client"]


def _open_legacy(tmp_path, monkeypatch, *audits):
    """造一个 v0.5.0 旧库并切到它（conftest 已在默认路径建好新库，不能复用）。

    audits: (ts, client, ok, action) 四元组，写进 twin_audit 作为回填依据。
    """
    path = tmp_path / "legacy.sqlite3"
    _legacy_db(path)
    for ts, client, ok, action in audits:
        _audit(path, ts, client, ok=ok, action=action)
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(path))
    db._migrated.discard(str(path))
    return path


class TestClientColumn:
    def test_new_table_has_client(self):
        conn = db.connect()
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(twin_evidence)")}
        assert "client" in cols
        conn.close()

    def test_add_local_evidence_persists_client(self):
        conn = db.connect()
        res = db.add_local_evidence(
            conn, GOOD_DIMS, subject="带归因", content="正文A", client="zcode"
        )
        assert res["duplicate"] is False
        assert _client_of(conn, res["memory_id"]) == "zcode"
        conn.close()

    def test_default_client_is_empty(self):
        """无头宿主/未传 client：落空串（与历史行同义，页面显示「未记录写入方」）。"""
        conn = db.connect()
        res = db.add_local_evidence(conn, GOOD_DIMS, subject="无归因", content="正文B")
        assert _client_of(conn, res["memory_id"]) == ""
        conn.close()

    def test_duplicate_keeps_first_client(self):
        """去重命中不覆盖既有行的 client：重复写入不产生新证据，改归属等于篡改历史。"""
        conn = db.connect()
        first = db.add_local_evidence(
            conn, GOOD_DIMS, subject="首次", content="同一段正文", client="jingleAI"
        )
        again = db.add_local_evidence(
            conn, GOOD_DIMS, subject="重复", content="同一段正文", client="workbuddy"
        )
        assert again["duplicate"] is True
        assert again["memory_id"] == first["memory_id"]
        assert _client_of(conn, first["memory_id"]) == "jingleAI"
        conn.close()

    def test_record_evidence_accepts_client(self):
        conn = db.connect()
        db.record_evidence(
            conn, 42, GOOD_DIMS, subject="导入行", content="导入正文", client="codex"
        )
        assert _client_of(conn, 42) == "codex"
        conn.close()


class TestBackfillOnMigrate:
    def test_migrate_adds_column_and_backfills_exact_ts(self, tmp_path, monkeypatch):
        _open_legacy(tmp_path, monkeypatch,
                     ("2026-11-01T10:00:00+00:00", "jingleAI", 1, "write"))
        conn = db.connect()
        assert _client_of(conn) == "jingleAI"
        conn.close()

    def test_backfill_tolerates_one_second_offset(self, tmp_path, monkeypatch):
        """created_at 是插入时刻、审计 ts 是请求结束时刻，实测有 0-1s 偏移。"""
        _open_legacy(tmp_path, monkeypatch,
                     ("2026-11-01T10:00:01+00:00", "workbuddy", 1, "write"))
        conn = db.connect()
        assert _client_of(conn) == "workbuddy"
        conn.close()

    def test_backfill_ignores_failed_write(self, tmp_path, monkeypatch):
        _open_legacy(tmp_path, monkeypatch,
                     ("2026-11-01T10:00:00+00:00", "zcode", 0, "write"))
        conn = db.connect()
        assert _client_of(conn) == ""
        conn.close()

    def test_backfill_ignores_non_write_actions(self, tmp_path, monkeypatch):
        _open_legacy(tmp_path, monkeypatch,
                     ("2026-11-01T10:00:00+00:00", "zcode", 1, "compile"))
        conn = db.connect()
        assert _client_of(conn) == ""
        conn.close()

    def test_backfill_skips_ambiguous_window(self, tmp_path, monkeypatch):
        """同一审计秒出现两个 client：宁缺勿错，保持空。"""
        _open_legacy(tmp_path, monkeypatch,
                     ("2026-11-01T10:00:00+00:00", "zcode", 1, "write"),
                     ("2026-11-01T10:00:00+00:00", "workbuddy", 1, "write"))
        conn = db.connect()
        assert _client_of(conn) == ""
        conn.close()

    def test_backfill_skips_cross_second_ambiguity(self, tmp_path, monkeypatch):
        """窗口跨秒的双 client 也必须跳过。

        evidence.created_at=10:00:00，窗口 [09:59:59, 10:00:01] 同时含
        10:00:00(zcode) 与 10:00:01(workbuddy)。旧口径只校验 b.ts=a.ts，
        守卫看不见后一个，LIMIT 1 会任取一个 → 误归。此用例锁死窗口口径。
        """
        _open_legacy(tmp_path, monkeypatch,
                     ("2026-11-01T10:00:00+00:00", "zcode", 1, "write"),
                     ("2026-11-01T10:00:01+00:00", "workbuddy", 1, "write"))
        conn = db.connect()
        assert _client_of(conn) == ""
        conn.close()

    def test_backfill_is_idempotent(self, tmp_path, monkeypatch):
        path = _open_legacy(
            tmp_path, monkeypatch,
            ("2026-11-01T10:00:00+00:00", "codex", 1, "write"),
        )
        conn = db.connect()
        assert _client_of(conn) == "codex"
        conn.close()
        # 二次连接：已迁移过的进程内标记需清掉才会重跑迁移
        db._migrated.discard(str(path))
        conn = db.connect()
        assert _client_of(conn) == "codex"
        # 已填过的行不被后续审计改写
        _audit(path, "2026-11-01T10:00:00+00:00", "sprout-cloud")
        conn.close()
        db._migrated.discard(str(path))
        conn = db.connect()
        assert _client_of(conn) == "codex"
        conn.close()
