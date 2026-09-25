"""v0.4 P3 playbook 闭环：task_evaluate / playbook_submit / playbook_rollback /
验证门 / 连续性分级注入（规格 mema-twin-v0.4-exec-design-2026-09-26.md §9）。"""
import json

import pytest

from mema_twin import flow, playbook_actions, server, templates


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(tmp_path / "twin.sqlite3"))
    monkeypatch.setenv("MEMA_TWIN_PROMPTS_DIR", str(tmp_path / "prompts"))
    monkeypatch.setenv("MEMA_TWIN_DELIVERABLES_DIR", str(tmp_path / "deliverables"))
    flow._schema_ready.clear()
    flow.ensure_schema()
    yield tmp_path
    flow._todos_by_session.clear()


def _dims():
    return {
        "work_type": {"ok": True, "kind": "work_type", "raw": "工作汇报",
                      "code": "work_report", "label_zh": "工作汇报",
                      "matched_by": "exact_or_alias"},
        "audience": {"ok": True, "kind": "audience", "raw": "高层",
                     "code": "leadership", "label_zh": "高层与决策层",
                     "matched_by": "exact_or_alias"},
        "purpose": {"ok": True, "kind": "purpose", "raw": "同步",
                    "code": "sync_info", "label_zh": "信息同步与知会",
                    "matched_by": "exact_or_alias"},
    }


def _closed_task(reflection="模板错位——应先确认模板版本"):
    t = flow.insert_task(brief="写周报", status="planning", dims=_dims())
    plan = server._twin_impl("plan_set", {"task_id": t["id"], "steps": [
        {"title": "取数"}, {"title": "成稿"}]})
    ids = {s["seq"]: s["id"] for s in plan["steps"]}
    if reflection:
        server._twin_impl("step_update", {"step_id": ids[1], "status": "in_progress"})
        server._twin_impl("step_update", {"step_id": ids[1], "status": "failed",
                                          "reflection": reflection})
        server._twin_impl("step_update", {"step_id": ids[1], "status": "in_progress"})
    server._twin_impl("step_update", {"step_id": ids[1], "status": "done"})
    server._twin_impl("step_update", {"step_id": ids[2], "status": "done"})
    sub = server._twin_impl("task_submit", {"task_id": t["id"], "deliverable_md": "# 周报"})
    assert sub["ok"]
    return t


_PB_MD = ("# playbook\n\n## 工具面板\n\n"
          "- 工具 `rg`：找代码；首选 rg；降级 grep；兜底人工。\n"
          "- 工具 `kimi-skill`：周报模板；首选 v2；降级 v1；兜底手写。\n\n"
          "## 失败规避\n\n- 先确认模板版本 `<!-- task: %d -->`\n")


def _submit(key="work_report", tid=1, origin=None, content=None, **kw):
    data = {"key": key, "content_md": content or (_PB_MD % tid),
            "source_task_ids": [tid], "model": "test", **kw}
    if origin:
        data["origin"] = origin
    return server._twin_impl("playbook_submit", data)


# ---- task_evaluate ----

def test_evaluate_watermark_and_material(env):
    t = _closed_task()
    out = server._twin_impl("task_evaluate", {})
    assert out["ok"] and out["task_count"] == 1
    assert out["watermark_moved_to"] == t["id"]
    assert out["key_hint"] == "work_report"
    assert "模板错位" in out["material"]  # reflection 进素材
    assert "失败规避" in out["material"]
    # 水位推进后不重出
    again = server._twin_impl("task_evaluate", {})
    assert again["task_count"] == 0
    # 显式补评估（含 id < 水位）单调推进
    back = server._twin_impl("task_evaluate", {"task_ids": [t["id"]]})
    assert back["task_count"] == 1
    assert back["watermark_moved_to"] == t["id"]


def test_evaluate_tool_usage_layered_by_client(env):
    t = _closed_task()
    server._twin_impl("tool_log", {"task_id": t["id"], "client": "kimi", "entries": [
        {"tool": "rg", "purpose": "找数据", "outcome": "success"}]})
    server._twin_impl("tool_log", {"task_id": t["id"], "client": "jinleai", "entries": [
        {"tool": "rg", "purpose": "找数据", "outcome": "fail", "note": "路径不同"}]})
    out = server._twin_impl("task_evaluate", {})
    assert "宿主 kimi" in out["material"] and "宿主 jinleai" in out["material"]


def test_evaluate_limit_clamp(env):
    _closed_task()
    out = server._twin_impl("task_evaluate", {"limit": 999})
    assert out["ok"] and out["task_count"] == 1
    bad = server._twin_impl("task_evaluate", {"limit": "abc"})
    assert bad["ok"] is False


# ---- playbook_submit 验证门 ----

def test_submit_key_validation(env):
    bad_prefix = server._twin_impl("playbook_submit", {
        "key": "pb-work_report", "content_md": "# x\n", "source_task_ids": [1]})
    assert bad_prefix["ok"] is False and "不带前缀" in bad_prefix["reason"]
    bad_code = server._twin_impl("playbook_submit", {
        "key": "no_such_type", "content_md": "# x\n", "source_task_ids": [1]})
    assert bad_code["ok"] is False and "unknown work_type" in bad_code["reason"]
    no_src = server._twin_impl("playbook_submit", {
        "key": "work_report", "content_md": "# x\n", "source_task_ids": []})
    assert no_src["ok"] is False and "溯源" in no_src["reason"]


def test_submit_foreign_ids_scheduled_vs_interactive(env):
    _closed_task()
    ghost = 424242
    rej = _submit(tid=ghost, origin="scheduled")
    assert rej["ok"] is False and rej["error"] == "validation_failed"
    conn = flow.db.connect()
    try:
        cnt = flow.get_meta("playbook_reject:work_report")
    finally:
        conn.close()
    assert cnt and json.loads(cnt)["count"] == 1
    ok = _submit(tid=ghost)  # 交互式：剔除 + 警告落版
    assert ok["ok"] and any("剔除" in w for w in ok["warnings"])


def test_submit_g1_echo_and_selflock(env):
    t = _closed_task()
    echo_md = ("# x\n\n## 任务执行记录\n\n- 复述素材包\n")
    rej = _submit(tid=t["id"], origin="scheduled", content=echo_md)
    assert rej["ok"] is False and rej["error"] == "validation_failed"
    # 自锁守卫：active 已含标记 → 沿袭降级为警告
    base = _submit(tid=t["id"], content=echo_md)
    assert base["ok"]  # 交互式 violations 只警告
    t2 = _closed_task()
    inherited = _submit(key="work_report", tid=t2["id"],
                        content=echo_md + "\n更新一条\n")
    assert inherited["ok"] and any("沿袭" in w for w in inherited["warnings"])


def test_submit_g2_no_headings_scheduled(env):
    t = _closed_task()
    flat = "没有标题的纯文本 playbook，一段话写完所有内容"
    rej = _submit(tid=t["id"], origin="scheduled", content=flat)
    assert rej["ok"] is False and rej["error"] == "validation_failed"


def test_submit_idle_damping(env):
    t = _closed_task()
    first = _submit(tid=t["id"], origin="scheduled")
    assert first["ok"] and first["version"] == 1
    # 同一批任务再提交（无新任务）→ scheduled 拒、交互放行
    damp = _submit(tid=t["id"], origin="scheduled", content=("# playbook\n\n## x\n\n新内容\n"))
    assert damp["ok"] is False and damp["error"] == "no_new_evidence"
    manual = _submit(tid=t["id"], content=("# playbook\n\n## x\n\n新内容\n"))
    assert manual["ok"] and manual["version"] == 2


def test_submit_first_version_not_damped(env):
    t = _closed_task()
    ok = _submit(tid=t["id"], origin="scheduled")
    assert ok["ok"] and ok["supersedes"] is None


# ---- playbook_rollback ----

def test_rollback_flow(env):
    t = _closed_task()
    _submit(tid=t["id"])
    t2 = _closed_task()
    v2 = _submit(tid=t2["id"], content=("# playbook\n\n## x\n\nv2 新增一条\n"))
    assert v2["ok"] and v2["version"] == 2
    back = server._twin_impl("playbook_rollback", {"key": "work_report"})
    assert back["ok"] and back["version"] == 1 and back["rolled_back_from"] == 2
    again = server._twin_impl("playbook_rollback", {"key": "work_report"})
    assert again["ok"] and again["version"] == 2
    same = server._twin_impl("playbook_rollback", {"key": "work_report", "version": 2})
    assert same["ok"] and "已是 active" in same["note"]
    missing = server._twin_impl("playbook_rollback", {"key": "work_report", "version": 9})
    assert missing["ok"] is False and "可用版本" in missing["reason"]
    empty = server._twin_impl("playbook_rollback", {"key": "no_such"})
    assert empty["ok"] is False


# ---- 连续性分级注入 ----

def test_inject_continuity_levels(env):
    t = _closed_task()
    _submit(tid=t["id"])
    # task_start 走全链路需要 mema——直接用 inject_playbook 单测
    from mema_twin import db, identity
    # 同 client（playbook 无 last_used_client → 视为换 client 重注入）
    out1: dict = {}
    conn = db.connect()
    try:
        playbook_actions.inject_playbook(conn, out1, "work_report", {"client": "kimi"})
    finally:
        conn.close()
    assert "playbook_md" in out1 and out1["available_tools_required"] is True
    assert "其他宿主" in out1["playbook_note"]
    # 同 client 再注入 → 轻注入
    out2: dict = {}
    conn = db.connect()
    try:
        playbook_actions.inject_playbook(conn, out2, "work_report", {"client": "kimi"})
    finally:
        conn.close()
    assert "available_tools_required" not in out2
    assert "近期验证过" in out2["playbook_note"]
    assert out2["playbook_md"] == out1["playbook_md"]


def test_inject_tool_gap(env):
    _closed_task()
    from mema_twin import db
    _submit(tid=1)  # active playbook 含 rg/kimi-skill 工具面板
    out: dict = {}
    conn = db.connect()
    try:
        playbook_actions.inject_playbook(conn, out, "work_report",
                                         {"client": "kimi",
                                          "available_tools": ["rg", "grep"]})
    finally:
        conn.close()
    assert out["tool_gap"] == ["kimi-skill"]
    out2: dict = {}
    conn = db.connect()
    try:
        playbook_actions.inject_playbook(conn, out2, "work_report",
                                         {"client": "kimi",
                                          "available_tools": ["rg", "kimi-skill"]})
    finally:
        conn.close()
    assert "tool_gap" not in out2
    out3: dict = {}
    conn = db.connect()
    try:
        # 两级回退：本类型无 playbook → global；再无 → 不注入
        playbook_actions.inject_playbook(conn, out3, "no_such_type",
                                         {"client": "kimi"})
    finally:
        conn.close()
    assert "playbook_md" not in out3


# ---- 素材包标记互证（防模板改字门静默失效，照既有测试模式）----

def test_playbook_markers_in_material(env):
    _closed_task()
    out = server._twin_impl("task_evaluate", {})
    for marker in templates.PLAYBOOK_MARKERS:
        assert marker in out["material"], marker
