"""宿主身份（多 Agent 共接）：X-Mema-Client 头与显式 data.client 的校验合并。

v0.3.8 从 server.py 拆出（单文件 ≤1000 行）。头值读取依赖 FastMCP 请求上下文
（传输面），留在 server.py 于 _twin_impl 入口读一次、经 contextvar 注入本模块
（复用 sink notices 的 contextvar 模式，anyio.to_thread 传播）；动作模块统一调
effective_client(data)，调用点形态与拆分前一致：脏值/重复头/头与 data.client
不一致一律 fail-fast（invalid_input）。
"""
from __future__ import annotations

import contextvars
import ipaddress
import re

_client_header: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mema_twin_client_header", default=None)

# 与 mema X-Mema-Client 同款字符集
_CLIENT_RE = re.compile(r"^[A-Za-z0-9._:@-]{1,64}$")


def set_client_header(value: str | None) -> None:
    """请求头身份注入（server 入口每调用一次；值已过校验或为 None）。"""
    _client_header.set(value)


def client_header() -> str | None:
    return _client_header.get()


def validate_client_value(value: str) -> str:
    """单个 client 值校验（头与 data.client 共用）：未 strip 的值不做修正直接拒
    （mema 同款：normalized != value 即拒）。"""
    if value != value.strip():
        raise ValueError(f"client 非法（含首尾空白）: {value[:32]!r}")
    if not _CLIENT_RE.fullmatch(value):
        raise ValueError(
            f"client 非法（仅 [A-Za-z0-9._:@-]、≤64 字符）: {value[:32]!r}")
    return value


def effective_client(data: dict) -> str | None:
    """本次调用的宿主身份。http 头存在时头是权威（连接身份），显式 data.client
    只能与头一致或省略（mema _identity_mismatch 同款语义，堵跨宿主冒充）；
    stdio 无头时 data.client > env（env 回落由 sink._env_client 校验）。
    非字符串/带首尾空白一律打回。"""
    header = client_header()
    raw = data.get("client")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return header
    if not isinstance(raw, str):
        raise ValueError(f"client 必须是字符串，收到 {type(raw).__name__}")
    validate_client_value(raw)
    if header is not None and raw != header:
        raise ValueError(
            f"client 与连接身份不一致：X-Mema-Client 头为 {header!r}，data.client 为 {raw!r}"
            "（http 模式下以连接头为准，请移除 data.client 或保持一致）")
    return raw


def is_loopback_host(host: str) -> bool:
    """http 绑定白名单：localhost / 127.x / ::1（core request_identity 同款）。"""
    normalized = str(host or "").strip().strip("[]").casefold()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False
