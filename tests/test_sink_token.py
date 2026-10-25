"""云端网关形态的 sink 鉴权与错误面（2026-10-25）。

覆盖三件事：MEMA_TWIN_MEMA_TOKEN 非空时带 Authorization: Bearer、
空/未设时不发头（本地无鉴权 mema 形态不变）、HTTP 被拒（401 等）报
「被拒」而非「不可达」——云端部署首故障是 token 缺失，误报不可达会把
排查引向网络层。
"""
import io
import json
import urllib.error

import pytest

from mema_twin import server, sink


class _FakeResp(io.BytesIO):
    """urlopen 上下文管理器形态。"""

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
        captured["timeout"] = timeout
        if raise_ is not None:
            raise raise_
        return _FakeResp(body if body is not None else _ok_body())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return captured


# ---- token 头 ----

def test_token_env_adds_bearer_header(monkeypatch):
    """云端网关形态：token 非空 → Authorization: Bearer 随请求发出。"""
    monkeypatch.setenv("MEMA_TWIN_MEMA_TOKEN", "twin-test-token-abc.def-123")
    cap = _capture_urlopen(monkeypatch)
    assert sink._call("memory", {"action": "read", "data": {"memory_id": 1}})
    assert cap["req"].get_header("Authorization") == "Bearer twin-test-token-abc.def-123"


def test_token_absent_sends_no_auth_header(monkeypatch):
    """本地无鉴权 mema 形态不变：未设 token 不发 Authorization 头。"""
    monkeypatch.delenv("MEMA_TWIN_MEMA_TOKEN", raising=False)
    cap = _capture_urlopen(monkeypatch)
    sink._call("memory", {"action": "read", "data": {"memory_id": 1}})
    assert cap["req"].get_header("Authorization") is None

    monkeypatch.setenv("MEMA_TWIN_MEMA_TOKEN", "")  # 空串同义
    cap = _capture_urlopen(monkeypatch)
    sink._call("memory", {"action": "read", "data": {"memory_id": 1}})
    assert cap["req"].get_header("Authorization") is None


def test_token_identity_headers_still_present(monkeypatch):
    """鉴权头不替代身份头：X-Mema-Client / X-Mema-Agent-Id 照发（core 硬门）。"""
    monkeypatch.setenv("MEMA_TWIN_MEMA_TOKEN", "test-token-x")
    cap = _capture_urlopen(monkeypatch)
    sink._call("memory", {"action": "read", "data": {"memory_id": 1}}, client="kimi")
    assert cap["req"].get_header("X-mema-client") == "kimi"
    assert cap["req"].get_header("X-mema-agent-id") == "mema-twin"


def test_dirty_token_env_rejected(monkeypatch):
    """脏 token（空白/换行/不可见）就地打回，不把坏头发给 mema。"""
    for bad in ("has space", "a\nb", "tok\tx", "a\r\nX-Injected: 1"):
        monkeypatch.setenv("MEMA_TWIN_MEMA_TOKEN", bad)
        with pytest.raises(ValueError):
            sink._headers()


def test_startup_validates_token_env(monkeypatch):
    """server 启动校验覆盖 token env：脏值 SystemExit，不留给运行期。"""
    monkeypatch.setenv("MEMA_TWIN_UPDATE_CHECK", "0")  # 防 daemon 线程联网
    monkeypatch.setenv("MEMA_TWIN_TRANSPORT", "stdio")
    monkeypatch.setenv("MEMA_TWIN_MEMA_TOKEN", "bad token")
    with pytest.raises(SystemExit) as ei:
        server.main()
    assert "MEMA_TWIN_MEMA_TOKEN" in str(ei.value)


# ---- HTTP 错误面 ----

def test_401_reported_as_rejected_not_unreachable(monkeypatch):
    """401 是被拒（token 问题）不是不可达：报文带 token 指引。"""
    err = urllib.error.HTTPError("http://x/mcp", 401, "Unauthorized", {},
                                 io.BytesIO(b'{"detail":"invalid_token"}'))
    _capture_urlopen(monkeypatch, raise_=err)
    with pytest.raises(sink.SinkError) as ei:
        sink._call("memory", {"action": "read", "data": {"memory_id": 1}})
    msg = str(ei.value)
    assert "401" in msg and "不可达" not in msg
    assert "MEMA_TWIN_MEMA_TOKEN" in msg  # 云端首故障指引
    assert "invalid_token" in msg  # 响应 detail 透出


def test_421_hint_points_at_host_gate(monkeypatch):
    """421 host 门（core invalid_host）带指向性提示，不误报网络问题。"""
    err = urllib.error.HTTPError("http://x/mcp", 421, "Misdirected Request", {},
                                 io.BytesIO(b""))
    _capture_urlopen(monkeypatch, raise_=err)
    with pytest.raises(sink.SinkError) as ei:
        sink._call("memory", {"action": "read", "data": {"memory_id": 1}})
    assert "421" in str(ei.value) and "host" in str(ei.value)


def test_connection_error_still_unreachable(monkeypatch):
    """真连不上（URLError）仍报不可达——语义未被 401 分支吞掉。"""
    _capture_urlopen(monkeypatch, raise_=urllib.error.URLError("refused"))
    with pytest.raises(sink.SinkError) as ei:
        sink._call("memory", {"action": "read", "data": {"memory_id": 1}})
    assert "不可达" in str(ei.value)
