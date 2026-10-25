"""偏好记忆读写通道：直连本机（或远端）mema 的 HTTP MCP。

实测（2026-09-02，mema 0.15.2）：端点无状态，tools/call 可不先 initialize；
必须带 X-Mema-Client / X-Mema-Agent-Id 身份头，否则 invalid_mema_identity。
响应为 SSE 帧（event: message / data: {...}），此处只解析 data 行。

鉴权端点形态（2026-10-25）：mema 端点带 Bearer 鉴权时（如部署在鉴权网关后，
无 token 即 401），MEMA_TWIN_MEMA_TOKEN 非空随请求带 Authorization: Bearer；
本地 loopback mema 无鉴权，不设即不发——两个形态同一代码路径。
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


class SinkAuthError(SinkError):
    """鉴权被拒（HTTP 401/403）：token 无效/缺失/吊销。调用方据此与
    「网关半可用」「网络不可达」分开映射（多租户入口的三态错误面）。"""


class SinkUnavailableError(SinkError):
    """对端在但答不上来（5xx/响应形态异常）：不是网络问题也不是鉴权问题。"""


# ---- 请求级 token 透传（多租户，2026-10-25）----
#
# 宿主 MCP 配置携带各自的 Authorization 头，server 入口读出后经
# contextvar 传入线程（anyio to_thread 传播），_headers 原样转发——
# 写谁的库由网关按 token 裁决，twin 全程不落盘不落日志。
# 优先级：请求头（原样，含 Bearer 前缀）> MEMA_TWIN_MEMA_TOKEN env
#（拼 Bearer，本地指向鉴权网关的单机形态兜底）> 不发头（本地无鉴权）。
# 线程内副本随线程消亡；入口仍显式清零（对齐 reset_notices 模式）。
_request_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mema_request_token", default=None)


def set_request_token(value: str | None) -> None:
    _request_token.set(value)


def reset_request_token() -> None:
    _request_token.set(None)


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


# token 是给鉴权端点（Bearer）的凭证，不是身份——校验只挡明显脏值
# （空白/换行会直接搞坏 Authorization 头），格式由鉴权方判。
# 空串/未设 = 不发头（本地无鉴权 mema 形态），不是错误。
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~+/=-]{1,4096}$")


def _env_token() -> str:
    value = (os.environ.get("MEMA_TWIN_MEMA_TOKEN") or "").strip()
    if not value:
        return ""
    if not _TOKEN_RE.fullmatch(value):
        # 不回显任何片段：脏 token 往往是真 token 带了粘贴换行/空格，
        # 前缀随报错进日志/journalctl 就是实质泄露（对抗评审轮2 P1）
        raise ValueError(
            f"MEMA_TWIN_MEMA_TOKEN env 非法（仅允许 token 常用字符集、"
            f"长度 1-4096；当前值长度 {len(value)}）")
    return value


def _headers(client: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "X-Mema-Client": client or _env_client(),
        "X-Mema-Agent-Id": _AGENT_ID,
    }
    req_token = _request_token.get()
    if req_token:
        headers["Authorization"] = req_token  # 原样转发（含调用方 Bearer 前缀）
    else:
        env_token = _env_token()
        if env_token:
            headers["Authorization"] = f"Bearer {env_token}"
    return headers


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
    except urllib.error.HTTPError as e:
        # HTTPError 是 URLError 子类，必须先于「不可达」分支捕获——被拒
        # （401 token 错/403 tool 拦/421 host 门）≠ 不可达，云端部署首故障
        # 就是 token 缺失，误报"不可达"会把排查引向网络层。
        detail = ""
        try:
            detail = e.read(2048).decode("utf-8", "replace")[:200]
        except Exception:
            pass
        hint = ""
        if e.code == 401:
            hint = "（token 缺失或无效：鉴权端点形态需 MEMA_TWIN_MEMA_TOKEN）"
        elif e.code == 421:
            hint = "（host 门：检查 MEMA_TWIN_MEMA_URL 指向的端点）"
        raise SinkError(f"mema HTTP {e.code}{hint}（{_base_url()}）: {detail or e.reason}") from e
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


def whoami_url() -> str:
    """网关身份端点：由 MCP 端点推导（…/mcp → …/whoami）。twin 必须与网关
    同机 loopback 直连（/whoami 不在公网 nginx 的 /mcp location 内）。"""
    base = _base_url().rstrip("/")
    if base.endswith("/mcp"):
        base = base[: -len("/mcp")]
    return f"{base}/whoami"


def whoami(token: str, timeout: int = 10) -> dict:
    """网关身份解析（多租户）：GET /whoami + token 原样透传 → {user_id,name}。

    三态错误面（P2 分诊）：401/403 → SinkAuthError（token 被拒）；5xx/响应
    形态异常 → SinkUnavailableError；网络不可达 → SinkError。报文永不回显
    token（值只进请求头，不进任何异常/日志）。"""
    req = urllib.request.Request(
        whoami_url(), method="GET",
        headers={"Accept": "application/json", "Authorization": token})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(4096).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read(2048).decode("utf-8", "replace")[:200]
        except Exception:
            pass
        if e.code in (401, 403):
            raise SinkAuthError(
                f"网关拒绝该 token（HTTP {e.code}）——无效或已吊销，"
                "检查宿主 MCP 配置的 Authorization 头") from e
        raise SinkUnavailableError(
            f"whoami HTTP {e.code}（{whoami_url()}）: {detail or e.reason}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SinkError(f"mema 网关不可达（{whoami_url()}）: {e}") from e
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as e:
        raise SinkUnavailableError(f"whoami 响应非 JSON: {body[:120]!r}") from e
    if not (isinstance(parsed, dict) and parsed.get("ok")
            and isinstance(parsed.get("user_id"), int)):
        raise SinkUnavailableError(f"whoami 响应形态异常: {str(parsed)[:120]}")
    return parsed


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
