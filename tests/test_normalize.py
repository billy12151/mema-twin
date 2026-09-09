"""归一门下的归一语义（v0.3.8）：命中/空值/未命中带动态清单；票据由 gate_reject 创建。"""
import sqlite3

import pytest

from mema_twin import db as twin_db
from mema_twin import normalize, taxonomy


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


def test_miss_returns_candidates_not_pending(conn):
    """未命中不落库、返回动态清单——票据只在 gate_reject 打回时创建。"""
    r = normalize.normalize_value("work_type", "玄幻门派设定集", conn)
    assert not r["ok"] and r.get("error") is None
    assert r["candidates"] and {"code": "work_report", "zh": "工作汇报"} in r["candidates"]
    n = conn.execute("SELECT COUNT(*) AS c FROM twin_pending_values").fetchone()["c"]
    assert n == 0
    # 动态清单不含 other（v0.3.8）
    assert all(c["code"] != "other" for c in r["candidates"])


def test_gate_reject_creates_tickets(conn):
    """打回时创建/续接票据（hit_count=打回次数），一次性全报多维。"""
    m1 = normalize.normalize_value("work_type", "玄幻门派设定集", conn)
    m2 = normalize.normalize_value("audience", "外星领导", conn)
    resp = normalize.gate_reject([m1, m2])
    assert resp["ok"] is False and resp["error"] == "unmatched_value"
    assert set(resp["fields"]) == {"work_type", "audience"}
    assert resp["fields"]["work_type"]["candidates"] == m1["candidates"]
    assert "必须先与用户逐个确认" in resp["guidance"] and "canonicalize" in resp["guidance"]
    rows = {r["raw_value"]: r for r in twin_db.list_pending(conn)}
    assert rows["玄幻门派设定集"]["hit_count"] == 1
    # 同值再次打回：hit_count 续增
    m3 = normalize.normalize_value("work_type", "玄幻门派设定集", conn)
    normalize.gate_reject([m3])
    rows = {r["raw_value"]: r for r in twin_db.list_pending(conn)}
    assert rows["玄幻门派设定集"]["hit_count"] == 2


def test_map_resolution_makes_alias_then_hits(conn):
    pid = twin_db.upsert_pending(conn, "purpose", "催办")
    twin_db.append_alias(conn, "purpose", "drive_action", "催办")
    twin_db.set_pending(conn, pid, "mapped", "drive_action")
    r2 = normalize.normalize_value("purpose", "催办", conn)
    assert r2["ok"] and r2["code"] == "drive_action" and r2["matched_by"] == "db_alias"


def test_canonicalize_creates_custom_and_lists(conn):
    """自建码进清单即刻可见（匹配侧与候选清单同源）。"""
    twin_db.add_canonical(conn, "work_type", "xianxia_doc", "玄幻设定文档",
                          "xianxia setting", "专业服务", ["设定集"])
    r = normalize.normalize_value("work_type", "设定集", conn)
    assert r["ok"] and r["code"] == "xianxia_doc" and r["matched_by"] == "db_alias"
    cands = normalize.candidates("work_type", conn)
    assert {"code": "xianxia_doc", "zh": "玄幻设定文档"} in cands


def test_seed_idempotent_and_preserves_governance(conn):
    twin_db.append_alias(conn, "work_type", "work_report", "日报")
    conn2 = twin_db.connect()
    row = conn2.execute("SELECT aliases FROM twin_types WHERE type_kind='work_type' AND code='work_report'").fetchone()
    assert "日报" in row["aliases"]
    assert normalize.normalize_value("work_type", "日报", conn2)["ok"]
    conn2.close()


def test_rejected_ticket_reencounters_as_pending(conn):
    """reject 后同值再现（再次打回）必须复活为 pending，而不是撞 UNIQUE 崩掉。"""
    pid = twin_db.upsert_pending(conn, "work_type", "灵能审计年报")
    twin_db.set_pending(conn, pid, "rejected", None)
    r = normalize.normalize_value("work_type", "灵能审计年报", conn)
    resp = normalize.gate_reject([r])
    row = conn.execute(
        "SELECT status, hit_count FROM twin_pending_values WHERE id=?",
        (resp["fields"]["work_type"]["pending_id"],)).fetchone()
    assert row["status"] == "pending" and row["hit_count"] >= 2


def test_migration_removes_other_and_private_alias(tmp_path, monkeypatch):
    """v0.3.8 迁移（评审 P1-1）：播种 INSERT-only，只改内置枚举不动库则静默失效。
    用旧形态库直连验证：3 行 other 种子删除、self 行摘「私人」（保治理追加）、
    pending 旧列清除；行为上 其他/其它/私人 不再命中。"""
    p = str(tmp_path / "old.sqlite3")
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", p)
    raw = sqlite3.connect(p)
    raw.executescript(twin_db._SCHEMA)
    raw.row_factory = sqlite3.Row
    twin_db._seed_types(raw)  # 旧库先有全量内置种子（v0.3.7 形态）
    ts = "2026-09-01T00:00:00+00:00"
    for kind in taxonomy.KINDS:
        raw.execute(
            "INSERT INTO twin_types(type_kind,code,label_zh,label_en,domain,aliases,"
            "is_custom,status,created_at) VALUES(?,?,?,?,?,?,0,'active',?)",
            (kind, "other", "其他", "other", "其他", '["其它"]', ts))
    raw.execute("UPDATE twin_types SET aliases='[\"自己\",\"自用\",\"个人\",\"私人\",\"本人\"]'"
                " WHERE type_kind='audience' AND code='self'")
    raw.execute("ALTER TABLE twin_pending_values ADD COLUMN first_seen_memory_id TEXT")
    raw.commit()
    raw.close()
    conn = twin_db.connect()  # 迁移在首个 connect 触发
    assert conn.execute("SELECT COUNT(*) AS c FROM twin_types WHERE code='other'").fetchone()["c"] == 0
    self_row = conn.execute(
        "SELECT aliases FROM twin_types WHERE type_kind='audience' AND code='self'").fetchone()
    assert "私人" not in self_row["aliases"] and "本人" in self_row["aliases"]
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(twin_pending_values)")]
    assert "first_seen_memory_id" not in cols
    assert normalize.normalize_value("audience", "私人", conn)["ok"] is False
    assert normalize.normalize_value("audience", "本人", conn)["code"] == "self"
    assert normalize.normalize_value("work_type", "其他", conn)["ok"] is False
    conn.close()
