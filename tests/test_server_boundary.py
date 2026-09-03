"""review#2/#10/#11 + 第二轮对抗性 review 回归。"""
import pytest

from mema_twin import db, flow, server


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(tmp_path / "twin.sqlite3"))
    monkeypatch.setenv("MEMA_TWIN_PROMPTS_DIR", str(tmp_path / "prompts"))
    monkeypatch.setenv("MEMA_TWIN_DELIVERABLES_DIR", str(tmp_path / "deliverables"))
    flow._schema_ready.clear()
    flow.ensure_schema()
    yield
    flow._todos_by_session.clear()


def test_malformed_task_id_types():
    for bad in ({"task_id": {"x": 1}}, {"task_id": [1]}, {"task_id": None}):
        r = server.twin("task_get", bad)
        assert r.get("ok") is False and r.get("error") in ("invalid_input", "internal_error"), bad


def test_malformed_pending_resolve(monkeypatch):
    r = server.twin("resolve", {"pending_id": {"bad": 1}, "decision": "map", "code": "work_report"})
    assert r.get("ok") is False
    r2 = server.twin("resolve", {"pending_id": 1, "decision": "canonicalize", "new_type": "not-a-dict"})
    assert r2.get("ok") is False


def test_malformed_source_memory_ids():
    r = server.twin("submit", {"work_type": "work_report", "prompt_md": "# x",
                               "source_memory_ids": "123"})
    assert r.get("ok") is False and r.get("field") == "source_memory_ids"
    r2 = server.twin("submit", {"work_type": "work_report", "prompt_md": "# x",
                                "source_memory_ids": [1, 2.9]})
    assert r2.get("ok") is False


def test_bucket_env_traversal_rejected(monkeypatch):
    """workspace 覆盖入口已删；桶名只来自 env，坏值必须拦在入口。"""
    for bad in ("../escape", "a/b", "x" * 65):
        monkeypatch.setenv("MEMA_TWIN_WORKSPACE", bad)
        r = server.twin("status", {})
        assert r.get("ok") is False and r.get("error") == "invalid_input", bad
    monkeypatch.setenv("MEMA_TWIN_WORKSPACE", "mema-twin")


def test_write_rejects_oversized_dimension():
    r = server.twin("write", {"content": "x", "work_type": "长" * 300,
                              "audience": "高层", "purpose": "同步"})
    assert r.get("ok") is False and r.get("field") == "work_type"


# ---- 第二轮对抗性 review 回归 ----

def test_canonicalize_code_traversal_rejected():
    """对抗#1：自定义 code 不能带路径段（会进 prompts/<ws>/<code>/）。"""
    from mema_twin import db as twin_db
    import pytest
    for bad in ("/tmp/abs", "../pwn", "..", "a/b"):
        r = server.twin("resolve", {"pending_id": 999999, "decision": "canonicalize",
                                    "new_type": {"code": bad, "zh": "x"}})
        # 走到 add_canonical 的校验或 not_found 之前，code 校验先行——两种都算拦下：
        # 直接构造 db 调用验证更精确
    conn = twin_db.connect()
    for bad in ("/tmp/abs", "../pwn", "..", "a/b", "x" * 65):
        with pytest.raises(ValueError):
            twin_db.add_canonical(conn, "work_type", bad, "测试")


def test_mark_compiled_scopes_work_type(tmp_path, monkeypatch):
    """对抗#2：A 类型 submit 带错 id 不得吞掉 B 类型的证据。"""
    from mema_twin import db as twin_db
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(tmp_path / "t.sqlite3"))
    conn = twin_db.connect()
    dims_wr = {"work_type": {"ok": True, "code": "work_report", "raw": "x"},
               "audience": {"ok": True, "code": "leadership", "raw": "y"},
               "purpose": {"ok": True, "code": "sync_info", "raw": "z"}}
    dims_pp = {**dims_wr, "work_type": {"ok": True, "code": "presentation", "raw": "p"}}
    twin_db.record_evidence(conn, 101, dims_wr)
    twin_db.record_evidence(conn, 102, dims_pp)
    assert twin_db.mark_compiled(conn, [101, 102], 1, "presentation") == 1
    assert twin_db.uncompiled_evidence(conn, "work_report")[0]["memory_id"] == 101


def test_resolve_backfills_stranded_evidence(tmp_path, monkeypatch):
    """对抗#3：pending 维度证据在裁定后回填 code，compile 可见。"""
    from mema_twin import db as twin_db, normalize
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(tmp_path / "t.sqlite3"))
    conn = twin_db.connect()
    r = normalize.normalize_value("work_type", "灵能审计年报", conn, defer_pending=True)
    assert r.get("deferred_pending")
    twin_db.record_evidence(conn, 55, {
        "work_type": r, "audience": {"ok": True, "code": "self", "raw": "自己"},
        "purpose": {"ok": True, "code": "record_evidence", "raw": "存证"}})
    assert twin_db.uncompiled_evidence(conn, "xianxia_doc") == []
    # canonicalize 后回填
    twin_db.add_canonical(conn, "work_type", "xianxia_doc", "玄幻设定文档")
    n = twin_db.backfill_evidence_codes(conn, "work_type", "灵能审计年报", "xianxia_doc")
    assert n == 1
    rows = twin_db.uncompiled_evidence(conn, "xianxia_doc")
    assert [x["memory_id"] for x in rows] == [55]


def test_resolve_double_map_rejected(tmp_path, monkeypatch):
    """对抗#7：同一 pending 不得二次裁定。"""
    from mema_twin import db as twin_db, normalize
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(tmp_path / "t.sqlite3"))
    conn = twin_db.connect()
    pid = normalize.normalize_value("purpose", "灵能催办", conn)["pending_id"]
    twin_db.append_alias(conn, "purpose", "drive_action", "灵能催办")
    twin_db.set_pending(conn, pid, "mapped", "drive_action")
    r = server.twin("resolve", {"pending_id": pid, "decision": "map", "code": "sync_info"})
    assert r.get("ok") is False and "不可重复裁定" in r.get("reason", "")


def test_append_alias_conflict_rejected(tmp_path, monkeypatch):
    from mema_twin import db as twin_db
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(tmp_path / "t.sqlite3"))
    conn = twin_db.connect()
    twin_db.append_alias(conn, "purpose", "drive_action", "催办x")
    with pytest.raises(ValueError):
        twin_db.append_alias(conn, "purpose", "sync_info", "催办x")


def test_write_strips_twin_namespace_tags(monkeypatch):
    """对抗#13：用户 tags 不能伪造维度命名空间。"""
    captured = {}
    from mema_twin import server as srv, sink

    def fake_remember(content, subject, tags, workspace, source_ref="", event_time=""):
        captured["tags"] = tags
        return {"ok": True, "data": {"id": 1}}
    monkeypatch.setattr(sink, "remember", fake_remember)
    r = srv.twin("write", {"content": "c", "work_type": "周报", "audience": "高层",
                           "purpose": "同步", "tags": ["twin:wt:presentation", "正常tag"]})
    assert r["ok"]
    assert "twin:wt:presentation" not in captured["tags"]
    assert "正常tag" in captured["tags"]
    assert captured["tags"].count("twin:wt:work_report") == 1


def test_write_no_ghost_pending_on_sink_failure(monkeypatch):
    """对抗#14：mema 写失败不留幽灵 pending。"""
    from mema_twin import server as srv, sink
    monkeypatch.setattr(sink, "remember",
                        lambda *a, **k: (_ for _ in ()).throw(sink.SinkError("down")))
    r = srv.twin("write", {"content": "c", "work_type": "灵能审计年报",
                           "audience": "高层", "purpose": "同步"})
    assert r.get("error") == "mema_unreachable"
    conn = db.connect()
    n = conn.execute("SELECT COUNT(*) AS c FROM twin_pending_values").fetchone()["c"]
    conn.close()
    assert n == 0


def test_revise_child_is_planning_not_forged_approved():
    """对抗#8：approved 任务的修订子任务回 planning，不伪造审计。"""
    t = flow.insert_task(brief="T", status="approved", dims={})
    flow.add_review(t["id"], "approved", "r1")
    r = server.twin("task_revise", {"task_id": t["id"], "revision_reason": "返工"})
    assert r["ok"] and r["status"] == "planning"
    child = flow.get_task(r["task_id"])
    assert child["parent_task_id"] == t["id"]
    assert flow.list_reviews(r["task_id"]) == []  # 子任务无评审记录


def test_resume_allows_planning():
    """对抗#8：进行中任务可续作。"""
    t = flow.insert_task(brief="T", status="planning", dims={})
    r = server.twin("task_resume", {"task_id": t["id"], "session": "s"})
    assert r["ok"] and r["new_task_id"]


def test_task_close_action():
    t = flow.insert_task(brief="T", status="planning", dims={})
    r = server.twin("task_close", {"task_id": t["id"], "reason": "不做"})
    assert r["ok"] and flow.get_task(t["id"])["status"] == "superseded"


def test_submit_after_supersede_rejected():
    """对抗#4：被让位的任务不能再 submit 复活。"""
    t = flow.insert_task(brief="T", status="planning", dims={})
    flow.set_status(t["id"], "superseded", reason="让位")
    r = server.twin("task_submit", {"task_id": t["id"], "deliverable_md": "x"})
    assert r.get("ok") is False
