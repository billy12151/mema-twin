"""v0.7.0 分流治理（开发方案存 mema #1731，twin 任务 #1162 交付稿）：W1 判据
同文 / W2.1 no_op reflection 丢弃提示 / W3 跨工种同文扇出提示。

W1 是纯文案：单测只能钉住「判据文案在各处存在且一致」，钉不住宿主读了会不会
照做（那靠盲测与两周度量，方案 W1.9）。T5/T8 同时是系统性质回归：done 的
reflection 进素材包、source_memory_ids 全列入才不留永久增补注入。
T15 与方案用例原文矛盾（规格 bug，详见用例内注释），按 W3 规格语义钉住。
"""
import pathlib

from mema_twin import db, flow, server, templates

REPO = pathlib.Path(__file__).resolve().parent.parent


def _dims():
    return {
        "work_type": {"ok": True, "kind": "work_type", "raw": "提案", "code": "proposal",
                      "label_zh": "提案", "matched_by": "exact_or_alias"},
        "audience": {"ok": True, "kind": "audience", "raw": "本人", "code": "self",
                     "label_zh": "本人", "matched_by": "exact_or_alias"},
        "purpose": {"ok": True, "kind": "purpose", "raw": "同步", "code": "sync_info",
                    "label_zh": "信息同步与知会", "matched_by": "exact_or_alias"},
    }


def _write(content, wt="proposal", aud="self", purpose="sync_info", **kw):
    data = {"content": content, "audience": aud, "purpose": purpose, "client": "zcode"}
    if wt is not None:
        data["work_type"] = wt
    data.update(kw)
    return server._twin_impl("write", data)


def _plan_to_done(reflection=None):
    """建档 → 两步计划 → 全部 done，返回 (task, plan)。"""
    t = flow.insert_task(brief="写周报", status="planning", dims=_dims())
    plan = server._twin_impl("plan_set", {"task_id": t["id"], "steps": [
        {"title": "取数"}, {"title": "成稿"}]})
    ids = {s["seq"]: s["id"] for s in plan["steps"]}
    assert server._twin_impl("step_update", {
        "step_id": ids[1], "status": "in_progress"})["ok"]
    done_kw = {"status": "done"}
    if reflection is not None:
        done_kw["reflection"] = reflection
    assert server._twin_impl("step_update",
                             {"step_id": ids[1], **done_kw})["ok"]
    assert server._twin_impl("step_update",
                             {"step_id": ids[2], "status": "done"})["ok"]
    return t, plan


# ---- W1：判据文案各处同文 ----

def test_t1_skill_files_carry_portability_judgment():
    skill = (REPO / "skill" / "SKILL.md").read_text(encoding="utf-8")
    gov = (REPO / "skill-governance" / "SKILL.md").read_text(encoding="utf-8")
    for marker in ("稿子会被打回", "换宿主"):
        assert marker in skill, f"skill/SKILL.md 缺判据句：{marker}"
        assert marker in gov, f"skill-governance/SKILL.md 缺判据句：{marker}"


def test_t2_judgment_texts_consistent():
    type_rules = "\n".join(templates._TYPE_RULES)
    aud_rules = "\n".join(templates._AUDIENCE_RULES)
    pb_rules = "\n".join(templates._PLAYBOOK_RULES)
    # 四处文案都含「技能文档」（WRITE_GUIDANCE / _TYPE_RULES / _AUDIENCE_RULES /
    # _PLAYBOOK_RULES——画像支线守门句系轮 2 评审补，关死 aud-engineering v10 事故路径）
    for text in (templates.WRITE_GUIDANCE, type_rules, aud_rules, pb_rules):
        assert "技能文档" in text
    # WRITE_GUIDANCE 与 _TYPE_RULES 另含「参数名」「工具选路」
    for text in (templates.WRITE_GUIDANCE, type_rules):
        assert "参数名" in text
        assert "工具选路" in text
    # W1.2：被剔除条款的证据 id 仍照抄进 source_memory_ids（漏列=永久增补注入）
    assert "source_memory_ids" in type_rules


def test_t3_help_write_carries_guidance():
    r = server._twin_impl("help", {"topic": "write"})
    assert r["ok"] and r["write_guidance"]
    assert 'scope="audience"' in r["write_guidance"]
    assert "不要 write" in r["write_guidance"]


def test_t4_tool_log_doc_in_help_and_response():
    r = server._twin_impl("help", {"topic": "tool_log"})
    assert "不带 task_id 的只留痕" in r["help"]
    res = server._twin_impl("tool_log", {"entries": [
        {"tool": "renderer", "purpose": "出图", "outcome": "degraded",
         "note": "超时后换备用通道"}]})
    assert res["ok"]
    assert "不带 task_id 的只留痕" in res["guidance"]


# ---- W1/W2：reflection 通路（系统性质回归）----

def test_t5_done_reflection_enters_evaluate_material():
    t, _ = _plan_to_done(reflection="下次先确认模板版本再动手")
    sub = server._twin_impl("task_submit",
                            {"task_id": t["id"], "deliverable_md": "# 周报"})
    assert sub["ok"]
    ev = server._twin_impl("task_evaluate", {})
    assert ev["ok"] and ev["task_count"] >= 1
    assert "下次先确认模板版本再动手" in ev["material"]


def test_t6_noop_done_with_reflection_reports_not_written():
    _, plan = _plan_to_done()
    sid = plan["steps"][0]["id"]
    r = server._twin_impl("step_update", {"step_id": sid, "status": "done",
                                          "reflection": "后补的反思不该写入"})
    assert r["ok"] and r["no_op"] is True
    assert "未写入" in r["guidance"]
    conn = db.connect()
    try:
        row = conn.execute("SELECT reflection FROM twin_plan_steps WHERE id=?",
                           (sid,)).fetchone()
    finally:
        conn.close()
    assert not (row["reflection"] or "").strip()


def test_t6b_submit_steps_noop_reflection_surfaced_on_success():
    # 轮2 A/B P1：全闭环任务的 task_submit 带 steps 批量补 reflection 必然全
    # no_op——成功收口路径也要显式计数（被拒路径此前已有 hint，成功路径曾静默）
    t, plan = _plan_to_done()
    sub = server._twin_impl("task_submit", {
        "task_id": t["id"], "deliverable_md": "# 周报",
        "steps": [{"step_id": plan["steps"][0]["id"], "status": "done",
                   "reflection": "收口时顺手补的反思"},
                  {"step_id": plan["steps"][1]["id"], "status": "done",
                   "reflection": "第二条顺手补的反思"}]})
    assert sub["ok"] and sub["status"] == "submitted"
    assert sub["steps_reflection_not_written"] == 2
    assert "未写入" in sub["guidance"]
    assert "tool_log" in sub["guidance"]
    # 裸幂等（不带 reflection）不背「reflection 未写入」话术（轮2 A P2-1）
    t2 = flow.insert_task(brief="再写一份周报", status="planning", dims=_dims())
    plan2 = server._twin_impl("plan_set", {"task_id": t2["id"], "steps": [
        {"title": "取数"}]})
    server._twin_impl("step_update", {"step_id": plan2["steps"][0]["id"],
                                      "status": "done"})
    sub2 = server._twin_impl("task_submit", {
        "task_id": t2["id"], "deliverable_md": "# 周报2",
        "steps": [{"step_id": plan2["steps"][0]["id"], "status": "done"}]})
    assert sub2["ok"]
    assert "steps_reflection_not_written" not in sub2
    assert "reflection 未写入" not in sub2["guidance"]


def test_t7_tool_log_without_task_id_stays_out_of_material():
    t, _ = _plan_to_done()
    sub = server._twin_impl("task_submit",
                            {"task_id": t["id"], "deliverable_md": "# 周报"})
    assert sub["ok"]
    res = server._twin_impl("tool_log", {"entries": [
        {"tool": "裸记工具", "purpose": "无任务留痕", "outcome": "success",
         "note": "不带task_id的独特标记XYZ"}]})
    assert res["ok"]
    ev = server._twin_impl("task_evaluate", {})
    assert ev["ok"]
    assert "XYZ" not in ev["material"]


def test_t8_reviewed_but_not_encoded_ids_leave_no_supplement():
    a = _write("交付文档表头一律加粗，默认格式直接做不必再问")
    b = _write("用 textIndent:true 实现首行缩进")
    assert a["ok"] and b["ok"] and not a["duplicate"] and not b["duplicate"]
    comp = server._twin_impl("compile", {"work_type": "proposal"})
    assert comp["ok"]
    prompt = ("# persona：提案 v1\n\n## 结构与格式\n\n"
              f"- 交付文档表头一律加粗。<!-- src: {a['memory_id']} -->\n")
    sub = server._twin_impl("submit", {
        "work_type": "proposal", "prompt_md": prompt,
        "source_memory_ids": [a["memory_id"], b["memory_id"]]})
    assert sub["ok"]
    st = server._twin_impl("status", {})
    assert "proposal" not in st["uncompiled"]
    g = server._twin_impl("get", {"work_type": "proposal"})
    assert "persona_supplement" not in g


# ---- W3：跨工种同文扇出提示 ----

_TEXT = "含表格文档表头 一律加粗并加浅灰底，交付说明注明手工步骤"


def test_t9_single_other_type_no_echo():
    assert _write(_TEXT, wt="proposal")["ok"]
    r = _write(_TEXT, wt="research_report")
    assert r["ok"] and not r["duplicate"]
    assert "cross_type_echo" not in r
    assert "cross_type_echo_skipped" not in r


def test_t10_two_other_types_echo_count_two():
    assert _write(_TEXT, wt="proposal")["ok"]
    assert _write(_TEXT, wt="research_report")["ok"]
    r = _write(_TEXT, wt="data_analysis")
    assert r["ok"]
    assert r["cross_type_echo"]["count"] == 2
    assert set(r["cross_type_echo"]["work_types"]) == {"proposal", "research_report"}
    assert "echo_guidance" in r
    assert "2 个其他工种" in r["echo_guidance"]


def test_t11_three_other_types_echo_count_three():
    for wt in ("proposal", "research_report", "data_analysis"):
        assert _write(_TEXT, wt=wt)["ok"]
    r = _write(_TEXT, wt="work_report")
    assert r["ok"]
    assert r["cross_type_echo"]["count"] == 3


def test_t12_audience_scoped_threshold_one_and_void_guidance():
    assert _write(_TEXT, wt="proposal")["ok"]
    r = _write(_TEXT, scope="audience", wt=None)
    assert r["ok"]
    assert r["cross_type_echo"]["count"] == 1
    assert "void" in r["echo_guidance"]


def test_t13_same_type_same_text_duplicate():
    assert _write(_TEXT, wt="proposal")["ok"]
    r = _write(_TEXT, wt="proposal")
    assert r["ok"] and r["duplicate"] is True
    assert "cross_type_echo" not in r  # 提前返回，不进扇出检测


def test_t14_different_audience_no_echo():
    assert _write(_TEXT, wt="proposal", aud="leadership")["ok"]
    r = _write(_TEXT, wt="research_report", aud="direct_manager")
    assert r["ok"]
    assert "cross_type_echo" not in r
    assert "cross_type_echo_skipped" not in r


def test_t15_audience_level_row_not_counted():
    # 工种级 1 条 + 受众级 1 条（aud- 行）同文，再写第 2 个工种：aud- 行不在
    # 比对集（NOT LIKE 'aud-%'），活跃工种级同文仅 1 个 < 阈值 2 → 无提示键。
    # （方案用例原文写「count == 1 无 guidance」与 W3.1/W3.2 规格自相矛盾——
    # aud- 行不计入就不可能观察到 count==1 的回显键；此处按规格语义钉「不计入」。）
    assert _write(_TEXT, wt="proposal")["ok"]
    assert _write(_TEXT, scope="audience", wt=None)["ok"]
    r = _write(_TEXT, wt="research_report")
    assert r["ok"]
    assert "cross_type_echo" not in r
    assert "cross_type_echo_skipped" not in r


def test_t16_voided_same_text_not_counted():
    a = _write(_TEXT, wt="proposal")
    v = server._twin_impl("void", {"memory_id": a["memory_id"]})
    assert v["ok"]
    r = _write(_TEXT, wt="research_report")
    assert r["ok"]
    assert "cross_type_echo" not in r


def _insert_fillers(count, start=1):
    """直接插 count 行同受众长度相近填充行（造比对窗数据，断言走真实 write 路径）。
    memory_id 从 start 起手发（绕开 write 的 MAX+1 发号撞号）。"""
    conn = db.connect()
    try:
        conn.executemany(
            db._EVIDENCE_INSERT,
            [(i, "data_analysis", "self", "sync_info", "", "", "",
              f"填充行 {i}", f"填充内容第{i}行保持相近长度凑比对窗", "[]",
              "", "zcode", db.now_iso())
             for i in range(start, start + count)])
        conn.commit()
    finally:
        conn.close()


def test_t17_at_cap_returns_hits_over_cap_skipped():
    # 恰好 5000 行（= 比对窗帽）：不跳过，照常检测（无 skipped 键）
    _insert_fillers(5000)
    r = _write(_TEXT, wt="proposal")
    assert r["ok"]
    assert "cross_type_echo_skipped" not in r
    assert "cross_type_echo" not in r  # 填充行与目标不同文，命中 0
    # 第 5001 行起超窗：回 skipped（方案 §六 T17）
    _insert_fillers(1, start=5002)
    r2 = _write(_TEXT + "二", wt="research_report")
    assert r2["ok"]
    assert r2["cross_type_echo_skipped"] == "over_scan_cap"
    assert "cross_type_echo" not in r2


def test_t18_nfkc_variant_counts_as_same_text():
    assert _write(_TEXT, wt="proposal")["ok"]
    assert _write(_TEXT, wt="research_report")["ok"]
    # 全角空白 + 多空白变体：NFKC 归一（U+3000→空格）+ 空白压一后与原文同
    # digest → 计入同文（duplicate 判重同键，跨工种不触发）
    variant = _TEXT.replace("表头 一律", "表头　　  一律")
    r = _write(variant, wt="data_analysis")
    assert r["ok"] and not r["duplicate"]
    assert r["cross_type_echo"]["count"] == 2
