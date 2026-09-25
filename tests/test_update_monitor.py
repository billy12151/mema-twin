"""v0.4 P0 升级提示：版本比较、单源 current_version、抑制窗口、post_upgrade、
状态容错、双通道 fetch、禁用 env（规格 mema-twin-v0.4-exec-design-2026-09-26.md §9）。"""
import datetime as dt
import importlib.metadata
import json
import re

import pytest

from mema_twin import db, server, update_monitor as um


@pytest.fixture()
def um_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMA_TWIN_UPDATE_STATE_PATH",
                       str(tmp_path / "update_state.json"))
    monkeypatch.setenv("MEMA_TWIN_UPDATE_CHECK", "")  # 确认未禁用
    # 隔离线程态与缓存
    monkeypatch.setattr(um, "_check_thread", None)
    yield tmp_path


def _write_state(path, **kw):
    path.write_text(json.dumps(kw), encoding="utf-8")


def _read_state(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_compare_versions():
    assert um.compare_versions("0.4.0", "0.3.11") > 0
    assert um.compare_versions("0.3.11", "0.3.11") == 0
    assert um.compare_versions("0.3.9", "0.3.10") < 0
    assert um.compare_versions("1.0", "1.0.0") == 0  # 短侧补 0
    assert um.compare_versions("0.4.0.dev7", "0.3.11") >= 0  # 非数字段按 0


def test_current_version_fallback(um_env, monkeypatch):
    # 安装态：importlib 命中
    v = um.current_version()
    assert v.count(".") >= 2
    # 开发态：importlib miss → 回落仓库 pyproject.toml（动态读，bump 版本不破测试）
    def _miss(_n):
        raise importlib.metadata.PackageNotFoundError("mema-twin")
    monkeypatch.setattr(um.importlib.metadata, "version", _miss)
    v2 = um.current_version()
    pyproject = (db.PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    expected = re.search(r'(?m)^version\s*=\s*"([^"]+)"', pyproject).group(1)
    assert v2 == expected  # 版本单源修复（0.3.8/0.3.11 drift bug）


def test_disabled_env(um_env, monkeypatch):
    monkeypatch.setenv("MEMA_TWIN_UPDATE_CHECK", "0")
    assert um.consume_notices() == []
    assert um.maybe_start_check_if_due() is False
    monkeypatch.setenv("MEMA_TWIN_UPDATE_CHECK", "false")
    assert um.consume_notices() == []


def test_update_available_notice_and_suppression(um_env):
    state = um_env / "update_state.json"
    _write_state(state, latest_version="9.9.9",
                 last_checked_at=um._now_iso())
    first = um.consume_notices()
    assert len(first) == 1 and first[0]["type"] == "update_available"
    assert first[0]["latest"] == "9.9.9"
    # 7 天内同版本抑制
    assert um.consume_notices() == []
    # 抑制窗口过期（7 天前通知过）→ 重发
    _write_state(state, latest_version="9.9.9",
                 last_checked_at=um._now_iso(),
                 last_update_notified_version="9.9.9",
                 last_update_notified_at=(um._now()
                                          - dt.timedelta(days=8)).isoformat())
    again = um.consume_notices()
    assert len(again) == 1 and again[0]["type"] == "update_available"


def test_no_notice_when_up_to_date(um_env):
    state = um_env / "update_state.json"
    _write_state(state, latest_version="0.0.1", last_checked_at=um._now_iso())
    assert um.consume_notices() == []


def test_post_upgrade_first_install_vs_real_upgrade(um_env):
    # 首次安装（无基线）→ 只建锚不发通知
    first = um.consume_notices()
    assert first == []
    state = um_env / "update_state.json"
    assert _read_state(state)["post_upgrade_notified_for"]
    # 真升级（基线 < current）→ 发一次
    _read_state(state)
    _write_state(state, post_upgrade_notified_for="0.0.1")
    up = um.consume_notices()
    assert len(up) == 1 and up[0]["type"] == "post_upgrade"
    assert up[0]["from"] == "0.0.1"
    assert um.consume_notices() == []  # 只一次


def test_corrupt_state_tolerated(um_env):
    state = um_env / "update_state.json"
    state.write_text("{not json", encoding="utf-8")
    assert um.consume_notices() == []  # 坏文件不炸（当空状态）
    state.write_text('["wrong shape"]', encoding="utf-8")
    assert um.consume_notices() == []


def test_fetch_remote_version_dual_channel(um_env, monkeypatch):
    calls = []

    def ok_raw(url, timeout=5.0):
        calls.append(url)
        if "raw.githubusercontent" in url:
            return 'version = "9.9.9"'
        return "{}"

    monkeypatch.setattr(um, "_fetch_url", ok_raw)
    assert um._fetch_remote_version() == "9.9.9"

    def raw_down(url, timeout=5.0):
        calls.append(url)
        if "raw.githubusercontent" in url:
            raise um.URLError("down")
        import base64
        return json.dumps({"encoding": "base64",
                           "content": base64.b64encode(b'version = "8.8.8"').decode()})

    monkeypatch.setattr(um, "_fetch_url", raw_down)
    assert um._fetch_remote_version() == "8.8.8"

    def all_down(url, timeout=5.0):
        raise um.URLError("down")

    monkeypatch.setattr(um, "_fetch_url", all_down)
    assert um._fetch_remote_version() is None


def test_maybe_start_check_if_due(um_env, monkeypatch):
    assert um.maybe_start_check_if_due() is True  # 从未检查 → due → 起线程
    # 线程在跑（mock 网络让线程快速结束前，第二次调用被启动互斥拦下）
    assert um.maybe_start_check_if_due() is False
    um._check_thread.join(timeout=5)
    state = _read_state(um_env / "update_state.json")
    assert state.get("last_checked_at")  # 线程真的写入了状态（mock 真实网络通道）


def test_run_one_check_failure_recorded(um_env, monkeypatch):
    monkeypatch.setattr(um, "_fetch_remote_version", lambda: None)
    um._run_one_check()
    state = _read_state(um_env / "update_state.json")
    assert state.get("last_check_failed_at")
    assert "latest_version" not in state


def test_twin_notices_only_on_ok(um_env, monkeypatch):
    monkeypatch.setattr(um, "consume_notices",
                        lambda: [{"type": "update_available", "current": "0.3.11",
                                  "latest": "9.9.9"}])
    ok = server._twin_impl("task_recent", {})
    assert ok["ok"] and ok["twin_notices"][0]["latest"] == "9.9.9"
    err = server._twin_impl("nope", {})
    assert err["ok"] is False and "twin_notices" not in err


def test_twin_notices_and_mema_notices_coexist(um_env, monkeypatch):
    monkeypatch.setattr(um, "consume_notices",
                        lambda: [{"type": "update_available"}])
    # mema_notices 经 contextvar 收集（_twin_impl reset 在 handler 之前）——
    # 把 drain 挂进 handler 执行期内，验证两键可同现
    from mema_twin import sink
    orig = server._ACTIONS["task_recent"]

    def fake(data):
        sink._drain_notices({"notices": [{"type": "similar_active_memory"}]})
        return orig(data)

    monkeypatch.setitem(server._ACTIONS, "task_recent", fake)
    ok = server._twin_impl("task_recent", {})
    assert "mema_notices" in ok and "twin_notices" in ok
