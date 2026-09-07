"""v0.3.7 mema notice 透传：全部 sink 调用点收集（contextvar 并发隔离）、
外层响应统一附带 + 分诊分层指引、status 治理计数软失败。

stub 层选 sink._call（drain 所在层）——直接替 read_memory/remember 会绕过
_drain_notices，测不到真实透传路径。"""
import pytest

from mema_twin import db, flow, server, sink


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(tmp_path / "twin.sqlite3"))
    monkeypatch.setenv("MEMA_TWIN_PROMPTS_DIR", str(tmp_path / "prompts"))
    monkeypatch.setenv("MEMA_TWIN_DELIVERABLES_DIR", str(tmp_path / "deliverables"))
    flow._schema_ready.clear()
    flow.ensure_schema()
    yield
    flow._todos_by_session.clear()


SIMILAR_NOTICE = {"type": "similar_active_memory", "severity": "info",
                  "matches": [{"memory_id": 1, "subject": "s"}]}
SEMANTIC_NOTICE = {"notice_id": 7, "severity": "warning", "type": "semantic_evidence",
                   "action_required": "read_semantic_notice",
                   "read_call": {"tool": "memory_repair", "task": "notice"}}


def _stub_call(monkeypatch, dispatch):
    monkeypatch.setattr(sink, "_call",
                        lambda name, arguments, client=None, timeout=30:
                        dispatch(name, arguments))


def _mk_evidence(mid):
    conn = db.connect()
    db.record_evidence(conn, mid, {
        "work_type": {"ok": True, "code": "work_report", "raw": "周报"},
        "audience": {"ok": True, "code": "leadership", "raw": "高层"},
        "purpose": {"ok": True, "code": "sync_info", "raw": "同步"}})
    conn.close()


def test_notices_from_internal_reads_surface(monkeypatch):
    """内部 read 循环 claim 的 notice 也要透传到外层响应（评审 P1-2 回归）。"""
    def dispatch(name, arguments):
        assert name == "memory" and arguments["action"] == "read"
        return {"ok": True, "data": {"memory": {"id": arguments["data"]["memory_id"],
                                                "subject": "s", "content": "c"}},
                "notices": [SEMANTIC_NOTICE]}
    _stub_call(monkeypatch, dispatch)
    _mk_evidence(501)
    r = server._twin_impl("task_start", {"brief": "B", "work_type": "周报"})
    assert r["ok"]
    assert SEMANTIC_NOTICE in r["mema_notices"]
    assert "memory_repair" in r["mema_notices_guidance"]
    assert "三选项" in r["mema_notices_guidance"] and "void" in r["mema_notices_guidance"]


def test_similar_notice_silent_triage_layering(monkeypatch):
    def dispatch(name, arguments):
        assert arguments["action"] == "remember"
        return {"ok": True, "data": {"id": 502}, "notices": [SIMILAR_NOTICE]}
    _stub_call(monkeypatch, dispatch)
    r = server._twin_impl("write", {"content": "偏好X", "work_type": "周报",
                              "audience": "高层", "purpose": "同步"})
    assert r["ok"] and r["evidence_id"] == 502
    g = r["mema_notices_guidance"]
    assert "静默分诊" in g and "不必打扰用户" in g
    assert "三选项" not in g  # 重复提示不升级问用户


def test_no_notices_no_extra_fields(monkeypatch):
    def dispatch(name, arguments):
        return {"ok": True, "data": {"memory": {"id": arguments["data"]["memory_id"],
                                                "subject": "s", "content": "c"}}}
    _stub_call(monkeypatch, dispatch)
    _mk_evidence(503)
    r = server._twin_impl("task_start", {"brief": "B", "work_type": "周报"})
    assert r["ok"] and "mema_notices" not in r and "mema_notices_guidance" not in r


def test_status_conflict_count_soft_fail(monkeypatch):
    # mema 挂：status 照常，无 open_conflicts 字段（夜间第一步不被拖垮）
    def boom(*a, **k):
        raise sink.SinkError("mema 不可达")
    monkeypatch.setattr(sink, "review_conflicts", boom)
    s = server._twin_impl("status", {})
    assert s["ok"] and "open_conflicts" not in s and s["open_tasks"] == 0
    # mema 在：twin 桶 open 冲突计数（他 workspace 的不算）
    monkeypatch.setattr(sink, "review_conflicts", lambda *a, **k: {
        "ok": True, "data": {"conflicts": [
            {"id": 1, "status": "open", "workspace_canonical": "mema-twin"},
            {"id": 2, "status": "open", "workspace_canonical": "other-ws"},
            {"id": 3, "status": "resolved", "workspace_canonical": "mema-twin"},
        ]}})
    s2 = server._twin_impl("status", {})
    assert s2["ok"] and s2["open_conflicts"] == 1
    # 有开放任务时计数在
    flow.insert_task(brief="T", status="planning", dims={})
    s3 = server._twin_impl("status", {})
    assert s3["open_tasks"] == 1
