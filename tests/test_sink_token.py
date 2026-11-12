"""sink 收缩后的身份通道面（v0.5.0 拆分 #1604）。

数据面（remember/read/find/review_conflicts + notices 透传 + token 透传）
已整体退役——本文件钉住三件事：
1. 数据面函数删除后 import 无死引用（hasattr 反向回归）；
2. 启动校验只查宿主身份 env（MEMA_TWIN_CLIENT_ID），token env 不再校验
   （数据面没了，MEMA_TWIN_MEMA_TOKEN 仅剩多租户互斥校验语义）；
3. whoami 端点推导（…/mcp → …/whoami）与三态错误类仍在（多租户路由面，
   三态行为细节在 tests/test_tenant.py）。
"""
import urllib.request

import pytest

from mema_twin import server, sink


# ---- 数据面函数删除（sink 收缩回归）----------------------------------


def test_sink_data_plane_functions_removed():
    """remember/read_memory/find/review_conflicts/notices/token 透传整体退役：
    函数不存在（运行时调用点已删的断言见 test_v050_separation.py 的
    status/compile 响应键检查）。"""
    for name in ("remember", "read_memory", "find", "review_conflicts",
                 "_call", "_headers", "_drain_notices", "_parse_sse",
                 "collect_notices", "reset_notices", "set_request_token",
                 "reset_request_token", "_request_token", "_env_token",
                 "_env_token_header", "_AGENT_ID"):
        assert not hasattr(sink, name), name
    for name in ("SinkError", "SinkAuthError", "SinkUnavailableError",
                 "whoami", "whoami_url", "_env_client"):
        assert hasattr(sink, name), name


def test_whoami_url_derived_from_mcp_endpoint(monkeypatch):
    """身份端点由 MCP 端点推导：…/mcp → …/whoami（非 /mcp 端点原样拼）。"""
    monkeypatch.setenv("MEMA_TWIN_MEMA_URL", "http://127.0.0.1:8000/mcp")
    assert sink.whoami_url() == "http://127.0.0.1:8000/whoami"
    monkeypatch.setenv("MEMA_TWIN_MEMA_URL", "http://127.0.0.1:8000/nested/mcp/")
    assert sink.whoami_url() == "http://127.0.0.1:8000/nested/whoami"


# ---- 宿主身份 env 校验（唯一存续的 mema 相关 env 校验）----------------


def test_env_client_dirty_rejected_clean_accepted(monkeypatch):
    """MEMA_TWIN_CLIENT_ID 脏值就地打回 ValueError，合法值原样返回。"""
    monkeypatch.setenv("MEMA_TWIN_CLIENT_ID", "has space")
    with pytest.raises(ValueError, match="MEMA_TWIN_CLIENT_ID"):
        sink._env_client()
    monkeypatch.setenv("MEMA_TWIN_CLIENT_ID", "zcode")
    assert sink._env_client() == "zcode"
    monkeypatch.delenv("MEMA_TWIN_CLIENT_ID", raising=False)
    assert sink._env_client() == "zcode"  # 缺省回落


def _run_main(monkeypatch, tmp_path):
    """跑 server.main() 但不让 stdio 真起来（stub mcp.run 吸收调用）。"""
    monkeypatch.setenv("MEMA_TWIN_UPDATE_CHECK", "0")
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(tmp_path / "twin.sqlite3"))
    monkeypatch.setenv("MEMA_TWIN_PROMPTS_DIR", str(tmp_path / "prompts"))
    monkeypatch.setenv("MEMA_TWIN_DELIVERABLES_DIR", str(tmp_path / "deliverables"))
    calls = []
    monkeypatch.setattr(server.mcp, "run", lambda transport=None: calls.append(transport))
    server.main()
    return calls


def test_startup_validates_client_env_only(monkeypatch, tmp_path):
    """启动即校验宿主身份 env：脏值 SystemExit，不留给运行期中途炸。"""
    monkeypatch.setenv("MEMA_TWIN_CLIENT_ID", "bad token")
    with pytest.raises(SystemExit) as ei:
        _run_main(monkeypatch, tmp_path)
    assert "MEMA_TWIN_CLIENT_ID" in str(ei.value)


def test_startup_no_longer_validates_token_env(monkeypatch, tmp_path):
    """v0.5.0：MEMA_TWIN_MEMA_TOKEN 数据面退役——脏 token env 不再阻断启动
    （该 env 只剩多租户互斥校验语义，default 形态无人消费）。"""
    monkeypatch.setenv("MEMA_TWIN_CLIENT_ID", "zcode")
    monkeypatch.setenv("MEMA_TWIN_MEMA_TOKEN", "bad token with space")
    calls = _run_main(monkeypatch, tmp_path)
    assert calls == [None]  # 启动继续（stdio 形态 mcp.run 被调）
