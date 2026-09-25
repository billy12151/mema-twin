"""v0.4 P1/P2 执行计划层：状态机、三门、收口门、深拷贝、plan_set/step_update/
plan_revise/tool_log（规格 mema-twin-v0.4-exec-design-2026-09-26.md §9）。"""
import json

import pytest

from mema_twin import exec_actions, flow, server


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


def _mk_task(**kw):
    return flow.insert_task(brief=kw.pop("brief", "T"), status="planning",
                            dims=_dims(), **kw)


def _plan(task_id, steps=None, questions=None):
    return server._twin_impl("plan_set", {
        "task_id": task_id,
        "steps": steps or [{"title": "s1"},
                           {"title": "s2", "depends_on": [1]},
                           {"title": "s3", "depends_on": [2]}],
        "open_questions": questions or [],
    })


def _step_ids(r):
    return {s["seq"]: s["id"] for s in r["steps"]}


# ---- 迁移表 ----

def test_transition_full_chain(env):
    t = _mk_task()
    r = _plan(t["id"])
    s1 = _step_ids(r)[1]
    assert server._twin_impl("step_update", {"step_id": s1, "status": "in_progress"})["ok"]
    assert server._twin_impl("step_update", {"step_id": s1, "status": "done"})["ok"]
    # done 态的非法迁移：in_progress / failed
    assert server._twin_impl("step_update", {"step_id": s1, "status": "in_progress"})["ok"] is False
    assert server._twin_impl("step_update", {"step_id": s1, "status": "failed",
                                             "reflection": "x"})["ok"] is False
    # done→pending 撤销（带 reason）→ failed → 原步骤重试 → done
    assert server._twin_impl("step_update", {"step_id": s1, "status": "pending",
                                             "reason": "返工"})["ok"]
    server._twin_impl("step_update", {"step_id": s1, "status": "in_progress"})
    assert server._twin_impl("step_update", {"step_id": s1, "status": "failed",
                                             "reflection": "依赖缺材料"})["ok"]
    assert server._twin_impl("step_update", {"step_id": s1, "status": "in_progress"})["ok"]
    assert server._twin_impl("step_update", {"step_id": s1, "status": "done"})["ok"]
    # failed→pending 非法
    server._twin_impl("step_update", {"step_id": s1, "status": "in_progress"})
    server._twin_impl("step_update", {"step_id": s1, "status": "failed",
                                      "reflection": "again"})
    assert server._twin_impl("step_update", {"step_id": s1, "status": "pending"})["ok"] is False


def test_reflection_required_on_failed(env):
    t = _mk_task()
    r = _plan(t["id"])
    s1 = _step_ids(r)[1]
    server._twin_impl("step_update", {"step_id": s1, "status": "in_progress"})
    miss = server._twin_impl("step_update", {"step_id": s1, "status": "failed"})
    assert miss["ok"] is False and "reflection" in miss["reason"]
    blank = server._twin_impl("step_update", {"step_id": s1, "status": "failed",
                                              "reflection": "   "})
    assert blank["ok"] is False
    ok = server._twin_impl("step_update", {"step_id": s1, "status": "failed",
                                           "reflection": "工具不可用，应先确认环境"})
    assert ok["ok"] and "三选一" in ok["guidance"]


def test_backfilled_auto_flag(env):
    t = _mk_task()
    r = _plan(t["id"])
    ids = _step_ids(r)
    r1 = server._twin_impl("step_update", {"step_id": ids[1], "status": "done"})
    assert r1["ok"] and r1["backfilled"] is True  # pending 直跳 done = 追认
    server._twin_impl("step_update", {"step_id": ids[2], "status": "in_progress"})
    r2 = server._twin_impl("step_update", {"step_id": ids[2], "status": "done"})
    assert r2["ok"] and r2["backfilled"] is False  # 正常路径不标


def test_concurrent_transition_conflict(env):
    t = _mk_task()
    r = _plan(t["id"])
    s1 = _step_ids(r)[1]
    server._twin_impl("step_update", {"step_id": s1, "status": "in_progress"})
    # 模拟并发：读旧快照后他方改状态，本方按旧 from 条件迁移 → 打回当前态
    stale = exec_actions.get_step(s1)
    conn = flow.db.connect()
    try:
        conn.execute("UPDATE twin_plan_steps SET status='done' WHERE id=?", (s1,))
        conn.commit()
        with pytest.raises(ValueError):
            exec_actions._apply_step_transition(conn, stale, "done", None, None)
    finally:
        conn.close()


# ---- 三门 ----

def test_dependency_gate(env):
    t = _mk_task()
    r = _plan(t["id"])
    ids = _step_ids(r)
    blocked = server._twin_impl("step_update", {"step_id": ids[2], "status": "in_progress"})
    assert blocked["ok"] is False and "依赖" in blocked["reason"]
    assert blocked["open_steps"][0]["id"] == ids[1]
    # 依赖 skipped → 放行（此路不通已确认）
    assert server._twin_impl("step_update", {"step_id": ids[1], "status": "skipped",
                                             "reason": "不需要"})["ok"]
    # ids[2] 依赖 ids[1]（skipped）且 ids[1] 之前依赖无——但单一 in_progress 门不拦（无 in_progress）
    assert server._twin_impl("step_update", {"step_id": ids[2], "status": "in_progress"})["ok"]


def test_single_in_progress_gate_and_backfill_exempt(env):
    t = _mk_task()
    # 并行无依赖步骤：隔离单一门（依赖门不先拦）
    r = _plan(t["id"], steps=[{"title": "a"}, {"title": "b"}, {"title": "c"}])
    ids = _step_ids(r)
    server._twin_impl("step_update", {"step_id": ids[1], "status": "in_progress"})
    blocked = server._twin_impl("step_update", {"step_id": ids[2], "status": "in_progress"})
    assert blocked["ok"] is False and "单一" in blocked["reason"]
    # 追认 done 不受单一门限制
    assert server._twin_impl("step_update", {"step_id": ids[3], "status": "done"})["ok"]


def test_blocking_question_gate_and_unlock(env):
    t = _mk_task()
    q = [{"question": "格式要跟哪个模板？", "step_ids": [2], "blocking": True}]
    r = _plan(t["id"], questions=q)
    ids = _step_ids(r)
    qid = r["open_questions"][0]["id"]
    # fyi 步骤不受门限
    server._twin_impl("step_update", {"step_id": ids[1], "status": "in_progress"})
    server._twin_impl("step_update", {"step_id": ids[1], "status": "done"})
    blocked = server._twin_impl("step_update", {"step_id": ids[2], "status": "in_progress"})
    assert blocked["ok"] is False and "blocking" in blocked["reason"]
    assert blocked["blocking_questions"][0]["id"] == qid
    # 解锁后放行
    server._twin_impl("plan_revise", {"task_id": t["id"],
                                      "answers": [{"question_id": qid, "answer": "用 A 模板"}]})
    assert server._twin_impl("step_update", {"step_id": ids[2], "status": "in_progress"})["ok"]
    dup = server._twin_impl("plan_revise", {"task_id": t["id"],
                                            "answers": [{"question_id": qid, "answer": "again"}]})
    assert dup["ok"] is False and "已解答" in dup["reason"]


def test_fyi_question_does_not_block(env):
    t = _mk_task()
    r = _plan(t["id"], questions=[{"question": "顺便问下口径？", "step_ids": [1]}])
    ids = _step_ids(r)
    assert server._twin_impl("step_update", {"step_id": ids[1], "status": "in_progress"})["ok"]


def test_gate_order_blocking_first(env):
    """同时撞多门只报第一道：blocking 疑问门在依赖门之前。"""
    t = _mk_task()
    r = _plan(t["id"], questions=[{"question": "q?", "step_ids": [3], "blocking": True}])
    ids = _step_ids(r)
    server._twin_impl("step_update", {"step_id": ids[1], "status": "in_progress"})
    hit = server._twin_impl("step_update", {"step_id": ids[3], "status": "in_progress"})
    assert hit["ok"] is False and "blocking_questions" in hit


# ---- plan_set ----

def test_plan_set_validation(env):
    t = _mk_task()
    cyc = server._twin_impl("plan_set", {"task_id": t["id"], "steps": [
        {"title": "a", "depends_on": [2]}, {"title": "b", "depends_on": [1]}]})
    assert cyc["ok"] is False and "环" in cyc["reason"]
    self_dep = server._twin_impl("plan_set", {"task_id": t["id"], "steps": [
        {"title": "a", "depends_on": [1]}]})
    assert self_dep["ok"] is False and "自身" in self_dep["reason"]
    oob = server._twin_impl("plan_set", {"task_id": t["id"], "steps": [
        {"title": "a", "depends_on": [5]}]})
    assert oob["ok"] is False and "超出范围" in oob["reason"]
    missing = server._twin_impl("plan_set", {"task_id": 99999, "steps": [{"title": "a"}]})
    assert missing["ok"] is False and missing["error"] == "not_found"


def test_plan_set_seq_to_id_translation(env):
    t = _mk_task()
    r = _plan(t["id"], questions=[{"question": "q?", "step_ids": [3], "blocking": True}])
    ids = _step_ids(r)
    assert r["open_questions"][0]["step_ids"] == [ids[3]]  # 序号已翻译为 id


def test_plan_set_replan(env):
    t = _mk_task()
    r = _plan(t["id"])
    ids = _step_ids(r)
    server._twin_impl("step_update", {"step_id": ids[1], "status": "done"})
    server._twin_impl("step_update", {"step_id": ids[2], "status": "in_progress"})
    r2 = _plan(t["id"], steps=[{"title": "new1"}, {"title": "new2"}])
    assert r2["ok"] and r2["replanned"] is True
    assert r2["replanned_steps"] == 2  # in_progress + pending 被跳过；done 保留
    conn = flow.db.connect()
    try:
        kept_done = conn.execute(
            "SELECT status FROM twin_plan_steps WHERE id=?", (ids[1],)).fetchone()
        old_ip = conn.execute(
            "SELECT status, reason FROM twin_plan_steps WHERE id=?", (ids[2],)).fetchone()
    finally:
        conn.close()
    assert kept_done["status"] == "done"  # done 历史保留
    assert old_ip["status"] == "skipped" and old_ip["reason"] == "replanned"


# ---- plan_revise ----

def test_plan_revise_application_order(env):
    t = _mk_task()
    r = _plan(t["id"])
    ids = _step_ids(r)
    out = server._twin_impl("plan_revise", {"task_id": t["id"],
                                            "steps_remove": [ids[3]],
                                            "steps_add": [{"title": "s4"}],
                                            "revision_reason": "改计划"})
    assert out["ok"] and out["removed"] == [ids[3]] and out["added"][0]["seq"] == 3


def test_plan_revise_remove_guards(env):
    t = _mk_task()
    r = _plan(t["id"])
    ids = _step_ids(r)
    server._twin_impl("step_update", {"step_id": ids[1], "status": "in_progress"})
    # 已开工不可删
    started = server._twin_impl("plan_revise", {"task_id": t["id"],
                                                "steps_remove": [ids[1]]})
    assert started["ok"] is False and "已开工" in started["reason"]
    server._twin_impl("step_update", {"step_id": ids[1], "status": "skipped",
                                      "reason": "pass"})
    # 链尾 s3 无人依赖 → 可删
    removable = server._twin_impl("plan_revise", {"task_id": t["id"],
                                                  "steps_remove": [ids[3]]})
    assert removable["ok"] and removable["removed"] == [ids[3]]
    # 加 e 依赖 s2；再删 s2 → 被本代未删步骤 e 依赖 → 拦
    added = server._twin_impl("plan_revise", {"task_id": t["id"],
                                              "steps_add": [{"title": "e",
                                                             "depends_on": [ids[2]]}]})
    assert added["ok"]
    depended = server._twin_impl("plan_revise", {"task_id": t["id"],
                                                 "steps_remove": [ids[2]]})
    assert depended["ok"] is False and "依赖" in depended["reason"]


def test_plan_revise_requires_existing_plan(env):
    t = _mk_task()
    out = server._twin_impl("plan_revise", {"task_id": t["id"], "revision_reason": "x"})
    assert out["ok"] is False and "尚未建计划" in out["reason"]


def test_plan_revise_cycle_check(env):
    """环经 steps_update 构造：4 步链 a→b→c→d，改 a 依赖 d 得 a→d→c→b→a。"""
    t = _mk_task()
    r = _plan(t["id"], steps=[{"title": "a"}, {"title": "b", "depends_on": [1]},
                              {"title": "c", "depends_on": [2]},
                              {"title": "d", "depends_on": [3]}])
    ids = _step_ids(r)
    out = server._twin_impl("plan_revise", {"task_id": t["id"],
                                            "steps_update": [
                                                {"id": ids[1], "depends_on": [ids[4]]},
                                            ]})
    assert out["ok"] is False and "环" in out["reason"]


# ---- 收口门 / outcome / task_close ----

def test_submit_gate_no_plan_unaffected(env):
    t = _mk_task()
    r = server._twin_impl("task_submit", {"task_id": t["id"], "deliverable_md": "# 稿"})
    assert r["ok"]


def test_submit_gate_blocks_open_steps(env):
    t = _mk_task()
    r = _plan(t["id"])
    ids = _step_ids(r)
    server._twin_impl("step_update", {"step_id": ids[1], "status": "in_progress"})
    blocked = server._twin_impl("task_submit", {"task_id": t["id"],
                                                "deliverable_md": "# 稿"})
    assert blocked["ok"] is False and "未闭环" in blocked["reason"]
    assert {s["id"] for s in blocked["open_steps"]} == {ids[1], ids[2], ids[3]}
    # 对账后放行 + outcome=success
    server._twin_impl("step_update", {"step_id": ids[1], "status": "done"})
    server._twin_impl("step_update", {"step_id": ids[2], "status": "skipped",
                                      "reason": "砍掉"})
    server._twin_impl("step_update", {"step_id": ids[3], "status": "done"})
    ok = server._twin_impl("task_submit", {"task_id": t["id"], "deliverable_md": "# 稿"})
    assert ok["ok"]
    assert flow.get_task(t["id"])["outcome"] == "success"


def test_submit_blocking_question_warns_not_blocks(env):
    t = _mk_task()
    r = _plan(t["id"], questions=[{"question": "q?", "step_ids": [1], "blocking": True}])
    ids = _step_ids(r)
    server._twin_impl("step_update", {"step_id": ids[1], "status": "done"})
    server._twin_impl("step_update", {"step_id": ids[2], "status": "done"})
    server._twin_impl("step_update", {"step_id": ids[3], "status": "done"})
    ok = server._twin_impl("task_submit", {"task_id": t["id"], "deliverable_md": "# 稿"})
    assert ok["ok"]
    assert any("blocking" in w for w in ok.get("warnings", []))


def test_task_close_batch_skip_and_outcome(env):
    t = _mk_task()
    r = _plan(t["id"])
    ids = _step_ids(r)
    server._twin_impl("step_update", {"step_id": ids[1], "status": "in_progress"})
    out = server._twin_impl("task_close", {"task_id": t["id"], "outcome": "failed"})
    assert out["ok"] and out["warnings"] and "closed 跳过" in out["warnings"][0]
    assert flow.get_task(t["id"])["outcome"] == "failed"
    conn = flow.db.connect()
    try:
        rows = conn.execute(
            "SELECT status, reason FROM twin_plan_steps WHERE task_id=?",
            (t["id"],)).fetchall()
    finally:
        conn.close()
    assert all(x["status"] == "skipped" for x in rows)
    bad = server._twin_impl("task_close", {"task_id": t["id"], "outcome": "boom"})
    assert bad["ok"] is False


# ---- 终态任务保护 ----

def test_step_update_on_submitted_task_rejected(env):
    t = _mk_task()
    r = _plan(t["id"])
    ids = _step_ids(r)
    server._twin_impl("step_update", {"step_id": ids[1], "status": "done"})
    server._twin_impl("step_update", {"step_id": ids[2], "status": "done"})
    server._twin_impl("step_update", {"step_id": ids[3], "status": "done"})
    server._twin_impl("task_submit", {"task_id": t["id"], "deliverable_md": "# 稿"})
    out = server._twin_impl("step_update", {"step_id": ids[1], "status": "pending",
                                            "reason": "reopen"})
    assert out["ok"] is False and "planning" in out["reason"]


# ---- 深拷贝 ----

def test_resume_copies_plan_with_remap(env):
    t = _mk_task()
    r = _plan(t["id"])
    ids = _step_ids(r)
    server._twin_impl("step_update", {"step_id": ids[1], "status": "done"})
    server._twin_impl("step_update", {"step_id": ids[2], "status": "in_progress"})
    q = [{"question": "q?", "step_ids": [3], "blocking": True}]
    _plan(t["id"], questions=q)  # 重建：done 保留，其余 replanned
    r2 = _plan(t["id"], questions=q)
    ids = _step_ids(r2)
    server._twin_impl("step_update", {"step_id": ids[2], "status": "in_progress"})
    resumed = server._twin_impl("task_resume", {"task_id": t["id"]})
    assert resumed["ok"] and resumed["plan_copied"]["steps"] >= 1
    new_tid = resumed["new_task_id"]
    conn = flow.db.connect()
    try:
        steps = conn.execute(
            "SELECT * FROM twin_plan_steps WHERE task_id=? ORDER BY seq", (new_tid,)).fetchall()
        questions = conn.execute(
            "SELECT * FROM twin_plan_questions WHERE task_id=?", (new_tid,)).fetchall()
        old_superseded = conn.execute(
            "SELECT status FROM twin_tasks WHERE id=?", (t["id"],)).fetchone()
    finally:
        conn.close()
    assert old_superseded["status"] == "superseded"
    assert all(s["status"] == "pending" for s in steps)  # 新代重置，failed/in_progress 不遗传
    assert all(s["origin_step_id"] for s in steps)
    deps = {s["id"]: json.loads(s["depends_on"]) for s in steps}
    for sid, ds in deps.items():
        for d in ds:
            assert any(x["id"] == d for x in steps)  # 依赖全部落在本代
    assert len(questions) == 1  # open 疑问拷贝


def test_revise_copies_plan(env):
    t = _mk_task()
    r = _plan(t["id"])
    for sid in _step_ids(r).values():
        server._twin_impl("step_update", {"step_id": sid, "status": "done"})
    sub = server._twin_impl("task_submit", {"task_id": t["id"], "deliverable_md": "# 稿"})
    assert sub["ok"]
    out = server._twin_impl("task_revise", {"task_id": t["id"], "revision_reason": "返工"})
    assert out["ok"] and out.get("plan_copied", {}).get("steps", 0) == 0  # 全闭环无可拷


# ---- tool_log ----

def test_tool_log_batch(env):
    t = _mk_task()
    ok = server._twin_impl("tool_log", {"task_id": t["id"], "entries": [
        {"tool": "grep", "purpose": "找定义", "outcome": "fail", "note": "没这文件"},
        {"tool": "rg", "purpose": "找定义", "outcome": "success"},
        {"tool": "kimi-skill", "purpose": "写周报", "outcome": "degraded",
         "skill_digest": "模板导出要点…"},
    ]})
    assert ok["ok"] and ok["recorded"] == 3
    empty = server._twin_impl("tool_log", {"entries": []})
    assert empty["ok"] is False
    too_many = server._twin_impl("tool_log", {"entries": [
        {"tool": "x", "outcome": "success"}] * 51})
    assert too_many["ok"] is False and "50" in too_many["reason"]
    bad_outcome = server._twin_impl("tool_log", {"entries": [
        {"tool": "x", "outcome": "win"}]})
    assert bad_outcome["ok"] is False
    no_task = server._twin_impl("tool_log", {"task_id": 424242, "entries": [
        {"tool": "x", "outcome": "success"}]})
    assert no_task["ok"] is False and no_task["error"] == "not_found"


# ---- status plan_stats ----

def test_status_plan_stats(env):
    t = _mk_task()
    _plan(t["id"])
    out = server._twin_impl("status", {})
    assert out["ok"]
    stats = out["plan_stats"]
    assert stats["tasks_planned"] == 1
    assert stats["steps_total"] == 3
