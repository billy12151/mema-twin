"""F4 同域 fallback 注入（v0.4.5，方案 mema-twin-f4-fallback-design-2026-10-01.md
——mema #956 拍板 + 方案对抗评审 5 P2/7 P3 修订版）。

同域样例（taxonomy 内置）：
- 记录与同步：work_report / meeting_minutes / comm_copy / personal_notes …
- 说服与传播：presentation / proposal / …
- 规格与执行：product_doc / project_plan / …
"""
import pytest

from mema_twin import db, flow, server, store, task_actions


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(tmp_path / "twin.sqlite3"))
    monkeypatch.setenv("MEMA_TWIN_PROMPTS_DIR", str(tmp_path / "prompts"))
    monkeypatch.setenv("MEMA_TWIN_DELIVERABLES_DIR", str(tmp_path / "deliverables"))
    flow._schema_ready.clear()
    flow.ensure_schema()
    yield tmp_path
    flow._todos_by_session.clear()


def _mk_persona(env, code, version, md=None):
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


_mid = [90000]


def _mk_evidence(env, code, n, status="uncompiled"):
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


def _start(code):
    return server._twin_impl("task_start", {"brief": "B", "work_type": code})


def test_no_persona_same_domain_injects_most_mature(env):
    _mk_persona(env, "work_report", 1, md="# work_report v1\nA")
    _mk_persona(env, "meeting_minutes", 3, md="# meeting_minutes v3\nB")
    _mk_evidence(env, "work_report", 5)
    _mk_evidence(env, "meeting_minutes", 2)
    r = _start("comm_copy")  # 记录与同步，无专属
    assert r["ok"]
    assert r["fallback_from"]["work_type"] == "work_report"  # 活证据多者
    assert r["fallback_from"]["evidence_count"] == 5
    assert r["fallback_persona_md"] == "# work_report v1\nA"
    assert "垫底" in r["fallback_persona_note"] and "权威来源为准" in r["fallback_persona_note"]
    assert "meeting_minutes" in r["fallback_persona_note"]  # 其余候选在列
    assert "优先级" in r["fallback_persona_note"]


def test_own_persona_present_no_fallback(env):
    _mk_persona(env, "work_report", 2)
    _mk_persona(env, "meeting_minutes", 1)
    r = _start("work_report")
    assert r["ok"] and "fallback_persona_md" not in r  # 专属在场=自动退出


def test_other_domain_only_no_fallback(env):
    _mk_persona(env, "product_doc", 1)  # 规格与执行
    r = _start("comm_copy")  # 记录与同步
    assert r["ok"] and "fallback_persona_md" not in r
    assert "hint" in r  # 原样回退 hint


def test_custom_code_empty_domain_no_fallback_borrow(env):
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO twin_types(type_kind, code, label_zh, label_en, domain,"
            " aliases, is_custom, status, created_at)"
            " VALUES('work_type','my_custom','自定义','my','',?,'1','active',?)",
            ("[]", db.now_iso()))
        conn.commit()
    finally:
        conn.close()
    _mk_persona(env, "work_report", 1)
    r = _start("my_custom")
    assert r["ok"] and "fallback_persona_md" not in r  # domain 空：不借入


def test_custom_code_with_domain_lends_out(env):
    # is_custom=1 + domain 非空 → 可借出（按 domain 判，不做 is_custom 过滤）
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO twin_types(type_kind, code, label_zh, label_en, domain,"
            " aliases, is_custom, status, created_at)"
            " VALUES('work_type','my_rpt','自定义汇报','my','记录与同步',?,'1','active',?)",
            ("[]", db.now_iso()))
        conn.commit()
    finally:
        conn.close()
    _mk_persona(env, "my_rpt", 1, md="# my_rpt\nX")
    _mk_evidence(env, "my_rpt", 3)
    r = _start("comm_copy")
    assert r["ok"] and r["fallback_from"]["work_type"] == "my_rpt"


def test_aud_profile_not_a_candidate(env):
    _mk_persona(env, "aud-leadership", 1)  # 受众画像伪类型
    r = _start("comm_copy")
    assert r["ok"] and "fallback_persona_md" not in r


def test_void_evidence_excluded_flips_donor(env):
    _mk_persona(env, "work_report", 1)
    _mk_persona(env, "meeting_minutes", 1)
    _mk_evidence(env, "work_report", 3)
    _mk_evidence(env, "meeting_minutes", 2)
    r1 = _start("comm_copy")
    assert r1["fallback_from"]["work_type"] == "work_report"
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE twin_evidence SET status='void' WHERE work_type='work_report'")
        conn.commit()
    finally:
        conn.close()
    r2 = _start("comm_copy")
    assert r2["fallback_from"]["work_type"] == "meeting_minutes"  # void 不计，翻转
    assert r2["fallback_from"]["evidence_count"] == 2


def test_tiebreak_version_then_code(env):
    _mk_persona(env, "work_report", 2)
    _mk_persona(env, "meeting_minutes", 2)
    _mk_persona(env, "comm_copy_tmp", 1)  # 占位防命中，无此码——跳过
    _mk_evidence(env, "work_report", 4)
    _mk_evidence(env, "meeting_minutes", 4)
    r = _start("comm_copy")
    # 证据数并列(4=4)、版本并列(2=2) → code 字典序最大 work_report
    assert r["fallback_from"]["work_type"] == "work_report"


def test_tiebreak_version_higher_wins(env):
    _mk_persona(env, "work_report", 1)
    _mk_persona(env, "meeting_minutes", 9)
    _mk_evidence(env, "work_report", 4)
    _mk_evidence(env, "meeting_minutes", 4)
    r = _start("comm_copy")
    assert r["fallback_from"]["work_type"] == "meeting_minutes"  # 版本高者


def test_resume_symmetric_injection(env):
    t = flow.insert_task(brief="T", status="planning", dims={
        "work_type": {"ok": True, "kind": "work_type", "raw": "邮件文案",
                      "code": "comm_copy", "label_zh": "邮件与沟通文案",
                      "matched_by": "exact_or_alias"}})
    _mk_persona(env, "work_report", 1, md="# work_report v1\nresume-ref")
    _mk_evidence(env, "work_report", 2)
    r = server._twin_impl("task_resume", {"task_id": t["id"]})
    assert r["ok"]
    assert r.get("fallback_persona_md") == "# work_report v1\nresume-ref"


def test_have_version_does_not_shortcut_fallback(env):
    _mk_persona(env, "work_report", 1)
    _mk_evidence(env, "work_report", 1)
    r = server._twin_impl("task_start", {
        "brief": "B", "work_type": "comm_copy", "have_persona_version": 7})
    assert r["ok"] and "fallback_persona_md" in r  # 申报机制不作用于 fallback


def test_candidate_missing_twin_types_row_falls_back_to_taxonomy(env):
    # 老库版本新增码缺行：双源回落 taxonomy 取 domain（方案评审 P2-5）
    conn = db.connect()
    try:
        conn.execute("DELETE FROM twin_types WHERE code='work_report'")
        conn.commit()
    finally:
        conn.close()
    _mk_persona(env, "work_report", 1)
    _mk_evidence(env, "work_report", 1)
    r = _start("comm_copy")
    assert r["ok"] and r["fallback_from"]["work_type"] == "work_report"


def test_donor_single_snapshot_no_second_read(env):
    # 注入文本 = 候选 SELECT 快照（P3-1：不二次读库，无并发换版错位面）
    _mk_persona(env, "work_report", 1, md="# snapshot\ndonor text")
    _mk_evidence(env, "work_report", 1)
    md = task_actions._fallback_payload("comm_copy")
    assert md["fallback_persona_md"] == "# snapshot\ndonor text"
    # 快照后再改库不影响已返回的注入物
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE twin_prompt_versions SET prompt_md='# changed' "
            "WHERE work_type='work_report'")
        conn.commit()
    finally:
        conn.close()
    assert md["fallback_persona_md"] == "# snapshot\ndonor text"


# ---- 轮1 review 补锚 ----

def test_candidates_all_listed_per_line(env):
    # P3-3：全列不截断 + 各一行（拍板字面）——3 个其余候选逐行在场
    for w in ("work_report", "meeting_minutes", "personal_notes",
              "official_doc", "test_report"):
        _mk_persona(env, w, 1)
        _mk_evidence(env, w, 1)
    r = _start("comm_copy")
    assert r["ok"]
    note = r["fallback_persona_note"]
    for w in ("meeting_minutes", "personal_notes", "official_doc", "test_report"):
        assert w in note
    assert "\n- " in note  # 各一行（轮1 P3-1：拍板「各一行」非分号串联）


def test_supplement_coexists_with_fallback_elif_branch(env, monkeypatch):
    # P3-4：elif-supplement 分支——本工种有未编译增补 + 无 persona → 增补与
    # fallback 共存（删掉此处注入的回归会红）。增补走 mema 读，隔离环境 stub 掉
    monkeypatch.setattr(task_actions, "_supplement_payload",
                        lambda code, persona, client=None: {
                            "persona_supplement": ["本工种未编译偏好一条"],
                            "persona_supplement_note": "stub 注入"})
    _mk_persona(env, "work_report", 1, md="# wr\n")
    _mk_evidence(env, "work_report", 2)
    r = _start("comm_copy")
    assert r["ok"]
    assert r["persona_supplement"] == ["本工种未编译偏好一条"]  # 走的确实是 elif 分支
    assert "fallback_persona_md" in r and r["fallback_from"]["work_type"] == "work_report"


def test_mirror_own_persona_present_no_fallback(env, monkeypatch):
    # 轮1 提示：mirror 降级读到的专属 persona 同样算在场（version=None 也不触发）
    import json as _json
    mirror_dir = env / "prompts" / "work_report"
    mirror_dir.mkdir(parents=True)
    (mirror_dir / "active.md").write_text("# mirror persona\n", encoding="utf-8")
    _mk_persona(env, "meeting_minutes", 1)
    _mk_evidence(env, "meeting_minutes", 1)
    r = _start("work_report")
    assert r["ok"]
    assert "fallback_persona_md" not in r  # mirror 专属在场
    assert r.get("persona_prompt_md") == "# mirror persona\n"


# ---- 轮2 对抗修复回归 ----

def test_domain_whitespace_normalized(env):
    # P2-1：canonicalize 入口归一 + _wt_domain 读取归一——空格不再让同域失联
    from mema_twin import db as twin_db
    conn = db.connect()
    try:
        twin_db.add_canonical(conn, "work_type", "my_daily", "每日纪要",
                              domain=" 记录与同步 ")  # 前后空格（历史脏数据同款）
    finally:
        conn.close()
    _mk_persona(env, "my_daily", 1, md="# daily\nD")
    _mk_evidence(env, "my_daily", 5)
    _mk_persona(env, "work_report", 1)
    _mk_evidence(env, "work_report", 1)
    r = _start("comm_copy")
    assert r["ok"] and r["fallback_from"]["work_type"] == "my_daily"  # 证据最多者不再被空格否决


def test_empty_prompt_md_candidate_skipped(env):
    # P3-2：空正文 donor 无垫底价值——跳过，次成熟者顶上
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO twin_prompt_versions(work_type, version, prompt_md,"
            " source_memory_ids, model, status, evidence_count, created_at,"
            " activated_at) VALUES('work_report',1,'','[]','t','active',0,?,?)",
            (db.now_iso(), db.now_iso()))
        conn.commit()
    finally:
        conn.close()
    _mk_evidence(env, "work_report", 5)
    _mk_persona(env, "meeting_minutes", 1)
    _mk_evidence(env, "meeting_minutes", 1)
    r = _start("comm_copy")
    assert r["ok"] and r["fallback_from"]["work_type"] == "meeting_minutes"


def test_corrupt_version_candidate_skipped(env):
    # P3-1：病态 TEXT 版本号不炸 task_start，候选跳过
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO twin_prompt_versions(work_type, version, prompt_md,"
            " source_memory_ids, model, status, evidence_count, created_at,"
            " activated_at) VALUES('work_report','9x','# x','[]','t','active',0,?,?)",
            (db.now_iso(), db.now_iso()))
        conn.commit()
    finally:
        conn.close()
    _mk_evidence(env, "work_report", 5)
    r = _start("comm_copy")
    assert r["ok"] and "fallback_persona_md" not in r  # 唯一候选被跳过 → 无注入不炸


def test_label_zh_multiline_sanitized(env):
    # P3-5：治理表 label 含换行 → note 渲染压平（「各一行」契约不破）
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO twin_types(type_kind, code, label_zh, label_en, domain,"
            " aliases, is_custom, status, created_at)"
            " VALUES('work_type','my_rpt','多行\n标签','my','记录与同步',?,'1','active',?)",
            ("[]", db.now_iso()))
        conn.commit()
    finally:
        conn.close()
    _mk_persona(env, "my_rpt", 1)
    _mk_evidence(env, "my_rpt", 1)
    r = _start("comm_copy")
    assert r["ok"]
    first = r["fallback_persona_note"].splitlines()[0]
    assert "多行 标签" in first and "\n" not in first
