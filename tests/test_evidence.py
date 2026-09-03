import pytest

from mema_twin import db as twin_db


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(tmp_path / "twin.sqlite3"))
    monkeypatch.setenv("MEMA_TWIN_PROMPTS_DIR", str(tmp_path / "prompts"))
    monkeypatch.setenv("MEMA_TWIN_DELIVERABLES_DIR", str(tmp_path / "deliverables"))
    return tmp_path


def _dims(wt="work_report", au="leadership", pu="sync_info"):
    return {
        "work_type": {"ok": True, "kind": "work_type", "raw": wt, "code": wt,
                      "label_zh": "工作汇报", "matched_by": "exact_or_alias"},
        "audience": {"ok": True, "kind": "audience", "raw": au, "code": au,
                     "label_zh": "高层与决策层", "matched_by": "exact_or_alias"},
        "purpose": {"ok": True, "kind": "purpose", "raw": pu, "code": pu,
                    "label_zh": "信息同步与知会", "matched_by": "exact_or_alias"},
    }


def test_record_and_uncompiled(env):
    conn = twin_db.connect()
    twin_db.record_evidence(conn, 101, _dims(), subject="汇报偏好")
    twin_db.record_evidence(conn, 102, _dims(), subject="结论先行")
    rows = twin_db.uncompiled_evidence(conn, "work_report")
    assert [r["memory_id"] for r in rows] == [101, 102]
    assert rows[0]["audience"] == "leadership"
    assert twin_db.evidence_stats(conn) == {"work_report": 2}


def test_pending_dims_recorded_with_null_code(env):
    dims = _dims()
    dims["work_type"] = {"ok": False, "kind": "work_type", "raw": "星图设定",
                         "code": None, "matched_by": None}
    conn = twin_db.connect()
    twin_db.record_evidence(conn, 103, dims)
    rows = twin_db.uncompiled_evidence(conn, "work_report")
    assert rows == []  # work_type 为 NULL，不参与该类型召回
    row = conn.execute("SELECT * FROM twin_evidence WHERE memory_id=103").fetchone()
    assert row["work_type"] is None and row["work_type_raw"] == "星图设定"


def test_mark_compiled_and_idempotence(env):
    conn = twin_db.connect()
    twin_db.record_evidence(conn, 201, _dims())
    twin_db.record_evidence(conn, 202, _dims())
    assert twin_db.mark_compiled(conn, [201, 202], 3, "work_report") == 2
    assert twin_db.uncompiled_evidence(conn, "work_report") == []
    # 再标一次（幂等：只动 uncompiled 行）
    assert twin_db.mark_compiled(conn, [201], 4, "work_report") == 0
    row = conn.execute("SELECT * FROM twin_evidence WHERE memory_id=201").fetchone()
    assert row["status"] == "compiled" and row["compiled_version"] == 3


def test_duplicate_memory_id_ignored(env):
    conn = twin_db.connect()
    twin_db.record_evidence(conn, 301, _dims())
    twin_db.record_evidence(conn, 301, _dims())  # 二次登记不重复计数
    assert len(twin_db.uncompiled_evidence(conn, "work_report")) == 1


def test_global_no_workspace_scoping(env):
    """画像人级全局：证据不再按 workspace 分桶（列已删）。"""
    conn = twin_db.connect()
    twin_db.record_evidence(conn, 401, _dims())
    twin_db.record_evidence(conn, 402, _dims())
    assert [r["memory_id"] for r in twin_db.uncompiled_evidence(conn, "work_report")] == [401, 402]
    assert twin_db.mark_compiled(conn, [402], 1, "work_report") == 1
