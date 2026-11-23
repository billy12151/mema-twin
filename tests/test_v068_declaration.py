"""v0.6.8 申报键归一与未知键警告（方案 mema-twin-v068-declaration-keys-plan.md
§测试计划）：aud- 前缀剥壳归一 / 未知码软忽略+警告回显（不硬拒任务）/
收窄域（精确裸码+治理自建码，不认别名/中文名）/ 先分类后校验 / 同码 max
胜出（顺序无关）/ 三动作 return 前无条件附加 / help 形状文档。"""
from mema_twin import db, server


def _t(action, data=None):
    return server._twin_impl(action, data)


def _mk_profile(md="# 高层画像\n\n"):
    """造 leadership 画像：写证据 → compile aud-leadership → submit 落版。"""
    _t("write", {"content": "给高层的汇报先结论后论据", "work_type": "周报",
                 "audience": "高层", "purpose": "同步"})
    ra = _t("compile", {"work_type": "aud-leadership"})
    s = _t("submit", {"work_type": "aud-leadership",
                      "prompt_md": md + "口径条目。\n" * 200,
                      "source_memory_ids": ra["source_ids_suggested"]})
    assert s["ok"], s
    return s["version"]


def _ts(**extra):
    d = {"brief": "任务", "work_type": "周报", "audience": "高层"}
    d.update(extra)
    return _t("task_start", d)


# ---- 1 aud- 前缀剥壳归一 → 短路 ----

def test_v068_aud_prefix_stripped_short_circuit():
    v = _mk_profile()
    t1 = _ts()
    assert t1["audience_profile_md"]  # 首次全文
    t2 = _ts(have_audience_versions={"aud-leadership": v})
    assert t2.get("audience_profile_unchanged") is True
    assert t2["audience_profile_code"] == "leadership"
    assert "audience_profile_md" not in t2
    assert "have_audience_versions_unknown" not in t2  # 归一成功不算未知


# ---- 2 未知码软忽略 + 警告（不硬拒，宁重复不缺席）----

def test_v068_unknown_code_soft_ignored_with_warning():
    _mk_profile()
    t1 = _ts()
    assert t1["audience_profile_md"]
    t2 = _ts(have_audience_versions={"cto": 1})
    assert t2["ok"] is True  # 不硬拒任务
    assert t2["audience_profile_md"]  # 全文照注（宁重复不缺席）
    assert "audience_profile_unchanged" not in t2  # 与假 unchanged 互斥
    w = t2["have_audience_versions_unknown"]
    assert w["ignored"] == ["cto"] and w["total"] == 1
    assert "leadership" in w["note"]  # 实时合法码清单在场
    assert "aud-<合法裸码>" in w["note"]  # 措辞收窄（R1 P2-3）
    assert "leadership（" in w["note"]  # zh 对照（R2 P2-4）


# ---- 3 混合键三分：保留 / 归一 / 未知 ----

def test_v068_mixed_keys_classify_three_ways():
    v = _mk_profile()
    t2 = _ts(have_audience_versions={"leadership": v, "aud-self": 3, "cto": 1})
    assert t2.get("audience_profile_unchanged") is True  # leadership 命中短路
    assert t2["have_audience_versions_unknown"]["ignored"] == ["cto"]  # 仅真未知


# ---- 4 aud- 边界：空余数/不存在码/中文余数 → 未知（不 crash）----

def test_v068_aud_prefix_edge_cases():
    r = _ts(have_audience_versions={"aud-": 1, "aud-nonexistent": 2, "aud-高层": 3})
    assert r["ok"] is True
    assert set(r["have_audience_versions_unknown"]["ignored"]) == {
        "aud-", "aud-nonexistent", "aud-高层"}  # 中文名不用于申报（收窄域）


# ---- 5 无受众维度任务：警告仍附加（不进 if aud_code 块）----

def test_v068_warning_attached_without_audience_dimension():
    r = _t("task_start", {"brief": "b", "work_type": "周报",
                          "have_audience_versions": {"cto": 1}})
    assert r["ok"] is True
    assert "have_audience_versions_unknown" in r
    assert "audience_profile_md" not in r


# ---- 6 干净申报/未申报 → 无警告键 ----

def test_v068_clean_or_absent_declaration_no_warning():
    t = _t("task_start", {"brief": "b", "work_type": "周报"})
    assert "have_audience_versions_unknown" not in t
    t2 = _ts(have_audience_versions={"leadership": 1, "aud-self": 2})
    assert "have_audience_versions_unknown" not in t2  # 全合法（版本不等≠未知）


# ---- 7 形态门：非 dict 打回（「已废弃」文案）----

def test_v068_non_dict_shape_rejected():
    r = _ts(have_audience_versions="v5")
    assert r["ok"] is False and r["field"] == "have_audience_versions"
    assert "已废弃" in r["reason"]


# ---- 8 同码 max 胜出（与键序无关）----

def test_v068_same_code_max_wins_order_independent():
    _mk_profile()  # v1
    t1 = _ts(have_audience_versions={"leadership": 1, "aud-leadership": 9})
    t2 = _ts(have_audience_versions={"aud-leadership": 9, "leadership": 1})
    for t in (t1, t2):
        assert t["audience_profile_md"]  # max=9 ≠ v1 → 全文重注入
        assert "have_audience_versions_unknown" not in t  # 两键都归一成功
        assert t.get("audience_profile_unchanged") is not True


# ---- 9 治理自建受众码申报命中短路（R2 P0-1 回归位）----

def test_v068_custom_audience_code_declared_short_circuit():
    conn = db.connect()
    conn.execute(
        "INSERT INTO twin_types(type_kind, code, label_zh, label_en, domain, aliases,"
        " is_custom, status, created_at) VALUES"
        "('audience','board_observer','观察员','board observer','','[]',1,'active','2026-01-01')")
    conn.commit()
    conn.close()
    _t("write", {"content": "观察员简报要短", "work_type": "周报",
                 "audience": "board_observer", "purpose": "同步"})
    ra = _t("compile", {"work_type": "aud-board_observer"})
    s = _t("submit", {"work_type": "aud-board_observer",
                      "prompt_md": "# 观察员画像\n\n" + "口径。\n" * 200,
                      "source_memory_ids": ra["source_ids_suggested"]})
    assert s["ok"], s
    t1 = _t("task_start", {"brief": "b", "work_type": "周报",
                           "audience": "board_observer"})
    assert t1["audience_profile_md"]
    t2 = _t("task_start", {"brief": "b2", "work_type": "周报",
                           "audience": "board_observer",
                           "have_audience_versions": {"aud-board_observer": s["version"]}})
    assert t2.get("audience_profile_unchanged") is True  # 自建码归一域命中
    assert "have_audience_versions_unknown" not in t2


# ---- 10 aud- 归一 + 版本不匹配 → 全文重注入附变更说明 ----

def test_v068_reinjected_with_change_note_when_version_differs():
    v1 = _mk_profile()
    _t("write", {"content": "高层偏好表格化汇报", "work_type": "周报",
                 "audience": "高层", "purpose": "同步"})
    ra = _t("compile", {"work_type": "aud-leadership"})
    s2 = _t("submit", {"work_type": "aud-leadership",
                       "prompt_md": "# 高层画像 v2\n\n" + "新口径。\n" * 200,
                       "source_memory_ids": ra["source_ids_suggested"]})
    assert s2["ok"] and s2["version"] == 2
    t = _ts(have_audience_versions={"aud-leadership": v1})
    assert t["audience_profile_md"]
    assert "变更为 v2" in t.get("audience_profile_note", "")


# ---- 11 拼错当前受众（aud-slef）→ 未知键，绝不谎报 unchanged ----

def test_v068_unknown_never_claims_unchanged():
    v = _mk_profile()
    t = _ts(have_audience_versions={"aud-slef": v})
    assert t["audience_profile_md"]
    assert "audience_profile_unchanged" not in t
    assert t["have_audience_versions_unknown"]["ignored"] == ["aud-slef"]


# ---- 12 先分类后校验：识别键坏值硬拒；未识别键坏值软忽略 ----

def test_v068_recognized_key_bad_value_hard_reject():
    r = _ts(have_audience_versions={"leadership": "v5"})
    assert r["ok"] is False and r["field"] == "have_audience_versions"
    r2 = _ts(have_audience_versions={"cto": "v5"})
    assert r2["ok"] is True  # 未识别键不论值好坏一律软忽略（先分类后校验）
    assert r2["have_audience_versions_unknown"]["ignored"] == ["cto"]


# ---- 13 警告回显体积帽：前 10 条 / 单键 64 字符 ----

def test_v068_warning_caps():
    keys = {"x" * 80: 1}
    keys.update({f"bad{i}": 1 for i in range(14)})
    r = _ts(have_audience_versions=keys)
    w = r["have_audience_versions_unknown"]
    assert len(w["ignored"]) == 10 and w["total"] == 15
    assert w["ignored"][0].endswith("…") and len(w["ignored"][0]) == 65
    assert all(len(k) <= 65 for k in w["ignored"])


# ---- 14 help 形状文档（#4）----

def test_v068_help_documents_shape():
    hs = server._HELP_ENTRIES["task_start"]
    assert "have_audience_versions" in hs and "{受众码: 版本号}" in hs
    assert "audience_profile_unchanged" in hs  # 短路回显三键说全
    hr = server._HELP_ENTRIES["task_revise"]
    assert "have_audience_versions" in hr  # revise 口径+抄号告诫


# ---- 15 三动作 return 前均附加（resume/revise 各一）----

def test_v068_warning_on_resume_and_revise():
    t0 = _t("task_start", {"brief": "b", "work_type": "周报"})
    r = _t("task_resume", {"task_id": t0["task_id"],
                           "have_audience_versions": {"cto": 1}})
    assert r["ok"] is True
    assert r["have_audience_versions_unknown"]["ignored"] == ["cto"]
    # resume 是本测试首个 attach → 全量码表；seen 槽跨动作共享
    assert "leadership（" in r["have_audience_versions_unknown"]["note"]
    # resume 让位换代：交付与返工都对准新代任务行
    sub = _t("task_submit", {"task_id": r["new_task_id"], "deliverable_md": "# 交付"})
    assert sub["ok"], sub
    rv = _t("task_revise", {"task_id": r["new_task_id"], "revision_reason": "改稿",
                            "have_audience_versions": {"cto": 1}})
    assert rv["ok"] is True
    assert rv["have_audience_versions_unknown"]["ignored"] == ["cto"]
    # 同 client 第二次（revise）→ 指针（含 taxonomy 重取指引，R2 P1-1）
    assert "taxonomy" in rv["have_audience_versions_unknown"]["note"]
    assert "leadership（" not in rv["have_audience_versions_unknown"]["note"]


# ---- 16 lone surrogate 消毒（实施后 R2 P1）：回显 JSON-safe，http 序列化不炸 ----

def test_v068_lone_surrogate_sanitized_json_safe():
    import json as _json
    r = _ts(have_audience_versions={"a\ud800b": 1})
    assert r["ok"] is True  # 任务不受影响
    w = r["have_audience_versions_unknown"]
    assert w["ignored"] == ["a?b"]  # lone surrogate → 单个 '?'（JSON-safe）
    assert w["total"] == 1
    _json.dumps(r, ensure_ascii=False)  # 全响应可序列化（http model_dump_json 不炸）


def test_v068_b8_unknown_key_surrogate_sanitized():
    """B8 未知键回显同源兜底（R2 P1 同款）：reason/did_you_mean JSON-safe。"""
    import json as _json
    r = _t("task_start", {"brief": "b", "work_type": "周报", "bogus\ud800key": 1})
    assert r["ok"] is False and "unknown field" in r["reason"]
    _json.dumps(r, ensure_ascii=False)


# ---- 17（v0.6.9）码表教学 note S6 频控：同 client 二次起换指针 ----

def test_v068_warning_note_suppressed_second_call():
    _mk_profile()
    t1 = _ts(have_audience_versions={"cto": 1})
    assert "leadership（" in t1["have_audience_versions_unknown"]["note"]  # 首次全量码表
    t2 = _ts(have_audience_versions={"cto": 1})
    w2 = t2["have_audience_versions_unknown"]
    assert "help(topic=task_start)" in w2["note"]  # 二次起一句话指针
    assert "taxonomy" in w2["note"]  # 指针带实时重取指引（R2 P1-1：压缩自足）
    assert "leadership（" not in w2["note"]  # 码表不重复
    assert w2["ignored"] == ["cto"] and w2["total"] == 1  # 事实数据照旧
    assert t2["audience_profile_md"]  # 短路兜底不变（宁重复不缺席）


# ---- 18/19（实施后 R2）频控边界：http 无头宿主豁免 / 跨租户独立槽 ----

def test_v069_http_anonymous_hosts_always_full(monkeypatch):
    """http 多宿主形态（tenant 已路由）client 缺失 → 不占槽恒全量（R2 P1-2）。"""
    from mema_twin import task_actions as ta, tenant as tenant_mod
    monkeypatch.setattr(tenant_mod, "current_tenant", lambda: "u1")
    for _ in range(2):
        out = {}
        ta._attach_aud_unknown_warning(out, ["cto"], {})
        assert "leadership（" in out["have_audience_versions_unknown"]["note"]


def test_v069_cross_tenant_frequency_isolated(monkeypatch):
    """跨租户同 client 独立 seen 槽（R2 P2-3 补钉）。"""
    from mema_twin import task_actions as ta, tenant as tenant_mod
    monkeypatch.setattr(tenant_mod, "current_tenant", lambda: "u1")
    o1 = {}
    ta._attach_aud_unknown_warning(o1, ["cto"], {"client": "alpha"})
    assert "leadership（" in o1["have_audience_versions_unknown"]["note"]
    monkeypatch.setattr(tenant_mod, "current_tenant", lambda: "u2")
    o2 = {}
    ta._attach_aud_unknown_warning(o2, ["cto"], {"client": "alpha"})
    assert "leadership（" in o2["have_audience_versions_unknown"]["note"]  # u2:alpha 首次
