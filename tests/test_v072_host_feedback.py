"""0.7.2 宿主实测反馈修复（方案 mema-twin-v072-host-feedback-plan.md v1.1）：
F1 taxonomy compact 非布尔硬拒（对齐 help）/ F2 kind 报错补指路 /
F3 全新工种 fallback 只给指针不注全文（动作不失真）/ F4 SKILL 与 help 文案。
"""
import pathlib

from mema_twin import db, flow, server, taxonomy

REPO = pathlib.Path(__file__).resolve().parent.parent


def _dims(code):
    return {
        "work_type": {"ok": True, "kind": "work_type", "raw": code, "code": code,
                      "label_zh": code, "matched_by": "exact_or_alias"},
        "audience": {"ok": True, "kind": "audience", "raw": "本人", "code": "self",
                     "label_zh": "本人", "matched_by": "exact_or_alias"},
        "purpose": {"ok": True, "kind": "purpose", "raw": "同步", "code": "sync_info",
                    "label_zh": "信息同步与知会", "matched_by": "exact_or_alias"},
    }


def _mk_persona(code, version, md=None):
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO twin_prompt_versions"
            "(work_type, version, prompt_md, source_memory_ids, model, status,"
            " evidence_count, created_at, activated_at)"
            " VALUES(?,?,?,?,'test','active',?,?,?)",
            (code, version, md or f"# {code} v{version}\n正文", "[]", version,
             flow.db.now_iso(), flow.db.now_iso()))
        conn.commit()
    finally:
        conn.close()


_mid = [800000]


def _mk_evidence(code, n, status="uncompiled"):
    conn = db.connect()
    try:
        for i in range(n):
            _mid[0] += 1
            conn.execute(
                "INSERT INTO twin_evidence"
                "(memory_id, work_type, work_type_raw, subject, status, created_at)"
                " VALUES(?,?,?,?,?,?)",
                (_mid[0], code, code, f"s{i}", status, db.now_iso()))
        conn.commit()
    finally:
        conn.close()


# ---- T1：compact 非布尔硬拒 ----

def test_t1_compact_rejects_non_bool():
    for bad in ("true", 1, "false", 0):
        r = server._twin_impl("taxonomy", {"kind": "work_type", "compact": bad})
        assert not r["ok"] and r["error"] == "invalid_input"
        assert r["field"] == "compact" and "布尔" in r["reason"], (bad, r)
    # true → 精简（无 domain/en 键）；缺省/false/null → 完整版不报错
    good = server._twin_impl("taxonomy", {"kind": "work_type", "compact": True})
    assert good["ok"] and "domain" not in good["types"][0]
    for ok_val in (None, False):
        kw = {} if ok_val is None else {"compact": ok_val}
        r = server._twin_impl("taxonomy", {"kind": "work_type", **kw})
        assert r["ok"] and "domain" in r["types"][0]


# ---- T2：kind 报错指路 ----

def test_t2_kind_error_points_to_kind_names():
    r = server._twin_impl("taxonomy", {"kind": ["work_report"]})
    assert not r["ok"] and r["error"] == "invalid_input"
    assert "不是工种 code" in r["reason"]
    assert "kind='work_type'" in r["reason"]


# ---- T3/T4/T5：fallback 两档 ----

def _mk_task(code):
    return flow.insert_task(brief="B", status="planning", dims=_dims(code))


def test_t3_zero_evidence_work_type_gets_pointer_only():
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO twin_playbooks(key, version, content_md, status, created_at)"
            " VALUES('global', 1, '# global playbook\n- 条目', 'active', ?)",
            (db.now_iso(),))
        conn.commit()
    finally:
        conn.close()
    _mk_persona("work_report", 1, md="# wr\n垫底全文")
    _mk_persona("meeting_minutes", 9, md="# mm\n更高版本")
    _mk_evidence("work_report", 5)   # donor=meeting_minutes（0 证据 tie-break 退化为版本高者）
    _mk_evidence("meeting_minutes", 5)
    t = _mk_task("test_report")           # 全新工种：0 证据 0 persona
    r = server._twin_impl("task_start", {"brief": "B", "work_type": "test_report",
                                         "audience": "self", "purpose": "sync_info"})
    assert r["ok"]
    assert "fallback_persona_md" not in r and "fallback_from" not in r
    ptr = r["fallback_pointer"]
    assert ptr["work_type"] == "meeting_minutes" and ptr["domain"]
    assert "未注入" in r["fallback_persona_note"] and "meeting_minutes" in r["fallback_persona_note"]
    assert 'twin(action="get"' in r["fallback_persona_note"]   # 指针自足（v0.6.9 先例）
    assert "playbook_md" in r                                  # T5：playbook 独立通道照常


def test_t3b_resume_path_pointer():
    _mk_persona("work_report", 1)
    t = _mk_task("test_report")
    r = server._twin_impl("task_resume", {"task_id": t["id"]})
    assert r["ok"] and "fallback_persona_md" not in r and "fallback_pointer" in r


def test_t3c_void_only_evidence_still_pointer(env_or_none=None):
    # 被报案例本体：test_report 仅 1 条 void 证据——void 不计在世，仍走指针
    _mk_persona("work_report", 1)
    _mk_evidence("test_report", 1, status="void")
    t = _mk_task("test_report")
    r = server._twin_impl("task_start", {"brief": "B", "work_type": "test_report",
                                         "audience": "self", "purpose": "sync_info"})
    assert r["ok"] and "fallback_persona_md" not in r and "fallback_pointer" in r


def test_t3d_no_candidate_both_keys_absent(env_or_none=None):
    # 空 domain 自建码：无候选 → 无指针无注入，两键皆无（现状边界）
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO twin_types(type_kind, code, label_zh, label_en, domain,"
            " aliases, is_custom, status, created_at)"
            " VALUES('work_type','my_solo','独立工种','my','',?,'1','active',?)",
            ("[]", db.now_iso()))
        conn.commit()
    finally:
        conn.close()
    _mk_persona("work_report", 1)
    r = server._twin_impl("task_start", {"brief": "B", "work_type": "my_solo",
                                         "audience": "self", "purpose": "sync_info"})
    assert r["ok"]
    assert "fallback_pointer" not in r and "fallback_persona_md" not in r


def test_t3e_revise_path_pointer():
    # revise 通道同函数：0 证据工种返工同样只回指针
    _mk_persona("work_report", 1)
    parent = _mk_task("test_report")
    server._twin_impl("task_submit", {"task_id": parent["id"],
                                      "deliverable_md": "# 报告\n"})
    r = server._twin_impl("task_revise", {"task_id": parent["id"],
                                          "revision_reason": "返工"})
    assert r["ok"] and "fallback_persona_md" not in r and "fallback_pointer" in r


def test_t4_has_evidence_keeps_full_fallback():
    _mk_persona("work_report", 1, md="# wr\n全文垫底")
    _mk_evidence("test_report", 2)   # 类型在积累 → 维持全文路径
    t = _mk_task("test_report")
    r = server._twin_impl("task_start", {"brief": "B", "work_type": "test_report",
                                         "audience": "self", "purpose": "sync_info"})
    assert r["ok"]
    assert r["fallback_persona_md"] == "# wr\n全文垫底"
    assert r["fallback_from"]["work_type"] == "work_report"


# ---- T6：SKILL/help/README 同文 ----

def test_t6_docs_consistent():
    skill = (REPO / "skill" / "SKILL.md").read_text(encoding="utf-8")
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "fallback_pointer" in skill and "fallback_pointer" in readme
    assert "cross_type_echo" in skill and "steps_reflection_not_written" in skill
    # 旧口径孤句不得残留（0.7.2 前「尚无专属 persona 即全文」无条件承诺）
    assert "尚无专属 persona，给同域最成熟工种的 persona 垫底" not in skill
    assert "尚无专属 persona 时注入同域 fallback（#956 拍板）" not in (
        open(REPO / "mema_twin" / "server.py", encoding="utf-8").read())
    assert "分两档" in readme and "fallback_pointer" in readme
    assert "0 个时打回并提示先 task_start 建档" in server._HELP_ENTRIES["plan_set"]
