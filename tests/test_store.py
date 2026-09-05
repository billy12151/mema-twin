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


# ---- 0.3.4 compile 会话治理 ----

def test_create_version_reports_superseded(env):
    """submit 落版前捕获被取代的 active 版本：v1 无、v2 报 1。"""
    conn = twin_db.connect()
    v1 = store.create_version(conn, "work_report", "# v1", ["1"], model="m")
    assert v1["superseded_version"] is None
    v2 = store.create_version(conn, "work_report", "# v2", ["1"], model="m")
    assert v2["superseded_version"] == 1


# ---- 0.3.4 rollback ----

def test_activate_version_rollback_lifecycle(env):
    conn = twin_db.connect()
    for i in (1, 2, 3):
        store.create_version(conn, "work_report", f"# v{i}", ["1"], model="m")
    # 省略 version → 上一版（active v3 → v2）
    r = store.activate_version(conn, "work_report")
    assert r["version"] == 2 and r["superseded_version"] == 3
    assert store.get_active(conn, "work_report")["prompt_md"] == "# v2"
    assert (env / "prompts" / "work_report" / "active.md").read_text(encoding="utf-8") == "# v2"
    # v{n}.md 镜像不动
    assert (env / "prompts" / "work_report" / "v3.md").read_text(encoding="utf-8") == "# v3"
    # 指定版本 → v1
    assert store.activate_version(conn, "work_report", 1)["version"] == 1
    # 已是 active → 幂等不写库
    r3 = store.activate_version(conn, "work_report", 1)
    assert r3.get("already_active") is True
    assert [v["status"] for v in store.list_versions(conn, "work_report")] == \
        ["retired", "retired", "active"]  # list_versions 按 version DESC：v3,v2,v1
    # 版本号永不回收：回滚到 v1 后 submit 仍出 v4，且 supersedes 报 1
    v4 = store.create_version(conn, "work_report", "# v4", ["1"], model="m")
    assert v4["version"] == 4 and v4["superseded_version"] == 1


def test_activate_version_errors(env):
    conn = twin_db.connect()
    with pytest.raises(ValueError):  # 无任何版本
        store.activate_version(conn, "work_report")
    store.create_version(conn, "work_report", "# v1", ["1"], model="m")
    with pytest.raises(ValueError):  # 唯一版本，无历史可回滚
        store.activate_version(conn, "work_report")
    with pytest.raises(ValueError):  # 目标不存在
        store.activate_version(conn, "work_report", 9)


def test_activate_version_mirror_failure_degrades(env, monkeypatch):
    """镜像写失败不击穿已切换的 active（create_version 同款降级）。"""
    def boom(path, text):
        raise OSError("disk full (simulated)")
    monkeypatch.setattr(store, "_atomic_write", boom)
    conn = twin_db.connect()
    for i in (1, 2):
        store.create_version(conn, "work_report", f"# v{i}", ["1"], model="m")
    r = store.activate_version(conn, "work_report")
    assert r["version"] == 1 and "disk full" in r["warnings"][0]
    assert store.get_active(conn, "work_report")["prompt_md"] == "# v1"  # DB 已切换
