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
        r = server._twin_impl("task_get", bad)
        assert r.get("ok") is False and r.get("error") in ("invalid_input", "internal_error"), bad


def test_malformed_pending_resolve(monkeypatch):
    r = server._twin_impl("resolve", {"pending_id": {"bad": 1}, "decision": "map", "code": "work_report"})
    assert r.get("ok") is False
    r2 = server._twin_impl("resolve", {"pending_id": 1, "decision": "canonicalize", "new_type": "not-a-dict"})
    assert r2.get("ok") is False


def test_malformed_source_memory_ids():
    r = server._twin_impl("submit", {"work_type": "work_report", "prompt_md": "# x",
                               "source_memory_ids": "123"})
    assert r.get("ok") is False and r.get("field") == "source_memory_ids"
    r2 = server._twin_impl("submit", {"work_type": "work_report", "prompt_md": "# x",
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
        r = server._twin_impl("write", {"content": "c", "work_type": "周报",
                                  "audience": "高层", "purpose": "同步"})
        assert r.get("ok") is True, env_value
    assert captured["workspace"] == "mema-twin"


def test_write_rejects_oversized_dimension():
    r = server._twin_impl("write", {"content": "x", "work_type": "长" * 300,
                              "audience": "高层", "purpose": "同步"})
    assert r.get("ok") is False and r.get("field") == "work_type"


# ---- 第二轮对抗性 review 回归 ----

def test_canonicalize_code_traversal_rejected():
    """对抗#1：自定义 code 不能带路径段（会进 prompts/<ws>/<code>/）。"""
    from mema_twin import db as twin_db
    import pytest
    for bad in ("/tmp/abs", "../pwn", "..", "a/b"):
        r = server._twin_impl("resolve", {"pending_id": 999999, "decision": "canonicalize",
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



def test_resolve_double_map_rejected(tmp_path, monkeypatch):
    """对抗#7：同一 pending 不得二次裁定。"""
    from mema_twin import db as twin_db, normalize
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(tmp_path / "t.sqlite3"))
    conn = twin_db.connect()
    pid = twin_db.upsert_pending(conn, "purpose", "灵能催办")
    twin_db.append_alias(conn, "purpose", "drive_action", "灵能催办")
    twin_db.set_pending(conn, pid, "mapped", "drive_action")
    r = server._twin_impl("resolve", {"pending_id": pid, "decision": "map", "code": "sync_info"})
    assert r.get("ok") is False and "不可重复裁定" in r.get("reason", "")
    assert "重试原写入" in r.get("reason", "")  # v0.3.8：并发他方已裁定 → 指引直接重试


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
    r = srv._twin_impl("write", {"content": "c", "work_type": "周报", "audience": "高层",
                           "purpose": "同步", "tags": ["twin:wt:presentation", "正常tag"]})
    assert r["ok"]
    assert "twin:wt:presentation" not in captured["tags"]
    assert "正常tag" in captured["tags"]
    assert captured["tags"].count("twin:wt:work_report") == 1


def test_write_no_ghost_pending_on_sink_failure(monkeypatch):
    """对抗#14：mema 写失败不留幽灵 pending；v0.3.8 归一门下未命中更是先打回
    （mema 根本不被触达，票据即裁定义务，不是幽灵）。"""
    from mema_twin import server as srv, sink
    called = []
    monkeypatch.setattr(sink, "remember",
                        lambda *a, **k: called.append(1) or (_ for _ in ()).throw(sink.SinkError("down")))
    r = srv._twin_impl("write", {"content": "c", "work_type": "周报",
                           "audience": "高层", "purpose": "同步"})
    assert r.get("error") == "mema_unreachable" and called  # 门过、mema 失败
    conn = db.connect()
    n = conn.execute("SELECT COUNT(*) AS c FROM twin_pending_values").fetchone()["c"]
    conn.close()
    assert n == 0
    # 未命中：打回 + 票据，mema 不被调用
    called.clear()
    r2 = srv._twin_impl("write", {"content": "c", "work_type": "灵能审计年报",
                            "audience": "高层", "purpose": "同步"})
    assert r2.get("error") == "unmatched_value" and not called
    assert r2["fields"]["work_type"]["pending_id"]
    assert any(c["code"] == "data_analysis" for c in r2["fields"]["work_type"]["candidates"])


def test_revise_child_is_planning():
    """v0.3.8：已交付任务的修订子任务回 planning 重走，parent 进 superseded。"""
    t = flow.insert_task(brief="T", status="planning", dims={})
    server._twin_impl("task_submit", {"task_id": t["id"], "deliverable_md": "# v1"})
    r = server._twin_impl("task_revise", {"task_id": t["id"], "revision_reason": "返工"})
    assert r["ok"] and r["status"] == "planning"
    child = flow.get_task(r["task_id"])
    assert child["parent_task_id"] == t["id"]
    assert flow.get_task(t["id"])["status"] == "superseded"


def test_resume_allows_planning():
    """对抗#8：进行中任务可续作。"""
    t = flow.insert_task(brief="T", status="planning", dims={})
    r = server._twin_impl("task_resume", {"task_id": t["id"], "session": "s"})
    assert r["ok"] and r["new_task_id"]


def test_task_close_action():
    t = flow.insert_task(brief="T", status="planning", dims={})
    r = server._twin_impl("task_close", {"task_id": t["id"], "reason": "不做"})
    assert r["ok"] and flow.get_task(t["id"])["status"] == "superseded"


def test_submit_after_supersede_rejected():
    """对抗#4：被让位的任务不能再 submit 复活。"""
    t = flow.insert_task(brief="T", status="planning", dims={})
    flow.set_status(t["id"], "superseded", reason="让位")
    r = server._twin_impl("task_submit", {"task_id": t["id"], "deliverable_md": "x"})
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

    def fake_call(name, arguments, client=None, timeout=30):
        captured["client"] = client
        return {"ok": True, "data": {"id": 7}}
    monkeypatch.setattr(sink, "_call", fake_call)
    r = server._twin_impl("write", {"content": "c", "work_type": "周报", "audience": "高层",
                              "purpose": "同步", "client": "kimi"})
    assert r["ok"] and captured["client"] == "kimi"
    r2 = server._twin_impl("write", {"content": "c", "work_type": "周报", "audience": "高层",
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
    r = server._twin_impl("write", {"content": "c", "work_type": "周报",
                              "audience": "高层", "purpose": "同步"})
    assert r.get("ok") is False and r.get("error") == "invalid_input"


def test_write_client_header_authoritative(monkeypatch):
    """对抗#1：http 头存在时头是权威——data.client 不一致打回（堵跨宿主冒充），
    一致或省略放行。"""
    from mema_twin import sink
    captured = {}

    def fake_call(name, arguments, client=None, timeout=30):
        captured["client"] = client
        return {"ok": True, "data": {"id": 7}}
    monkeypatch.setattr(sink, "_call", fake_call)
    monkeypatch.setattr(server.mcp, "get_context",
                        lambda: _FakeCtx([("x-mema-client", "kimi")]))
    r = server._twin_impl("write", {"content": "c", "work_type": "周报", "audience": "高层",
                              "purpose": "同步", "client": "zcode"})
    assert r.get("ok") is False and r.get("error") == "invalid_input" and "不一致" in r.get("reason", "")
    r2 = server._twin_impl("write", {"content": "c", "work_type": "周报", "audience": "高层",
                               "purpose": "同步", "client": "kimi"})
    assert r2["ok"] and captured["client"] == "kimi"
    r3 = server._twin_impl("write", {"content": "c", "work_type": "周报",
                               "audience": "高层", "purpose": "同步"})
    assert r3["ok"] and captured["client"] == "kimi"


def test_write_rejects_dirty_explicit_client():
    r = server._twin_impl("write", {"content": "c", "work_type": "周报",
                              "audience": "高层", "purpose": "同步",
                              "client": "no spaces"})
    assert r.get("ok") is False and r.get("error") == "invalid_input"


def test_task_start_records_header_client(monkeypatch):
    """http 多宿主：task_start 建档的 client 来自头，不落 env 默认。"""
    monkeypatch.setattr(server.mcp, "get_context",
                        lambda: _FakeCtx([("x-mema-client", "kimi")]))
    r = server._twin_impl("task_start", {"brief": "B", "work_type": "周报"})
    assert r.get("ok") is True
    record = flow.get_task(r["task_id"])
    assert record["client"] == "kimi"


def test_dirty_explicit_client_rejected_at_entry():
    """轮1#1：脏 data.client 在入口打回，任何动作（含 task_start）不留幽灵 pending。"""
    r = server._twin_impl("task_start", {"brief": "B", "work_type": "周报",
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

    def fake_call(name, arguments, client=None, timeout=30):
        captured["client"] = client
        return {"ok": True, "data": {"id": 7}}
    monkeypatch.setattr(sink, "_call", fake_call)
    r = server._twin_impl("write", {"content": "c", "work_type": "周报", "audience": "高层",
                              "purpose": "同步", "client": "kimi"})
    assert r["ok"] and captured["client"] == "kimi"


def test_client_type_and_whitespace_rejected(monkeypatch):
    """对抗#6：非字符串 / 首尾空白一律打回，不做 str() 修正。"""
    for bad in ({"x": 1}, 123, True):
        r = server._twin_impl("write", {"content": "c", "work_type": "周报",
                                  "audience": "高层", "purpose": "同步", "client": bad})
        assert r.get("ok") is False and r.get("error") == "invalid_input", bad
    r2 = server._twin_impl("write", {"content": "c", "work_type": "周报",
                               "audience": "高层", "purpose": "同步", "client": " kimi "})
    assert r2.get("ok") is False and "空白" in r2.get("reason", "")


def test_header_whitespace_rejected(monkeypatch):
    """对抗#6：头值带首尾空白直接拒（mema 同款，不 strip 修正）。"""
    monkeypatch.setattr(server.mcp, "get_context",
                        lambda: _FakeCtx([("x-mema-client", " kimi ")]))
    r = server._twin_impl("status", {})
    assert r.get("ok") is False and r.get("error") == "invalid_input"


def test_duplicate_header_rejected(monkeypatch):
    """对抗#8：重复头（含大小写变体）打回。"""
    monkeypatch.setattr(server.mcp, "get_context",
                        lambda: _FakeCtx([("X-Mema-Client", "a"), ("x-mema-client", "b")]))
    r = server._twin_impl("status", {})
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
    r = server._twin_impl("task_start", {"brief": "B", "work_type": "周报"})
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
    r = server._twin_impl("compile", {"work_type": "周报"})
    assert r["ok"]
    assert "## 旧版本 prompt（编译参考，非执行依据）" in r["material"]
    assert "当前版本 prompt" not in r["material"]
    assert "取代" in r["material"] and "与旧版本冲突" in r["material"]
    # 会话分工提示下沉到响应（非 SKILL 宿主也可见），且是「转告用户」的显式指令
    assert "告知用户" in r["session_note"] and "换新会话" in r["session_note"]
    # 首版场景（无旧版本）同样成立
    r2 = server._twin_impl("compile", {"work_type": "PPT"})
    assert r2["ok"] and "首个版本" in r2["material"] and "告知用户" in r2["session_note"]


def test_submit_returns_supersedes():
    """submit 响应带 supersedes：落版即裁决，不等下一次注入。"""
    r = server._twin_impl("submit", {"work_type": "周报", "prompt_md": "# a", "model": "m"})
    assert r["ok"] and r["supersedes"] is None
    r2 = server._twin_impl("submit", {"work_type": "周报", "prompt_md": "# b", "model": "m"})
    assert r2["ok"] and r2["supersedes"] == 1
    # 收尾提示：显式指令 Agent 提醒用户换新会话，带本次版本号
    assert "提醒用户" in r2["session_note"] and "换新会话" in r2["session_note"]
    assert "have_persona_version=2" in r2["session_note"] and "v2 已生效（取代 v1）" in r2["session_note"]


# ---- 0.3.4 rollback ----

def test_rollback_action_boundary():
    r0 = server._twin_impl("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m"})
    server._twin_impl("submit", {"work_type": "周报", "prompt_md": "# v2", "model": "m"})
    r = server._twin_impl("rollback", {"work_type": "周报"})
    assert r["ok"] and r["version"] == 1 and r["rolled_back_from"] == 2
    # 幂等 + 注记
    r2 = server._twin_impl("rollback", {"work_type": "周报", "version": 1})
    assert r2["ok"] and r2.get("note") and "rolled_back_from" not in r2
    # 目标不存在报错带可用版本
    r3 = server._twin_impl("rollback", {"work_type": "周报", "version": 99})
    assert r3.get("ok") is False and "可用版本" in r3.get("reason", "")
    # 脏 version / 浮点 / bool / 超范围（对抗#1：超大 int 会 Overflow 到 internal_error）
    for bad in ("abc", 2.9, True, "1.5", 0, -1, 10**20, "1_0", "+2"):
        r4 = server._twin_impl("rollback", {"work_type": "周报", "version": bad})
        assert r4.get("ok") is False and r4.get("field") == "version", bad
    # unknown work_type / 缺 work_type
    assert server._twin_impl("rollback", {"work_type": "不存在"}).get("ok") is False
    assert server._twin_impl("rollback", {}).get("field") == "work_type"
    # 唯一版本省略 version → 报错附 status 提示
    server._twin_impl("submit", {"work_type": "PPT", "prompt_md": "# only", "model": "m"})
    r6 = server._twin_impl("rollback", {"work_type": "PPT"})
    assert r6.get("ok") is False and "status" in r6.get("reason", "")
    # 回滚后 get 拿到的是回滚版本
    g = server._twin_impl("get", {"work_type": "周报"})
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
    server._twin_impl("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m"})
    _mk_uncompiled([501, 502])
    r = server._twin_impl("task_start", {"brief": "B", "work_type": "周报"})
    assert r["ok"]
    assert r["persona_prompt_md"] == "# v1"
    assert len(r["persona_supplement"]) == 2
    assert r["persona_supplement"][0]["content"] == "偏好501"
    assert "以增补为准" in r["persona_supplement_note"]


def test_task_start_supplement_with_shortcircuit(monkeypatch):
    """短路相等分支必须同样带增补：增补是新进场材料，上文没有（#895 陷阱）。"""
    _stub_read(monkeypatch)
    server._twin_impl("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m"})
    _mk_uncompiled([503])
    r = server._twin_impl("task_start", {"brief": "B", "work_type": "周报",
                                   "have_persona_version": 1})
    assert r["ok"] and r.get("persona_unchanged") is True
    assert "persona_prompt_md" not in r
    assert len(r["persona_supplement"]) == 1


def test_task_start_supplement_cap(monkeypatch):
    """上限 10 条截旧留新 + 积压提醒（#905-C 拍板）。"""
    _stub_read(monkeypatch)
    server._twin_impl("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m"})
    _mk_uncompiled(list(range(600, 612)))
    r = server._twin_impl("task_start", {"brief": "B", "work_type": "周报"})
    assert len(r["persona_supplement"]) == 10
    assert r["persona_supplement"][0]["id"] == 602  # 最旧两条被截
    assert "另有 2 条" in r["persona_supplement_note"]
    assert "compile" in r["persona_supplement_note"]


def test_task_start_supplement_mema_fail_soft(monkeypatch):
    """mema 全挂 → 无增补字段，persona 照常注入（软失败，绝不缺席）。"""
    _stub_read(monkeypatch, fail=True)
    server._twin_impl("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m"})
    _mk_uncompiled([504, 505])
    r = server._twin_impl("task_start", {"brief": "B", "work_type": "周报"})
    assert r["ok"] and r["persona_prompt_md"] == "# v1"
    assert "persona_supplement" not in r


def test_task_start_empty_persona_proto(monkeypatch):
    """空 persona 分支（#905-A 拍板：给）：原始证据当雏形注入。"""
    _stub_read(monkeypatch)
    _mk_uncompiled([506])
    r = server._twin_impl("task_start", {"brief": "B", "work_type": "周报"})
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
    server._twin_impl("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m"})
    r = server._twin_impl("task_start", {"brief": "B", "work_type": "周报"})
    assert "persona_supplement" not in r


def test_task_resume_supplement(monkeypatch):
    """task_resume 同一注入点：增补照带。"""
    _stub_read(monkeypatch)
    server._twin_impl("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m"})
    start = server._twin_impl("task_start", {"brief": "B", "work_type": "周报"})
    _mk_uncompiled([507])
    r = server._twin_impl("task_resume", {"task_id": start["task_id"]})
    assert r["ok"]
    assert len(r["persona_supplement"]) == 1
    assert "以增补为准" in r["persona_supplement_note"]


def test_compile_material_conditional_and_prohibitions(monkeypatch):
    """#905-③：编译规则带条件化策略与高优先禁止项两行。"""
    r = server._twin_impl("compile", {"work_type": "周报"})
    assert r["ok"]
    assert "条件化策略" in r["material"] and "适用条件与例外" in r["material"]
    assert "高优先级禁止项" in r["material"]


# ---- v0.3.5 双跑对比（#905-④）----




# ---- 轮1 review 修复的回归 ----


def test_supplement_failfast_discriminating(monkeypatch):
    """首条连接级 SinkError → 跳过剩余（增补整体缺席），不是逐条重试。"""
    from mema_twin import sink
    calls = []
    def fake(mid, workspace=None, client=None):
        calls.append(mid)
        raise sink.SinkError("mema HTTP MCP 不可达（127.0.0.1:8000）")
    monkeypatch.setattr(sink, "read_memory", fake)
    server._twin_impl("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m"})
    _mk_uncompiled([508, 509])
    r = server._twin_impl("task_start", {"brief": "B", "work_type": "周报"})
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
    server._twin_impl("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m"})
    _mk_uncompiled([508, 509])
    r = server._twin_impl("task_start", {"brief": "B", "work_type": "周报"})
    assert len(r["persona_supplement"]) == 1
    assert r["persona_supplement"][0]["id"] == 509
    assert r["persona_supplement_skipped"] == [{"memory_id": 508,
                                                "reason": "not_found"}]


def test_mirror_persona_full_injection_no_offer(monkeypatch):
    """mirror 降级无版本身份：全文照注入（提议机制已删，增补照带）。"""
    _stub_read(monkeypatch)
    mirror = __import__("pathlib").Path(
        __import__("os").environ["MEMA_TWIN_PROMPTS_DIR"]) / "work_report" / "active.md"
    mirror.parent.mkdir(parents=True, exist_ok=True)
    mirror.write_text("# 镜像降级版", encoding="utf-8")
    _mk_uncompiled([510])
    r = server._twin_impl("task_start", {"brief": "B", "work_type": "周报"})
    assert r["persona_prompt_md"] == "# 镜像降级版"
    assert len(r["persona_supplement"]) == 1



def test_task_resume_empty_persona_supplement(monkeypatch):
    """resume 的空 persona 分支同样走雏形注入。"""
    _stub_read(monkeypatch)
    _mk_uncompiled([511])
    t1 = server._twin_impl("task_start", {"brief": "B", "work_type": "周报"})
    r = server._twin_impl("task_resume", {"task_id": t1["task_id"]})
    assert r["ok"]
    assert len(r["persona_supplement"]) == 1
    assert "尚无编译版 persona" in r["persona_supplement_note"]
    assert "已沉淀偏好" in r["hint"]


def test_notice_suppressed_by_scheduled_compile():
    """只建夜间任务（scheduled submit 刷 last_scheduled_compile_at）也消提醒。"""
    from mema_twin import scan
    assert scan.scan_notice() is not None
    server._twin_impl("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m",
                           "origin": "scheduled"})
    assert scan.scan_notice() is None


# ---- 轮2 对抗性 review 修复的回归 ----


def test_supplement_note_pinned_to_version(monkeypatch):
    """优先级声明钉死版本号，不宣称"编译后新增"（漏列 source id 时旧证据也走增补）。"""
    _stub_read(monkeypatch)
    server._twin_impl("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m"})
    _mk_uncompiled([520])
    r = server._twin_impl("task_start", {"brief": "B", "work_type": "周报"})
    note = r["persona_supplement_note"]
    assert "尚未被 persona v1 吸收" in note
    assert "与 v1 冲突时以增补为准" in note
    assert "编译后新增" not in note



def test_task_id_float_rejected():
    # " 4" 不在拒绝列表：strip 后比对是仓库矫正惯例（#895 have_version 同款）
    for bad in (4.9, True, "4_9", "+4", "4.0", [4]):
        r = server._twin_impl("task_get", {"task_id": bad})
        assert r.get("ok") is False and r.get("error") == "invalid_input", bad
    assert server._twin_impl("task_get", {"task_id": "4"}).get("error") == "not_found"


def test_submit_prompt_md_size_cap():
    r = server._twin_impl("submit", {"work_type": "周报", "prompt_md": "x" * 100_001})
    assert r.get("ok") is False and r.get("field") == "prompt_md"
    ok = server._twin_impl("submit", {"work_type": "周报", "prompt_md": "x" * 100_000})
    assert ok.get("ok") is True


def test_submit_leftover_unabsorbed_warning(monkeypatch):
    """source_memory_ids 漏列：当场警告 + 残留证据继续以增补在场。"""
    _stub_read(monkeypatch)
    _mk_uncompiled([601, 602])
    r = server._twin_impl("submit", {"work_type": "周报", "prompt_md": "# v1",
                               "model": "m", "source_memory_ids": [601]})
    assert r["ok"] and any("1 条在世证据未被本版吸收" in w for w in r["warnings"])
    t = server._twin_impl("task_start", {"brief": "B", "work_type": "周报"})
    assert [e["id"] for e in t["persona_supplement"]] == [602]


def test_task_start_dim_length_cap():
    r = server._twin_impl("task_start", {"brief": "B", "work_type": "周报",
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
    server._twin_impl("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m"})
    _mk_uncompiled([611, 612])
    r = server._twin_impl("task_start", {"brief": "B", "work_type": "周报"})
    assert "persona_supplement" not in r
    assert len(r["persona_supplement_skipped"]) == 2


# ---- v0.3.6 受众画像 ①：伪类型存储与防串味 ----

def test_audience_profile_create_and_status_split():
    """aud- 伪类型可落版；status 里与真类型分流（audience_profiles 单列）。"""
    r = server._twin_impl("submit", {"work_type": "aud-leadership", "prompt_md": "# 对领导画像",
                               "model": "m", "source_memory_ids": [1, 2]})
    assert r["ok"] and r["version"] == 1
    s = server._twin_impl("status", {})
    wt_codes = [p["work_type"] for p in s["prompts"]]
    aud_codes = [p["work_type"] for p in s["audience_profiles"]]
    assert "aud-leadership" not in wt_codes and "aud-leadership" in aud_codes


def test_audience_profile_unknown_audience_rejected():
    r = server._twin_impl("submit", {"work_type": "aud-nope", "prompt_md": "# x"})
    assert r.get("ok") is False and "unknown audience" in r.get("reason", "")


def test_aud_prefix_reserved_for_work_type_codes():
    from mema_twin import db as twin_db
    import pytest as _pytest
    conn = db.connect()
    with _pytest.raises(ValueError):
        twin_db.add_canonical(conn, "work_type", "aud-fake", "伪装画像")
    conn.close()


def test_get_reads_audience_profile():
    server._twin_impl("submit", {"work_type": "aud-leadership", "prompt_md": "# 画像v1", "model": "m"})
    g = server._twin_impl("get", {"work_type": "aud-leadership"})
    assert g["ok"] and g["prompt_md"] == "# 画像v1"
    g2 = server._twin_impl("get", {"work_type": "aud-leadership", "version": 99})
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
    s = server._twin_impl("status", {})
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
    server._twin_impl("submit", {"work_type": "aud-leadership", "prompt_md": "# 对领导要简洁白话",
                           "model": "m"})
    r = server._twin_impl("task_start", {"brief": "B", "work_type": "周报", "audience": "高层"})
    assert r["audience_profile_md"] == "# 对领导要简洁白话"
    assert "画像 v1" in r["audience_profile_note"]
    assert "本类型增补 > 类型 persona > 受众画像" in r["audience_profile_note"]


def test_task_start_audience_proto(monkeypatch):
    """画像未编出：雏形垫底（排除本类型行，防与增补重复）。"""
    _stub_read(monkeypatch)
    server._twin_impl("submit", {"work_type": "PPT", "prompt_md": "# p", "model": "m"})
    _mk_uncompiled([901])  # 周报+领导（本类型外）
    conn = db.connect()
    ppt_dims = {"work_type": {"ok": True, "code": "presentation", "raw": "PPT"},
                "audience": {"ok": True, "code": "leadership", "raw": "领导"},
                "purpose": {"ok": True, "code": "sync_info", "raw": "同步"}}
    db.record_evidence(conn, 902, ppt_dims)  # 本类型行：不进雏形（在增补里）
    conn.close()
    r = server._twin_impl("task_start", {"brief": "B", "work_type": "PPT", "audience": "高层"})
    assert [e["id"] for e in r["audience_profile_proto"]] == [901]
    assert "尚无该受众画像" in r["audience_profile_note"]
    assert [e["id"] for e in r["persona_supplement"]] == [902]


def test_task_start_no_audience_no_profile(monkeypatch):
    """audience 未传/未归一 → 无画像字段（软降级）。"""
    r = server._twin_impl("task_start", {"brief": "B", "work_type": "周报"})
    for k in ("audience_profile_md", "audience_profile_proto"):
        assert k not in r


def test_task_resume_injects_audience_profile():
    server._twin_impl("submit", {"work_type": "aud-leadership", "prompt_md": "# 画像",
                           "model": "m"})
    t1 = server._twin_impl("task_start", {"brief": "B", "work_type": "周报", "audience": "高层"})
    r = server._twin_impl("task_resume", {"task_id": t1["task_id"]})
    assert r["audience_profile_md"] == "# 画像"


# ---- v0.3.6 受众画像 ③：compile 参考节 + ④画像素材 ----

def test_compile_material_includes_audience_profiles(monkeypatch):
    """类型编译素材包含已有受众画像 + 守门句；无画像时提示待生成。"""
    _stub_read(monkeypatch)
    server._twin_impl("submit", {"work_type": "aud-leadership", "prompt_md": "# 简洁白话",
                           "model": "m"})
    _mk_uncompiled([921])  # 周报 + 领导：让类型证据能关联到该受众
    r = server._twin_impl("compile", {"work_type": "周报"})
    assert "同受众跨类型偏好参考" in r["material"]
    assert "aud-leadership 画像 v1" in r["material"] and "# 简洁白话" in r["material"]
    assert "守门" in r["material"] and "格式与结构仍以本类型证据为准" in r["material"]
    r2 = server._twin_impl("compile", {"work_type": "PPT"})
    assert "暂无" in r2["material"]


def test_compile_audience_mode_material(monkeypatch):
    """compile(aud-x)：受众画像素材包——全量证据（含已 compiled）、画像规则。"""
    _stub_read(monkeypatch)
    _mk_uncompiled([911])
    server._twin_impl("submit", {"work_type": "周报", "prompt_md": "# v1", "model": "m",
                           "source_memory_ids": [911]})
    r = server._twin_impl("compile", {"work_type": "aud-leadership"})
    assert r["ok"] and r["work_type"] == "aud-leadership"
    assert "受众画像素材包" in r["material"]
    assert "只保留对该受众稳定成立的口径类偏好" in r["material"]
    assert "[911]" in r["material"]  # 已 compiled 的证据也在画像素材里
    assert "条件段" in r["material"]


# ---- v0.3.6 受众画像 ④：触发器 ----

def test_audience_stale_trigger():
    """证据数≠画像吸收数（或无画像）→ stale；吸收齐 → 清；新证据 → 再 stale。"""
    from mema_twin import sink
    import unittest.mock as _mock
    with _mock.patch.object(sink, "read_memory",
                            lambda mid, workspace=None, client=None:
                            {"ok": True, "data": {"memory": {"id": mid, "subject": "s",
                                                             "content": "偏好"}}}):
        _mk_uncompiled([931, 932])
        s1 = server._twin_impl("status", {})
        assert s1["audience_stale"].get("leadership") == {"evidence": 2, "profile_evidence": None}
        server._twin_impl("submit", {"work_type": "aud-leadership", "prompt_md": "# 画像",
                               "model": "m", "source_memory_ids": [931, 932]})
        s2 = server._twin_impl("status", {})
        assert "leadership" not in s2["audience_stale"]
        _mk_uncompiled([933])
        s3 = server._twin_impl("status", {})
        assert s3["audience_stale"]["leadership"] == {"evidence": 3, "profile_evidence": 2}


# ---- v0.3.6 受众画像 ⑤：write 受众 scope ----

def test_write_audience_scope(monkeypatch):
    """scope=audience：work_type 可省略，证据落 aud- 行、tag 走 twin:aud 命名空间。"""
    from mema_twin import sink
    captured = {}
    def fake_remember(content, subject, tags, workspace, source_ref="", event_time="", client=None):
        captured["tags"] = tags
        return {"ok": True, "data": {"id": 941}}
    monkeypatch.setattr(sink, "remember", fake_remember)
    r = server._twin_impl("write", {"content": "对高层汇报永远要简洁白话",
                              "audience": "高层", "purpose": "同步",
                              "scope": "audience"})
    assert r["ok"] and r["evidence_id"] == 941
    assert r["dimensions"]["work_type"]["code"] == "aud-leadership"
    assert "twin:aud:leadership" in captured["tags"]
    assert not any(t.startswith("twin:wt:aud-") for t in captured["tags"])
    conn = db.connect()
    rows = db.uncompiled_evidence(conn, "aud-leadership")
    conn.close()
    assert [x["memory_id"] for x in rows] == [941]


def test_write_audience_scope_validations(monkeypatch):
    # scope 白名单
    r = server._twin_impl("write", {"content": "x", "work_type": "周报", "audience": "高层",
                              "purpose": "同步", "scope": "global"})
    assert r.get("ok") is False and r.get("field") == "scope"
    # audience 未归一 → 显式打回（不落 stranded 证据）
    from mema_twin import sink
    monkeypatch.setattr(sink, "remember",
                        lambda *a, **k: {"ok": True, "data": {"id": 1}})
    r2 = server._twin_impl("write", {"content": "x", "audience": "外星领导",
                               "purpose": "同步", "scope": "audience"})
    assert r2.get("ok") is False and r2.get("error") == "unmatched_value"
    assert "audience" in r2["fields"] and r2["fields"]["audience"]["candidates"]


# ---- v0.3.6 轮1 review 修复的回归 ----

def test_aud_submit_derived_nonconsuming_with_warning(monkeypatch):
    """aud- 落版：derived、不 mark_compiled、漏列 id 时当场警告（stale 会持续触发）。"""
    _stub_read(monkeypatch)
    _mk_uncompiled([951, 952])
    conn = db.connect()
    before = {r["memory_id"]: r["status"] for r in conn.execute(
        "SELECT memory_id, status FROM twin_evidence").fetchall()}
    conn.close()
    r = server._twin_impl("submit", {"work_type": "aud-leadership", "prompt_md": "# 画像",
                               "model": "m", "source_memory_ids": [951]})
    assert r["ok"] and r["derived"] is True
    assert any("共 2 条证据" in w for w in r["warnings"])
    conn = db.connect()
    after = {r2["memory_id"]: r2["status"] for r2 in conn.execute(
        "SELECT memory_id, status FROM twin_evidence").fetchall()}
    conn.close()
    assert after == before  # 证据未被消耗
    assert "compare_hint" not in r


def test_rollback_audience_profile():
    server._twin_impl("submit", {"work_type": "aud-leadership", "prompt_md": "# 画像v1",
                           "model": "m", "source_memory_ids": [1]})
    server._twin_impl("submit", {"work_type": "aud-leadership", "prompt_md": "# 画像v2",
                           "model": "m", "source_memory_ids": [1]})
    r = server._twin_impl("rollback", {"work_type": "aud-leadership"})
    assert r["ok"] and r["version"] == 1
    g = server._twin_impl("get", {"work_type": "aud-leadership"})
    assert g["prompt_md"] == "# 画像v1"


def test_aud_scheduled_submit_refreshes_night_signal():
    from mema_twin import scan
    assert scan.scan_notice() is not None
    server._twin_impl("submit", {"work_type": "aud-leadership", "prompt_md": "# 画像",
                           "model": "m", "origin": "scheduled", "source_memory_ids": []})
    assert scan.scan_notice() is None  # 受众型夜间在转也消提醒


def test_stale_ignores_worktype_null_rows(monkeypatch):
    """work_type 待裁的滞留证据不进 stale 计数（否则夜夜重编永不清零）。"""
    conn = db.connect()
    from mema_twin import normalize
    r = {"ok": False, "kind": "work_type", "raw": "灵能审计年报", "code": None}
    db.record_evidence(conn, 961, {
        "work_type": r, "audience": {"ok": True, "code": "leadership", "raw": "领导"},
        "purpose": {"ok": True, "code": "sync_info", "raw": "同步"}})
    conn.close()
    s = server._twin_impl("status", {})
    assert "leadership" not in s["audience_stale"]


def test_unknown_aud_suffix_rejected_everywhere():
    for action in ("get", "compile", "submit", "rollback"):
        payload = {"work_type": "aud-nope"}
        if action == "submit":
            payload["prompt_md"] = "# x"
        r = server._twin_impl(action, payload)
        assert r.get("ok") is False and "unknown audience" in r.get("reason", ""), action


def test_proto_truncation_and_partial_skips(monkeypatch):
    from mema_twin import sink
    def fake(mid, workspace=None, client=None):
        if mid == 976:  # 在最新 5 条窗口内失败：进 skipped 而非无声消失
            return {"ok": False, "error": "not_found"}
        return {"ok": True, "data": {"memory": {"id": mid, "subject": "s",
                                                "content": f"偏好{mid}"}}}
    monkeypatch.setattr(sink, "read_memory", fake)
    conn = db.connect()
    for mid in range(971, 979):  # 8 条跨类型证据（971-978），截断只留最新 5（974-978）
        db.record_evidence(conn, mid, {
            "work_type": {"ok": True, "code": "presentation", "raw": "PPT"},
            "audience": {"ok": True, "code": "leadership", "raw": "领导"},
            "purpose": {"ok": True, "code": "sync_info", "raw": "s"}})
    conn.close()
    r = server._twin_impl("task_start", {"brief": "B", "work_type": "周报", "audience": "高层"})
    proto = r["audience_profile_proto"]
    assert [e["id"] for e in proto] == [974, 975, 977, 978]
    assert r["audience_profile_skipped"] == [{"memory_id": 976, "reason": "not_found"}]


def test_profiled_audience_not_crowded_out(monkeypatch):
    """无画像的高频受众不挤掉有画像的低频受众（先过滤后 LIMIT）。"""
    _stub_read(monkeypatch)
    server._twin_impl("submit", {"work_type": "aud-leadership", "prompt_md": "# 领导画像",
                           "model": "m"})
    conn = db.connect()
    aud_l = {"ok": True, "code": "leadership", "raw": "领导"}
    aud_p = {"ok": True, "code": "team_peers", "raw": "同事"}
    for i, aud in enumerate([aud_p] * 3 + [aud_l]):
        db.record_evidence(conn, 980 + i, {
            "work_type": {"ok": True, "code": "work_report", "raw": "周报"},
            "audience": aud,
            "purpose": {"ok": True, "code": "sync_info", "raw": "s"}})
    conn.close()
    r = server._twin_impl("compile", {"work_type": "周报"})
    assert "aud-leadership 画像 v1" in r["material"]
    assert "aud-team_peers" not in r["material"]


def test_write_scope_ignores_supplied_worktype(monkeypatch):
    from mema_twin import sink
    captured = {}
    def fake_remember(content, subject, tags, workspace, source_ref="", event_time="", client=None):
        captured["subject"] = subject
        return {"ok": True, "data": {"id": 991}}
    monkeypatch.setattr(sink, "remember", fake_remember)
    r = server._twin_impl("write", {"content": "对高层要白话", "work_type": "周报",
                              "audience": "高层", "purpose": "同步",
                              "scope": "audience"})
    assert r["ok"] and r["dimensions"]["work_type"]["code"] == "aud-leadership"
    assert "受众级偏好" in captured["subject"]


# ---- v0.3.8 归一门 / 交付流收口 / 删双跑 ----

def test_task_start_gate_no_half_task():
    """打回发生在建档之前：twin_tasks 行数不变，票据在场。"""
    conn = db.connect()
    before = conn.execute("SELECT COUNT(*) AS c FROM twin_tasks").fetchone()["c"]
    conn.close()
    r = server._twin_impl("task_start", {"brief": "B", "work_type": "灵能审计年报",
                                  "audience": "外星领导", "purpose": "数据整理汇总"})
    assert r.get("error") == "unmatched_value"
    conn = db.connect()
    after = conn.execute("SELECT COUNT(*) AS c FROM twin_tasks").fetchone()["c"]
    rows = {(x["type_kind"], x["raw_value"]) for x in db.list_pending(conn)}
    conn.close()
    assert after == before  # 无半建任务
    assert rows == {("work_type", "灵能审计年报"), ("audience", "外星领导"), ("purpose", "数据整理汇总")}


def test_task_start_gate_optional_dims_absent_ok():
    """audience/purpose 可选：不给值不进门、不产票据，照常建档。"""
    r = server._twin_impl("task_start", {"brief": "B", "work_type": "周报"})
    assert r["ok"] and r["dimensions"]["audience"]["ok"] is False
    conn = db.connect()
    assert db.list_pending(conn) == []
    conn.close()


def test_write_multi_miss_all_at_once(monkeypatch):
    """多维未命中一次性全报（含 scope=audience 分支），不逐个往返。"""
    from mema_twin import sink
    monkeypatch.setattr(sink, "remember", lambda *a, **k: {"ok": True, "data": {"id": 1}})
    r = server._twin_impl("write", {"content": "c", "work_type": "aaa未知",
                            "audience": "bbb未知", "purpose": "ccc未知"})
    assert r.get("error") == "unmatched_value" and set(r["fields"]) == {
        "work_type", "audience", "purpose"}
    r2 = server._twin_impl("write", {"content": "c", "audience": "ddd未知",
                            "purpose": "同步", "scope": "audience"})
    assert r2.get("error") == "unmatched_value" and set(r2["fields"]) == {"audience"}


def test_gate_closed_loop_map_then_retry(monkeypatch):
    """票据 → map（别名学习）→ 原值重试命中，终身只问一次。"""
    from mema_twin import sink
    monkeypatch.setattr(sink, "remember",
                        lambda *a, **k: {"ok": True, "data": {"id": 77}})
    r = server._twin_impl("write", {"content": "c", "work_type": "数据筛选",
                            "audience": "本人", "purpose": "沉淀"})
    assert r.get("error") == "unmatched_value"
    pid = r["fields"]["work_type"]["pending_id"]
    rr = server._twin_impl("resolve", {"pending_id": pid, "decision": "map",
                               "code": "data_analysis"})
    assert rr["ok"]
    r2 = server._twin_impl("write", {"content": "c", "work_type": "数据筛选",
                            "audience": "本人", "purpose": "沉淀"})
    assert r2["ok"] and r2["dimensions"]["work_type"]["code"] == "data_analysis"
    assert "pending" not in r2  # 成功响应零 pending 键


def test_gate_reject_decision_note():
    """reject 裁定返回放弃指引（不得拿原值重试）。"""
    r = server._twin_impl("write", {"content": "c", "work_type": "不写的类型",
                            "audience": "本人", "purpose": "沉淀"})
    pid = r["fields"]["work_type"]["pending_id"]
    rr = server._twin_impl("resolve", {"pending_id": pid, "decision": "reject"})
    assert rr["ok"] and "不得再拿原值重试" in rr["note"]


def test_canonicalize_visible_in_dynamic_taxonomy():
    """自建码 canonicalize 后 taxonomy 动态清单立即可见（B3 回归锁定）。"""
    r = server._twin_impl("taxonomy", {"kind": "work_type"})
    assert r["ok"] and all(t["code"] != "other" for t in r["types"])
    assert any(t["code"] == "work_report" and "周报" in t["aliases"] for t in r["types"])
    conn = db.connect()
    db.add_canonical(conn, "work_type", "xianxia_doc", "玄幻设定文档", "", "专业服务")
    conn.close()
    r2 = server._twin_impl("taxonomy", {"kind": "work_type"})
    assert any(t["code"] == "xianxia_doc" and t["is_custom"] for t in r2["types"])


def test_submit_file_write_failure_downgraded(monkeypatch):
    """落盘失败（OSError / sqlite3.Error）降级 warning，状态已终态不回滚。"""
    t = flow.insert_task(brief="T", status="planning", dims={})
    def boom_os(tid, md):
        raise OSError("disk full")
    monkeypatch.setattr(flow, "write_deliverable_file", boom_os)
    r = server._twin_impl("task_submit", {"task_id": t["id"], "deliverable_md": "# 稿"})
    assert r["ok"] is True and flow.get_task(t["id"])["status"] == "submitted"
    assert any("写入失败" in w for w in r["warnings"])

    t2 = flow.insert_task(brief="T2", status="planning", dims={})
    def boom_db(tid, md):
        import sqlite3 as _s
        raise _s.OperationalError("database is locked")
    monkeypatch.setattr(flow, "write_deliverable_file", boom_db)
    r2 = server._twin_impl("task_submit", {"task_id": t2["id"], "deliverable_md": "# 稿"})
    assert r2["ok"] is True and any("写入失败" in w for w in r2["warnings"])


def test_submit_deliverable_file_written():
    t = flow.insert_task(brief="T", status="planning", dims={})
    r = server._twin_impl("task_submit", {"task_id": t["id"], "deliverable_md": "# 交付稿"})
    assert r["ok"] and r["deliverable_path"].endswith(f"task-{t['id']}.md")
    from pathlib import Path
    assert Path(r["deliverable_path"]).read_text(encoding="utf-8") == "# 交付稿"


def test_review_actions_retired():
    """task_review / task_pending 已删：unknown action。"""
    r = server._twin_impl("task_review", {"task_id": 1, "verdict": "approved"})
    assert r.get("error") == "invalid_input" and "task_review" not in r["actions"]
    r2 = server._twin_impl("task_pending", {"task_id": 1})
    assert r2.get("error") == "invalid_input"


def test_open_tasks_counts_planning_only():
    """open_tasks 仅数 planning：submitted 终态自动出清。"""
    conn = db.connect()
    conn.execute("INSERT INTO twin_tasks(brief, status, created_at)"
                 " VALUES('legacy', 'submitted', '2026-09-01T00:00:00+00:00')")
    conn.commit()
    conn.close()
    s0 = server._twin_impl("status", {})
    base = s0["open_tasks"]
    t = flow.insert_task(brief="T", status="planning", dims={})
    s1 = server._twin_impl("status", {})
    assert s1["open_tasks"] == base + 1
    server._twin_impl("task_submit", {"task_id": t["id"], "deliverable_md": "x"})
    s2 = server._twin_impl("status", {})
    assert s2["open_tasks"] == base


def test_double_run_meta_families_cleaned():
    """ensure_schema 幂等清理双跑三族 meta；last_scheduled_compile_at 保留。"""
    flow.set_meta("persona_origin:work_report:2", "scheduled")
    flow.set_meta("compare_prev:work_report:2", "1")
    flow.set_meta("compare_offered:work_report:2", "x")
    flow.set_meta("last_scheduled_compile_at", "2026-09-09T00:00:00+00:00")
    flow._schema_ready.clear()
    flow.ensure_schema()
    conn = db.connect()
    keys = {r["key"] for r in conn.execute("SELECT key FROM twin_meta")}
    conn.close()
    assert not any(k.startswith(("persona_origin:", "compare_prev:", "compare_offered:"))
                   for k in keys)
    assert "last_scheduled_compile_at" in keys


def test_write_success_zero_pending_rows(monkeypatch):
    """成功路径零票据、零 pending upsert（defer 机制消亡的行为锁定）。"""
    from mema_twin import sink
    monkeypatch.setattr(sink, "remember",
                        lambda *a, **k: {"ok": True, "data": {"id": 88}})
    r = server._twin_impl("write", {"content": "c", "work_type": "周报",
                            "audience": "高层", "purpose": "同步"})
    assert r["ok"] and "pending" not in r
    conn = db.connect()
    assert db.list_pending(conn) == []
    conn.close()


def test_submit_file_written_from_fresh_reread(monkeypatch):
    """轮1 P2-2：落盘内容取自库内重读值，不是请求参数（review#5 并发回归）。"""
    t = flow.insert_task(brief="T", status="planning", dims={})
    real_get = flow.get_task
    calls = {"n": 0}

    def fake_get(tid):
        d = real_get(tid)
        if calls["n"] >= 1:  # submit 流程内的重读（建档读之外第一次）
            d = dict(d)
            d["deliverable_md"] = "# 并发更新后的稿"
        calls["n"] += 1
        return d
    monkeypatch.setattr(flow, "get_task", fake_get)
    r = server._twin_impl("task_submit", {"task_id": t["id"], "deliverable_md": "# 旧稿"})
    assert r["ok"]
    from pathlib import Path
    assert Path(r["deliverable_path"]).read_text(encoding="utf-8") == "# 并发更新后的稿"


def test_gate_closed_loop_canonicalize_then_retry(monkeypatch):
    """canonicalize 闭环（_twin_impl 层）：新码即刻入列、原值重试命中。"""
    from mema_twin import sink
    monkeypatch.setattr(sink, "remember",
                        lambda *a, **k: {"ok": True, "data": {"id": 79}})
    r = server._twin_impl("write", {"content": "c", "work_type": "玄幻设定集",
                            "audience": "本人", "purpose": "沉淀"})
    assert r.get("error") == "unmatched_value"
    pid = r["fields"]["work_type"]["pending_id"]
    rr = server._twin_impl("resolve", {"pending_id": pid, "decision": "canonicalize",
                               "new_type": {"code": "xianxia_doc", "zh": "玄幻设定文档",
                                            "en": "", "domain": "专业服务"}})
    assert rr["ok"]
    r2 = server._twin_impl("write", {"content": "c", "work_type": "玄幻设定集",
                            "audience": "本人", "purpose": "沉淀"})
    assert r2["ok"] and r2["dimensions"]["work_type"]["code"] == "xianxia_doc"
    assert "persona_compare_offer" not in r2  # 双跑已删，任何响应不再出现


# ---- 轮2 对抗性 review 修复的回归 ----

def test_gate_reject_concurrent_resolution_wins():
    """P3-1 竞窗收窄：miss 后他方已 map，gate_reject 重查命中→返回 None、
    dims 就地改写、不落票据。"""
    from mema_twin import sink, normalize as nz
    conn = db.connect()
    m = nz.normalize_value("work_type", "竞窗测试值", conn)
    conn.close()
    assert m.get("ok") is False
    conn = db.connect()
    db.append_alias(conn, "work_type", "research_report", "竞窗测试值")
    conn.close()
    resp = nz.gate_reject([m])
    assert resp is None and m["ok"] is True and m["code"] == "research_report"
    conn = db.connect()
    assert [p for p in db.list_pending(conn) if p["raw_value"] == "竞窗测试值"] == []
    conn.close()


def test_append_alias_seeds_missing_builtin_row(tmp_path, monkeypatch):
    """P2-3：既有库缺新内置码的行（表非空即跳过的播种形态），map 到它不再炸
    unknown canonical——_ensure_type_row 补播。"""
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(tmp_path / "t.sqlite3"))
    from mema_twin import db as twin_db
    conn = twin_db.connect()
    conn.execute("DELETE FROM twin_types WHERE code='meeting_minutes'")
    conn.commit()
    conn.close()
    conn = twin_db.connect()
    db.append_alias(conn, "work_type", "meeting_minutes", "跨部门纪要")
    r = conn.close() or twin_db.connect()
    from mema_twin import normalize as nz
    assert nz.normalize_value("work_type", "跨部门纪要", r)["code"] == "meeting_minutes"
    r.close()


def test_legacy_deadend_wording():
    """P3-2：存量 approved/rejected 死端不再指路 task_revise 打转，直说无迁移入口。"""
    conn = db.connect()
    conn.execute("INSERT INTO twin_tasks(brief, status, created_at)"
                 " VALUES('legacy approved', 'approved', '2026-09-01T00:00:00+00:00')")
    tid = conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    conn.commit()
    conn.close()
    r = server._twin_impl("task_resume", {"task_id": tid})
    assert r.get("ok") is False and "无迁移入口" in r.get("reason", "")
    r2 = server._twin_impl("task_revise", {"task_id": tid, "revision_reason": "x"})
    assert r2.get("ok") is False and "无迁移入口" in r2.get("reason", "")


# ---- v0.3.10 write 任务上下文维度继承（#959）----

def _stub_remember_capture(monkeypatch):
    """capture sink.remember（防联网），返回 captured dict（tags/memory id）。"""
    from mema_twin import sink
    captured = {}

    def fake_remember(content, subject, tags, workspace, source_ref="", event_time="", client=None):
        captured["tags"] = tags
        captured["content"] = content
        return {"ok": True, "data": {"id": 777}}
    monkeypatch.setattr(sink, "remember", fake_remember)
    return captured


def test_write_inherits_all_dims_from_task(monkeypatch):
    """task_id + 仅 content：三维度全部沿用任务行（task_start 已归一的 canonical
    code），归一门零打扰；响应 dims_inherited 列出继承项；审计面不变（twin:*
    tags + twin_evidence 三 code 列）。"""
    captured = _stub_remember_capture(monkeypatch)
    t = server._twin_impl("task_start", {"brief": "B", "work_type": "周报",
                                          "audience": "高层", "purpose": "同步"})
    assert t["ok"]
    tid = t["task_id"]
    r = server._twin_impl("write", {"content": "偏好内容", "task_id": tid})
    assert r["ok"] is True
    assert sorted(r["dims_inherited"]) == ["audience", "purpose", "work_type"]
    assert r["dimensions"]["work_type"]["code"] == "work_report"
    assert r["dimensions"]["audience"]["code"] == "leadership"
    assert r["dimensions"]["purpose"]["code"] == "sync_info"
    assert "twin:wt:work_report" in captured["tags"]
    assert "twin:au:leadership" in captured["tags"]
    assert "twin:pu:sync_info" in captured["tags"]
    conn = db.connect()
    row = conn.execute("SELECT work_type, audience, purpose FROM twin_evidence"
                       " WHERE memory_id=777").fetchone()
    conn.close()
    assert row["work_type"] == "work_report" and row["audience"] == "leadership"


def test_write_explicit_dims_override_inheritance(monkeypatch):
    """显式传入优先：work_type 显式给值则不继承；未给的 audience/purpose 继承。"""
    _stub_remember_capture(monkeypatch)
    t = server._twin_impl("task_start", {"brief": "B", "work_type": "周报",
                                          "audience": "高层", "purpose": "同步"})
    tid = t["task_id"]
    r = server._twin_impl("write", {"content": "c", "task_id": tid, "work_type": "PPT"})
    assert r["ok"] is True
    assert r["dims_inherited"] == ["audience", "purpose"]
    assert r["dimensions"]["work_type"]["code"] == "presentation"


def test_write_task_not_found(monkeypatch):
    r = server._twin_impl("write", {"content": "c", "task_id": 99999,
                                     "work_type": "周报", "audience": "高层", "purpose": "同步"})
    assert r["ok"] is False and r["error"] == "invalid_input" and r["field"] == "task_id"
    assert "99999" in r["reason"]


def test_write_malformed_task_id_types():
    for bad in (True, 1.9, [1], {"x": 1}, "abc", "12a"):
        r = server._twin_impl("write", {"content": "c", "task_id": bad,
                                         "work_type": "周报", "audience": "高层", "purpose": "同步"})
        assert r.get("ok") is False and r.get("field") == "task_id", bad


def test_write_task_null_dim_falls_back_required(monkeypatch):
    """任务行维度可空（task_start 的 audience/purpose 可选）：继承不到时回退
    required 报错且说明来自任务空维度，不是静默猜。"""
    _stub_remember_capture(monkeypatch)
    t = server._twin_impl("task_start", {"brief": "B", "work_type": "周报"})
    tid = t["task_id"]
    r = server._twin_impl("write", {"content": "c", "task_id": tid})
    assert r["ok"] is False and r["error"] == "invalid_input" and r["field"] == "audience"
    assert "继承不到" in r["reason"] and f"#{tid}" in r["reason"]


def test_write_scope_audience_with_task_inheritance(monkeypatch):
    """scope=audience + task_id：audience/purpose 从任务继承，work_type 不继承
    （受众级偏好不绑工种），落 aud-{code} 证据行。"""
    captured = _stub_remember_capture(monkeypatch)
    t = server._twin_impl("task_start", {"brief": "B", "work_type": "周报",
                                          "audience": "高层", "purpose": "同步"})
    tid = t["task_id"]
    r = server._twin_impl("write", {"content": "c", "scope": "audience",
                                     "task_id": tid})
    assert r["ok"] is True
    assert sorted(r["dims_inherited"]) == ["audience", "purpose"]
    assert r["dimensions"]["work_type"]["code"] == "aud-leadership"
    assert "twin:au:leadership" in captured["tags"]
    assert not any(x.startswith("twin:wt:") for x in captured["tags"])  # 任务行 work_type 不进 tags
    conn = db.connect()
    row = conn.execute("SELECT work_type FROM twin_evidence WHERE memory_id=777").fetchone()
    conn.close()
    assert row["work_type"] == "aud-leadership"


def test_write_without_task_id_unchanged(monkeypatch):
    """裸写（不传 task_id）路径不变：缺维度照旧 required，归一门照旧生效。"""
    _stub_remember_capture(monkeypatch)
    r = server._twin_impl("write", {"content": "c"})
    assert r["ok"] is False and r["error"] == "invalid_input" and r["field"] == "work_type"
    assert r["reason"] == "required" and "dims_inherited" not in r


def test_write_blank_dim_treated_as_missing_inherits(monkeypatch):
    """显式空串/纯空白维度按"缺失"处理走继承（与 task_start 判空同款口径，
    裸写时空串会打回 required——两径差异由此测试锁定）。"""
    _stub_remember_capture(monkeypatch)
    t = server._twin_impl("task_start", {"brief": "B", "work_type": "周报",
                                          "audience": "高层", "purpose": "同步"})
    r = server._twin_impl("write", {"content": "c", "task_id": t["task_id"],
                                     "work_type": "   ", "audience": "",
                                     "purpose": "同步"})  # purpose 显式：不进继承清单
    assert r["ok"] is True
    assert sorted(r["dims_inherited"]) == ["audience", "work_type"]
    assert r["dimensions"]["work_type"]["code"] == "work_report"
