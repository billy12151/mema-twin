"""v0.3.7 全量投影与证据生命周期：compile 素材含已编译证据（切断弱底稿路径
依赖）、void 全链路排除、作废条款节、persona_stale 生命周期、audience_stale
经 void 触发、编译规则三件套（稳定律/变更分级/硬预算）。"""
import json

import pytest

from mema_twin import db, flow, scan, server, templates


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(tmp_path / "twin.sqlite3"))
    monkeypatch.setenv("MEMA_TWIN_PROMPTS_DIR", str(tmp_path / "prompts"))
    monkeypatch.setenv("MEMA_TWIN_DELIVERABLES_DIR", str(tmp_path / "deliverables"))
    flow._schema_ready.clear()
    flow.ensure_schema()
    yield
    flow._todos_by_session.clear()


GOOD_MD = "# 整体风格\n\n- 规则一\n\n## 前置确认清单\n\n- 问题一\n"


def _mk_uncompiled(mids, code="work_report", audience="leadership"):
    conn = db.connect()
    dims = {"work_type": {"ok": True, "code": code, "raw": "周报"},
            "audience": {"ok": True, "code": audience, "raw": "高层"},
            "purpose": {"ok": True, "code": "sync_info", "raw": "同步"}}
    for mid in mids:
        db.record_evidence(conn, mid, dims)
    conn.close()


def _stub_read(monkeypatch):
    from mema_twin import sink

    def fake(mid, workspace=None, client=None):
        return {"ok": True, "data": {"memory": {"id": mid, "subject": f"偏好{mid}",
                                                "content": f"内容{mid}"}}}
    monkeypatch.setattr(sink, "read_memory", fake)


# ---- 全量投影 ----

def test_compile_material_includes_compiled_evidence(monkeypatch):
    """v1 吸收 801 后再 compile：已编译的 801 仍在素材里（全量投影，弱底稿
    不遗传——每版从头重编）。"""
    _stub_read(monkeypatch)
    _mk_uncompiled([801])
    server._twin_impl("submit", {"work_type": "周报", "prompt_md": GOOD_MD,
                           "model": "m", "source_memory_ids": [801]})
    r = server._twin_impl("compile", {"work_type": "周报"})
    assert r["ok"] and r["evidence_count"] == 1
    assert "[801]" in r["material"]
    assert "全部偏好证据" in r["material"] and "全量投影" in r["material"]


def test_find_fallback_only_when_alive_empty(monkeypatch):
    """有在世行时绝不走 find 兜底（防素材集/门期望集分叉）；alive 为空才兜底。
    read 先 stub（防单测真实联网——评审轮1 P2-4）。"""
    from mema_twin import sink
    _stub_read(monkeypatch)
    calls = []

    def fake_find(*a, **k):
        calls.append(a)
        return {"ok": True, "data": {"results": [
            {"id": 811, "tags": ["twin:wt:work_report"], "subject": "s", "content": "c"}]}}

    monkeypatch.setattr(sink, "find", fake_find)
    _mk_uncompiled([810])
    r = server._twin_impl("compile", {"work_type": "周报"})
    assert r["ok"] and calls == []
    r2 = server._twin_impl("compile", {"work_type": "周报"})
    assert "[810]" in r2["material"] and calls == []


def test_compile_rules_trio_present(monkeypatch):
    """编译规则三件套进素材包：稳定律 / 变更分级（归因）/ 硬预算（默认值）。"""
    from mema_twin import sink
    monkeypatch.setattr(sink, "find", lambda *a, **k: {"ok": True, "data": {"results": []}})
    r = server._twin_impl("compile", {"work_type": "周报"})
    m = r["material"]
    assert "含义稳定，表达自由" in m
    assert "归因到证据 id" in m or "归因到证据" in m
    assert f"≤{templates.BUDGET_TYPE_CHARS} 字符" in m
    r2 = server._twin_impl("compile", {"work_type": "aud-leadership"})
    assert f"≤{templates.BUDGET_AUD_CHARS} 字符" in r2["material"]


def test_status_size_and_budget_flag():
    server._twin_impl("submit", {"work_type": "周报", "prompt_md": GOOD_MD, "model": "m"})
    s = server._twin_impl("status", {})
    p = s["prompts"][0]
    assert p["size_chars"] == len(GOOD_MD) and p["over_budget"] is False
    big = "# 整体风格\n" + "规则。\n" * 5000  # 远超 8000
    server._twin_impl("submit", {"work_type": "PPT", "prompt_md": big, "model": "m"})
    s2 = server._twin_impl("status", {})
    ppt = next(x for x in s2["prompts"] if x["work_type"] == "presentation")
    assert ppt["over_budget"] is True


# ---- void 与全链路排除 ----

def test_void_excludes_everywhere(monkeypatch):
    _stub_read(monkeypatch)
    _mk_uncompiled([821, 822])
    server._twin_impl("submit", {"work_type": "周报", "prompt_md": GOOD_MD,
                           "model": "m", "source_memory_ids": [821, 822]})
    # 作废 821（曾入编译）→ 素材不再含、增补不含、画像投影不含、stale 记录在案
    r = server._twin_impl("void", {"memory_id": 821})
    assert r["ok"] and r["work_type"] == "work_report" and r["was_compiled"] is True
    m = server._twin_impl("compile", {"work_type": "周报"})
    # 证据节不含 821、含 822；作废条款节列出 821（曾入 v1）
    evidence_part, voided_part = m["material"].split("## 已作废条款")
    assert "[821]" not in evidence_part and "[822]" in evidence_part
    assert "[821]" in voided_part and "v1" in voided_part
    # audience_stale：受众证据计数变了（821 被 void）→ 需重抽象
    s = server._twin_impl("status", {})
    assert "leadership" in s["audience_stale"]
    # persona_stale 标记 + status 可见 + 成功落版即清
    assert "persona_stale:work_report" in flow.list_meta("persona_stale:")
    assert s["persona_stale"][0]["work_type"] == "work_report"
    server._twin_impl("submit", {"work_type": "周报", "prompt_md": GOOD_MD,
                           "model": "m", "source_memory_ids": [822]})
    assert flow.get_meta("persona_stale:work_report") is None


def test_void_uncompiled_marks_stale_false(monkeypatch):
    _stub_read(monkeypatch)
    _mk_uncompiled([831])
    r = server._twin_impl("void", {"memory_id": 831})
    assert r["ok"] and r["was_compiled"] is False
    assert flow.get_meta("persona_stale:work_report") is None  # 未入编译不标 stale


def test_void_not_found_and_aud_row():
    assert server._twin_impl("void", {"memory_id": 999}).get("error") == "not_found"
    # 受众级行 + 一条同受众的跨类型行：作废受众级行后受众仍有证据（计数 1≠无画像）
    _mk_uncompiled([842])
    conn = db.connect()
    db.record_evidence(conn, 841, {
        "work_type": {"ok": True, "code": "aud-leadership", "raw": "(受众级偏好)"},
        "audience": {"ok": True, "code": "leadership", "raw": "高层"},
        "purpose": {"ok": True, "code": "sync_info", "raw": "同步"}})
    conn.close()
    r = server._twin_impl("void", {"memory_id": 841})
    assert r["ok"] and r["work_type"] == "aud-leadership"
    # 受众级行（compiled_version 恒 NULL）不设 persona_stale，走 audience_stale 计数
    assert flow.get_meta("persona_stale:aud-leadership") is None
    s = server._twin_impl("status", {})
    assert "leadership" in s["audience_stale"]


def test_audience_evidence_excludes_void(monkeypatch):
    _stub_read(monkeypatch)
    _mk_uncompiled([851])
    server._twin_impl("void", {"memory_id": 851})
    conn = db.connect()
    rows = db.audience_evidence(conn, "leadership")
    conn.close()
    assert all(r["memory_id"] != 851 for r in rows)


def test_damping_bypasses_when_evidence_base_shrunk(monkeypatch):
    """评审轮1 P1-1：void 后期望集收缩（E⊊old），画像/类型重抽象提交必是旧
    source 集真子集——阻尼必须放行，否则 void 驱动的重编被永久拦死。"""
    _stub_read(monkeypatch)
    _mk_uncompiled([861, 862])
    # 画像 v1 吸收两条受众证据
    server._twin_impl("submit", {"work_type": "aud-leadership",
                           "prompt_md": "# 画像\n\n口径\n",
                           "model": "m", "source_memory_ids": [861, 862]})
    # void 861 → 受众证据计数 1 ≠ 画像吸收数 2 → stale
    server._twin_impl("void", {"memory_id": 861})
    s = server._twin_impl("status", {})
    assert "leadership" in s["audience_stale"]
    # 夜间重抽象只带剩余 1 条（旧集真子集）→ 放行落版，stale 清零
    r = server._twin_impl("submit", {"work_type": "aud-leadership",
                               "prompt_md": "# 画像\n\n口径\n",
                               "origin": "scheduled", "source_memory_ids": [862]})
    assert r["ok"] and r["version"] == 2, r
    s2 = server._twin_impl("status", {})
    assert "leadership" not in s2["audience_stale"]
    # 对照：基座未缩的真空转仍被拒
    r2 = server._twin_impl("submit", {"work_type": "aud-leadership",
                                "prompt_md": "# 画像\n\n口径\n",
                                "origin": "scheduled", "source_memory_ids": [862]})
    assert r2["ok"] is False and r2["error"] == "no_new_evidence"


def test_profile_material_voided_section_by_audience(monkeypatch):
    """评审轮1 P1-2：画像模式的作废条款节按 audience 查（跨类型行也算）。"""
    _stub_read(monkeypatch)
    _mk_uncompiled([871, 872])
    server._twin_impl("void", {"memory_id": 871})
    r = server._twin_impl("compile", {"work_type": "aud-leadership"})
    assert r["ok"]
    _, voided_part = r["material"].split("## 已作废条款")
    assert "[871]" in voided_part and "未入编译" in voided_part


def test_void_idempotent_no_stale_remark(monkeypatch):
    """评审轮1 P3-1：重复 void 幂等返回，不重置已清的 persona_stale。"""
    _stub_read(monkeypatch)
    _mk_uncompiled([881])
    server._twin_impl("submit", {"work_type": "周报", "prompt_md": GOOD_MD,
                           "model": "m", "source_memory_ids": [881]})
    server._twin_impl("void", {"memory_id": 881})
    # 成功落版清 stale 后，再次 void 不得重置
    server._twin_impl("submit", {"work_type": "周报", "prompt_md": GOOD_MD,
                           "model": "m", "source_memory_ids": []})
    assert flow.get_meta("persona_stale:work_report") is None
    r = server._twin_impl("void", {"memory_id": 881})
    assert r["ok"] and r["already_void"] is True
    assert flow.get_meta("persona_stale:work_report") is None


# ---- persona_stale 消费（夜间 spec 触发 + status 展示） ----

def test_spec_stale_trigger_wording():
    spec = scan.SCHEDULED_TASKS_SPEC["tasks"][0]
    status_note = spec["calls"][0]["data"]["note"]
    assert "persona_stale" in status_note  # status note 提到 stale 触发源


def test_status_lists_persona_stale(monkeypatch):
    flow.set_meta("persona_stale:work_report",
                  json.dumps({"at": "t", "voided": [901]}, ensure_ascii=False))
    s = server._twin_impl("status", {})
    assert s["persona_stale"][0]["voided"] == [901]


def test_rollback_revives_void_guard(monkeypatch):
    """评审轮2 P2-2：回滚到 source 含已作废证据的版本 → 重标 persona_stale。"""
    _stub_read(monkeypatch)
    _mk_uncompiled([891, 892])
    server._twin_impl("submit", {"work_type": "周报", "prompt_md": GOOD_MD,
                                 "model": "m", "source_memory_ids": [891, 892]})
    server._twin_impl("void", {"memory_id": 892})
    # 夜间重编剔除（stale 清）
    server._twin_impl("submit", {"work_type": "周报", "prompt_md": GOOD_MD,
                                 "origin": "scheduled", "source_memory_ids": [891]})
    assert flow.get_meta("persona_stale:work_report") is None
    # 回滚到 v1（source 含已作废的 892）→ stale 重标 + 警告
    r = server._twin_impl("rollback", {"work_type": "周报", "version": 1})
    assert r["ok"] and any("已作废" in w for w in r.get("warnings", []))
    s = server._twin_impl("status", {})
    assert s["persona_stale"][0]["work_type"] == "work_report"
