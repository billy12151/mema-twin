"""review#2/#10/#11：畸形参数不得击穿工具边界。"""
import pytest

from mema_twin import server


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(tmp_path / "twin.sqlite3"))
    monkeypatch.setenv("MEMA_TWIN_PROMPTS_DIR", str(tmp_path / "prompts"))
    monkeypatch.setenv("MEMA_TWIN_DELIVERABLES_DIR", str(tmp_path / "deliverables"))


def test_malformed_task_id_types():
    for bad in ({"task_id": {"x": 1}}, {"task_id": [1]}, {"task_id": None}):
        r = server.twin("task_get", bad)
        assert r.get("ok") is False and r.get("error") in ("invalid_input", "internal_error"), bad


def test_malformed_pending_resolve(monkeypatch):
    r = server.twin("resolve", {"pending_id": {"bad": 1}, "decision": "map", "code": "work_report"})
    assert r.get("ok") is False
    r2 = server.twin("resolve", {"pending_id": 1, "decision": "canonicalize", "new_type": "not-a-dict"})
    assert r2.get("ok") is False


def test_malformed_source_memory_ids():
    r = server.twin("submit", {"work_type": "work_report", "prompt_md": "# x",
                               "source_memory_ids": "123"})
    assert r.get("ok") is False and r.get("field") == "source_memory_ids"
    r2 = server.twin("submit", {"work_type": "work_report", "prompt_md": "# x",
                                "source_memory_ids": [1, 2.9]})
    assert r2.get("ok") is False


def test_workspace_path_traversal_rejected():
    # 空串走 or 回退默认 workspace，属合法；只拦真正带分隔符/穿越的值
    for bad in ("../escape", "a/b", "a\\b", "x" * 65):
        r = server.twin("status", {"workspace": bad})
        assert r.get("ok") is False and r.get("error") == "invalid_input", bad


def test_write_rejects_oversized_dimension():
    r = server.twin("write", {"content": "x", "work_type": "长" * 300,
                              "audience": "高层", "purpose": "同步"})
    assert r.get("ok") is False and r.get("field") == "work_type"
