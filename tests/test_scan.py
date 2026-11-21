"""v0.3.7 定时体系：单一夜间编译任务 spec；v0.6.1 提醒判据=积压驱动（逐信号锚）。"""
import datetime as _dt
import json

import pytest

from mema_twin import flow, scan, server


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(tmp_path / "twin.sqlite3"))
    monkeypatch.setenv("MEMA_TWIN_PROMPTS_DIR", str(tmp_path / "prompts"))
    flow._schema_ready.clear()
    flow.ensure_schema()
    return tmp_path


def _seed_uncompiled(days_old=4):
    """造 uncompiled 积压：证据 created_at 回拨 days_old 天（S8 锚=最老未编译证据）。"""
    from mema_twin import db
    conn = db.connect()
    old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days_old)
           ).replace(microsecond=0).isoformat()
    conn.execute(
        "INSERT INTO twin_evidence(memory_id, work_type, work_type_raw, content,"
        " created_at) VALUES(900001, 'work_report', '周报', '待编译偏好正文', ?)",
        (old,))
    conn.commit()
    conn.close()


def _clear_backlog():
    from mema_twin import db
    conn = db.connect()
    conn.execute("DELETE FROM twin_evidence WHERE memory_id=900001")
    conn.execute("DELETE FROM twin_prompt_versions")
    conn.commit()
    conn.close()


def test_notice_not_shown_without_backlog():
    """v0.6.1 积压驱动：空库（无任何积压）不出提醒——旧心跳判据下空库也出（已废）。"""
    assert scan.scan_notice() is None


def test_notice_shown_when_backlog_overdue():
    _seed_uncompiled(days_old=4)
    r = scan.scan_notice()
    assert r is not None
    assert "uncompiled" in r["overdue_signals"]
    assert r["backlog"]["uncompiled"]["work_report"] == 1
    _clear_backlog()


def test_notice_not_shown_when_backlog_fresh():
    """积压刚形成（<3 天）不提醒——手动编译勤的库不打扰。"""
    _seed_uncompiled(days_old=0)
    assert scan.scan_notice() is None
    _clear_backlog()


def test_notice_disappears_after_backlog_digested():
    """消化即自消失：积压清零后不再提醒（旧口径=心跳刷掉，新口径=积压没了）。"""
    _seed_uncompiled(days_old=4)
    assert scan.scan_notice() is not None
    _clear_backlog()
    assert scan.scan_notice() is None


def test_notice_from_persona_stale_signal():
    """persona_stale 锚=meta rec.at 最老值。"""
    old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=4)
           ).replace(microsecond=0).isoformat()
    flow.set_meta("persona_stale:work_report",
                  json.dumps({"at": old, "voided": [901]}, ensure_ascii=False))
    r = scan.scan_notice()
    assert r is not None and "persona_stale" in r["overdue_signals"]
    flow.delete_meta("persona_stale:work_report")


def test_notice_dirty_anchor_is_conservative():
    """脏时间戳锚（不可解析）视为不超窗——提醒面不炸也不误报。"""
    flow.set_meta("persona_stale:work_report",
                  json.dumps({"at": "t", "voided": [901]}, ensure_ascii=False))
    assert scan.scan_notice() is None
    flow.delete_meta("persona_stale:work_report")


def test_notice_tasks_unevaluated_signal():
    """未评估任务积压：锚=decided_at（刚收口不提醒，R2 误-1 回归）。"""
    conn = flow.db.connect()
    old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=4)
           ).replace(microsecond=0).isoformat()
    conn.execute(
        "INSERT INTO twin_tasks(id, brief, status, decided_at, created_at)"
        " VALUES(5001, '旧任务', 'submitted', ?, ?)",
        (old, old))
    conn.commit()
    conn.close()
    r = scan.scan_notice()
    assert r is not None and "tasks_unevaluated" in r["overdue_signals"]
    conn = flow.db.connect()
    fresh = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()
    conn.execute("UPDATE twin_tasks SET decided_at=? WHERE id=5001", (fresh,))
    conn.commit()
    conn.close()
    assert scan.scan_notice() is None
    conn = flow.db.connect()
    conn.execute("DELETE FROM twin_tasks WHERE id=5001")
    conn.commit()
    conn.close()


def test_spec_single_task():
    spec = scan.SCHEDULED_TASKS_SPEC
    names = [t["name"] for t in spec["tasks"]]
    # v0.4.1：合并单任务双支线（评估支线前置；twin_scan 已退役）
    assert names == ["twin_nightly_compile"]
    nightly = spec["tasks"][0]
    assert nightly["cadence"] == "daily"
    assert [c["action"] for c in nightly["calls"]] == [
        "status", "task_evaluate", "playbook_submit", "pending",
        "compile", "submit", "compile", "submit"]
    # 评估支线前置：task_evaluate 带 origin=scheduled（夜编清章：把提醒还给白天）
    evaluate_call = nightly["calls"][1]
    assert evaluate_call["data"]["origin"] == "scheduled"
    # v0.6 A8-3：说明文本外置 instruction（data 只放真参数——B8 顶层未知键硬拒）
    # v0.6.1：心跳叙述退役，instruction 只讲 origin 的验证门/清章语义
    assert "心跳" not in evaluate_call["instruction"]
    assert "scheduled" in evaluate_call["instruction"]
    assert nightly["calls"][2]["data"]["origin"] == "scheduled"
    assert "twin" in scan.AGENT_INSTRUCTION
    assert "创建一个定时任务" in scan.AGENT_INSTRUCTION  # 单任务口径
    assert "task_evaluate" in scan.AGENT_INSTRUCTION
    assert "把当天未编译偏好自动整理进 persona" in scan.AGENT_INSTRUCTION
    assert "执行经验评估" in scan.AGENT_INSTRUCTION
    assert "每周治理扫描" not in scan.AGENT_INSTRUCTION
    # 空转阻尼与被拒语义进了 spec（宿主快照同步的对照源）
    submit_rule = nightly["calls"][5]["instruction"]
    assert "validation_failed" in submit_rule and "no_new_evidence" in submit_rule
    pb_rule = nightly["calls"][2]["instruction"]
    assert "validation_failed" in pb_rule and "no_new_evidence" in pb_rule
    assert nightly["calls"][2]["data"]["origin"] == "scheduled"
    assert "nightly_rejected" in submit_rule
    assert "pending_count" in submit_rule  # 汇总输出带治理计数（twin_scan 退役归置）
    assert "保守封套" in nightly["calls"][4]["instruction"]  # 夜间不做无因重组
    pending_rule = nightly["calls"][3]["instruction"]
    assert "待裁票据" in pending_rule and "夜间不代裁" in pending_rule  # v0.3.8 晨报兜底
    assert "双跑" not in submit_rule  # v0.3.8：双跑句已删
    # v0.6：spec 全部 data 载荷键 ∈ B8 白名单（说明文本混进 data 会被打回）
    from mema_twin.server import _ACTION_KEYS
    for call in nightly["calls"]:
        bad = set(call["data"]) - _ACTION_KEYS[call["action"]]
        assert not bad, f"{call['action']} 的 data 含非法键: {bad}"


# ---- v0.4.3 提示抑制窗口（用户拍板：3 天） ----

def test_notice_snoozed_three_days_after_shown():
    """v0.4.3 抑制窗口保留：首次提示即全局盖章，窗口内所有调用静默（多宿主共享）。"""
    _seed_uncompiled(days_old=4)
    assert scan.scan_notice() is not None  # 首次提示 + 盖章
    assert scan.scan_notice() is None      # 窗口内静默（积压仍在也静默）
    _clear_backlog()


def test_notice_resumes_after_snooze_window():
    """3 天窗口过后积压仍未消化 → 恢复提示并重盖。"""
    _seed_uncompiled(days_old=4)
    old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=4)
           ).replace(microsecond=0).isoformat()
    flow.set_meta(scan.SCAN_NOTICE_SHOWN_KEY, old)
    assert scan.scan_notice() is not None
    assert scan.scan_notice() is None  # 重盖后再静默
    _clear_backlog()


def test_night_session_clears_stamp_only_when_backlog_digested():
    """v0.6.1 夜编清章=**条件清**（R2 炸-2 收口）：积压未消化时章保留（避免日频
    骚扰）；积压已消化（无超窗信号）时章被 evaluate(origin=scheduled) 清除——
    心跳 meta 不再写入。"""
    _seed_uncompiled(days_old=4)
    assert scan.scan_notice() is not None  # 章被首个调用者（可能是夜编）领走
    assert scan.scan_notice() is None
    # 积压未消化：不清章（日频骚扰防护）
    r = server._twin_impl("task_evaluate", {"origin": "scheduled"})
    assert r["ok"]
    assert flow.get_meta(scan.SCAN_NOTICE_SHOWN_KEY) is not None
    # 积压已消化：清章
    _clear_backlog()
    server._twin_impl("task_evaluate", {"origin": "scheduled"})
    assert flow.get_meta(scan.SCAN_NOTICE_SHOWN_KEY) is None
    assert flow.get_meta("last_scheduled_playbook_at") is None  # 心跳退役


def test_night_clear_only_for_scheduled_origin():
    """只有 origin=scheduled 清章；交互式调用不动章。"""
    _seed_uncompiled(days_old=4)
    assert scan.scan_notice() is not None
    server._twin_impl("task_evaluate", {})
    assert flow.get_meta(scan.SCAN_NOTICE_SHOWN_KEY) is not None
    bad = server._twin_impl("task_evaluate", {"origin": "manual"})
    assert bad["ok"] is False and "origin" in bad["reason"]
    _clear_backlog()
