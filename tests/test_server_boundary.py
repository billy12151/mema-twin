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


def test_workspace_env_no_longer_overrides(monkeypatch):
    """0.3.3 起桶名写死常量：MEMA_TWIN_WORKSPACE（含坏值）一律无效，
    发往 mema 的 workspace 恒为 mema-twin。"""
    from mema_twin import sink
    captured = {}

    def fake_remember(content, subject, tags, workspace, source_ref="", event_time="", client=None):
        captured["workspace"] = workspace
        return {"ok": True, "data": {"id": 1}}
    monkeypatch.setattr(sink, "remember", fake_remember)
    for env_value in ("../escape", "a/b", "other-bucket"):
        monkeypatch.setenv("MEMA_TWIN_WORKSPACE", env_value)
        r = server.twin("write", {"content": "c", "work_type": "周报",
                                  "audience": "高层", "purpose": "同步"})
        assert r.get("ok") is True, env_value
    assert captured["workspace"] == "mema-twin"


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

    def fake_remember(content, subject, tags, workspace, source_ref="", event_time="", client=None):
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


# ---- HTTP 传输与多 Agent 身份 ----

def test_main_transport_selection(monkeypatch):
    """stdio 默认；http 走 streamable-http 并设 host/port；坏值报错。"""
    calls = []
    monkeypatch.setattr(server.mcp, "run", lambda transport=None: calls.append(transport))
    server.main()
    assert calls == [None]
    monkeypatch.setenv("MEMA_TWIN_TRANSPORT", "http")
    monkeypatch.setenv("MEMA_TWIN_HTTP_PORT", "9001")
    server.main()
    assert calls == [None, "streamable-http"]
    assert server.mcp.settings.port == 9001
    monkeypatch.setenv("MEMA_TWIN_TRANSPORT", "bogus")
    import pytest
    with pytest.raises(SystemExit):
        server.main()


def test_write_client_identity_passthrough(monkeypatch):
    """多 Agent：write 的 data.client 决定发往 mema 的 X-Mema-Client。"""
    from mema_twin import sink
    captured = {}

    def fake_call(name, arguments, client=None):
        captured["client"] = client
        return {"ok": True, "data": {"id": 7}}
    monkeypatch.setattr(sink, "_call", fake_call)
    r = server.twin("write", {"content": "c", "work_type": "周报", "audience": "高层",
                              "purpose": "同步", "client": "kimi"})
    assert r["ok"] and captured["client"] == "kimi"
    r2 = server.twin("write", {"content": "c", "work_type": "周报", "audience": "高层",
                               "purpose": "同步"})
    assert r2["ok"] and captured["client"] is None  # 回落 env 默认


# ---- 0.3.3 client 头透传 ----

class _FakeCtx:
    """模拟 FastMCP 工具执行时的请求上下文。headers 用真 starlette Headers
    （大小写不敏感、有 getlist）；本 SDK 版本的 Headers() 只吃 dict，重复头
    场景绕过构造器直接填内部 _list（raw bytes pairs）。"""

    def __init__(self, header_pairs=None, no_request=False):
        from starlette.datastructures import Headers as _H
        h = _H({})
        if header_pairs:
            h._list = [(str(k).lower().encode("latin-1"), str(v).encode("latin-1"))
                       for k, v in header_pairs]
        rc = type("RC", (), {})()
        rc.request = None if no_request else type("R", (), {"headers": h})()
        self.request_context = rc


def test_request_client_reads_header(monkeypatch):
    monkeypatch.setattr(server.mcp, "get_context", lambda: _FakeCtx([("x-mema-client", "kimi")]))
    assert server._request_client() == "kimi"


def test_request_client_no_header_is_none(monkeypatch):
    monkeypatch.setattr(server.mcp, "get_context", lambda: _FakeCtx())
    assert server._request_client() is None


def test_request_client_no_request_is_none(monkeypatch):
    # stdio/直调：无活跃请求（request 为 None / get_context 抛错）都回落 None
    monkeypatch.setattr(server.mcp, "get_context", lambda: _FakeCtx(no_request=True))
    assert server._request_client() is None

    def _boom():
        raise LookupError("no session")
    monkeypatch.setattr(server.mcp, "get_context", _boom)
    assert server._request_client() is None


def test_request_client_rejects_dirty_header(monkeypatch):
    monkeypatch.setattr(server.mcp, "get_context", lambda: _FakeCtx([("x-mema-client", "bad client!")]))
    r = server.twin("write", {"content": "c", "work_type": "周报",
                              "audience": "高层", "purpose": "同步"})
    assert r.get("ok") is False and r.get("error") == "invalid_input"


def test_write_client_header_authoritative(monkeypatch):
    """对抗#1：http 头存在时头是权威——data.client 不一致打回（堵跨宿主冒充），
    一致或省略放行。"""
    from mema_twin import sink
    captured = {}

    def fake_call(name, arguments, client=None):
        captured["client"] = client
        return {"ok": True, "data": {"id": 7}}
    monkeypatch.setattr(sink, "_call", fake_call)
    monkeypatch.setattr(server.mcp, "get_context",
                        lambda: _FakeCtx([("x-mema-client", "kimi")]))
    r = server.twin("write", {"content": "c", "work_type": "周报", "audience": "高层",
                              "purpose": "同步", "client": "zcode"})
    assert r.get("ok") is False and r.get("error") == "invalid_input" and "不一致" in r.get("reason", "")
    r2 = server.twin("write", {"content": "c", "work_type": "周报", "audience": "高层",
                               "purpose": "同步", "client": "kimi"})
    assert r2["ok"] and captured["client"] == "kimi"
    r3 = server.twin("write", {"content": "c", "work_type": "周报",
                               "audience": "高层", "purpose": "同步"})
    assert r3["ok"] and captured["client"] == "kimi"


def test_write_rejects_dirty_explicit_client():
    r = server.twin("write", {"content": "c", "work_type": "周报",
                              "audience": "高层", "purpose": "同步",
                              "client": "no spaces"})
    assert r.get("ok") is False and r.get("error") == "invalid_input"


def test_task_start_records_header_client(monkeypatch):
    """http 多宿主：task_start 建档的 client 来自头，不落 env 默认。"""
    monkeypatch.setattr(server.mcp, "get_context",
                        lambda: _FakeCtx([("x-mema-client", "kimi")]))
    r = server.twin("task_start", {"brief": "B", "work_type": "周报"})
    assert r.get("ok") is True
    record = flow.get_task(r["task_id"])
    assert record["client"] == "kimi"


def test_dirty_explicit_client_rejected_at_entry():
    """轮1#1：脏 data.client 在入口打回，任何动作（含 task_start）不留幽灵 pending。"""
    r = server.twin("task_start", {"brief": "B", "work_type": "周报",
                                   "client": "bad client!"})
    assert r.get("ok") is False and r.get("error") == "invalid_input"
    conn = db.connect()
    n = conn.execute("SELECT COUNT(*) AS c FROM twin_pending_values").fetchone()["c"]
    conn.close()
    assert n == 0


def test_sink_env_client_dirty_rejected(monkeypatch):
    """轮1#9：MEMA_TWIN_CLIENT_ID env 脏值就地打回，不发脏头给 mema。"""
    from mema_twin import sink
    monkeypatch.setenv("MEMA_TWIN_CLIENT_ID", "has space")
    with pytest.raises(ValueError):
        sink._headers()
    monkeypatch.setenv("MEMA_TWIN_CLIENT_ID", "zcode")
    assert sink._headers()["X-Mema-Client"] == "zcode"


# ---- 第二轮对抗性 review 回归（0.3.3）----

def test_stdio_explicit_client_still_works(monkeypatch):
    """对抗#1 正例：stdio 无头时 data.client 仍是唯一显式归属手段。"""
    from mema_twin import sink
    captured = {}

    def fake_call(name, arguments, client=None):
        captured["client"] = client
        return {"ok": True, "data": {"id": 7}}
    monkeypatch.setattr(sink, "_call", fake_call)
    r = server.twin("write", {"content": "c", "work_type": "周报", "audience": "高层",
                              "purpose": "同步", "client": "kimi"})
    assert r["ok"] and captured["client"] == "kimi"


def test_client_type_and_whitespace_rejected(monkeypatch):
    """对抗#6：非字符串 / 首尾空白一律打回，不做 str() 修正。"""
    for bad in ({"x": 1}, 123, True):
        r = server.twin("write", {"content": "c", "work_type": "周报",
                                  "audience": "高层", "purpose": "同步", "client": bad})
        assert r.get("ok") is False and r.get("error") == "invalid_input", bad
    r2 = server.twin("write", {"content": "c", "work_type": "周报",
                               "audience": "高层", "purpose": "同步", "client": " kimi "})
    assert r2.get("ok") is False and "空白" in r2.get("reason", "")


def test_header_whitespace_rejected(monkeypatch):
    """对抗#6：头值带首尾空白直接拒（mema 同款，不 strip 修正）。"""
    monkeypatch.setattr(server.mcp, "get_context",
                        lambda: _FakeCtx([("x-mema-client", " kimi ")]))
    r = server.twin("status", {})
    assert r.get("ok") is False and r.get("error") == "invalid_input"


def test_duplicate_header_rejected(monkeypatch):
    """对抗#8：重复头（含大小写变体）打回。"""
    monkeypatch.setattr(server.mcp, "get_context",
                        lambda: _FakeCtx([("X-Mema-Client", "a"), ("x-mema-client", "b")]))
    r = server.twin("status", {})
    assert r.get("ok") is False and "恰好一个" in r.get("reason", "")


def test_header_case_insensitive(monkeypatch):
    """对抗#8：大小写变体头也能读到（starlette Headers 天然不敏感）。"""
    monkeypatch.setattr(server.mcp, "get_context",
                        lambda: _FakeCtx([("X-MEMA-CLIENT", "kimi")]))
    assert server._request_client() == "kimi"


def test_main_rejects_non_loopback_host(monkeypatch):
    """对抗#2：非 loopback 绑定拒绝（X-Mema-Client 头不是鉴权）。"""
    monkeypatch.setenv("MEMA_TWIN_TRANSPORT", "http")
    monkeypatch.setenv("MEMA_TWIN_HTTP_HOST", "0.0.0.0")
    import pytest
    with pytest.raises(SystemExit, match="loopback"):
        server.main()
    monkeypatch.setenv("MEMA_TWIN_HTTP_HOST", "192.168.1.5")
    with pytest.raises(SystemExit, match="loopback"):
        server.main()
    calls = []
    monkeypatch.setattr(server.mcp, "run", lambda transport=None: calls.append(transport))
    for ok_host in ("127.0.0.1", "localhost", "::1"):
        monkeypatch.setenv("MEMA_TWIN_HTTP_HOST", ok_host)
        server.main()
    assert calls == ["streamable-http"] * 3


def test_main_rejects_dirty_env_client(monkeypatch):
    """对抗#7：脏 MEMA_TWIN_CLIENT_ID 启动即拒，不留运行期中途炸。"""
    monkeypatch.setenv("MEMA_TWIN_CLIENT_ID", "has space")
    import pytest
    with pytest.raises(SystemExit):
        server.main()


def test_flow_env_fallback_validated(monkeypatch):
    """对抗#3：twin_tasks.client 的 env 回落同样过校验。"""
    monkeypatch.setenv("MEMA_TWIN_CLIENT_ID", "dirty value")
    r = server.twin("task_start", {"brief": "B", "work_type": "周报"})
    assert r.get("ok") is False and r.get("error") == "invalid_input"


# ---- 0.3.4 compile 会话治理 ----

def test_compile_material_labels_old_version(monkeypatch):
    """素材包旧版标签：出生即「编译参考，非执行依据」，不再自标当前版本
    （submit 落新版后该标签会变假话且无法回收——从源头不产生）。"""
    from mema_twin import sink, store
    monkeypatch.setattr(sink, "find", lambda *a, **k: {"ok": True, "data": {"results": []}})
    conn = db.connect()
    store.create_version(conn, "work_report", "# v1 内容", ["1"], model="m")
    conn.close()
    r = server.twin("compile", {"work_type": "周报"})
    assert r["ok"]
    assert "## 旧版本 prompt（编译参考，非执行依据）" in r["material"]
    assert "当前版本 prompt" not in r["material"]
    assert "取代" in r["material"] and "与旧版本冲突" in r["material"]
    # 首版场景（无旧版本）同样成立
    r2 = server.twin("compile", {"work_type": "PPT"})
    assert r2["ok"] and "首个版本" in r2["material"]


def test_submit_returns_supersedes():
    """submit 响应带 supersedes：落版即裁决，不等下一次注入。"""
    r = server.twin("submit", {"work_type": "周报", "prompt_md": "# a", "model": "m"})
    assert r["ok"] and r["supersedes"] is None
    r2 = server.twin("submit", {"work_type": "周报", "prompt_md": "# b", "model": "m"})
    assert r2["ok"] and r2["supersedes"] == 1


# ---- 0.3.4 rollback ----

def test_rollback_action_boundary():
    r0 = server.twin("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m"})
    server.twin("submit", {"work_type": "周报", "prompt_md": "# v2", "model": "m"})
    r = server.twin("rollback", {"work_type": "周报"})
    assert r["ok"] and r["version"] == 1 and r["rolled_back_from"] == 2
    # 幂等 + 注记
    r2 = server.twin("rollback", {"work_type": "周报", "version": 1})
    assert r2["ok"] and r2.get("note") and "rolled_back_from" not in r2
    # 目标不存在报错带可用版本
    r3 = server.twin("rollback", {"work_type": "周报", "version": 99})
    assert r3.get("ok") is False and "可用版本" in r3.get("reason", "")
    # 脏 version / 浮点 / bool
    for bad in ("abc", 2.9, True, "1.5", 0, -1):
        r4 = server.twin("rollback", {"work_type": "周报", "version": bad})
        assert r4.get("ok") is False and r4.get("field") == "version", bad
    # unknown work_type / 缺 work_type
    assert server.twin("rollback", {"work_type": "不存在"}).get("ok") is False
    assert server.twin("rollback", {}).get("field") == "work_type"
    # 唯一版本省略 version → 报错附 status 提示
    server.twin("submit", {"work_type": "PPT", "prompt_md": "# only", "model": "m"})
    r6 = server.twin("rollback", {"work_type": "PPT"})
    assert r6.get("ok") is False and "status" in r6.get("reason", "")
    # 回滚后 get 拿到的是回滚版本
    g = server.twin("get", {"work_type": "周报"})
    assert g["ok"] and g["version"] == 1 and g["prompt_md"] == "# v1"
