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
    # 会话分工提示下沉到响应（非 SKILL 宿主也可见），且是「转告用户」的显式指令
    assert "告知用户" in r["session_note"] and "换新会话" in r["session_note"]
    # 首版场景（无旧版本）同样成立
    r2 = server.twin("compile", {"work_type": "PPT"})
    assert r2["ok"] and "首个版本" in r2["material"] and "告知用户" in r2["session_note"]


def test_submit_returns_supersedes():
    """submit 响应带 supersedes：落版即裁决，不等下一次注入。"""
    r = server.twin("submit", {"work_type": "周报", "prompt_md": "# a", "model": "m"})
    assert r["ok"] and r["supersedes"] is None
    r2 = server.twin("submit", {"work_type": "周报", "prompt_md": "# b", "model": "m"})
    assert r2["ok"] and r2["supersedes"] == 1
    # 收尾提示：显式指令 Agent 提醒用户换新会话，带本次版本号
    assert "提醒用户" in r2["session_note"] and "换新会话" in r2["session_note"]
    assert "have_persona_version=2" in r2["session_note"] and "v2 已生效（取代 v1）" in r2["session_note"]


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
    # 脏 version / 浮点 / bool / 超范围（对抗#1：超大 int 会 Overflow 到 internal_error）
    for bad in ("abc", 2.9, True, "1.5", 0, -1, 10**20, "1_0", "+2"):
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


# ---- v0.3.5 增补注入（#905-②）----

def _mk_uncompiled(mids, code="work_report"):
    conn = db.connect()
    dims = {"work_type": {"ok": True, "code": code, "raw": "周报"},
            "audience": {"ok": True, "code": "leadership", "raw": "高层"},
            "purpose": {"ok": True, "code": "sync_info", "raw": "同步"}}
    for mid in mids:
        db.record_evidence(conn, mid, dims)
    conn.close()


def _stub_read(monkeypatch, contents=None, fail=False):
    from mema_twin import sink
    if fail:
        def boom(mid, workspace=None, client=None):
            raise sink.SinkError("mema HTTP MCP 不可达（127.0.0.1:8000）")
        monkeypatch.setattr(sink, "read_memory", boom)
        return
    def fake(mid, workspace=None, client=None):
        return {"ok": True, "data": {"memory": {"id": mid,
                "subject": f"s{mid}", "content": contents or f"偏好{mid}"}}}
    monkeypatch.setattr(sink, "read_memory", fake)


def test_task_start_supplement_injected(monkeypatch):
    """有 persona + 未编译证据 → 增补随注入，优先级声明在场。"""
    _stub_read(monkeypatch)
    server.twin("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m"})
    _mk_uncompiled([501, 502])
    r = server.twin("task_start", {"brief": "B", "work_type": "周报"})
    assert r["ok"]
    assert r["persona_prompt_md"] == "# v1"
    assert len(r["persona_supplement"]) == 2
    assert r["persona_supplement"][0]["content"] == "偏好501"
    assert "以增补为准" in r["persona_supplement_note"]


def test_task_start_supplement_with_shortcircuit(monkeypatch):
    """短路相等分支必须同样带增补：增补是新进场材料，上文没有（#895 陷阱）。"""
    _stub_read(monkeypatch)
    server.twin("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m"})
    _mk_uncompiled([503])
    r = server.twin("task_start", {"brief": "B", "work_type": "周报",
                                   "have_persona_version": 1})
    assert r["ok"] and r.get("persona_unchanged") is True
    assert "persona_prompt_md" not in r
    assert len(r["persona_supplement"]) == 1


def test_task_start_supplement_cap(monkeypatch):
    """上限 10 条截旧留新 + 积压提醒（#905-C 拍板）。"""
    _stub_read(monkeypatch)
    server.twin("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m"})
    _mk_uncompiled(list(range(600, 612)))
    r = server.twin("task_start", {"brief": "B", "work_type": "周报"})
    assert len(r["persona_supplement"]) == 10
    assert r["persona_supplement"][0]["id"] == 602  # 最旧两条被截
    assert "另有 2 条" in r["persona_supplement_note"]
    assert "compile" in r["persona_supplement_note"]


def test_task_start_supplement_mema_fail_soft(monkeypatch):
    """mema 全挂 → 无增补字段，persona 照常注入（软失败，绝不缺席）。"""
    _stub_read(monkeypatch, fail=True)
    server.twin("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m"})
    _mk_uncompiled([504, 505])
    r = server.twin("task_start", {"brief": "B", "work_type": "周报"})
    assert r["ok"] and r["persona_prompt_md"] == "# v1"
    assert "persona_supplement" not in r


def test_task_start_empty_persona_proto(monkeypatch):
    """空 persona 分支（#905-A 拍板：给）：原始证据当雏形注入。"""
    _stub_read(monkeypatch)
    _mk_uncompiled([506])
    r = server.twin("task_start", {"brief": "B", "work_type": "周报"})
    assert r["ok"]
    assert len(r["persona_supplement"]) == 1
    assert "尚无编译版 persona" in r["persona_supplement_note"]
    assert "已沉淀偏好" in r["hint"]
    assert "通用标准" not in r["hint"]


def test_task_start_no_evidence_no_supplement(monkeypatch):
    """无未编译证据 → 不产生增补字段（也不能走 find 兜底，AR-3）。"""
    from mema_twin import sink
    monkeypatch.setattr(sink, "find", lambda *a, **k: {"ok": True, "data": {"results": [
        {"id": 999, "tags": ["twin:wt:work_report"], "content": "历史偏好"}]}})
    server.twin("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m"})
    r = server.twin("task_start", {"brief": "B", "work_type": "周报"})
    assert "persona_supplement" not in r


def test_task_resume_supplement(monkeypatch):
    """task_resume 同一注入点：增补照带。"""
    _stub_read(monkeypatch)
    server.twin("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m"})
    start = server.twin("task_start", {"brief": "B", "work_type": "周报"})
    _mk_uncompiled([507])
    r = server.twin("task_resume", {"task_id": start["task_id"]})
    assert r["ok"]
    assert len(r["persona_supplement"]) == 1
    assert "以增补为准" in r["persona_supplement_note"]


def test_compile_material_conditional_and_prohibitions(monkeypatch):
    """#905-③：编译规则带条件化策略与高优先禁止项两行。"""
    r = server.twin("compile", {"work_type": "周报"})
    assert r["ok"]
    assert "条件化策略" in r["material"] and "适用条件与例外" in r["material"]
    assert "高优先级禁止项" in r["material"]


# ---- v0.3.5 双跑对比（#905-④）----

def test_compare_offer_scheduled_once():
    """夜间 origin 落版 → 首个 task_start 附提议（不含旧版全文），且只附一次。"""
    server.twin("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m"})
    r2 = server.twin("submit", {"work_type": "周报", "prompt_md": "# v2", "model": "m",
                                "origin": "scheduled"})
    assert r2["ok"] and r2["supersedes"] == 1
    assert "compare_hint" not in r2  # scheduled 落版走 task_start 提议，不走 hint
    t1 = server.twin("task_start", {"brief": "B", "work_type": "周报"})
    offer = t1.get("persona_compare_offer")
    assert offer and offer["current_version"] == 2 and offer["previous_version"] == 1
    assert "夜间定时任务" in offer["hint"] and "双跑" in offer["hint"]
    assert "get" in offer["hint"] and "非执行依据" in offer["hint"]
    assert "prompt_md" not in offer  # 提议不携带旧版全文
    t2 = server.twin("task_start", {"brief": "B2", "work_type": "周报"})
    assert "persona_compare_offer" not in t2


def test_manual_submit_compare_hint_no_offer():
    """交互式落版带 compare_hint（v1 无旧版不带）；手动编译永不触发提议。"""
    r1 = server.twin("submit", {"work_type": "PPT", "prompt_md": "# a", "model": "m"})
    assert r1["ok"] and "compare_hint" not in r1
    r2 = server.twin("submit", {"work_type": "PPT", "prompt_md": "# b", "model": "m"})
    assert r2["ok"] and "compare_hint" in r2
    assert '"version": 1' in r2["compare_hint"] and "双跑" in r2["compare_hint"]
    t = server.twin("task_start", {"brief": "B", "work_type": "PPT"})
    assert "persona_compare_offer" not in t


def test_scheduled_first_version_no_offer():
    """scheduled 落 v1：无旧版可比（compare_prev 不记）→ 永不提议。"""
    r = server.twin("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m",
                               "origin": "scheduled"})
    assert r["ok"] and r["supersedes"] is None and "compare_hint" not in r
    t = server.twin("task_start", {"brief": "B", "work_type": "周报"})
    assert "persona_compare_offer" not in t


def test_submit_origin_whitelist():
    r = server.twin("submit", {"work_type": "周报", "prompt_md": "# x",
                               "origin": "cron"})
    assert r.get("ok") is False and r.get("field") == "origin"


def test_get_version_param():
    server.twin("submit", {"work_type": "周报", "prompt_md": "# a", "model": "m"})
    server.twin("submit", {"work_type": "周报", "prompt_md": "# b", "model": "m"})
    g = server.twin("get", {"work_type": "周报"})
    assert g["ok"] and g["version"] == 2 and g["prompt_md"] == "# b"
    g1 = server.twin("get", {"work_type": "周报", "version": 1})
    assert g1["ok"] and g1["version"] == 1 and g1["prompt_md"] == "# a"
    gn = server.twin("get", {"work_type": "周报", "version": 99})
    assert gn.get("ok") is False and gn.get("error") == "not_found"
    assert "镜像降级" in gn.get("reason", "")
    for bad in ("abc", 2.9, True, "1.5", 0, -1, 10**20, "1_0", "+2"):
        gb = server.twin("get", {"work_type": "周报", "version": bad})
        assert gb.get("ok") is False and gb.get("field") == "version", bad


# ---- 轮1 review 修复的回归 ----

def test_task_resume_no_offer_and_does_not_burn():
    """task_resume 不附提议（拍板），且不消耗一次性标记——留给下一个 task_start。"""
    server.twin("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m"})
    t1 = server.twin("task_start", {"brief": "B", "work_type": "周报"})
    server.twin("submit", {"work_type": "周报", "prompt_md": "# v2", "model": "m",
                           "origin": "scheduled"})
    r = server.twin("task_resume", {"task_id": t1["task_id"]})
    assert r["ok"] and "persona_compare_offer" not in r
    t2 = server.twin("task_start", {"brief": "B2", "work_type": "周报"})
    assert t2.get("persona_compare_offer", {}).get("current_version") == 2


def test_supplement_failfast_discriminating(monkeypatch):
    """首条连接级 SinkError → 跳过剩余（增补整体缺席），不是逐条重试。"""
    from mema_twin import sink
    calls = []
    def fake(mid, workspace=None, client=None):
        calls.append(mid)
        raise sink.SinkError("mema HTTP MCP 不可达（127.0.0.1:8000）")
    monkeypatch.setattr(sink, "read_memory", fake)
    server.twin("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m"})
    _mk_uncompiled([508, 509])
    r = server.twin("task_start", {"brief": "B", "work_type": "周报"})
    assert "persona_supplement" not in r
    assert "persona_prompt_md" in r
    assert len(calls) == 1  # 第二条没再打 mema


def test_supplement_single_row_notok_no_failfast(monkeypatch):
    """单条 read 未命中不熔断：后续条目照读，skipped 如实上报。"""
    from mema_twin import sink
    def fake(mid, workspace=None, client=None):
        if mid == 508:
            return {"ok": False, "error": "not_found"}
        return {"ok": True, "data": {"memory": {"id": mid, "subject": "s",
                                                "content": f"偏好{mid}"}}}
    monkeypatch.setattr(sink, "read_memory", fake)
    server.twin("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m"})
    _mk_uncompiled([508, 509])
    r = server.twin("task_start", {"brief": "B", "work_type": "周报"})
    assert len(r["persona_supplement"]) == 1
    assert r["persona_supplement"][0]["id"] == 509
    assert r["persona_supplement_skipped"] == [{"memory_id": 508,
                                                "reason": "not_found"}]


def test_compare_offer_suppressed_for_mirror_persona(monkeypatch):
    """mirror 降级无版本身份：全文照注入，提议不触发。"""
    _stub_read(monkeypatch)
    mirror = __import__("pathlib").Path(
        __import__("os").environ["MEMA_TWIN_PROMPTS_DIR"]) / "work_report" / "active.md"
    mirror.parent.mkdir(parents=True, exist_ok=True)
    mirror.write_text("# 镜像降级版", encoding="utf-8")
    _mk_uncompiled([510])
    r = server.twin("task_start", {"brief": "B", "work_type": "周报"})
    assert r["persona_prompt_md"] == "# 镜像降级版"
    assert "persona_compare_offer" not in r
    assert len(r["persona_supplement"]) == 1  # 增补与提议正交，照带


def test_task_resume_empty_persona_supplement(monkeypatch):
    """resume 的空 persona 分支同样走雏形注入。"""
    _stub_read(monkeypatch)
    _mk_uncompiled([511])
    t1 = server.twin("task_start", {"brief": "B", "work_type": "周报"})
    r = server.twin("task_resume", {"task_id": t1["task_id"]})
    assert r["ok"]
    assert len(r["persona_supplement"]) == 1
    assert "尚无编译版 persona" in r["persona_supplement_note"]
    assert "已沉淀偏好" in r["hint"]


def test_notice_suppressed_by_scheduled_compile():
    """只建夜间任务（scheduled submit 刷 last_scheduled_compile_at）也消提醒。"""
    from mema_twin import scan
    assert scan.scan_notice() is not None
    server.twin("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m",
                           "origin": "scheduled"})
    assert scan.scan_notice() is None


# ---- 轮2 对抗性 review 修复的回归 ----

def test_claim_meta_atomic_first_wins():
    """一次性标记原子抢占：首个 claim 赢，第二个输（多宿主并发只问一次）。"""
    from mema_twin import flow
    assert flow.claim_meta("t:x:1", "a") is True
    assert flow.claim_meta("t:x:1", "b") is False


def test_supplement_note_pinned_to_version(monkeypatch):
    """优先级声明钉死版本号，不宣称"编译后新增"（漏列 source id 时旧证据也走增补）。"""
    _stub_read(monkeypatch)
    server.twin("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m"})
    _mk_uncompiled([520])
    r = server.twin("task_start", {"brief": "B", "work_type": "周报"})
    note = r["persona_supplement_note"]
    assert "尚未被 persona v1 吸收" in note
    assert "与 v1 冲突时以增补为准" in note
    assert "编译后新增" not in note


def test_offer_hint_pins_version():
    server.twin("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m"})
    server.twin("submit", {"work_type": "周报", "prompt_md": "# v2", "model": "m",
                           "origin": "scheduled"})
    r = server.twin("task_start", {"brief": "B", "work_type": "周报"})
    hint = r["persona_compare_offer"]["hint"]
    assert "以 v2 为执行依据" in hint and "更高版本" in hint
    h = server.twin("submit", {"work_type": "PPT", "prompt_md": "# a", "model": "m"})
    h2 = server.twin("submit", {"work_type": "PPT", "prompt_md": "# b", "model": "m"})
    assert "以 v2 为执行依据" in h2["compare_hint"]


def test_task_id_float_rejected():
    # " 4" 不在拒绝列表：strip 后比对是仓库矫正惯例（#895 have_version 同款）
    for bad in (4.9, True, "4_9", "+4", "4.0", [4]):
        r = server.twin("task_get", {"task_id": bad})
        assert r.get("ok") is False and r.get("error") == "invalid_input", bad
    assert server.twin("task_get", {"task_id": "4"}).get("error") == "not_found"


def test_submit_prompt_md_size_cap():
    r = server.twin("submit", {"work_type": "周报", "prompt_md": "x" * 100_001})
    assert r.get("ok") is False and r.get("field") == "prompt_md"
    ok = server.twin("submit", {"work_type": "周报", "prompt_md": "x" * 100_000})
    assert ok.get("ok") is True


def test_submit_leftover_unabsorbed_warning(monkeypatch):
    """source_memory_ids 漏列：当场警告 + 残留证据继续以增补在场。"""
    _stub_read(monkeypatch)
    _mk_uncompiled([601, 602])
    r = server.twin("submit", {"work_type": "周报", "prompt_md": "# v1",
                               "model": "m", "source_memory_ids": [601]})
    assert r["ok"] and any("1 条未编译证据未被本版吸收" in w for w in r["warnings"])
    t = server.twin("task_start", {"brief": "B", "work_type": "周报"})
    assert [e["id"] for e in t["persona_supplement"]] == [602]


def test_task_start_dim_length_cap():
    r = server.twin("task_start", {"brief": "B", "work_type": "周报",
                                   "audience": "长" * 201})
    assert r.get("ok") is False and r.get("field") == "audience"
    # 失败不留幽灵 pending（defer 生效）
    conn = db.connect()
    assert db.list_pending(conn) == []
    conn.close()


def test_custom_code_charset_whitelist():
    from mema_twin import db as twin_db
    conn = db.connect()
    for bad in ('we"ird', "a:b", ".", "a b", "a\nb"):
        import pytest as _pytest
        with _pytest.raises(ValueError):
            twin_db.add_canonical(conn, "work_type", bad, "测试")
    conn.close()


def test_supplement_all_skipped_reports_skipped(monkeypatch):
    """全部条目读未命中：无增补字段，但 skipped 如实上报（可区分"没有未编译"）。"""
    from mema_twin import sink
    monkeypatch.setattr(sink, "read_memory",
                        lambda mid, workspace=None, client=None:
                        {"ok": False, "error": "not_found"})
    server.twin("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m"})
    _mk_uncompiled([611, 612])
    r = server.twin("task_start", {"brief": "B", "work_type": "周报"})
    assert "persona_supplement" not in r
    assert len(r["persona_supplement_skipped"]) == 2


# ---- v0.3.6 受众画像 ①：伪类型存储与防串味 ----

def test_audience_profile_create_and_status_split():
    """aud- 伪类型可落版；status 里与真类型分流（audience_profiles 单列）。"""
    r = server.twin("submit", {"work_type": "aud-leadership", "prompt_md": "# 对领导画像",
                               "model": "m", "source_memory_ids": [1, 2]})
    assert r["ok"] and r["version"] == 1
    s = server.twin("status", {})
    wt_codes = [p["work_type"] for p in s["prompts"]]
    aud_codes = [p["work_type"] for p in s["audience_profiles"]]
    assert "aud-leadership" not in wt_codes and "aud-leadership" in aud_codes


def test_audience_profile_unknown_audience_rejected():
    r = server.twin("submit", {"work_type": "aud-nope", "prompt_md": "# x"})
    assert r.get("ok") is False and "unknown audience" in r.get("reason", "")


def test_aud_prefix_reserved_for_work_type_codes():
    from mema_twin import db as twin_db
    import pytest as _pytest
    conn = db.connect()
    with _pytest.raises(ValueError):
        twin_db.add_canonical(conn, "work_type", "aud-fake", "伪装画像")
    conn.close()


def test_get_reads_audience_profile():
    server.twin("submit", {"work_type": "aud-leadership", "prompt_md": "# 画像v1", "model": "m"})
    g = server.twin("get", {"work_type": "aud-leadership"})
    assert g["ok"] and g["prompt_md"] == "# 画像v1"
    g2 = server.twin("get", {"work_type": "aud-leadership", "version": 99})
    assert g2.get("ok") is False and g2.get("error") == "not_found"


def test_evidence_stats_excludes_aud_rows():
    from mema_twin import db as twin_db
    conn = db.connect()
    dims_l = {"work_type": {"ok": True, "code": "aud-leadership", "raw": "(受众级)"},
              "audience": {"ok": True, "code": "leadership", "raw": "领导"},
              "purpose": {"ok": True, "code": "sync_info", "raw": "同步"}}
    dims_doc = {**dims_l, "work_type": {"ok": True, "code": "work_report", "raw": "周报"}}
    twin_db.record_evidence(conn, 701, dims_l)
    twin_db.record_evidence(conn, 702, dims_doc)
    conn.close()
    s = server.twin("status", {})
    assert s["uncompiled"].get("work_report") == 1
    assert "aud-leadership" not in s["uncompiled"]


def test_audience_evidence_query_semantics():
    from mema_twin import db as twin_db
    conn = db.connect()
    dims = {"work_type": {"ok": True, "code": "work_report", "raw": "周报"},
            "audience": {"ok": True, "code": "leadership", "raw": "领导"},
            "purpose": {"ok": True, "code": "sync_info", "raw": "同步"}}
    ppt = {**dims, "work_type": {"ok": True, "code": "presentation", "raw": "PPT"}}
    aud_dims = {"work_type": {"ok": True, "code": "aud-leadership", "raw": "(受众级)"},
                **{k: dims[k] for k in ("audience", "purpose")}}
    twin_db.record_evidence(conn, 801, dims)   # 周报+领导
    twin_db.record_evidence(conn, 802, ppt)    # PPT+领导
    twin_db.record_evidence(conn, 803, aud_dims)  # 受众级
    all_rows = twin_db.audience_evidence(conn, "leadership")
    assert [r["memory_id"] for r in all_rows] == [801, 802, 803]  # 不分 compiled
    no_doc = twin_db.audience_evidence(conn, "leadership", exclude_work_type="work_report")
    assert [r["memory_id"] for r in no_doc] == [802, 803]  # 去重本类型，aud- 行保留
    conn.close()


# ---- v0.3.6 受众画像 ②：注入贯通 ----

def test_task_start_injects_audience_profile(monkeypatch):
    """画像存在：audience_profile_md 全文 + 优先级链标签。"""
    server.twin("submit", {"work_type": "aud-leadership", "prompt_md": "# 对领导要简洁白话",
                           "model": "m"})
    r = server.twin("task_start", {"brief": "B", "work_type": "周报", "audience": "领导"})
    assert r["audience_profile_md"] == "# 对领导要简洁白话"
    assert "画像 v1" in r["audience_profile_note"]
    assert "本类型增补 > 类型 persona > 受众画像" in r["audience_profile_note"]


def test_task_start_audience_proto(monkeypatch):
    """画像未编出：雏形垫底（排除本类型行，防与增补重复）。"""
    _stub_read(monkeypatch)
    server.twin("submit", {"work_type": "PPT", "prompt_md": "# p", "model": "m"})
    _mk_uncompiled([901])  # 周报+领导（本类型外）
    conn = db.connect()
    ppt_dims = {"work_type": {"ok": True, "code": "presentation", "raw": "PPT"},
                "audience": {"ok": True, "code": "leadership", "raw": "领导"},
                "purpose": {"ok": True, "code": "sync_info", "raw": "同步"}}
    db.record_evidence(conn, 902, ppt_dims)  # 本类型行：不进雏形（在增补里）
    conn.close()
    r = server.twin("task_start", {"brief": "B", "work_type": "PPT", "audience": "领导"})
    assert [e["id"] for e in r["audience_profile_proto"]] == [901]
    assert "雏形" in r["audience_profile_note"]
    assert [e["id"] for e in r["persona_supplement"]] == [902]


def test_task_start_no_audience_no_profile(monkeypatch):
    """audience 未传/未归一 → 无画像字段（软降级）。"""
    r = server.twin("task_start", {"brief": "B", "work_type": "周报"})
    for k in ("audience_profile_md", "audience_profile_proto"):
        assert k not in r


def test_task_resume_injects_audience_profile():
    server.twin("submit", {"work_type": "aud-leadership", "prompt_md": "# 画像",
                           "model": "m"})
    t1 = server.twin("task_start", {"brief": "B", "work_type": "周报", "audience": "领导"})
    r = server.twin("task_resume", {"task_id": t1["task_id"]})
    assert r["audience_profile_md"] == "# 画像"


# ---- v0.3.6 受众画像 ③：compile 参考节 + ④画像素材 ----

def test_compile_material_includes_audience_profiles(monkeypatch):
    """类型编译素材包含已有受众画像 + 守门句；无画像时提示待生成。"""
    _stub_read(monkeypatch)
    server.twin("submit", {"work_type": "aud-leadership", "prompt_md": "# 简洁白话",
                           "model": "m"})
    _mk_uncompiled([921])  # 周报 + 领导：让类型证据能关联到该受众
    r = server.twin("compile", {"work_type": "周报"})
    assert "同受众跨类型偏好参考" in r["material"]
    assert "aud-leadership 画像 v1" in r["material"] and "# 简洁白话" in r["material"]
    assert "守门" in r["material"] and "格式与结构仍以本类型证据为准" in r["material"]
    r2 = server.twin("compile", {"work_type": "PPT"})
    assert "暂无" in r2["material"]


def test_compile_audience_mode_material(monkeypatch):
    """compile(aud-x)：受众画像素材包——全量证据（含已 compiled）、画像规则。"""
    _stub_read(monkeypatch)
    _mk_uncompiled([911])
    server.twin("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m",
                           "source_memory_ids": [911]})
    r = server.twin("compile", {"work_type": "aud-leadership"})
    assert r["ok"] and r["work_type"] == "aud-leadership"
    assert "受众画像素材包" in r["material"]
    assert "只保留对该受众稳定成立的口径类偏好" in r["material"]
    assert "[911]" in r["material"]  # 已 compiled 的证据也在画像素材里
    assert "条件段" in r["material"]


# ---- v0.3.6 受众画像 ④：触发器 ----

def test_audience_stale_trigger():
    """证据数≠画像吸收数（或无画像）→ stale；吸收齐 → 清；新证据 → 再 stale。"""
    _stub_read(None) if False else None
    from mema_twin import sink
    import unittest.mock as _mock
    with _mock.patch.object(sink, "read_memory",
                            lambda mid, workspace=None, client=None:
                            {"ok": True, "data": {"memory": {"id": mid, "subject": "s",
                                                             "content": "偏好"}}}):
        _mk_uncompiled([931, 932])
        s1 = server.twin("status", {})
        assert s1["audience_stale"].get("leadership") == {"evidence": 2, "profile_evidence": None}
        server.twin("submit", {"work_type": "aud-leadership", "prompt_md": "# 画像",
                               "model": "m", "source_memory_ids": [931, 932]})
        s2 = server.twin("status", {})
        assert "leadership" not in s2["audience_stale"]
        _mk_uncompiled([933])
        s3 = server.twin("status", {})
        assert s3["audience_stale"]["leadership"] == {"evidence": 3, "profile_evidence": 2}
