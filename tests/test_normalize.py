import pytest

from mema_twin import db as twin_db
from mema_twin import normalize


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(tmp_path / "twin.sqlite3"))
    monkeypatch.setenv("MEMA_TWIN_PROMPTS_DIR", str(tmp_path / "prompts"))
    c = twin_db.connect()
    yield c
    c.close()


def test_exact_hit(conn):
    r = normalize.normalize_value("work_type", "周报", conn)
    assert r["ok"] and r["code"] == "work_report" and r["matched_by"] == "exact_or_alias"


def test_empty_rejected(conn):
    r = normalize.normalize_value("purpose", "  ", conn)
    assert not r["ok"] and r.get("error") == "invalid_input"


def test_unknown_goes_pending_and_increments(conn):
    r1 = normalize.normalize_value("work_type", "玄幻门派设定集", conn, memory_id="m1")
    assert not r1["ok"] and r1["pending_id"]
    r2 = normalize.normalize_value("work_type", "玄幻门派设定集", conn)
    assert r2["pending_id"] == r1["pending_id"]
    row = conn.execute("SELECT hit_count FROM twin_pending_values WHERE id=?",
                       (r1["pending_id"],)).fetchone()
    assert row["hit_count"] == 2


def test_map_resolution_makes_alias(conn):
    r = normalize.normalize_value("purpose", "催办", conn)
    assert not r["ok"]
    twin_db.append_alias(conn, "purpose", "drive_action", "催办")
    twin_db.set_pending(conn, r["pending_id"], "mapped", "drive_action")
    r2 = normalize.normalize_value("purpose", "催办", conn)
    assert r2["ok"] and r2["code"] == "drive_action" and r2["matched_by"] == "db_alias"


def test_canonicalize_creates_custom(conn):
    twin_db.add_canonical(conn, "work_type", "xianxia_doc", "玄幻设定文档",
                          "xianxia setting", "专业服务", ["设定集"])
    r = normalize.normalize_value("work_type", "设定集", conn)
    assert r["ok"] and r["code"] == "xianxia_doc" and r["matched_by"] == "db_alias"


def test_seed_idempotent_and_preserves_governance(conn):
    twin_db.append_alias(conn, "work_type", "work_report", "日报")
    conn2 = twin_db.connect()
    row = conn2.execute("SELECT aliases FROM twin_types WHERE type_kind='work_type' AND code='work_report'").fetchone()
    assert "日报" in row["aliases"]
    assert normalize.normalize_value("work_type", "日报", conn2)["ok"]
    conn2.close()


def test_rejected_value_reencounters_as_pending(conn):
    """review#1：reject 后同值再现必须复活为 pending，而不是撞 UNIQUE 崩掉。"""
    r1 = normalize.normalize_value("work_type", "灵能审计年报", conn)
    assert not r1["ok"]
    twin_db.set_pending(conn, r1["pending_id"], "rejected", None)
    r2 = normalize.normalize_value("work_type", "灵能审计年报", conn)
    assert not r2["ok"] and r2["pending_id"]
    row = conn.execute("SELECT status, hit_count FROM twin_pending_values WHERE id=?",
                       (r2["pending_id"],)).fetchone()
    assert row["status"] == "pending" and row["hit_count"] >= 2
