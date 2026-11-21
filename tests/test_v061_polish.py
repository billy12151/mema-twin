"""v0.6.1 实测卡点修复（方案 mema-twin-v061-scan-notice-dedup-plan.md §测试计划）：
S1 step_update 归属校验 / S2 seq_map / S5 next 照抄指引 / S6 静态文案抑制 /
S7 去重规范化+相似提示。S8 已由 test_scan/test_server_boundary/test_gate/
test_playbook 重写组覆盖。"""
from mema_twin import db, flow, server, templates


def _t(action, data=None):
    return server._twin_impl(action, data)


# ---- S1 step_update 可选 task_id 归属校验 ----

def _setup_task(steps=2):
    t = flow.insert_task(brief="b", status="planning", dims={})
    plan = _t("plan_set", {"task_id": t["id"], "steps": [
        {"title": f"s{i}"} for i in range(steps)]})
    return t, [s["id"] for s in plan["steps"]]


def test_s1_task_id_match_passes():
    t, ids = _setup_task()
    r = _t("step_update", {"step_id": ids[0], "status": "in_progress",
                           "task_id": t["id"]})
    assert r["ok"] and r["task_id"] == t["id"]


def test_s1_task_id_mismatch_rejected_with_guidance():
    t2, ids2 = _setup_task()
    t1, _ = _setup_task()  # 两个任务；把 t2 的步骤谎报成 t1
    r = _t("step_update", {"step_id": ids2[0], "status": "in_progress",
                           "task_id": t1["id"]})
    assert r["ok"] is False and r["error"] == "invalid_input"
    assert f"属于任务 #{t2['id']}" in r["reason"]
    assert "task_get" in r["reason"] and "task_recent" in r["reason"]


def test_s1_unknown_task_id_not_found():
    _t_task, ids = _setup_task()
    r = _t("step_update", {"step_id": ids[0], "status": "in_progress",
                           "task_id": 999999})
    assert r["ok"] is False and r["error"] == "not_found"
    assert "task_recent" in r["reason"]


def test_s1_no_task_id_unchanged():
    """不传 task_id 行为完全不变（零破坏口径）。"""
    t, ids = _setup_task()
    r = _t("step_update", {"step_id": ids[0], "status": "in_progress"})
    assert r["ok"]


# ---- S2 plan_set seq_map ----

def test_s2_seq_map_matches_steps():
    t, _ = _setup_task()
    plan = _t("plan_set", {"task_id": t["id"], "steps": [
        {"title": "a"}, {"title": "b", "depends_on": [1]}, {"title": "c"}],
        "open_questions": [{"question": "q?", "step_ids": [2]}]})
    assert plan["seq_map"] == {str(s["seq"]): s["id"] for s in plan["steps"]}
    # open_questions 响应 step_ids 是全局 id（同名异义），seq_map 可换算
    q = plan["open_questions"][0]
    assert q["step_ids"] == [plan["seq_map"]["2"]]


def test_s2_seq_map_on_replanned_points_to_new_rows():
    t, _ = _setup_task()
    first = _t("plan_set", {"task_id": t["id"], "steps": [{"title": "a"}]})
    old_id = first["steps"][0]["id"]
    _t("step_update", {"step_id": old_id, "status": "in_progress"})
    second = _t("plan_set", {"task_id": t["id"], "steps": [{"title": "a2"}]})
    assert second["replanned"] is True
    assert second["seq_map"]["1"] == second["steps"][0]["id"] != old_id


# ---- S5 compile next 照抄指引 ----

def _feed(code="work_report"):
    _t("write", {"content": "偏好一", "work_type": "周报",
                 "audience": "高层", "purpose": "同步"})
    return _t("compile", {"work_type": code})


def test_s5_next_copies_suggested():
    r = _feed()
    assert "source_ids_suggested" in r["next"] and "复制" in r["next"]


# ---- S6 静态文案抑制 ----

def test_s6_write_hint_second_call_omitted():
    r1 = _t("write", {"content": "偏好一", "work_type": "周报",
                      "audience": "高层", "purpose": "同步"})
    assert "hint" in r1  # 首调在场
    r2 = _t("write", {"content": "偏好二", "work_type": "周报",
                      "audience": "高层", "purpose": "同步"})
    assert "hint" not in r2  # 同 client+code+版本 第二次省略


def test_s6_write_hint_returns_after_version_bump():
    _t("write", {"content": "偏好一", "work_type": "周报",
                 "audience": "高层", "purpose": "同步"})
    assert "hint" not in _t("write", {"content": "偏好二", "work_type": "周报",
                                      "audience": "高层", "purpose": "同步"})
    r = _feed()  # compile+submit 出 v1 → 版本变化 → hint 重现
    s = _t("submit", {"work_type": "周报", "prompt_md": "# v\n\n## 分区\n\n正文",
                      "source_memory_ids": r["source_ids_suggested"]})
    assert s["ok"]
    r2 = _t("write", {"content": "偏好三", "work_type": "周报",
                      "audience": "高层", "purpose": "同步"})
    assert r2["hint"] and "v1" in r2["hint"]


def test_s6_duplicate_note_never_suppressed():
    _t("write", {"content": "偏好一", "work_type": "周报",
                 "audience": "高层", "purpose": "同步"})
    r = _t("write", {"content": "偏好一", "work_type": "周报",
                     "audience": "高层", "purpose": "同步"})
    assert r["duplicate"] is True and r["note"]  # 幂等提示永不省


def test_s6_compile_note_second_call_omitted():
    r1 = _feed()
    assert r1["note"] == templates.STRONG_MODEL_NOTE
    r2 = _feed()
    assert "note" not in r2
    assert r2["next"] and r2["session_note"]  # next/session_note 恒在


def test_s6_task_evaluate_note_shared_suppression():
    """compile 与 task_evaluate 共用同一抑制 set（P2-6 同型纳入）。"""
    _feed()  # compile 已盖章 client
    r = _t("task_evaluate", {})
    assert "note" not in r  # 同 client 静态 note 省略
    assert r["next"]


# ---- S7 去重规范化 + 相似提示 ----

def test_s7_dedup_normalized_variants():
    r1 = _t("write", {"content": "PRD 先写问题定义", "work_type": "周报",
                      "audience": "高层", "purpose": "同步"})
    assert r1["duplicate"] is False
    # 全角空格/多空白变体：规范化后判重
    r2 = _t("write", {"content": "PRD　先写问题定义", "work_type": "周报",
                      "audience": "高层", "purpose": "同步"})
    assert r2["duplicate"] is True and r2["memory_id"] == r1["memory_id"]


def test_s7_similar_existing_surfaced_not_blocked():
    r1 = _t("write", {"content": "PRD 交付前必须逐条核对链接可达与数字一致",
                      "work_type": "周报", "audience": "高层", "purpose": "同步"})
    assert r1["ok"] and "similar_existing" not in r1  # 首条无既有
    r2 = _t("write", {"content": "PRD 交付之前必须逐条核对链接可达与数字一致哦",
                      "work_type": "周报", "audience": "高层", "purpose": "同步"})
    assert r2["ok"] is True  # 软通道不拦截
    sim = r2.get("similar_existing")
    assert sim and sim[0]["memory_id"] == r1["memory_id"]
    assert 0.85 <= sim[0]["ratio"] <= 1.0


def test_s7_unrelated_text_no_similar():
    _t("write", {"content": "PRD 交付前必须逐条核对链接可达与数字一致",
                 "work_type": "周报", "audience": "高层", "purpose": "同步"})
    r = _t("write", {"content": "周报落款必须带部门全称与汇报周期",
                     "work_type": "周报", "audience": "高层", "purpose": "同步"})
    assert r["ok"] and "similar_existing" not in r


def test_s7_similar_excludes_self_and_caps_at_three():
    base = "PRD 交付前必须逐条核对链接可达与数字一致"
    first = _t("write", {"content": base, "work_type": "周报",
                         "audience": "高层", "purpose": "同步"})
    r = _t("write", {"content": base + "，并复核截取日期", "work_type": "周报",
                     "audience": "高层", "purpose": "同步"})
    sim_ids = {s["memory_id"] for s in r.get("similar_existing", [])}
    assert r["memory_id"] not in sim_ids  # 排除自身
    assert first["memory_id"] in sim_ids


def test_s7_long_content_skipped():
    """护栏：任一侧 >2000 字符跳过相似提示（防 8000² ratio 阻塞）。"""
    _t("write", {"content": "长" * 2500, "work_type": "周报",
                 "audience": "高层", "purpose": "同步"})
    r = _t("write", {"content": "长" * 2499 + "尾", "work_type": "周报",
                     "audience": "高层", "purpose": "同步"})
    assert r["ok"] and "similar_existing" not in r


def test_s7_similar_capped_at_three():
    """3 条帽（R1 P1-1）：库内 4 条相似，响应最多报 3。"""
    base = "PRD 交付前必须逐条核对链接可达与数字一致"
    _t("write", {"content": base, "work_type": "周报",
                 "audience": "高层", "purpose": "同步"})
    for suffix in ("哦", "呀", "呢", "啦"):
        _t("write", {"content": base + suffix, "work_type": "周报",
                     "audience": "高层", "purpose": "同步"})
    r = _t("write", {"content": base + "，并复核截取日期", "work_type": "周报",
                     "audience": "高层", "purpose": "同步"})
    sim = r.get("similar_existing", [])
    assert 1 <= len(sim) <= 3
    assert all(s["ratio"] >= 0.82 for s in sim)


def test_s7_row_window_100():
    """100 行帽（R1 P1-1）：比对集=最近 100 行，窗外相似不报。"""
    first = _t("write", {"content": "PRD 交付前必须逐条核对链接可达与数字一致",
                         "work_type": "周报", "audience": "高层", "purpose": "同步"})
    conn = db.connect()
    for i in range(100):
        conn.execute(
            "INSERT INTO twin_evidence(memory_id, work_type, work_type_raw,"
            " audience, audience_raw, purpose, purpose_raw, content, created_at)"
            " VALUES(?, 'work_report', '周报', 'leadership', '高层', 'sync_info',"
            " '同步', ?, ?)",
            (800000 + i, f"无关占位行 {i}", db.now_iso()))
    conn.commit()
    conn.close()
    r = _t("write", {"content": "PRD 交付之前必须逐条核对链接可达与数字一致哦",
                     "work_type": "周报", "audience": "高层", "purpose": "同步"})
    sim_ids = {s["memory_id"] for s in r.get("similar_existing", [])}
    assert first["memory_id"] not in sim_ids  # 最老 base 已挤出 100 行窗
    conn = db.connect()
    conn.execute("DELETE FROM twin_evidence WHERE memory_id >= 800000")
    conn.commit()
    conn.close()
