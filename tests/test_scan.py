"""v0.3.7 定时体系：单一夜间编译任务 spec + 停转保险丝（twin_scan 已退役）。"""
import datetime as _dt

import pytest

from mema_twin import flow, scan


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(tmp_path / "twin.sqlite3"))
    monkeypatch.setenv("MEMA_TWIN_PROMPTS_DIR", str(tmp_path / "prompts"))
    flow._schema_ready.clear()
    flow.ensure_schema()
    return tmp_path


def test_notice_appears_when_never_run():
    assert scan.scan_notice() is not None


def test_notice_disappears_after_recent_scheduled_compile():
    flow.set_meta("last_scheduled_compile_at",
                  _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat())
    assert scan.scan_notice() is None


def test_notice_reappears_when_stale():
    flow.set_meta("last_scheduled_compile_at",
                  (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=8)
                   ).replace(microsecond=0).isoformat())
    assert scan.scan_notice() is not None


def test_notice_single_key_last_scan_at_is_dead():
    """twin_scan 退役后 last_scan_at 是死键：再新也不消提醒（只有夜间编译在转才算）。"""
    flow.set_meta("last_scan_at",
                  _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat())
    assert scan.scan_notice() is not None


def test_naive_timestamp_does_not_crash():
    flow.set_meta("last_scheduled_compile_at", "2020-01-01T00:00:00")  # 无时区：按 UTC
    assert scan.scan_notice() is not None


def test_spec_single_task():
    spec = scan.SCHEDULED_TASKS_SPEC
    names = [t["name"] for t in spec["tasks"]]
    assert names == ["twin_nightly_compile"]  # twin_scan 已退役
    nightly = spec["tasks"][0]
    assert nightly["cadence"] == "daily"
    assert [c["action"] for c in nightly["calls"]] == ["status", "compile", "submit", "compile", "submit"]
    assert "twin" in scan.AGENT_INSTRUCTION
    assert "夜间 persona 编译" in scan.AGENT_INSTRUCTION
    assert "每周治理扫描" not in scan.AGENT_INSTRUCTION  # 单任务口径
    # 空转阻尼与被拒语义进了 spec（宿主快照同步的对照源）
    submit_rule = nightly["calls"][2]["data"]["rule"]
    assert "validation_failed" in submit_rule and "no_new_evidence" in submit_rule
    assert "nightly_rejected" in submit_rule
