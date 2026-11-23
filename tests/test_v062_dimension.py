"""v0.6.2 维度治理闭环（方案 mema-twin-v062-dimension-plan.md §测试计划）：
D1 description 字段/回填 / D2 gate 全量+说明+similar / D3 resolve confirm 门 /
D4 票据快照+client+3 步闭环 / D5 孤儿票提示 / D6 约定理由文案。"""
from mema_twin import db, flow, server, taxonomy


def _t(action, data=None):
    return server._twin_impl(action, data)


# ---- D1 description ----

def test_d1_builtin_descriptions_complete():
    for kind in ("work_type", "audience", "purpose"):
        for t in taxonomy.all_types(kind):
            assert taxonomy.description_for(t.code), f"{kind}/{t.code} 缺说明"


def test_d1_migration_backfills_existing_db(tmp_path, monkeypatch):
    """R1 P0-1：0.6.1 旧库（twin_types 无 description 列）升级——迁移加列+回填
    后 desc 非空。裸 sqlite3 预建旧 schema 真实模拟升级路径。"""
    import sqlite3 as _sq
    from mema_twin import db
    old = tmp_path / "old.sqlite3"
    conn = _sq.connect(old)
    conn.execute(
        "CREATE TABLE twin_types(type_kind TEXT NOT NULL, code TEXT NOT NULL,"
        " label_zh TEXT, label_en TEXT, domain TEXT, aliases TEXT,"
        " is_custom INTEGER DEFAULT 0, status TEXT DEFAULT 'active',"
        " created_at TEXT, UNIQUE(type_kind, code))")
    conn.execute(
        "INSERT INTO twin_types(type_kind, code, label_zh, label_en, domain,"
        " aliases, is_custom, status, created_at)"
        " VALUES('work_type', 'work_report', '工作汇报', 'work report', '',"
        " '[]', 0, 'active', '2026-01-01')")
    conn.commit()
    conn.close()
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(old))
    monkeypatch.setenv("MEMA_TWIN_PROMPTS_DIR", str(tmp_path / "p"))
    flow._schema_ready.clear()
    conn = db.connect()  # 新代码首连：ALTER 加列+回填
    rows = conn.execute(
        "SELECT COUNT(*) AS c FROM twin_types WHERE type_kind='work_type'"
        " AND is_custom=0 AND description != ''").fetchone()
    conn.close()
    assert rows["c"] == 1


def test_d1_taxonomy_full_has_desc_compact_not():
    _t("write", {"content": "偏好", "work_type": "周报",
                 "audience": "高层", "purpose": "同步"})
    full = _t("taxonomy", {"kind": "work_type"})
    assert all(t.get("description") for t in full["types"][:5])
    compact = _t("taxonomy", {"kind": "work_type", "compact": True})
    assert all("description" not in t for t in compact["types"])


# ---- D2 gate 全量+说明+similar ----

def test_d2_gate_reject_carries_all_values_and_similar():
    r = _t("write", {"content": "c", "work_type": "周报",
                     "audience": "灵能监理司", "purpose": "同步"})
    f = r["fields"]["audience"]
    assert f["pending_id"]
    assert len(f["all_values"]) == 9 and all(v["description"] for v in f["all_values"])
    assert f["candidates"][0].get("description")  # candidates 形状加法：+desc
    assert isinstance(f["similar"], list)  # 灵能监理司无高分相似（regulator 类似但 <0.82 阈值边缘）


def test_d2_similar_flags_close_value():
    _t("write", {"content": "c1", "work_type": "周报",
                 "audience": "高层", "purpose": "同步"})
    r = _t("write", {"content": "c2", "work_type": "周报",
                     "audience": "高层与决策层", "purpose": "同步"})
    # 「高层与决策层」精确命中（是 leadership 正名）→ 不打回；改用近似未知值
    r = _t("write", {"content": "c3", "work_type": "周报",
                     "audience": "高层与决策", "purpose": "同步"})
    if r.get("error") == "unmatched_value":
        sim = r["fields"]["audience"]["similar"]
        assert sim and sim[0]["code"] == "leadership"


# ---- D3 resolve confirm 门 ----

def _make_pending(kind="audience", raw="灵能监理司"):
    _t("write", {"content": "c", "work_type": "周报", "audience": raw,
                 "purpose": "同步"})
    rows = db.list_pending(db.connect(), "pending")
    return next(r["id"] for r in rows if r["type_kind"] == kind and r["raw_value"] == raw)


def test_d3_canonicalize_similar_blocked_without_confirm():
    pid = _make_pending()
    r = _t("resolve", {"pending_id": pid, "decision": "canonicalize",
                       "new_type": {"code": "regulators", "zh": "监管与审查机构x"}})
    assert r["ok"] is False and r["require_confirm"] is True
    assert r["similar"] and any(s["code"] == "regulator" for s in r["similar"])
    assert r["all_values"]
    # confirm=true 放行
    r2 = _t("resolve", {"pending_id": pid, "decision": "canonicalize",
                        "new_type": {"code": "spirit_regulator",
                                     "zh": "灵能监理司"},
                        "confirm": True})
    assert r2["ok"] and r2["resolved_code"] == "spirit_regulator"
    assert r2.get("content_snapshot") == "c"  # D4 快照随响应


def test_d3_canonicalize_exact_alias_hit_requires_confirm():
    """R1 P2-2：zh 精确撞既有**别名**（difflib 短串不可靠，精确撞检兜底）——
    pending raw 是未知值，但立的新 zh 恰好是另一个码的别名。"""
    pid = _make_pending(raw="灵能监理司")
    r = _t("resolve", {"pending_id": pid, "decision": "canonicalize",
                       "new_type": {"code": "boss_aud", "zh": "老板"}})
    assert r["ok"] is False and r["require_confirm"] is True
    assert any(s["code"] == "direct_manager" for s in r["similar"])  # 别名精确撞


def test_d3_canonicalize_no_similar_passes_directly():
    pid = _make_pending(raw="灵能监理司")
    r = _t("resolve", {"pending_id": pid, "decision": "canonicalize",
                       "new_type": {"code": "spirit_regulator",
                                    "zh": "灵能监理司"}})
    assert r["ok"] and r["resolved_code"] == "spirit_regulator"  # 0 确认往返


def test_d3_confirm_strict_bool():
    pid = _make_pending()
    r = _t("resolve", {"pending_id": pid, "decision": "canonicalize",
                       "new_type": {"code": "regulators", "zh": "监管审查机构"},
                       "confirm": "true"})
    assert r["ok"] is False and r["require_confirm"] is True  # 字符串不算确认（相似 0.857 在场）


def test_d3_map_invalid_code_carries_all_values():
    pid = _make_pending()
    r = _t("resolve", {"pending_id": pid, "decision": "map", "code": "nope"})
    assert r["ok"] is False and "all_values" in r
    assert len(r["all_values"]) == 9


def test_d3_batch_mixed_confirm_flow():
    """R2 炸-1：两段式——相似未 confirm 票摘出 confirm_required，余票照常裁定
    （非全笔回滚）。"""
    pid_aud = _make_pending(raw="灵能监理司")
    _t("write", {"content": "c2", "work_type": "灵能审计年报",
                 "audience": "灵能监理司", "purpose": "灵能合规"})
    rows = db.list_pending(db.connect(), "pending")
    pid_wt = next(r["id"] for r in rows if r["type_kind"] == "work_type")
    pid_p = next(r["id"] for r in rows
                 if r["type_kind"] == "purpose" and r["raw_value"] == "灵能合规")
    r = _t("resolve", {"tickets": [
        {"pending_id": pid_wt, "decision": "map", "code": "product_doc"},
        {"pending_id": pid_aud, "decision": "canonicalize",
         "new_type": {"code": "regulators", "zh": "监管审查机构x"}},  # 相似无 confirm
        {"pending_id": pid_p, "decision": "reject"},
    ]})
    assert r["ok"] is True  # 混合结果：2 票裁定 + 1 票 confirm_required
    assert r["applied"] == 2
    assert len(r["confirm_required"]) == 1
    assert r["confirm_required"][0]["pending_id"] == pid_aud
    assert r["confirm_required"][0]["require_confirm"] is True
    conn = db.connect()
    wt = conn.execute("SELECT status FROM twin_pending_values WHERE id=?",
                      (pid_wt,)).fetchone()["status"]
    conn.close()
    assert wt == "mapped"  # 其余票照常落库（未被相似票拖累）


# ---- D4 票据快照 + client + 3 步闭环 ----

def test_d4_snapshot_and_client_recorded():
    _t("write", {"content": "支付类 PRD 的异常分支必须给报错文案", "work_type": "周报",
                 "audience": "灵能监理司", "purpose": "同步", "client": "e2e-host"})
    it = _t("pending", {"status": "pending"})["items"][0]
    assert it["client"] == "e2e-host"
    assert it["content_preview"] == "支付类 PRD 的异常分支必须给报错文案"
    assert it["content_chars"] == len("支付类 PRD 的异常分支必须给报错文案")
    assert "content_snapshot" not in it  # 列表不回全文（R1 P1-3）


def test_d4_pending_no_full_snapshot_leak():
    _t("write", {"content": "xyz" * 100, "work_type": "周报",
                 "audience": "灵能监理司", "purpose": "同步"})
    items = _t("pending", {})["items"]
    assert all("content_snapshot" not in it for it in items)
    long = [it for it in items if it["content_chars"] == 300]
    assert long and len(long[0]["content_preview"]) == 200


def test_d4_three_step_closed_loop():
    """E2E：write 拒 → resolve（响应带全文）→ 照抄 write 成功——零丢失。"""
    content = "跨会话积压场景的偏好正文验证：必须含结论与数据双栏"
    r1 = _t("write", {"content": content, "work_type": "周报",
                      "audience": "灵能监理司", "purpose": "同步"})
    assert r1["ok"] is False
    pid = r1["fields"]["audience"]["pending_id"]
    r2 = _t("resolve", {"pending_id": pid, "decision": "canonicalize",
                        "new_type": {"code": "spirit_regulator",
                                     "zh": "灵能监理司"},
                        "confirm": True})
    assert r2["ok"] and r2["content_snapshot"] == content  # 全文带回
    r3 = _t("write", {"content": r2["content_snapshot"], "work_type": "周报",
                      "audience": "灵能监理司", "purpose": "同步"})
    assert r3["ok"] and r3["memory_id"]  # 照抄成功


def test_d4_client_precedence_error_priority():
    """R2 炸-2 副作用钉：脏 client + 归一 miss → invalid_input（client 校验
    前置）优先于 unmatched_value。"""
    r = _t("write", {"content": "c", "work_type": "灵能审计年报",
                     "audience": "高层", "purpose": "同步",
                     "client": "x" * 100})
    assert r["ok"] is False and "unmatched_value" not in str(r.get("error"))


# ---- D5 孤儿票提示 ----

def test_d5_orphan_ticket_hinted_on_success():
    content = "评审意见必须逐条回复处理状态"
    _t("write", {"content": content, "work_type": "周报",
                 "audience": "灵能监理司", "purpose": "同步"})  # 拒→落票
    r = _t("write", {"content": content, "work_type": "周报",
                     "audience": "高层", "purpose": "同步"})  # 换合法值成功
    assert r["ok"]
    orphans = r.get("orphan_pending_tickets")
    assert orphans and orphans[0]["pending_id"] > 0
    assert orphans[0]["ratio"] >= 0.82
    assert "勿盲目 reject" in r["orphan_guidance"]


def test_d5_no_orphan_for_other_client():
    content = "评审意见必须逐条回复处理状态"
    _t("write", {"content": content, "work_type": "周报",
                 "audience": "灵能监理司", "purpose": "同步",
                 "client": "host-a"})
    r = _t("write", {"content": content, "work_type": "周报",
                     "audience": "高层", "purpose": "同步",
                     "client": "host-b"})
    assert r["ok"] and "orphan_pending_tickets" not in r  # 跨宿主不提示


def test_d5_mapped_ticket_not_scanned():
    content = "评审意见必须逐条回复处理状态"
    _t("write", {"content": content, "work_type": "周报",
                 "audience": "灵能监理司", "purpose": "同步"})
    rows = db.list_pending(db.connect(), "pending")
    pid = rows[0]["id"]
    _t("resolve", {"pending_id": pid, "decision": "map", "code": "leadership"})
    r = _t("write", {"content": content, "work_type": "周报",
                     "audience": "高层", "purpose": "同步"})
    assert "orphan_pending_tickets" not in r  # mapped 票不进扫描


def test_d5_task_start_success_no_orphan_hint():
    """D5 只挂 write 成功路径；task_start 成功不提示（保守不扩）。"""
    content = "评审意见必须逐条回复处理状态"
    _t("write", {"content": content, "work_type": "周报",
                 "audience": "灵能监理司", "purpose": "同步"})
    t = flow.insert_task(brief="b", status="planning", dims={})
    plan = _t("plan_set", {"task_id": t["id"], "steps": [{"title": "a"}]})
    assert plan["ok"] and "orphan_pending_tickets" not in plan


# ---- D6 约定理由文案 ----

def test_d6_precheck_rationale_in_place():
    full = _t("help", {"topic": "write"})["help"]
    assert "自主对齐已有口径" in full and "不需要被打断" in full


def test_d3_description_persisted_and_capped():
    pid = _make_pending()
    r = _t("resolve", {"pending_id": pid, "decision": "canonicalize",
                       "new_type": {"code": "spirit_regulator", "zh": "灵能监理司",
                                    "description": "灵能世界的监理机构"}})
    assert r["ok"]
    conn = db.connect()
    row = conn.execute("SELECT description FROM twin_types WHERE code='spirit_regulator'").fetchone()
    conn.close()
    assert row["description"] == "灵能世界的监理机构"
    # >200 帽打回
    pid2 = _make_pending(raw="灵能监理司2")
    r2 = _t("resolve", {"pending_id": pid2, "decision": "canonicalize",
                        "new_type": {"code": "spirit_regulator2", "zh": "灵能监理司2",
                                     "description": "长" * 201}})
    assert r2["ok"] is False and "new_type.description" in str(r2.get("field"))


def test_d4_reject_response_carries_snapshot():
    pid = _make_pending()
    r = _t("resolve", {"pending_id": pid, "decision": "reject"})
    assert r["ok"] and r.get("content_snapshot") == "c"  # 关票前最后一次看到内容


def test_d5_long_content_skipped_for_orphan():
    _t("write", {"content": "长" * 2500, "work_type": "周报",
                 "audience": "灵能监理司", "purpose": "同步"})  # 快照 2500 字符
    r = _t("write", {"content": "长" * 2499 + "尾", "work_type": "周报",
                     "audience": "高层", "purpose": "同步"})
    assert r["ok"] and "orphan_pending_tickets" not in r  # >2000 护栏


def test_d5_duplicate_also_hints_orphan():
    content = "评审意见必须逐条回复处理状态"
    _t("write", {"content": content, "work_type": "周报",
                 "audience": "灵能监理司", "purpose": "同步"})
    r = _t("write", {"content": content, "work_type": "周报",
                     "audience": "高层", "purpose": "同步"})
    r2 = _t("write", {"content": content, "work_type": "周报",
                      "audience": "高层", "purpose": "同步"})  # duplicate=true
    assert r2["duplicate"] is True
    assert r2.get("orphan_pending_tickets")  # 两路共用插桩
    assert r.get("orphan_pending_tickets")


def test_d6_skill_rationale_grep():
    src = open("skill/SKILL.md", encoding="utf-8").read()
    assert "自主对齐已有口径" in src and "不需要被打断" in src


def test_v063_source_kind_tracked_and_guided():
    """v0.6.3 source_kind：write 与 task_start 来源分流，resolve 响应按来源
    区别引导（R2 险-1 二义性结构化修复）。"""
    # write 来源
    pid_w = _make_pending()
    r_w = _t("resolve", {"pending_id": pid_w, "decision": "map", "code": "leadership"})
    assert r_w["ok"] and r_w.get("content_source") == "write"
    assert "可直接照抄" in r_w.get("content_source_note", "")
    # task_start 来源（map 后「灵能监理司」已命中，换未命中值）
    _t("task_start", {"brief": "帮老板写一份双十一大促复盘报告", "work_type": "周报",
                      "audience": "灵能监理司2", "purpose": "同步"})
    items = _t("pending", {})["items"]
    ts_ticket = next(it for it in items if it["source_kind"] == "task_start")
    assert ts_ticket["content_preview"].startswith("帮老板写")
    r_ts = _t("resolve", {"pending_id": ts_ticket["id"], "decision": "map",
                          "code": "regulator"})
    assert r_ts["ok"] and r_ts.get("content_source") == "task_start"
    assert "不是偏好" in r_ts.get("content_source_note", "")
    assert "垃圾证据" in r_ts.get("content_source_note", "")


def test_v063_reject_and_unknown_forms():
    """reject 响应 content_source 在场（含 task_start note）；旧票 unknown 形态。"""
    _t("task_start", {"brief": "另一份 brief", "work_type": "周报",
                      "audience": "灵能监理司3", "purpose": "同步"})
    items = _t("pending", {})["items"]
    ts = next(it for it in items if it["source_kind"] == "task_start")
    r = _t("resolve", {"pending_id": ts["id"], "decision": "reject"})
    assert r["ok"] and r.get("content_source") == "task_start"
    assert "不是偏好" in r.get("content_source_note", "")
    # 旧票形态（source_kind='' 的 pending 票——模拟 0.6.2 前存量）：content_source
    # 为 unknown 且无 note
    _t("write", {"content": "旧票正文", "work_type": "周报",
                 "audience": "灵能监理司4", "purpose": "同步"})
    conn = db.connect()
    conn.execute("UPDATE twin_pending_values SET source_kind='' WHERE client=''",
                 ())
    conn.commit()
    conn.close()
    items = _t("pending", {})["items"]
    old = next(it for it in items if it["source_kind"] == "unknown")
    r2 = _t("resolve", {"pending_id": old["id"], "decision": "map",
                        "code": "leadership"})
    assert r2["ok"] and r2.get("content_source") == "unknown"
    assert "content_source_note" not in r2  # unknown 无引导 note


def test_v064_tool_gap_normalized():
    """v0.6.4 tool_gap 归一：twin.* 前缀特判 + 归一双向子串——jingleAI 实测
    14 条全量误报（mema-twin-twin vs twin.plan_set、write vs write_file）修复。"""
    from mema_twin.playbook_actions import _tool_available
    host = ["mema-twin-twin", "bash", "read", "write"]
    # twin.* 前缀：宿主挂 twin 工具即可用
    assert _tool_available("twin.plan_set", host)
    assert _tool_available("twin", host)
    # 归一双向子串：write_file vs write 命中
    assert _tool_available("write_file", host)
    assert _tool_available("read", host)
    # 真缺口仍报
    assert not _tool_available("sqlite3-cli", host)
    assert not _tool_available("ripgrep-absolute-name", host)
    # 已知过宽（文档化行为，勿当 bug 修）：短 token 子串误命中——方向是
    # 少报缺口（gap 纯 advisory，注入面本就要求宿主按实际环境降级），
    # 好于 v0.6.3 前的全量误报
    assert _tool_available("read", ["threadreader"])  # read ⊂ threadreader
    assert _tool_available("write", ["rewrite"])


def _compile_submit():
    """两轮 compile+submit 产出 v1/v2（供 status 当前行断言）。"""
    for i in (1, 2):
        _t("write", {"content": f"偏好{i}", "work_type": "周报",
                     "audience": "高层", "purpose": "同步"})
        r = _t("compile", {"work_type": "周报"})
        s = _t("submit", {"work_type": "周报",
                          "prompt_md": f"# v{i}\n\n## 分区\n\n正文",
                          "source_memory_ids": r["source_ids_suggested"]})
        assert s["ok"], s


def test_v064_status_current_row_only():
    """v0.6.4（owner 拍板）：status 版本明细**永远只回当前行**（active 优先，
    无 active 回最高版本）——全量历史无真实消费方；latest_only 参数已撤
    （传了按 B8 未知键拒）。"""
    _compile_submit()
    st = _t("status", {})
    wt = next(p for p in st["prompts"] if p["work_type"] == "work_report")
    assert len(wt["versions"]) == 1 and wt["versions"][0]["status"] == "active"
    assert wt["active"] == 2 and wt["versions"][0]["version"] == 2
    # 画像侧与 versions 共用同一过滤循环（代码路径覆盖，无需重复数据断言）
    # 参数已撤：latest_only 按 B8 未知键拒（allowed 只剩 client——参数不存在了）
    bad = _t("status", {"latest_only": True})
    assert bad["ok"] is False and "unknown field" in bad["reason"]
    assert "latest_only" not in bad["allowed"]
    # rollback 后 status 回低版本 active 行（P1：当前行制下回滚语义钉死）
    rb = _t("rollback", {"work_type": "周报", "version": 1})
    assert rb["ok"] and rb["version"] == 1
    st2 = _t("status", {})
    wt3 = next(p for p in st2["prompts"] if p["work_type"] == "work_report")
    assert len(wt3["versions"]) == 1
    assert wt3["versions"][0]["version"] == 1 and wt3["active"] == 1
