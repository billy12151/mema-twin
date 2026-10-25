"""租户上下文与路径单点（多租户 C 形态，2026-10-25）。

形态判定：``MEMA_TWIN_USERS_DIR`` 非空 = 云端多租户形态——twin 账本
（twin.sqlite3）与文件镜像（twin-files/）按租户落在网关 ``users/<uid>/``
下，与该用户的 memory.sqlite3 同目录（备份/恢复/迁移天然成对）；未设 =
default 形态（本地/开源，自有 twin.sqlite3 + env 镜像目录，行为与历史
版本完全一致）。

租户解析：请求 Authorization 头 → 网关 GET /whoami（token 原样透传）
→ user_id → "u<uid>"。**不缓存**——吊销即时生效，网关侧只是一次 sqlite
反查（#1419 不分账口径，whoami 不 bump usage）。

设计不变量（对抗评审 v4 轮采纳）：
- USERS_DIR 形态下路径解析单点在本模块，镜像/DB env 与之**互斥且在
  解析点强制**（conftest 等绕过启动校验的调用也拦得住，P1-4）；
- twin 只在自己的文件上 DDL，绝不打开 memory.sqlite3，绝不创建
  users/<uid>/ 目录（目录由网关 /whoami 预建 store 时产生，P0-1）；
- fail-closed：USERS_DIR 形态下租户 contextvar 未设时 db.connect()
  直接抛错（覆盖 audit/normalize 等全部直连点，P1-2）。
"""
from __future__ import annotations

import contextvars
import os
from pathlib import Path

from . import sink

USERS_DIR_ENV = "MEMA_TWIN_USERS_DIR"

#: USERS_DIR 形态下与之互斥的 env（token 静态化 = 跨租户写风险；镜像/DB
#: 路径 = 分脑：DB 租户化而镜像写全局目录）。启动校验与解析点双重强制。
EXCLUSIVE_ENVS = (
    "MEMA_TWIN_MEMA_TOKEN",
    "MEMA_TWIN_DB_PATH",
    "MEMA_TWIN_PROMPTS_DIR",
    "MEMA_TWIN_DELIVERABLES_DIR",
    "MEMA_TWIN_PLAYBOOKS_DIR",
)

# 进程内租户（"u<uid>"）；default 形态恒为 None。在 _twin_impl 线程内
# 设置——anyio.to_thread 的 context 副本随线程消亡，跨请求无残留；
# 入口仍显式清零（对齐 sink.reset_notices 既有模式）。
_tenant: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mema_tenant", default=None)


class TenantError(RuntimeError):
    """租户形态的结构化拒绝（互斥 env/目录缺失/contextvar 未设）。"""


def users_dir_mode() -> bool:
    return bool((os.environ.get(USERS_DIR_ENV) or "").strip())


def current_tenant() -> str | None:
    return _tenant.get()


def set_tenant(tenant: str) -> None:
    _tenant.set(tenant)


def reset_tenant() -> None:
    _tenant.set(None)


def _users_root() -> Path:
    raw = (os.environ.get(USERS_DIR_ENV) or "").strip()
    if not raw:
        raise TenantError(f"{USERS_DIR_ENV} 未设（多租户形态前提）")
    for env in EXCLUSIVE_ENVS:
        if (os.environ.get(env) or "").strip():
            raise TenantError(
                f"{env} 与 {USERS_DIR_ENV} 互斥（多租户形态 token 随请求透传、"
                "路径按租户收口）")
    return Path(raw)


def resolve(token: str) -> str:
    """token → 租户 id（"u<uid>"）。经网关 /whoami，三态错误原样上抛：
    SinkAuthError（token 被拒）/ SinkUnavailableError（网关半可用）/
    SinkError（不可达）。"""
    info = sink.whoami(token)
    return f"u{int(info['user_id'])}"


def tenant_root() -> Path:
    """当前租户在网关 users/ 下的根目录（twin.sqlite3 与 twin-files/ 的家）。"""
    tenant = current_tenant()
    if not tenant or not tenant.startswith("u") or not tenant[1:].isdigit():
        raise TenantError(
            "多租户形态下租户未解析（入口必须先经 /whoami 设置 contextvar）")
    root = _users_root() / tenant[1:]
    if not root.is_dir():
        # 不代建：目录应由网关 /whoami 预建 store 时产生（P0-1——twin 先建
        # 的目录里若落进 core 的库会被判 unknown generation）。
        raise TenantError(
            f"用户目录不存在：{root}（应由网关 /whoami 预建；twin 不代建）")
    return root


def db_path() -> Path:
    """当前租户的 twin 账本路径（users/<uid>/twin.sqlite3）。"""
    return tenant_root() / "twin.sqlite3"


def mirror_root() -> Path:
    """当前租户的文件镜像根（users/<uid>/twin-files/，命名空间登记见 runbook：
    users/<uid>/ 内 twin* 前缀归 twin，其余归网关/core）。"""
    return tenant_root() / "twin-files"
