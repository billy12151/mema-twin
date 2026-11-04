"""v0.4.7 往返优化：plan_set 缺省回退 / step_update 批量 / task_submit 附带
steps / taxonomy kind 数组+compact / compile 冲突警示（sink 通道）。

方案：~/ZCodeProject/docs/twin-v047-roundtrip-optimization-plan.md（v1.1）。
"""
import pytest

from mema_twin import compile_actions, exec_actions, flow, pref_actions, sink


def _start_task(brief="测试任务", wt="product_doc"):
    from mema_twin import task_actions
    r = task_actions._action_task_start(
        {"brief": brief, "work_type": wt, "audience": "team_peers",
         "purpose": "drive_action"})
    assert r["ok"], r
    return r["task_id"]


def _plan(task_id=None, n=2, **kw):
    steps = kw.get("steps") or [{"title": f"步骤{i+1}"} for i in range(n)]
    data = {"steps": steps}
    if task_id is not None:
        data["task_id"] = task_id
    return exec_actions._action_plan_set(data)


def _step_ids(task_id):
    conn = flow.db.connect()
    try:
        return [r["id"] for r in conn.execute(
            "SELECT id FROM twin_plan_steps WHERE task_id=? ORDER BY seq",
            (task_id,))]
    finally:
        conn.close()


def _step_status(step_id):
    conn = flow.db.connect()
    try:
        return conn.execute("SELECT status FROM twin_plan_steps WHERE id=?",
                            (step_id,)).fetchone()["status"]
    finally:
        conn.close()


# ---------- A. plan_set 缺省回退 ----------

def test_plan_set_fallback_unique_planning():
    tid = _start_task(brief="唯一在途任务")
    r = _plan()
    assert r["ok"], r
    assert r["task_id"] == tid
    assert r["task_id_resolved"] is True
    assert r["resolved_brief"] == "唯一在途任务"
    assert len(r["steps"]) == 2


def test_plan_set_fallback_no_planning_rejected():
    r = _plan()
    assert not r["ok"]
    assert r["error"] == "invalid_input"
    assert "没有 planning 任务" in r["reason"]


def test_plan_set_fallback_multiple_planning_rejected():
    t1 = _start_task(brief="任务一")
    t2 = _start_task(brief="任务二")
    # task_start 会自动让位——手工造出双 planning（瞬态并发残留形态）
    conn = flow.db.connect()
    conn.execute("UPDATE twin_tasks SET status='planning' WHERE id=?", (t1,))
    conn.commit()
    conn.close()
    r = _plan()
    assert not r["ok"]
    assert "2 个 planning 任务" in r["reason"]
    assert f"#{t1}" in r["reason"] and f"#{t2}" in r["reason"]
    assert "任务一" in r["reason"]  # brief 也在清单里


def test_plan_set_explicit_dirty_id_still_hard_rejected():
    _start_task()
    r = _plan(task_id="abc")
    assert not r["ok"]
    assert r["error"] == "invalid_input"
    assert "abc" in r["reason"]


def test_plan_set_explicit_id_no_resolved_keys():
    tid = _start_task()
    r = _plan(task_id=tid)
    assert r["ok"]
    assert "task_id_resolved" not in r
    assert "resolved_brief" not in r


# ---------- B. step_update 批量 ----------

def test_step_update_batch_happy_path():
    tid = _start_task()
    p = _plan(task_id=tid, n=3)
    sids = [s["id"] for s in p["steps"]]
    r = exec_actions._action_step_update({
        "task_id": tid,
        "steps": [{"step_id": sids[0], "status": "done"},
                  {"step_id": sids[1], "status": "done"},
                  {"step_id": sids[2], "status": "skipped", "reason": "不再需要"}]})
    assert r["ok"], r
    assert r["applied"] == 3 and r["noop"] == 0
    assert [x["step_id"] for x in r["results"]] == sids
    assert _step_status(sids[0]) == "done"


def test_step_update_batch_atomic_rollback_on_gate_reject():
    tid = _start_task()
    p = _plan(task_id=tid, n=2)
    sids = [s["id"] for s in p["steps"]]
    # 第 1 条合法 done；第 2 条 skipped 缺 reason（reason_required）→ 整笔回滚
    r = exec_actions._action_step_update({
        "task_id": tid,
        "steps": [{"step_id": sids[0], "status": "done"},
                  {"step_id": sids[1], "status": "skipped"}]})
    assert not r["ok"]
    assert r.get("failed_index") == 1
    assert "reason" in r["reason"]
    assert _step_status(sids[0]) == "pending"  # 前面已过的也回滚


def test_step_update_batch_noop_counter_restored_on_rollback():
    tid = _start_task()
    p = _plan(task_id=tid, n=2)
    sids = [s["id"] for s in p["steps"]]
    exec_actions._noop_counts.clear()
    # 第 1 条 done→done 幂等 no_op（计数 +1）；第 2 条非法 → 回滚应把计数也还原
    exec_actions._action_step_update({"step_id": sids[0], "status": "done"})
    r = exec_actions._action_step_update({
        "task_id": tid,
        "steps": [{"step_id": sids[0], "status": "done"},
                  {"step_id": sids[1], "status": "skipped"}]})
    assert not r["ok"]
    # 回滚恢复到批前状态：批前无计数键（首打是真实迁移），批内 no_op 计数不残留
    assert (sids[0], "done") not in exec_actions._noop_counts


def test_step_update_batch_counts_noop_separately():
    tid = _start_task()
    p = _plan(task_id=tid, n=2)
    sids = [s["id"] for s in p["steps"]]
    exec_actions._action_step_update({"step_id": sids[0], "status": "done"})
    r = exec_actions._action_step_update({
        "task_id": tid,
        "steps": [{"step_id": sids[0], "status": "done"},   # no_op
                  {"step_id": sids[1], "status": "done"}]})  # applied
    assert r["ok"]
    assert r["applied"] == 1 and r["noop"] == 1


def test_step_update_batch_requires_task_id_and_mutex():
    tid = _start_task()
    p = _plan(task_id=tid, n=1)
    sid = p["steps"][0]["id"]
    r = exec_actions._action_step_update(
        {"steps": [{"step_id": sid, "status": "done"}]})
    assert not r["ok"] and r["field"] == "task_id"
    r = exec_actions._action_step_update(
        {"task_id": tid, "step_id": sid, "status": "done",
         "steps": [{"step_id": sid, "status": "done"}]})
    assert not r["ok"] and "互斥" in r["reason"]
    r = exec_actions._action_step_update(
        {"task_id": tid, "status": "done",
         "steps": [{"step_id": sid, "status": "done"}]})
    assert not r["ok"] and "互斥" in r["reason"]
    r = exec_actions._action_step_update({"task_id": tid, "steps": []})
    assert not r["ok"] and "1..50" in r["reason"]


def test_step_update_batch_cross_task_rejected():
    t1 = _start_task(brief="任务A")
    p1 = _plan(task_id=t1, n=1)          # t1 仍在 planning 时建计划
    sid1 = p1["steps"][0]["id"]
    t2 = _start_task(brief="任务B")      # t1 被让位
    conn = flow.db.connect()             # 手工恢复 t1 planning（瞬态残留形态）
    conn.execute("UPDATE twin_tasks SET status='planning' WHERE id=?", (t1,))
    conn.commit()
    conn.close()
    r = exec_actions._action_step_update({
        "task_id": t2, "steps": [{"step_id": sid1, "status": "done"}]})
    assert not r["ok"]
    assert "不允许跨任务" in r["reason"]


# ---------- C. task_submit 附带 steps ----------

def _submit(data):
    from mema_twin import task_actions
    return task_actions._action_task_submit(data)


def test_task_submit_with_steps_one_round():
    tid = _start_task()
    p = _plan(task_id=tid, n=2)
    sids = [s["id"] for s in p["steps"]]
    r = _submit({"task_id": tid, "deliverable_md": "# 交付稿",
                 "steps": [{"step_id": sids[0], "status": "done"},
                           {"step_id": sids[1], "status": "done"}]})
    assert r["ok"], r
    assert r["status"] == "submitted"
    assert r["steps_applied"] == 2


def test_task_submit_gate_reject_after_steps_carries_applied():
    """§七 P0 指定场景：steps 落库后 gate 拒 → steps_applied + hint +
    任务仍 planning + 库内打卡在（重试无需重新打卡）。"""
    tid = _start_task()
    p = _plan(task_id=tid, n=2)
    sids = [s["id"] for s in p["steps"]]
    r = _submit({"task_id": tid, "deliverable_md": "# 交付稿",
                 "steps": [{"step_id": sids[0], "status": "done"}]})  # 步骤2留 pending
    assert not r["ok"]
    assert r["error"] == "invalid_input"
    assert r["steps_applied"] == 1
    assert "无需重新打卡" in r["hint"]
    assert "open_steps" in r  # gate 拒绝体的对账清单在场
    assert _step_status(sids[0]) == "done"      # 打卡已落库
    assert _step_status(sids[1]) == "pending"
    conn = flow.db.connect()
    st = conn.execute("SELECT status FROM twin_tasks WHERE id=?", (tid,)).fetchone()["status"]
    conn.close()
    assert st == "planning"                     # 任务未收口
    # 重试（补齐步骤 2）：无需重新打卡步骤 1
    r2 = _submit({"task_id": tid, "deliverable_md": "# 交付稿",
                  "steps": [{"step_id": sids[1], "status": "done"}]})
    assert r2["ok"], r2
    assert r2["steps_applied"] == 1


def test_task_submit_steps_cross_task_rejected_nothing_moved():
    t1 = _start_task(brief="甲")
    p1 = _plan(task_id=t1, n=1)          # t1 仍在 planning 时建计划
    t2 = _start_task(brief="乙")         # t1 被让位
    p2 = _plan(task_id=t2, n=1)
    sid1 = p1["steps"][0]["id"]
    conn = flow.db.connect()             # 手工恢复 t1 planning（瞬态残留形态）
    conn.execute("UPDATE twin_tasks SET status='planning' WHERE id=?", (t1,))
    conn.commit()
    conn.close()
    r = _submit({"task_id": t2, "deliverable_md": "# 乙交付",
                 "steps": [{"step_id": sid1, "status": "done"},
                           {"step_id": p2["steps"][0]["id"], "status": "done"}]})
    assert not r["ok"]
    assert "不允许跨任务" in r["reason"]
    assert _step_status(p2["steps"][0]["id"]) == "pending"  # 整笔回滚
    conn = flow.db.connect()
    st = conn.execute("SELECT status FROM twin_tasks WHERE id=?", (t2,)).fetchone()["status"]
    conn.close()
    assert st == "planning"


def test_task_submit_without_steps_no_key():
    tid = _start_task()
    p = _plan(task_id=tid, n=1)
    exec_actions._action_step_update(
        {"step_id": p["steps"][0]["id"], "status": "done"})
    r = _submit({"task_id": tid, "deliverable_md": "# 交付"})
    assert r["ok"]
    assert "steps_applied" not in r


# ---------- D. taxonomy ----------

def test_taxonomy_single_kind_shape_unchanged():
    r = pref_actions._action_taxonomy({})
    assert r["ok"]
    assert r["kind"] == "work_type"
    assert "kinds" not in r and "count_total" not in r
    # 逐键全等（不是子集）：钉死单 kind 形状无新增多余键
    assert r["types"] and set(r["types"][0]) == {"code", "zh", "en", "domain",
                                                 "aliases", "is_custom"}


def test_taxonomy_kind_array_and_all_and_compact():
    r = pref_actions._action_taxonomy({"kind": ["work_type", "audience"]})
    assert r["ok"]
    assert set(r["kinds"]) == {"work_type", "audience"}
    assert r["count_total"] == (r["kinds"]["work_type"]["count"]
                                + r["kinds"]["audience"]["count"])
    assert all(it["kind"] == "work_type"
               for it in r["kinds"]["work_type"]["types"])
    r2 = pref_actions._action_taxonomy({"kind": "all", "compact": True})
    assert r2["ok"] and set(r2["kinds"]) == set(["work_type", "audience", "purpose"])
    it = r2["kinds"]["work_type"]["types"][0]
    assert set(it) == {"code", "zh", "aliases", "kind"}
    assert isinstance(it["aliases"], list) and len(it["aliases"]) <= 5
    # 数组传单元素也走 kinds 形状（形状跟随输入类型）
    r3 = pref_actions._action_taxonomy({"kind": ["purpose"]})
    assert "kinds" in r3 and "kind" not in r3


def test_taxonomy_bad_kind_rejected():
    r = pref_actions._action_taxonomy({"kind": ["work_type", "bogus"]})
    assert not r["ok"] and "bogus" in r["reason"]


# ---------- E. compile 冲突警示（sink 通道） ----------

def _mk_conflicts(*items):
    return {"ok": True, "data": {"conflicts": list(items)}}


def test_compile_conflict_warning_in_material(monkeypatch):
    tid = _start_task()  # 保证库非空即可
    monkeypatch.setattr(
        compile_actions.sink, "review_conflicts",
        lambda client=None: _mk_conflicts(
            {"id": 9, "memory_ids": [1, 2], "reason": "语义冲突", "status": "open",
             "workspace_canonical": "mema-twin", "created_at": "2026-11-04"},
            {"id": 8, "status": "open", "workspace_canonical": "other-ws"}))
    monkeypatch.setattr(compile_actions, "_fetch_evidence", lambda *a, **k: ([], []))
    monkeypatch.setattr(compile_actions, "_compile_audience_profiles",
                        lambda *a, **k: [])
    r = compile_actions._action_compile({"work_type": "product_doc"})
    assert r["ok"], r
    assert r["conflict_warning"]["pending"] == 1  # 他桶被滤掉
    assert r["conflict_warning"]["items"][0]["id"] == 9
    assert "1 条 twin 偏好冲突未解决" in r["material"]


def test_compile_conflict_warning_absent_when_clean_or_broken(monkeypatch):
    _start_task()
    monkeypatch.setattr(compile_actions.sink, "review_conflicts",
                        lambda client=None: _mk_conflicts())
    monkeypatch.setattr(compile_actions, "_fetch_evidence", lambda *a, **k: ([], []))
    monkeypatch.setattr(compile_actions, "_compile_audience_profiles",
                        lambda *a, **k: [])
    r = compile_actions._action_compile({"work_type": "product_doc"})
    assert r["ok"] and "conflict_warning" not in r
    # 响应缺 conflicts 键（形状漂移）→ None 且 ok
    monkeypatch.setattr(compile_actions.sink, "review_conflicts",
                        lambda client=None: {"ok": True, "data": {}})
    r2 = compile_actions._action_compile({"work_type": "product_doc"})
    assert r2["ok"] and "conflict_warning" not in r2
    # SinkError → None 且 ok（缺警示不阻断编译）
    def _boom(client=None):
        raise sink.SinkError("mema 不可达")
    monkeypatch.setattr(compile_actions.sink, "review_conflicts", _boom)
    r3 = compile_actions._action_compile({"work_type": "product_doc"})
    assert r3["ok"] and "conflict_warning" not in r3


# ---------- 批量审计挂载 ----------

def test_step_update_batch_audit_row_has_task_id():
    from mema_twin import audit, server
    tid = _start_task()
    p = _plan(task_id=tid, n=1)
    sid = p["steps"][0]["id"]
    r = server._twin_impl("step_update", {
        "task_id": tid, "steps": [{"step_id": sid, "status": "done"}]})
    assert r["ok"], r
    rows = audit.recent(action="step_update", limit=5)
    assert rows and rows[0]["task_id"] == tid


def test_step_update_batch_edges():
    tid = _start_task()
    p = _plan(task_id=tid, n=2)
    sids = [s["id"] for s in p["steps"]]
    # >50 拒
    r = exec_actions._action_step_update({
        "task_id": tid,
        "steps": [{"step_id": sids[0], "status": "done"}] * 51})
    assert not r["ok"] and "最多" in r["reason"]
    # step 不存在（not_found）→ 整笔回滚（前一条合法也不落）
    r = exec_actions._action_step_update({
        "task_id": tid,
        "steps": [{"step_id": sids[0], "status": "done"},
                  {"step_id": 999999, "status": "done"}]})
    assert not r["ok"] and r["error"] == "not_found"
    assert _step_status(sids[0]) == "pending"
    # step_id 口径收紧："012" 打回（对齐 _coerce_task_id 严格口径）
    r = exec_actions._action_step_update({
        "task_id": tid, "steps": [{"step_id": f"{sids[0]:03d}", "status": "done"}]})
    assert not r["ok"] and "invalid step_id" in r["reason"]
    # 顶层 reason 与 steps 互斥（防静默丢弃）
    r = exec_actions._action_step_update({
        "task_id": tid, "reason": "x",
        "steps": [{"step_id": sids[0], "status": "done"}]})
    assert not r["ok"] and "互斥" in r["reason"]


def test_compile_conflict_warning_called_before_evidence_reads(monkeypatch):
    """钉住「警示先于逐条证据 read」的落点（mema 挂死时先 10s 软失败）。"""
    _start_task()
    order: list[str] = []
    monkeypatch.setattr(compile_actions, "_conflict_warning",
                        lambda client=None: order.append("conflict") or None)
    monkeypatch.setattr(compile_actions, "_fetch_evidence",
                        lambda *a, **k: order.append("evidence") or ([], []))
    monkeypatch.setattr(compile_actions, "_compile_audience_profiles",
                        lambda *a, **k: [])
    r = compile_actions._action_compile({"work_type": "product_doc"})
    assert r["ok"]
    assert order == ["conflict", "evidence"]
