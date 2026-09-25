"""升级提示机制（v0.4 P0，移植 memory_arbiter/update_monitor.py 并适配 twin）。

适配点（#1079 v2-⑤）：版本源不用 PyPI（twin 走 git 安装、GitHub 无 release/tag）——
主通道 raw.githubusercontent.com 读 main/pyproject.toml 的 version，api.github.com
contents 兜底（均已实测可用）。当前版本 importlib.metadata 单源（修 0.3.8/0.3.11
drift），开发态回落解析仓库内 pyproject.toml。

通知只留两款：update_available / post_upgrade。7 天按版本抑制；状态存安装级
JSON（~/.local/share/mema-twin/，多库多路径共享一份安装态，不进 twin_meta）；
网络检查在 daemon 线程（不阻塞工具响应），出口 consume_notices 仅 ok 响应附
twin_notices。env MEMA_TWIN_UPDATE_CHECK=0 全链路禁用（零网络零写盘）。

并发模型（两把锁）：_check_lock 只管线程启动互斥；_state_lock 管状态 JSON 的
全部读改写。无文件锁——安装级单用户场景接受竞窗。
"""
from __future__ import annotations

import base64
import datetime as _dt
import importlib.metadata
import json
import os
import re
import threading
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

REPO_SLUG = "billy12151/mema-twin"
RAW_VERSION_URL = f"https://raw.githubusercontent.com/{REPO_SLUG}/main/pyproject.toml"
API_VERSION_URL = f"https://api.github.com/repos/{REPO_SLUG}/contents/pyproject.toml"
CHECK_INTERVAL_HOURS = 24
NOTICE_SUPPRESS_DAYS = 7

_check_lock = threading.Lock()
_state_lock = threading.Lock()
_check_thread: threading.Thread | None = None


def _disabled() -> bool:
    return (os.environ.get("MEMA_TWIN_UPDATE_CHECK") or "").strip().lower() in (
        "0", "false", "off")


def _state_path() -> Path:
    env = os.environ.get("MEMA_TWIN_UPDATE_STATE_PATH")
    if env:
        return Path(env)
    return Path.home() / ".local" / "share" / "mema-twin" / "update_state.json"


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _now_iso() -> str:
    return _now().replace(microsecond=0).isoformat()


def _parse_iso(value) -> _dt.datetime | None:
    if not value:
        return None
    try:
        t = _dt.datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=_dt.timezone.utc)
    return t


def _version_parts(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for seg in str(version or "").strip().split("."):
        try:
            parts.append(int(seg))
        except ValueError:
            parts.append(0)
    return tuple(parts) or (0,)


def compare_versions(left: str, right: str) -> int:
    """按 `.` 拆 int 段逐位比；段数不齐短侧补 0；非数字段按 0（参照实现同款）。"""
    a, b = _version_parts(left), _version_parts(right)
    n = max(len(a), len(b))
    a += (0,) * (n - len(a))
    b += (0,) * (n - len(b))
    return (a > b) - (a < b)


def current_version() -> str:
    """安装态版本单源 importlib.metadata；开发态（未安装直跑源码）回落解析仓库内
    pyproject.toml；再不行给 0.0.0（比较语义=永远「有更新」，notice 只多不少，无害）。"""
    try:
        return importlib.metadata.version("mema-twin")
    except importlib.metadata.PackageNotFoundError:
        pass
    try:
        text = (_state_project_pyproject()).read_text(encoding="utf-8")
    except OSError:
        return "0.0.0"
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else "0.0.0"


def _state_project_pyproject() -> Path:
    # 延迟 import 防环：db 不依赖本模块，但保持本模块对 actions 树零依赖的惯例
    from . import db
    return db.PROJECT_ROOT / "pyproject.toml"


# ---- 状态 JSON ----

def _read_state_unlocked() -> dict:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state_unlocked(state: dict) -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass  # 状态盘写失败不击穿工具面：大不了下周期重新提示


def _fetch_url(url: str, timeout: float = 5.0) -> str:
    req = Request(url, headers={"User-Agent": f"mema-twin/{current_version()}"})
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310（固定 https 常量，无注入面）
        return resp.read().decode("utf-8")


def _version_from_pyproject_text(text: str) -> str | None:
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text or "")
    return m.group(1) if m else None


def _fetch_remote_version() -> str | None:
    """主通道 raw（明文 pyproject）失败 → api.github contents（base64）兜底；
    双通道全挂返回 None（记失败时间戳，下周期重试，不抛）。"""
    try:
        return _version_from_pyproject_text(_fetch_url(RAW_VERSION_URL))
    except (URLError, TimeoutError, OSError, ValueError):
        pass
    try:
        payload = json.loads(_fetch_url(API_VERSION_URL))
        if payload.get("encoding") == "base64":
            text = base64.b64decode(payload.get("content") or "").decode("utf-8")
            return _version_from_pyproject_text(text)
    except (URLError, TimeoutError, OSError, ValueError):
        pass
    return None


def _due_unlocked(state: dict) -> bool:
    last = _parse_iso(state.get("last_checked_at"))
    if last is None:
        return True
    return (_now() - last) >= _dt.timedelta(hours=CHECK_INTERVAL_HOURS)


def maybe_start_check_if_due() -> bool:
    """到期则起 daemon 线程跑一次网络检查；返回是否真的启动。禁用态恒 False。"""
    if _disabled():
        return False
    state = _read_state_unlocked()
    if not _due_unlocked(state):
        return False
    global _check_thread
    with _check_lock:
        if _check_thread is not None and _check_thread.is_alive():
            return False
        _check_thread = threading.Thread(target=_run_one_check,
                                         name="mema-twin-update-check", daemon=True)
        _check_thread.start()
        return True


def _run_one_check() -> None:
    latest = _fetch_remote_version()
    with _state_lock:
        state = _read_state_unlocked()
        if latest is None:
            state["last_check_failed_at"] = _now_iso()
        else:
            state["latest_version"] = latest
            state["last_checked_at"] = _now_iso()
            state.pop("last_check_failed_at", None)
        _write_state_unlocked(state)


def _suppress_expired(state: dict) -> bool:
    at = _parse_iso(state.get("last_update_notified_at"))
    if at is None:
        return True
    return (_now() - at) >= _dt.timedelta(days=NOTICE_SUPPRESS_DAYS)


_UPDATE_INSTRUCTION = (
    "告知用户：mema-twin 有新版本 {latest}（当前 {current}）。只告知这一次，"
    "不要自动升级、不要重复提醒；用户要求升级时按其 README 的 git 安装方式操作。")

_POST_UPGRADE_INSTRUCTION = (
    "告知用户：mema-twin 已升级到 {current}（自 {previous}），建议浏览 CHANGELOG "
    "了解变更（特别是行为变化类条目）。只告知这一次。")


def consume_notices() -> list[dict]:
    """出口：返回 0..2 条 notice（读侧按版本抑制键去重）。仅 ok 响应携带——
    升级 notice 无丢失风险（抑制键存续，下次成功响应必然再出）。"""
    if _disabled():
        return []
    cur = current_version()
    out: list[dict] = []
    with _state_lock:
        state = _read_state_unlocked()
        latest = str(state.get("latest_version") or "")
        if latest and compare_versions(latest, cur) > 0:
            if (str(state.get("last_update_notified_version") or "") != latest
                    or _suppress_expired(state)):
                state["last_update_notified_version"] = latest
                state["last_update_notified_at"] = _now_iso()
                out.append({"type": "update_available", "current": cur,
                            "latest": latest,
                            "agent_instruction": _UPDATE_INSTRUCTION.format(
                                latest=latest, current=cur)})
        prior = state.get("post_upgrade_notified_for")
        if prior is None:
            # 首次安装基线：建立锚点不发通知（确有一次升级发生后 post_upgrade 才有意义）
            state["post_upgrade_notified_for"] = cur
        elif compare_versions(cur, str(prior)) > 0:
            state["post_upgrade_notified_for"] = cur
            out.append({"type": "post_upgrade", "current": cur, "from": str(prior),
                        "agent_instruction": _POST_UPGRADE_INSTRUCTION.format(
                            current=cur, previous=prior)})
        if out or prior is None:
            _write_state_unlocked(state)
    return out
