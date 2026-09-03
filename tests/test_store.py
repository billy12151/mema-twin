import pytest

from mema_twin import db as twin_db
from mema_twin import store


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(tmp_path / "twin.sqlite3"))
    monkeypatch.setenv("MEMA_TWIN_PROMPTS_DIR", str(tmp_path / "prompts"))
    return tmp_path


def test_version_lifecycle_and_mirror(env):
    conn = twin_db.connect()
    v1 = store.create_version(conn, "work_report", "# v1", ["1", "2"], model="test-model")
    assert v1["version"] == 1 and v1["source_count"] == 2
    v2 = store.create_version(conn, "work_report", "# v2", ["1", "2", "3"], model="test-model")
    assert v2["version"] == 2
    active = store.get_active(conn, "work_report")
    assert active["version"] == 2 and active["prompt_md"] == "# v2" and not active["from_mirror"]
    mirror = env / "prompts" / "work_report"
    assert (mirror / "v1.md").exists() and (mirror / "v2.md").exists()
    assert (mirror / "active.md").read_text(encoding="utf-8") == "# v2"
    assert [v["status"] for v in store.list_versions(conn, "work_report")] == ["active", "retired"]


def test_mirror_fallback(env):
    conn = twin_db.connect()
    store.create_version(conn, "work_report", "# only", ["1"], model="m")
    conn2 = twin_db.connect()
    conn2.execute("DELETE FROM twin_prompt_versions")
    conn2.commit()
    rec = store.get_active(conn2, "work_report")
    assert rec is not None and rec["from_mirror"] and rec["prompt_md"] == "# only"
    conn2.close()


def test_unknown_work_type_rejected(env):
    conn = twin_db.connect()
    with pytest.raises(ValueError):
        store.create_version(conn, "not_a_type", "# x", [], model="m")


def test_resolve_work_type_code(env):
    conn = twin_db.connect()
    assert store.resolve_work_type_code(conn, "周报") == "work_report"
    assert store.resolve_work_type_code(conn, "work_report") == "work_report"
    assert store.resolve_work_type_code(conn, "不存在") is None


def test_mirror_failure_degrades_to_warning(env, monkeypatch):
    """review#3：镜像写失败不击穿已落库版本。"""
    from mema_twin import store as twin_store

    def boom(path, text):
        raise OSError("disk full (simulated)")
    monkeypatch.setattr(twin_store, "_atomic_write", boom)
    conn = twin_db.connect()
    rec = twin_store.create_version(conn, "work_report", "# v1", ["1"], model="m")
    assert rec["version"] == 1
    assert rec["warnings"] and "disk full" in rec["warnings"][0]
    active = store.get_active(conn, "work_report")
    assert active["prompt_md"] == "# v1"  # DB 版本完好
