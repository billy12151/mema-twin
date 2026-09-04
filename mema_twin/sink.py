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


def _headers(client: str | None = None) -> dict[str, str]:
    """身份头：agent_id 固定 mema-twin（子 agent 范式，写入者标识）；
    client 标识调用方宿主——多 Agent 共接 HTTP 时随调用传入（write 的 data.client），
    未传回落 env。"""
    return {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "X-Mema-Client": client or os.environ.get("MEMA_TWIN_CLIENT_ID", "zcode"),
        "X-Mema-Agent-Id": os.environ.get("MEMA_TWIN_AGENT_ID", "mema-twin"),
    }


def _parse_sse(body: str) -> dict:
    payloads = [line[len("data:"):].strip()
                for line in body.splitlines() if line.startswith("data:")]
    if not payloads:
        raise SinkError(f"response has no data frame: {body[:200]!r}")
    try:
        return json.loads(payloads[-1])
    except json.JSONDecodeError as e:
        # 不能让 JSONDecodeError 以 ValueError 身份被上层误标为 invalid_input（review#4）
        raise SinkError(f"SSE data 帧不是合法 JSON: {e}") from e


def _call(name: str, arguments: dict, client: str | None = None) -> dict:
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    req = urllib.request.Request(_base_url(), data=payload.encode("utf-8"),
                                 headers=_headers(client), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # URLError 不覆盖读 body 途中超时/连接重置（review#4）
        raise SinkError(f"mema HTTP MCP 不可达（{_base_url()}）: {e}") from e
    msg = _parse_sse(body)
    if isinstance(msg, list):
        raise SinkError("unexpected JSON-RPC batch response")
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
        # 对抗 review#9④：不可解析响应带错误码，别让调用方拿到裸 raw 无从归因
        return {"ok": False, "error": "mema_unparsed_response", "raw_head": text[:200]}


def remember(content: str, subject: str, tags: list[str], workspace: str,
             source_ref: str = "", event_time: str = "",
             client: str | None = None) -> dict:
    data = {"content": content, "subject": subject, "tags": tags,
            "source_type": "agent_generated", "workspace": workspace}
    if source_ref:
        data["source_ref"] = source_ref
    if event_time:
        data["event_time"] = event_time
    return _call("memory", {"action": "remember", "data": data}, client=client)


def find(query: str, workspace: str | None = None, include_content: bool = True) -> dict:
    """语义召回。0.15.4 起 find 默认是索引页（无 content），需要正文时必须
    传 include_content=true——compile 兜底路径靠它取偏好全文。"""
    data: dict = {"query": query, "include_content": include_content}
    if workspace:
        data["workspace"] = workspace
    return _call("memory", {"action": "find", "data": data})


def read_memory(memory_id: int, workspace: str | None = None) -> dict:
    """按 id 精确取单条全文（0.14+ read 始终返回完整原文）。"""
    data: dict = {"memory_id": int(memory_id)}
    if workspace:
        data["workspace"] = workspace
    return _call("memory", {"action": "read", "data": data})
