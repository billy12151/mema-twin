"""v0.3.7 验证门（瘦身版）：G1/G2 拦 scheduled、G3/G4 只警告、空转阻尼、
nightly_reject 计数生命周期、零副作用。"""
import json

import pytest

from mema_twin import db, flow, server, templates


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


# ---- G1/G2：scheduled 拦截（唯一 blocking 通道）----

def test_scheduled_material_echo_blocked():
    bad = "# 标题\n\n## 编译规则\n- 复述素材包\n"
    r = server.twin("submit", {"work_type": "周报", "prompt_md": bad,
                               "origin": "scheduled", "source_memory_ids": []})
    assert r["ok"] is False and r["error"] == "validation_failed"
    assert any(v["check"] == "material_echo" for v in r["violations"])
    assert "nightly_rejected" in r["hint"]  # 计数通道在提示里
    # 未落版 + nightly_reject 已记
    assert server.twin("status", {})["prompts"] == []
    s = server.twin("status", {})
    assert s["nightly_rejected"][0]["work_type"] == "work_report"
    assert s["nightly_rejected"][0]["count"] == 1
    assert s["nightly_rejected"][0]["last_check"] == "material_echo"


def test_scheduled_no_headings_blocked():
    r = server.twin("submit", {"work_type": "周报", "prompt_md": "没有标题的纯文本稿",
                               "origin": "scheduled", "source_memory_ids": []})
    assert r["ok"] is False and r["error"] == "validation_failed"
    assert any(v["check"] == "no_headings" for v in r["violations"])


def test_rejected_submit_no_side_effects():
    from mema_twin import scan
    assert scan.scan_notice() is not None
    r = server.twin("submit", {"work_type": "周报", "prompt_md": "无标题稿",
                               "origin": "scheduled", "source_memory_ids": []})
    assert r["ok"] is False
    assert scan.scan_notice() is not None  # 未刷夜间在转信号
    assert flow.get_meta("persona_origin:work_report:1") is None


def test_g1_selflock_downgrades_when_active_carries_marker():
    _mk_uncompiled([611])
    v1 = "# 风格\n\n## 编译规则（反面示例，勿照抄分区）\n\n- 命名规则\n"
    r1 = server.twin("submit", {"work_type": "周报", "prompt_md": v1, "model": "m"})
    # 交互式首版：echo 是违规但只警告（用户治理优先），落版成功
    assert r1["ok"] and any("素材回声" in w for w in r1["warnings"])
    v2 = "# 风格\n\n## 编译规则（反面示例，勿照抄分区）\n\n- 命名规则（沿袭 v1）\n"
    r2 = server.twin("submit", {"work_type": "周报", "prompt_md": v2,
                                "origin": "scheduled", "source_memory_ids": [611]})
    # active 已含同标记：新稿沿袭降级为警告，scheduled 不再被拦（自锁守卫）
    assert r2["ok"] and any("沿袭旧版" in w for w in r2.get("warnings", []))
    # active 干净 + 新稿带标记 → 照拦
    _mk_uncompiled([612])
    server.twin("submit", {"work_type": "PPT", "prompt_md": "# 干净版\n\n- 规则\n",
                           "model": "m"})
    r3 = server.twin("submit", {"work_type": "PPT",
                                "prompt_md": "# 干净版\n\n## 编译规则\n- 复述\n",
                                "origin": "scheduled", "source_memory_ids": [612]})
    assert r3["ok"] is False and r3["error"] == "validation_failed"


def test_g1_level_rewrite_detected():
    r = server.twin("submit", {"work_type": "周报",
                               "prompt_md": "# 标题\n\n# 编译规则\n- 复述\n",
                               "origin": "scheduled", "source_memory_ids": []})
    assert r["ok"] is False
    assert any(v["check"] == "material_echo" for v in r["violations"])


# ---- G3/G4：只警告不拦（期望集 = 在世证据全量）----

def test_g3_warning_alive_semantics():
    # v1 吸收 601；新增 603 后夜间只报 [603]（漏 601）：过阻尼（603 是新证据），
    # 落版 + G3 全量口径警告（601 在世但未进本版）
    _mk_uncompiled([601])
    server.twin("submit", {"work_type": "周报", "prompt_md": GOOD_MD,
                           "model": "m", "source_memory_ids": [601]})
    _mk_uncompiled([603])
    r = server.twin("submit", {"work_type": "周报", "prompt_md": GOOD_MD,
                               "origin": "scheduled", "source_memory_ids": [603]})
    assert r["ok"]  # 不拦
    assert any("1 条在世证据未被本版吸收" in w and "601" in w
               for w in r.get("warnings", []))
    w = next(w for w in r["warnings"] if "在世证据" in w)
    assert "漏列" in w and "新写入" in w


def test_g4_warning_not_blocking():
    _mk_uncompiled([701, 702])
    r = server.twin("submit", {"work_type": "aud-leadership",
                               "prompt_md": "# 画像\n\n口径\n",
                               "origin": "scheduled", "source_memory_ids": [701]})
    assert r["ok"] and r["derived"] is True
    assert any("共 2 条证据" in w for w in r.get("warnings", []))


def test_manual_violations_warn_not_block():
    bad = "# 标题\n\n## 编译规则\n- 复述\n"
    r = server.twin("submit", {"work_type": "周报", "prompt_md": bad,
                               "source_memory_ids": []})
    assert r["ok"] is True
    assert any("验证门" in w and "素材" in w for w in r["warnings"])
    assert all(isinstance(w, str) for w in r["warnings"])


# ---- 空转阻尼 ----

def test_damping_rejects_no_new_evidence():
    _mk_uncompiled([711])
    server.twin("submit", {"work_type": "周报", "prompt_md": GOOD_MD,
                           "model": "m", "source_memory_ids": [711]})
    # 夜间重提同样的 id 集合：无新证据 → 拒绝空转落版
    r = server.twin("submit", {"work_type": "周报", "prompt_md": GOOD_MD,
                               "origin": "scheduled", "source_memory_ids": [711]})
    assert r["ok"] is False and r["error"] == "no_new_evidence"
    s = server.twin("status", {})
    assert s["nightly_rejected"][0]["last_check"] == "no_new_evidence"
    assert s["prompts"][0]["active"] == 1  # 版本号没空转


def test_damping_allows_new_evidence_and_stale():
    _mk_uncompiled([721])
    server.twin("submit", {"work_type": "周报", "prompt_md": GOOD_MD,
                           "model": "m", "source_memory_ids": [721]})
    # 新证据进集合 → 放行
    _mk_uncompiled([722])
    r = server.twin("submit", {"work_type": "周报", "prompt_md": GOOD_MD,
                               "origin": "scheduled", "source_memory_ids": [721, 722]})
    assert r["ok"] and r["version"] == 2
    # 空 source_ids ⊆ 旧集合 → 阻尼拒绝
    r3 = server.twin("submit", {"work_type": "周报", "prompt_md": GOOD_MD,
                                "origin": "scheduled", "source_memory_ids": []})
    assert r3["ok"] is False and r3["error"] == "no_new_evidence"
    # 同集合但 persona_stale 在场（条款作废待重编）→ 放行
    flow.set_meta("persona_stale:work_report", json.dumps({"at": "t", "voided": [721]}))
    r2 = server.twin("submit", {"work_type": "周报", "prompt_md": GOOD_MD,
                                "origin": "scheduled", "source_memory_ids": [721, 722]})
    assert r2["ok"] and r2["version"] == 3


# ---- nightly_reject 生命周期 ----

def test_nightly_reject_clears_on_success():
    server.twin("submit", {"work_type": "周报", "prompt_md": "无标题稿",
                           "origin": "scheduled", "source_memory_ids": []})
    server.twin("submit", {"work_type": "周报", "prompt_md": "仍是无标题稿",
                           "origin": "scheduled", "source_memory_ids": []})
    s = server.twin("status", {})
    assert s["nightly_rejected"][0]["count"] == 2
    # 人工重出成功 → 计数清
    r = server.twin("submit", {"work_type": "周报", "prompt_md": GOOD_MD, "model": "m"})
    assert r["ok"]
    assert flow.get_meta("nightly_reject:work_report") is None
    assert "nightly_rejected" not in server.twin("status", {})


def test_nightly_reject_dirty_meta_tolerated():
    flow.set_meta("nightly_reject:work_report", "not-json")
    server.twin("submit", {"work_type": "周报", "prompt_md": "无标题稿",
                           "origin": "scheduled", "source_memory_ids": []})
    s = server.twin("status", {})
    assert s["nightly_rejected"][0]["count"] == 1  # 脏值按 0 起数，不打穿


# ---- 素材标记守卫 ----

def test_material_markers_drift_guard(monkeypatch):
    """G1 标记漂移守卫：类型 + 受众两种素材包合起来覆盖全部标记——
    模板改字而 MATERIAL_MARKERS 不同步会直接红，防止门静默失效。"""
    from mema_twin import sink
    monkeypatch.setattr(sink, "find", lambda *a, **k: {"ok": True, "data": {"results": []}})
    m1 = server.twin("compile", {"work_type": "周报"})
    m2 = server.twin("compile", {"work_type": "aud-leadership"})
    assert m1["ok"] and m2["ok"]
    combined = m1["material"] + m2["material"]
    for marker in templates.MATERIAL_MARKERS:
        assert marker in combined, marker
