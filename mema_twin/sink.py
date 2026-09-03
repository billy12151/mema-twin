"""偏好记忆读写通道：直连本机（或远端）mema 的 HTTP MCP。

实测（2026-09-02，mema 0.15.2）：端点无状态，tools/call 可不先 initialize；
必须带 X-Mema-Client / X-Mema-Agent-Id 身份头，否则 invalid_mema_identity。
响应为 SSE 帧（event: message / data: {...}），此处只解析 data 行。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class SinkError(RuntimeError):
    pass


def _base_url() -> str:
    return os.environ.get("MEMA_TWIN_MEMA_URL", "http://127.0.0.1:8000/mcp")


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "X-Mema-Client": os.environ.get("MEMA_TWIN_CLIENT_ID", "zcode"),
        "X-Mema-Agent-Id": os.environ.get("MEMA_TWIN_AGENT_ID", "mema-twin"),
    }


def _parse_sse(body: str) -> dict:
    payloads = [line[len("data:"):].strip()
                for line in body.splitlines() if line.startswith("data:")]
    if not payloads:
        raise SinkError(f"response has no data frame: {body[:200]!r}")
    return json.loads(payloads[-1])


def _call(name: str, arguments: dict) -> dict:
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    req = urllib.request.Request(_base_url(), data=payload.encode("utf-8"),
                                 headers=_headers(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.URLError as e:
        raise SinkError(f"mema HTTP MCP 不可达（{_base_url()}）: {e}") from e
    msg = _parse_sse(body)
    if "error" in msg:
        raise SinkError(f"mema JSON-RPC error: {msg['error']}")
    result = msg.get("result") or {}
    content = result.get("content") or []
    text = next((c.get("text") for c in content if c.get("type") == "text"), None)
    if text is None:
        return result
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def remember(content: str, subject: str, tags: list[str], workspace: str,
             source_ref: str = "", event_time: str = "") -> dict:
    data = {"content": content, "subject": subject, "tags": tags,
            "source_type": "agent_generated", "workspace": workspace}
    if source_ref:
        data["source_ref"] = source_ref
    if event_time:
        data["event_time"] = event_time
    return _call("memory", {"action": "remember", "data": data})


def find(query: str, workspace: str | None = None) -> dict:
    data: dict = {"query": query}
    if workspace:
        data["workspace"] = workspace
    return _call("memory", {"action": "find", "data": data})
