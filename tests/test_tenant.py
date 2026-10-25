"""多租户 C 形态（2026-10-25）：token 透传、租户路径双态、authorizer 命名
空间强制、入口硬拒矩阵、跨租户隔离。

形态开关 = MEMA_TWIN_USERS_DIR；conftest 全局设了 DB/PROMPTS/DELIVERABLES
env——USERS_DIR 用例里显式 delenv，正是 P1-4（解析点强制互斥）要拦的分脑
场景：绕过启动校验的调用也必须被拦住。
"""
import io
import json
import sqlite3
import urllib.error

import pytest

from mema_twin import db, flow, server, sink, tenant


# ---- 夹具：进入/退出多租户形态（清掉 conftest 的互斥 env）--------------


@pytest.fixture
def users_dir(tmp_path, monkeypatch):
    """网关 users/ 目录形貌：whoami 预建 store 时会产生 users/<uid>/。"""
    for env in ("MEMA_TWIN_DB_PATH", "MEMA_TWIN_PROMPTS_DIR",
                "MEMA_TWIN_DELIVERABLES_DIR", "MEMA_TWIN_PLAYBOOKS_DIR",
                "MEMA_TWIN_MEMA_TOKEN"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("MEMA_TWIN_USERS_DIR", str(tmp_path / "users"))
    for uid in ("1", "2"):
        (tmp_path / "users" / uid).mkdir(parents=True)
    return tmp_path / "users"


def _as_tenant(t: str):
    tenant.set_tenant(t)
    return t


# ---- sink：token 透传优先级 ------------------------------------------


class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _ok_body() -> bytes:
    payload = {"jsonrpc": "2.0", "id": 1,
               "result": {"content": [{"type": "text",
                                       "text": json.dumps({"ok": True, "data": {}})}]}}
    return f"event: message\ndata: {json.dumps(payload)}\n".encode()


def _capture_urlopen(monkeypatch, body=None, raise_=None):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["req"] = req
        if raise_ is not None:
            raise raise_
        return _FakeResp(body if body is not None else _ok_body())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return captured


def test_request_token_relayed_verbatim(monkeypatch):
    """请求头 token 原样转发（含调用方 Bearer 前缀，不重构）。"""
    sink.set_request_token("Bearer relay-tok-123")
    try:
        cap = _capture_urlopen(monkeypatch)
        sink._call("memory", {"action": "read", "data": {"memory_id": 1}})
        assert cap["req"].get_header("Authorization") == "Bearer relay-tok-123"
    finally:
        sink.reset_request_token()


def test_request_token_beats_env(monkeypatch):
    monkeypatch.setenv("MEMA_TWIN_MEMA_TOKEN", "env-tok")
    sink.set_request_token("Bearer req-tok")
    try:
        cap = _capture_urlopen(monkeypatch)
        sink._call("memory", {"action": "read", "data": {"memory_id": 1}})
        assert cap["req"].get_header("Authorization") == "Bearer req-tok"
    finally:
        sink.reset_request_token()


def test_env_token_still_works_without_request_token(monkeypatch):
    """default 形态兜底不变：无请求头时 env 拼前缀。"""
    monkeypatch.setenv("MEMA_TWIN_MEMA_TOKEN", "env-tok")
    cap = _capture_urlopen(monkeypatch)
    sink._call("memory", {"action": "read", "data": {"memory_id": 1}})
    assert cap["req"].get_header("Authorization") == "Bearer env-tok"


# ---- sink：whoami 客户端三态 ----------------------------------------


def _whoami_body(uid=7) -> bytes:
    return json.dumps({"ok": True, "user_id": uid, "name": "owner"}).encode()


def test_whoami_ok_parses_identity(monkeypatch):
    cap = _capture_urlopen(monkeypatch, body=_whoami_body(7))
    info = sink.whoami("Bearer t")
    assert info["user_id"] == 7
    assert cap["req"].full_url.endswith("/whoami")
    assert cap["req"].get_header("Authorization") == "Bearer t"


def test_whoami_401_maps_auth_error_no_token_echo(monkeypatch):
    secret = "Bearer super-secret-token-xyz"
    err = urllib.error.HTTPError("http://x/whoami", 401, "Unauthorized", {},
                                 io.BytesIO(b'{"detail":"invalid"}'))
    _capture_urlopen(monkeypatch, raise_=err)
    with pytest.raises(sink.SinkAuthError) as ei:
        sink.whoami(secret)
    assert "401" in str(ei.value)
    assert secret not in str(ei.value)  # token 零回显


def test_whoami_5xx_maps_unavailable(monkeypatch):
    err = urllib.error.HTTPError("http://x/whoami", 500, "Server Error", {},
                                 io.BytesIO(b"boom"))
    _capture_urlopen(monkeypatch, raise_=err)
    with pytest.raises(sink.SinkUnavailableError):
        sink.whoami("Bearer t")


def test_whoami_network_error_maps_unreachable(monkeypatch):
    _capture_urlopen(monkeypatch, raise_=urllib.error.URLError("refused"))
    with pytest.raises(sink.SinkError) as ei:
        sink.whoami("Bearer t")
    assert not isinstance(ei.value, (sink.SinkAuthError, sink.SinkUnavailableError))


def test_whoami_bad_payload_maps_unavailable(monkeypatch):
    _capture_urlopen(monkeypatch, body=b'{"ok": false}')
    with pytest.raises(sink.SinkUnavailableError):
        sink.whoami("Bearer t")


def test_resolve_maps_user_id(monkeypatch):
    monkeypatch.setattr(sink, "whoami", lambda t: {"ok": True, "user_id": 7})
    assert tenant.resolve("Bearer t") == "u7"


# ---- db 双态与 authorizer -------------------------------------------


def test_users_dir_db_and_mirror_paths(users_dir):
    _as_tenant("u1")
    try:
        assert db.db_path() == users_dir / "1" / "twin.sqlite3"
        conn = db.connect()  # 文件由 twin 创建（自己的账本）；目录已由网关预建
        conn.close()
        assert (users_dir / "1" / "twin.sqlite3").exists()
        assert tenant.mirror_root() == users_dir / "1" / "twin-files"
    finally:
        tenant.reset_tenant()


def test_users_dir_missing_uid_dir_never_created(users_dir, monkeypatch):
    """users/<uid>/ 不存在 → 结构化报错且**绝不代建**（P0-1 C 版：目录由
    网关 /whoami 预建）。"""
    monkeypatch.setattr(tenant, "current_tenant", lambda: "u9")
    with pytest.raises(tenant.TenantError, match="不应由|不代建|预建|不存在"):
        db.db_path()
    assert not (users_dir / "9").exists()


def test_fail_closed_without_tenant(users_dir):
    """USERS_DIR 形态下租户 contextvar 未设 → db.connect() 抛错（P1-2，
    覆盖 audit/normalize 等全部直连点）。"""
    with pytest.raises(tenant.TenantError):
        db.connect()
    assert not (users_dir / "default").exists()


def test_default_mode_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(tmp_path / "twin.sqlite3"))
    monkeypatch.delenv("MEMA_TWIN_USERS_DIR", raising=False)
    assert db.db_path() == tmp_path / "twin.sqlite3"
    db.connect().close()


def test_exclusive_env_raises_at_resolution(users_dir, monkeypatch):
    """互斥在解析点强制（P1-4）：绕过启动校验的调用（如测试继承的镜像
    env）也拦得住。"""
    monkeypatch.setenv("MEMA_TWIN_PROMPTS_DIR", "/tmp/should-not-be-used")
    _as_tenant("u1")
    try:
        with pytest.raises(tenant.TenantError, match="互斥"):
            tenant.mirror_root()
        with pytest.raises(tenant.TenantError, match="互斥"):
            db.db_path()
    finally:
        tenant.reset_tenant()


def test_authorizer_denies_foreign_tables(users_dir):
    _as_tenant("u1")
    try:
        conn = db.connect()
        with pytest.raises(sqlite3.Error):
            conn.execute("CREATE TABLE memories (id INTEGER)")  # core 表名越界
        with pytest.raises(sqlite3.Error):
            conn.execute("CREATE TABLE anything_else (x)")
        with pytest.raises(sqlite3.Error):
            conn.execute("ATTACH DATABASE '/tmp/x.sqlite3' AS x")  # 挂邻居库旁路
        conn.execute("SELECT COUNT(*) AS c FROM sqlite_master").fetchone()  # 内部表只读放行
        conn.close()
    finally:
        tenant.reset_tenant()


def test_two_tenants_fully_isolated(users_dir):
    for uid in ("1", "2"):
        _as_tenant(f"u{uid}")
        try:
            conn = db.connect()
            conn.execute(
                "INSERT INTO twin_pending_values(type_kind, raw_value, created_at)"
                " VALUES('work_type', ?, ?)", (f"raw-{uid}", db.now_iso()))
            conn.commit()
            conn.close()
        finally:
            tenant.reset_tenant()
    _as_tenant("u1")
    try:
        conn = db.connect()
        rows = {r["raw_value"] for r in conn.execute(
            "SELECT raw_value FROM twin_pending_values")}
        assert rows == {"raw-1"}  # u2 的写入不可见
        conn.close()
    finally:
        tenant.reset_tenant()


def test_todos_session_isolated_across_tenants(users_dir):
    """P0-1 回归：同 session 名两租户各写各读（进程内 todo 桶带租户前缀）。"""
    flow._todos_by_session.clear()
    _as_tenant("u1")
    flow.set_session_todos("s1", [{"content": "A 的活", "status": "pending"}])
    _as_tenant("u2")
    assert flow.current_todos("s1") == []  # u1 的清单对 u2 不可见
    flow.set_session_todos("s1", [{"content": "B 的活", "status": "pending"}])
    _as_tenant("u1")
    assert [t["content"] for t in flow.current_todos("s1")] == ["A 的活"]
    flow._todos_by_session.clear()
    tenant.reset_tenant()


# ---- server 入口矩阵 -------------------------------------------------


def _stub_mema_write(monkeypatch):
    def fake_call(name, arguments, client=None, timeout=30):
        return {"ok": True, "data": {"id": 55}}
    monkeypatch.setattr(sink, "_call", fake_call)


def test_users_dir_missing_header_rejected(users_dir):
    """无 http 请求上下文（测试直调）→ 无 Authorization 头 → 除 help 外全拒。"""
    r = server._twin_impl("write", {"content": "c", "work_type": "周报",
                                    "audience": "高层", "purpose": "同步"})
    assert r["ok"] is False and r["error"] == "unauthorized"
    assert "Authorization" in r["reason"]
    assert server._twin_impl("help", {})["ok"] is True  # help 豁免


def test_users_dir_bad_token_unauthorized(users_dir, monkeypatch):
    monkeypatch.setattr(server, "_request_auth_token", lambda: "Bearer bad-tok")
    monkeypatch.setattr(tenant, "resolve",
                        lambda t: (_ for _ in ()).throw(
                            sink.SinkAuthError("网关拒绝该 token（HTTP 401）")))
    r = server._twin_impl("status", {})
    assert r["ok"] is False and r["error"] == "unauthorized"
    assert "bad-tok" not in json.dumps(r)  # token 零回显


def test_users_dir_gateway_5xx_maps_tenant_unavailable(users_dir, monkeypatch):
    monkeypatch.setattr(server, "_request_auth_token", lambda: "Bearer t")
    monkeypatch.setattr(tenant, "resolve",
                        lambda t: (_ for _ in ()).throw(
                            sink.SinkUnavailableError("whoami HTTP 500")))
    r = server._twin_impl("status", {})
    assert r["ok"] is False and r["error"] == "tenant_unavailable"


def test_users_dir_write_lands_in_tenant_db(users_dir, monkeypatch):
    """端到端：透传 + 租户解析 → 偏好证据落该租户库；default 库不产生。"""
    monkeypatch.setattr(server, "_request_auth_token", lambda: "Bearer t")
    monkeypatch.setattr(tenant, "resolve", lambda t: "u1")
    _stub_mema_write(monkeypatch)
    r = server._twin_impl("write", {"content": "云端偏好", "work_type": "周报",
                                    "audience": "高层", "purpose": "同步"})
    assert r["ok"], r
    assert (users_dir / "1" / "twin.sqlite3").exists()
    tenant.set_tenant("u1")
    try:
        conn = db.connect()
        n = conn.execute("SELECT COUNT(*) AS c FROM twin_evidence").fetchone()["c"]
        conn.close()
        assert n == 1
    finally:
        tenant.reset_tenant()
    assert not (users_dir / "2" / "twin.sqlite3").exists()


# ---- 启动校验 --------------------------------------------------------


def _startup(monkeypatch, tmp_path, *, transport="http", extra=None):
    monkeypatch.delenv("MEMA_TWIN_DB_PATH", raising=False)
    monkeypatch.delenv("MEMA_TWIN_PROMPTS_DIR", raising=False)
    monkeypatch.delenv("MEMA_TWIN_DELIVERABLES_DIR", raising=False)
    monkeypatch.delenv("MEMA_TWIN_PLAYBOOKS_DIR", raising=False)
    monkeypatch.delenv("MEMA_TWIN_MEMA_TOKEN", raising=False)
    monkeypatch.setenv("MEMA_TWIN_UPDATE_CHECK", "0")
    monkeypatch.setenv("MEMA_TWIN_USERS_DIR", str(tmp_path / "users"))
    if transport:
        monkeypatch.setenv("MEMA_TWIN_TRANSPORT", transport)
    for k, v in (extra or {}).items():
        monkeypatch.setenv(k, v)


def test_startup_rejects_stdio_in_users_dir(users_dir, monkeypatch):
    _startup(monkeypatch, users_dir.parent, transport=None)  # 未设 → stdio
    with pytest.raises(SystemExit, match="http"):
        server.main()


def test_startup_rejects_static_token(users_dir, monkeypatch):
    _startup(monkeypatch, users_dir.parent,
             extra={"MEMA_TWIN_MEMA_TOKEN": "static-tok"})
    with pytest.raises(SystemExit, match="互斥"):
        server.main()


def test_startup_rejects_mirror_env(users_dir, monkeypatch):
    _startup(monkeypatch, users_dir.parent,
             extra={"MEMA_TWIN_PROMPTS_DIR": "/tmp/x"})
    with pytest.raises(SystemExit, match="互斥"):
        server.main()


def test_startup_rejects_non_gateway_users_dir(users_dir, monkeypatch):
    (users_dir / "not-a-uid").mkdir(exist_ok=True)
    _startup(monkeypatch, users_dir.parent)
    with pytest.raises(SystemExit, match="不像网关|非数字"):
        server.main()


# ---- 对抗轮2 修复回归（P0 路径穿越 / P1 TenantError 逃逸 / sqlite_ 写）----


def test_playbook_mirror_path_traversal_contained(users_dir):
    """P0 回归：key 经 alias 链携带 ../ 时，镜像路径用解析后的 canonical，
    DB 键保留原值。模拟历史污染库（绕过 append_alias 守卫直插别名）。"""
    from mema_twin import playbook_actions, store
    _as_tenant("u1")
    try:
        conn = db.connect()
        conn.execute(
            "INSERT OR IGNORE INTO twin_types(type_kind,code,label_zh,label_en,"
            "domain,aliases,is_custom,status,created_at)"
            " VALUES('work_type','trav_escape_probe','越权探针','','collate',"
            "?,1,'active',?)", (json.dumps(["../../../ESCAPED"]), db.now_iso()))
        conn.commit()
        assert store.resolve_work_type_code(conn, "../../../ESCAPED") == "trav_escape_probe"
        d = playbook_actions._playbook_mirror_dir(conn, "../../../ESCAPED")
        assert d == users_dir / "1" / "twin-files" / "playbooks" / "trav_escape_probe"
        assert ".." not in str(d) and "ESCAPED" not in str(d)
        # global 键不受影响
        assert playbook_actions._playbook_mirror_dir(
            conn, "global") == users_dir / "1" / "twin-files" / "playbooks" / "global"
        conn.close()
    finally:
        tenant.reset_tenant()


def test_append_alias_rejects_path_chars(users_dir):
    """P0 配套：路径字符 raw 不得被归一进体系（resolve 应走 reject）。"""
    _as_tenant("u1")
    try:
        conn = db.connect()
        with pytest.raises(ValueError, match="路径字符"):
            db.append_alias(conn, "work_type", "trav_escape_probe", "../../../x")
        conn.close()
    finally:
        tenant.reset_tenant()


def test_tenant_error_mapped_not_escaped(users_dir, monkeypatch):
    """P1 回归：租户目录缺失的 TenantError 必须落成结构化错误，不裸逃逸。"""
    monkeypatch.setattr(server, "_request_auth_token", lambda: "Bearer t")
    monkeypatch.setattr(tenant, "resolve", lambda t: "u9")  # users/9 不存在
    r = server._twin_impl("status", {})
    assert r["ok"] is False and r["error"] == "tenant_unavailable"
    assert "Traceback" not in r["reason"]


def test_authorizer_denies_writable_schema(users_dir):
    """P2 加固回归：writable_schema PRAGMA 引擎层封死（直写 sqlite_master
    的唯一使能开关；CREATE TABLE 的内部 sqlite_master INSERT 不受影响）。"""
    _as_tenant("u1")
    try:
        conn = db.connect()
        with pytest.raises(sqlite3.Error):
            conn.execute("PRAGMA writable_schema = ON")
        # AUTOINCREMENT 维护（sqlite_sequence）与建表路径不受影响
        conn.execute(
            "INSERT INTO twin_pending_values(type_kind, raw_value, created_at)"
            " VALUES('work_type', 'x', ?)", (db.now_iso(),))
        conn.commit()
        conn.close()
    finally:
        tenant.reset_tenant()
