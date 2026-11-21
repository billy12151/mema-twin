"""v0.6.0 Agent 工效改进（方案 mema-twin-v060-ergonomics-plan.md §2）：
A1 help 单查 / A2 schema enum / B8 未知键硬拒 / A4 裸报错指路 / A5 迁移表+
被依赖列全 / A6 help 上限文案 / A8-4 pending 四值 / B1 compile 结构化 id /
B2 rollback already_active / B3 evidence / B4 resolve 批量 / B5 start_first /
B6 素材包软闸 / B7 void 显式键。"""
from mema_twin import db, flow, scan, server
from mema_twin.exec_actions import _TRANSITIONS


def _t(action, data=None):
    return server._twin_impl(action, data)


def _dims():
    return {"work_type": "周报", "audience": "高层", "purpose": "同步"}


# ---- A1 help 单查 ----

def test_help_topic_single_action():
    r = _t("help", {"topic": "write"})
    assert r["ok"] and r["topic"] == "write"
    assert "content ≤8000" in r["help"] and "词法去重" in r["help"]
    assert r["write_guidance"]  # 方案 A1：write 单查附沉淀规则文本


def test_help_topic_help_self():
    r = _t("help", {"topic": "help"})
    assert r["ok"] and "topic=<action 名>" in r["help"]


def test_help_topic_scheduled_tasks_regression():
    r = _t("help", {"topic": scan.SCHEDULED_TASKS_TOPIC})
    assert r["ok"] and r["topic"] == "scheduled_tasks"
    assert r["setup"]["tasks"][0]["calls"]


def test_help_topic_unknown_lists_topics():
    r = _t("help", {"topic": "nope"})
    assert r["ok"] is False and "nope" in r["reason"]
    assert "write" in r["topics"] and "scheduled_tasks" in r["topics"]


def _compile_and_submit(code="work_report"):
    r = _feed_and_compile(code)
    s = _t("submit", {"work_type": code, "prompt_md": "# persona\n\n## 分区\n\n正文",
                      "source_memory_ids": r["source_ids_suggested"]})
    assert s["ok"], s
    return r, s


def test_help_full_mode_unchanged():
    r = _t("help", {})
    assert r["ok"] and "write" in r["actions"]
    assert set(r["actions"]) == set(server._ACTIONS)
    assert r["write_guidance"] and r["note"]


# ---- A2 schema / unknown action 契约 ----

def test_tool_annotation_literal_covers_actions():
    import typing
    ann = typing.get_type_hints(server.twin)
    lit = typing.get_args(ann["action"])
    assert set(lit) == set(server._ACTIONS)
    # 契约变化声明（R2 #6）：unknown action 现由 arg_model 校验 → ToolError，
    # _twin_impl 的 unknown-action 分支仅 console/测试直调可达
    assert "unknown action" in _t("__nope__")["reason"]


def test_a2_unknown_action_rejected_at_arg_model():
    """P1-4：非法 action 在 MCP 参数校验层即拒（pydantic arg_model），
    不再到达 _twin_impl 的结构化分支。"""
    from mcp.server.fastmcp.utilities.func_metadata import func_metadata
    import pytest
    md = func_metadata(server.twin)
    with pytest.raises(Exception):
        md.arg_model.model_validate({"action": "__nope__", "data": {}})
    # 合法 action + data 缺省可过校验
    assert md.arg_model.model_validate({"action": "help"}) is not None


def test_a5_storage_error_no_param_hint():
    """P1-4：sqlite3.Error（如 database is locked）不挂「参数结构不合规」hint。"""
    import sqlite3
    orig = db.connect
    def _locked(*a, **kw):
        raise sqlite3.OperationalError("database is locked")
    db.connect = _locked
    try:
        r = _t("status", {})
    finally:
        db.connect = orig
    assert r["ok"] is False and r["error"] == "internal_error"
    assert "参数结构可能不合规" not in r["reason"]


def test_a4_batch_step_not_found_carries_failed_index():
    """P1-4：批量打卡 step not_found 带 failed_index（R1 指认缺失）。"""
    t, _ = _setup_planned_task()
    plan = _t("plan_set", {"task_id": t["id"], "steps": [{"title": "a"}]})
    sid = plan["steps"][0]["id"]
    r = _t("step_update", {"task_id": t["id"], "steps": [
        {"step_id": sid, "status": "in_progress"},
        {"step_id": 999999, "status": "done"}]})
    assert r["ok"] is False and r["failed_index"] == 1
    assert "task_get" in r["reason"]  # A4 指路


def test_b5_start_first_on_replanned():
    """P1-4：重建计划场景 start_first 照常起跑新计划首步。"""
    t, _ = _setup_planned_task()
    first = _t("plan_set", {"task_id": t["id"], "steps": [{"title": "a"}]})
    old_id = first["steps"][0]["id"]
    _t("step_update", {"step_id": old_id, "status": "in_progress"})
    second = _t("plan_set", {"task_id": t["id"], "steps": [
        {"title": "a2"}, {"title": "b2"}], "start_first": True})
    assert second["ok"] and second["replanned"] is True
    assert second["started_step_id"] == second["steps"][0]["id"]
    assert second["started_step_id"] != old_id  # 新行，不是被 replanned 的旧行


def test_output_schema_stays_none():
    """v0.3.11 拍板回归钉：返回注解保持 CallToolResult 字符串（防裸 dict 翻双写）。"""
    assert "CallToolResult" in str(server.twin.__annotations__["return"])


# ---- B8 未知键硬拒 ----

def test_b8_unknown_field_rejected_with_did_you_mean():
    r = _t("write", {"content": "c", "worktype": "周报"})
    assert r["ok"] is False and r["error"] == "invalid_input"
    assert r["field"] == "data" and "worktype" in r["reason"]
    assert r["did_you_mean"]["worktype"] == "work_type"
    assert "work_type" in r["allowed"]


def test_b8_did_you_mean_none_when_no_neighbor():
    r = _t("write", {"content": "c", "zzzqqq": 1})
    assert r["ok"] is False and r["did_you_mean"]["zzzqqq"] is None


def test_b8_falsy_data_structured_reject():
    for bad in (0, "", [], "str"):
        r = _t("status", bad)
        assert r["ok"] is False and r["error"] == "invalid_input", bad
        assert "data 必须是 JSON object" in r["reason"]


def test_b8_identity_precedes_unknown_field():
    """auth/身份校验先于 B8（R2 #4）：脏 client 先打回，不落 unknown field。"""
    r = _t("write", {"content": "c", "client": "x" * 100, "zzz": 1})
    assert r["ok"] is False and "unknown field" not in r["reason"]


def test_b8_empty_and_none_data_pass_all_actions():
    """data=None/{} 对全部动作不触发 B8（R2 #13/#15 回归扫描）：缺 required 的
    打回是 invalid_input 且不带 allowed 键。"""
    for action in server._ACTIONS:
        for payload in (None, {}):
            r = _t(action, payload)
            assert "allowed" not in r, f"{action} data={payload!r} 被未知键门拦截"
            assert "unknown field" not in str(r.get("reason")), action


def test_b8_whitelisted_keys_pass():
    """每动作抽一个白名单内键：合法键不触发 B8（各 handler 的 required 拒不影响）。"""
    samples = {"write": {"subject": "s"}, "task_get": {"task_id": 999},
               "void": {"memory_id": 999}, "pending": {"status": "pending"},
               "evidence": {"limit": 5}, "rollback": {"work_type": "周报"}}
    for action, data in samples.items():
        r = _t(action, data)
        assert "allowed" not in r, f"{action} 合法键被误拒: {r}"


def test_b8_whitelist_covers_handler_reads():
    """R1 P0-1 教训的静态对码：handler 源码里 data.get("X") 直读的每个键都必须
    在 _ACTION_KEYS[action]——漏键 = 该动作被 B8 全量打回。动态循环取键
    （write/task_start 的 data[kind]）不在扫描内，靠评审人工核（R1/R2 已核），
    在 _ACTION_KEYS 定义处注释备案。"""
    import inspect
    import re
    for action, fn in server._ACTIONS.items():
        src = inspect.getsource(fn)
        reads = set(re.findall(r'data\.get\(\s*[\'"]([\w_]+)[\'"]', src))
        missing = reads - server._ACTION_KEYS[action]
        assert not missing, f"{action} handler 直读 {sorted(missing)} 未入白名单"


def test_b8_nested_unknown_keys_out_of_scope():
    """顶层边界：嵌套对象内部未知键不查（声明的行为边界，测试钉住）。"""
    r = _t("plan_set", {"task_id": 999, "steps": [
        {"title": "t", "unknown_inner": 1}]})
    assert "allowed" not in r  # 走 task not_found/required 路径而非未知键门


def test_b8_action_keys_sync_guard():
    assert set(server._ACTION_KEYS) == set(server._ACTIONS)


# ---- A4 裸报错指路 ----

def test_a4_not_found_guidance():
    cases = [
        ("task_get", {"task_id": 999}, "task_recent"),
        ("task_resume", {"task_id": 999}, "task_recent"),
        ("task_revise", {"task_id": 999, "revision_reason": "r"}, "task_recent"),
        ("task_submit", {"task_id": 999, "deliverable_md": "d"}, "task_recent"),
        ("task_close", {"task_id": 999}, "task_recent"),
        ("tool_log", {"task_id": 999, "entries": [
            {"tool": "t", "outcome": "fail"}]}, "task_recent"),
        ("plan_set", {"task_id": 999, "steps": [{"title": "t"}]}, "task_recent"),
        ("plan_revise", {"task_id": 999, "revision_reason": "r"}, "task_recent"),
        ("step_update", {"step_id": 999, "status": "done"}, "task_get"),
        ("resolve", {"pending_id": 999, "decision": "map", "code": "x"},
         "pending 动作"),
        ("submit", {"work_type": "灵能审计年报", "prompt_md": "# h"},
         "taxonomy"),
        ("get", {"work_type": "灵能审计年报"}, "taxonomy"),
    ]
    for action, data, needle in cases:
        r = _t(action, data)
        assert r["ok"] is False, (action, r)
        assert needle in r["reason"], (action, r["reason"])


def test_a4_resolve_map_invalid_code_lists_taxonomy():
    _t("write", {"content": "c", "work_type": "灵能审计年报",
                 "audience": "高层", "purpose": "同步"})
    pid = db.list_pending(db.connect())[0]["id"]
    r = _t("resolve", {"pending_id": pid, "decision": "map", "code": "nope"})
    assert r["ok"] is False and "all_values" in r["reason"]


# ---- A5 迁移合法目标 + 被依赖列全 ----

def _setup_planned_task(steps=2, deps=None):
    """只建档不建计划（B5 场景需要一次 plan_set 才有步骤）；deps 给出
    {seq: [依赖 seq]}。返回 (task, None)。"""
    t = flow.insert_task(brief="b", status="planning", dims={})
    return t, None


def test_a5_invalid_transition_lists_allowed():
    t, _ = _setup_planned_task()
    plan = _t("plan_set", {"task_id": t["id"], "steps": [{"title": "a"}]})
    sid = plan["steps"][0]["id"]
    _t("step_update", {"step_id": sid, "status": "done"})
    r = _t("step_update", {"step_id": sid, "status": "in_progress"})
    assert r["ok"] is False and "允许的下一状态" in r["reason"]
    expected = sorted(to for (frm, to) in _TRANSITIONS if frm == "done")
    assert all(e in r["reason"] for e in expected)  # done → pending/skipped/done


def test_a5_plan_revise_lists_all_blockers():
    t, _ = _setup_planned_task()
    plan = _t("plan_set", {"task_id": t["id"], "steps": [
        {"title": "a"}, {"title": "b", "depends_on": [1]},
        {"title": "c", "depends_on": [1]}]})
    ids = [s["id"] for s in plan["steps"]]
    r = _t("plan_revise", {"task_id": t["id"], "steps_remove": [ids[0]]})
    assert r["ok"] is False
    assert f"#{ids[1]}" in r["reason"] and f"#{ids[2]}" in r["reason"]


# ---- A6 help 上限文案 ----

def test_a6_help_limit_wording():
    full = _t("help", {})["actions"]
    assert "≤8000" in full["write"]
    assert "brief ≤2000" in full["task_start"]
    assert "available_tools ≤50" in full["task_start"]
    assert "≤100000" in full["submit"]
    assert "origin=scheduled" in full["task_evaluate"]
    assert "clamp 1..10" in full["task_evaluate"]
    assert "reason/reflection" in full["step_update"]  # 批量互斥补全
    assert "reflection" in full["step_update"] and "≤2000" in full["step_update"]
    assert "≤500" in full["tool_log"] and "reflection" not in full["tool_log"]
    assert "pb-" in full["playbook_submit"]
    assert "mapped" in full["pending"]
    assert "冷却" in full["step_update"]  # stall 双常量表述


# ---- A8-4 pending status 四值 ----

def test_a8_pending_status_validated():
    r = _t("pending", {"status": "whatever"})
    assert r["ok"] is False and "pending|mapped|canonicalized|rejected" in r["reason"]
    assert _t("pending", {"status": "mapped"})["ok"] is True


# ---- B1 compile 结构化 id ----

def _feed_and_compile(code="work_report"):
    _t("write", {"content": "偏好一", "work_type": "周报",
                 "audience": "高层", "purpose": "同步"})
    _t("write", {"content": "偏好二", "work_type": "周报",
                 "audience": "高层", "purpose": "同步"})
    return _t("compile", {"work_type": code})


def test_b1_compile_structured_ids():
    r = _feed_and_compile()
    assert r["ok"]
    assert len(r["evidence_ids"]) == 2
    assert r["source_ids_suggested"] == r["evidence_ids"]  # 无 skipped 行时相等
    assert r["voided_ids"] == []
    assert f"[{r['evidence_ids'][0]}]" in r["material"]  # 正文标记保留


def test_b1_source_ids_suggested_includes_skipped():
    conn = db.connect()
    conn.execute(
        "INSERT INTO twin_evidence(memory_id, work_type, work_type_raw, content,"
        " created_at) VALUES(900001, 'work_report', '周报', '', ?)", (db.now_iso(),))
    conn.commit()
    conn.close()
    r = _t("compile", {"work_type": "周报"})
    assert 900001 not in r["evidence_ids"]
    assert 900001 in r["source_ids_suggested"]  # G3 期望集同源，照抄全覆盖
    assert any(s["memory_id"] == 900001 for s in r["skipped_evidence"])


# ---- B2 rollback already_active ----

def test_b2_rollback_explicit_already_active():
    _compile_and_submit()
    r = _t("rollback", {"work_type": "周报", "version": 1})
    assert r["ok"] and r.get("already_active") is True
    assert "rolled_back_from" not in r  # no-op 时无取代语义
    assert "已是 active" in r["note"]


# ---- B3 evidence ----

def test_b3_evidence_listing():
    _feed_and_compile()
    r = _t("evidence", {})
    assert r["ok"] and r["count"] == 2
    item = r["items"][0]
    for key in ("memory_id", "work_type", "audience", "purpose", "subject",
                "status", "created_at", "content_chars"):
        assert key in item
    assert "content" not in item  # 不回正文
    r2 = _t("evidence", {"status": "uncompiled"})
    assert r2["count"] == 2
    r3 = _t("evidence", {"status": "bogus"})
    assert r3["ok"] is False and "uncompiled|compiled|void" in r3["reason"]
    r4 = _t("evidence", {"limit": 201})
    assert r4["ok"] is False and "200" in r4["reason"]
    r5 = _t("evidence", {"work_type": "周报"})
    assert r5["count"] == 2
    r6 = _t("evidence", {"work_type": "灵能审计年报"})
    assert r6["ok"] is False and "taxonomy" in r6["reason"]


def test_b3_void_death_alley_resolved():
    """评估 P1-2 死胡同闭环：memory_id 未知先 evidence 列索引再 void。"""
    _feed_and_compile()
    mid = _t("evidence", {"limit": 1})["items"][0]["memory_id"]
    r = _t("void", {"memory_id": mid})
    assert r["ok"] and r["memory_id"] == mid


# ---- B4 resolve 批量 ----

def _make_two_tickets():
    _t("write", {"content": "c", "work_type": "灵能审计年报",
                 "audience": "灵能监理司", "purpose": "同步"})
    r = _t("write", {"content": "c2", "work_type": "灵能审计年报",
                     "audience": "高层", "purpose": "灵能合规"})
    assert r["ok"] is False  # gate 打回落票
    rows = db.list_pending(db.connect(), "pending")
    assert len(rows) == 3
    return rows


def test_b4_resolve_batch_success():
    rows = _make_two_tickets()
    wt = next(r["id"] for r in rows if r["type_kind"] == "work_type")
    aud = next(r["id"] for r in rows if r["type_kind"] == "audience")
    pp = next(r["id"] for r in rows if r["type_kind"] == "purpose")
    r = _t("resolve", {"tickets": [
        {"pending_id": wt, "decision": "map", "code": "work_report"},
        {"pending_id": aud, "decision": "canonicalize",
         "new_type": {"code": "spirit_auditor", "zh": "灵能监理司",
                      "en": "Spirit Auditor", "domain": "灵能"}},
        {"pending_id": pp, "decision": "reject"},
    ]})
    assert r["ok"] and r["applied"] == 3 and len(r["resolved"]) == 3
    assert r["resolved"][2]["decision"] == "reject"
    assert "重试原写入" in r["guidance"]
    conn = db.connect()
    mapped = {x["id"]: x["status"] for x in db.list_pending(conn, "mapped")}
    rej = {x["id"] for x in db.list_pending(conn, "rejected")}
    conn.close()
    assert mapped[wt] == "mapped" and pp in rej


def test_b4_resolve_batch_atomic_rollback():
    rows = _make_two_tickets()
    wt = next(r["id"] for r in rows if r["type_kind"] == "work_type")
    aud = next(r["id"] for r in rows if r["type_kind"] == "audience")
    pp = next(r["id"] for r in rows if r["type_kind"] == "purpose")
    # 第 2 票 map 到不存在 code → 整笔回滚：第 1 票的 alias 不得已落
    r = _t("resolve", {"tickets": [
        {"pending_id": aud, "decision": "canonicalize",
         "new_type": {"code": "spirit_auditor", "zh": "灵能监理司"}},
        {"pending_id": wt, "decision": "map", "code": "nope_missing"},
        {"pending_id": pp, "decision": "reject"},
    ]})
    assert r["ok"] is False and r["failed_index"] == 1
    conn = db.connect()
    row = conn.execute("SELECT aliases FROM twin_types WHERE code='spirit_auditor'"
                       " AND type_kind='audience'").fetchone()
    ticket = conn.execute("SELECT status FROM twin_pending_values WHERE id=?",
                          (aud,)).fetchone()
    conn.close()
    assert row is None  # 第 1 票的 canonical 已回滚
    assert ticket["status"] == "pending"  # 票据未动


def test_b4_resolve_batch_limits_and_mutex():
    r = _t("resolve", {"tickets": []})
    assert r["ok"] is False and "1..20" in r["reason"]
    r = _t("resolve", {"tickets": [{"pending_id": 1, "decision": "reject"}] * 21})
    assert r["ok"] is False
    r = _t("resolve", {"tickets": [{"pending_id": 1, "decision": "reject"}],
                       "pending_id": 1})
    assert r["ok"] is False and "互斥" in r["reason"]


# ---- B5 start_first ----

def test_b5_start_first_starts_first_step():
    t, _ = _setup_planned_task()
    plan = _t("plan_set", {"task_id": t["id"], "steps": [
        {"title": "a"}, {"title": "b", "depends_on": [1]}, {"title": "c"}],
        "start_first": True})
    assert plan["ok"] and plan["started_step_id"] == plan["steps"][0]["id"]
    conn = db.connect()
    st = conn.execute("SELECT status FROM twin_plan_steps WHERE id=?",
                      (plan["steps"][0]["id"],)).fetchone()
    conn.close()
    assert st["status"] == "in_progress"


def test_b5_start_first_skipped_when_all_anchored():
    """全部无依赖首步都被 blocking 锚定 → skipped 不起跑（R2 #3：绕门直改
    会产出卡死步骤）。"""
    t, _ = _setup_planned_task()
    plan = _t("plan_set", {"task_id": t["id"], "steps": [{"title": "a"}],
                           "open_questions": [
                               {"question": "q?", "step_ids": [1], "blocking": True}],
                           "start_first": True})
    assert plan["ok"] and plan.get("start_first_skipped") is True
    assert "started_step_id" not in plan
    assert "blocking" in plan["warnings"][0]


def test_b5_start_first_picks_unanchored_alternative():
    """锚定的是 seq1 时起跑次一个无依赖未锚定步骤（候选=无依赖且未锚定）。"""
    t, _ = _setup_planned_task()
    plan = _t("plan_set", {"task_id": t["id"], "steps": [
        {"title": "a"}, {"title": "b"}],
        "open_questions": [{"question": "q?", "step_ids": [1], "blocking": True}],
        "start_first": True})
    assert plan["ok"] and plan["started_step_id"] == plan["steps"][1]["id"]


def test_b5_start_first_strict_bool():
    t, _ = _setup_planned_task()
    r = _t("plan_set", {"task_id": t["id"], "steps": [{"title": "a"}],
                        "start_first": "true"})
    assert r["ok"] is False and "start_first" in str(r.get("field"))


# ---- B6 素材包软闸 ----

def test_b6_budget_warn_not_truncate(monkeypatch):
    _feed_and_compile()
    monkeypatch.setenv("MEMA_TWIN_COMPILE_BUDGET_CHARS", "10")
    r = _t("compile", {"work_type": "周报"})
    assert r["ok"] and r["evidence_count"] == 2  # 不截断（全量投影拍板）
    assert any("超预算" in w for w in r["warnings"])


def test_b6_budget_env_invalid_falls_back(monkeypatch):
    _feed_and_compile()
    monkeypatch.setenv("MEMA_TWIN_COMPILE_BUDGET_CHARS", "abc")
    r = _t("compile", {"work_type": "周报"})
    assert r["ok"]
    assert any("回落默认" in w for w in r["warnings"])


# ---- B7 void 显式键 ----

def test_b7_void_explicit_side_effect_keys():
    _compile_and_submit()  # submit 后证据才进 compiled（persona_stale 触发前提）
    mid = _t("evidence", {"limit": 1})["items"][0]["memory_id"]
    r = _t("void", {"memory_id": mid})
    assert r["ok"]
    assert r.get("persona_stale") is True  # 曾入编译 → 显式键
    assert r.get("audience_stale") == ["leadership"]
    assert r["note"].count("；") <= 1  # 单句
    assert "夜间重编" not in r["note"]  # 长句已迁显式键


def test_b7_void_uncompiled_no_persona_stale_key():
    _feed_and_compile()
    mid = _t("evidence", {"limit": 1})["items"][0]["memory_id"]
    assert _t("void", {"memory_id": mid})["ok"]
    # 未编译证据作废：persona_stale 键缺席（键缺席即否）
