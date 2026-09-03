import datetime as _dt

import pytest

from mema_twin import db as twin_db, flow, scan


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(tmp_path / "twin.sqlite3"))
    monkeypatch.setenv("MEMA_TWIN_PROMPTS_DIR", str(tmp_path / "prompts"))
    flow._schema_ready.clear()
    flow.ensure_schema()
    return tmp_path


def test_notice_appears_when_never_scanned(env):
    assert scan.scan_notice() is not None


def test_notice_disappears_after_recent_scan(env):
    scan.run_scan()
    assert scan.scan_notice() is None


def test_notice_reappears_when_stale(env):
    flow.set_meta("last_scan_at",
                  (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=8)
                   ).replace(microsecond=0).isoformat())
    assert scan.scan_notice() is not None


def test_notice_is_global(env):
    """workspace 列已删：scan 提醒全局唯一，跑过即全消。"""
    scan.run_scan()
    assert scan.scan_notice() is None


def test_naive_timestamp_does_not_crash(env):
    flow.set_meta("last_scan_at", "2020-01-01T00:00:00")  # 无时区：按 UTC 处理，不炸
    assert scan.scan_notice() is not None


def test_scan_report_and_suggestions(env):
    conn = twin_db.connect()
    twin_db.record_evidence(conn, 1, {
        "work_type": {"ok": True, "code": "work_report", "raw": "工作汇报"},
        "audience": {"ok": True, "code": "leadership", "raw": "高层"},
        "purpose": {"ok": True, "code": "sync_info", "raw": "同步"},
    })
    conn.close()
    flow.insert_task(brief="T", status="planning", dims={})
    out = scan.run_scan()
    assert out["ok"] and out["uncompiled_total"] == 1
    assert out["open_tasks"] == 1
    assert any("compile" in s for s in out["suggestions"])


def test_spec_single_source(env):
    spec = scan.SCHEDULED_TASKS_SPEC
    assert spec["tasks"][0]["calls"][0]["action"] == "scan"
    assert "twin" in scan.AGENT_INSTRUCTION
