"""偏好记忆读写通道：直连本机（或远端）mema 的 HTTP MCP。

实测（2026-09-02，mema 0.15.2）：端点无状态，tools/call 可不先 initialize；
必须带 X-Mema-Client / X-Mema-Agent-Id 身份头，否则 invalid_mema_identity。
响应为 SSE 帧（event: message / data: {...}），此处只解析 data 行。
"""
from __future__ import annotations

import contextvars
import json
import os
import re
import urllib.error
import urllib.request


class SinkError(RuntimeError):
    pass


# ---- mema notice 透传（v0.3.7）----
#
# mema 的 notice 投递是"每个成功 memory 响应 claim 一条、claim 即 delivered、
# 不重发、非 strict 隔离下不区分 workspace、先到先得"——twin 的内部 mema 调用
# （逐条 read / find）同样会 claim，若只挂在 write 响应上会被内部调用吞掉
# （全量投影后每晚大量 read，必吞）。因此所有 _call 响应携带的 notices 统一
# 收集进 contextvar（并发隔离），由 twin() 入口清零、外层响应统一附带。
_notice_acc: contextvars.ContextVar[list] = contextvars.ContextVar("mema_notices",
                                                                   default=None)


def reset_notices() -> None:
    _notice_acc.set([])


def collect_notices() -> list[dict]:
    lst = _notice_acc.get()
    return list(lst) if lst else []


def _drain_notices(resp) -> None:
    if isinstance(resp, dict):
        ns = resp.get("notices")
        if isinstance(ns, list) and ns:
            lst = _notice_acc.get() or []
            lst.extend(n for n in ns if isinstance(n, dict))
            _notice_acc.set(lst)


def _base_url() -> str:
    return os.environ.get("MEMA_TWIN_MEMA_URL", "http://127.0.0.1:8000/mcp")


# twin 的 mema 身份：agent_id 写死（子 agent 范式，产品内部标识，不对用户暴露，
# 多 Agent 差异只走 client）；client 随调用传入（显式 data.client > http 头，见
# server._effective_client），未传回落 env。env 是配置面兜底层，脏值就地打回
# invalid_input 而非把脏头发给 mema 再吃 JSON-RPC 拒绝。
_AGENT_ID = "mema-twin"
_CLIENT_RE = re.compile(r"^[A-Za-z0-9._:@-]{1,64}$")


def _env_client() -> str:
    value = os.environ.get("MEMA_TWIN_CLIENT_ID", "zcode").strip()
    if not _CLIENT_RE.fullmatch(value):
        raise ValueError(
            f"MEMA_TWIN_CLIENT_ID env 非法（仅 [A-Za-z0-9._:@-]、≤64 字符）: {value[:32]!r}")
    return value


def _headers(client: str | None = None) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "X-Mema-Client": client or _env_client(),
        "X-Mema-Agent-Id": _AGENT_ID,
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


def _call(name: str, arguments: dict, client: str | None = None,
          timeout: int = 30) -> dict:
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    req = urllib.request.Request(_base_url(), data=payload.encode("utf-8"),
                                 headers=_headers(client), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
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
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # 对抗 review#9④：不可解析响应带错误码，别让调用方拿到裸 raw 无从归因
        return {"ok": False, "error": "mema_unparsed_response", "raw_head": text[:200]}
    return parsed


def _call_and_drain(name: str, arguments: dict, client: str | None = None,
                    timeout: int = 30) -> dict:
    """公开通道统一出口：调 _call 后收集响应携带的 notices（drain 在这一层而非
    _call 内——测试 stub _call 时真实 drain 路径仍可被测到）。"""
    resp = _call(name, arguments, client=client, timeout=timeout)
    _drain_notices(resp)
    return resp


def remember(content: str, subject: str, tags: list[str], workspace: str,
             source_ref: str = "", event_time: str = "",
             client: str | None = None) -> dict:
    data = {"content": content, "subject": subject, "tags": tags,
            "source_type": "agent_generated", "workspace": workspace}
    if source_ref:
        data["source_ref"] = source_ref
    if event_time:
        data["event_time"] = event_time
    return _call_and_drain("memory", {"action": "remember", "data": data}, client=client)


def find(query: str, workspace: str | None = None, include_content: bool = True,
         client: str | None = None) -> dict:
    """语义召回。0.15.4 起 find 默认是索引页（无 content），需要正文时必须
    传 include_content=true——compile 兜底路径靠它取偏好全文。"""
    data: dict = {"query": query, "include_content": include_content}
    if workspace:
        data["workspace"] = workspace
    return _call_and_drain("memory", {"action": "find", "data": data}, client=client)


def read_memory(memory_id: int, workspace: str | None = None,
                client: str | None = None) -> dict:
    """按 id 精确取单条全文（0.14+ read 始终返回完整原文）。"""
    data: dict = {"memory_id": int(memory_id)}
    if workspace:
        data["workspace"] = workspace
    return _call_and_drain("memory", {"action": "read", "data": data}, client=client)


def review_conflicts(client: str | None = None) -> dict:
    """查 mema 冲突表（v0.3.7 status 计数用）：短超时——status 是夜间任务第一步，
    mema 抖动时宁可少一个计数也不能拖垮整晚；调用方须自捕 SinkError 软失败。
    limit=500：mema 默认 50 会静默截断计数（评审轮2 P3-2）。"""
    return _call_and_drain("memory_review",
                           {"view": "conflicts", "data": {"limit": 500}},
                           client=client, timeout=10)
