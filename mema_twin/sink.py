"""网关身份通道（多租户路由）：/whoami 解析 + 错误三态 + 宿主身份 env 校验。

v0.5.0 拆分（#1604）：偏好本体自持 twin.sqlite3 后，mema 数据面通道
（remember/read/find/review_conflicts + notices 透传 + token 透传）整体退役，
本模块只剩多租户路由所必需的三件——whoami（token→租户）、三态错误类
（SinkAuthError/SinkUnavailableError/SinkError，tenant.resolve 的分诊面）、
_env_client（宿主身份 env 兜底的校验单点，flow 任务行归因仍用）。

历史形态备注：数据面时代（v0.3.x-v0.4.x）本模块是直连 mema HTTP MCP 的
读写通道（tools/call + SSE 解析 + X-Mema-Client/Agent-Id 身份头 + Bearer
透传）；2026-11-12 拆分后 twin 与 mema 的唯一交点是「同机网关的 /whoami
端点」（由 MEMA_TWIN_MEMA_URL 推导，…/mcp → …/whoami）。
"""
from __future__ import annotations

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


def _base_url() -> str:
    """网关 MCP 端点（多租户 whoami 推导用）。env 名保留 MEMA_TWIN_MEMA_URL
    （部署面不换名）；单用户 default 形态下本模块不被调用，该 env 可不设。"""
    return os.environ.get("MEMA_TWIN_MEMA_URL", "http://127.0.0.1:8000/mcp")


# twin 的宿主身份 env 兜底：client 随调用传入（显式 data.client > http 头），
# 未传回落 env。env 是配置面兜底层，脏值就地打回 invalid_input 而非让脏头
# 落进审计归因（v0.5.0：唯一存续的 mema 相关 env 校验——身份通道保留）。
_CLIENT_RE = re.compile(r"^[A-Za-z0-9._:@-]{1,64}$")


def _env_client() -> str:
    value = os.environ.get("MEMA_TWIN_CLIENT_ID", "zcode").strip()
    if not _CLIENT_RE.fullmatch(value):
        raise ValueError(
            f"MEMA_TWIN_CLIENT_ID env 非法（仅 [A-Za-z0-9._:@-]、≤64 字符）: {value[:32]!r}")
    return value


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
