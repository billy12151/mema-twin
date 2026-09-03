import pytest

from mema_twin import db as twin_db
from mema_twin import embed, normalize


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(tmp_path / "twin.sqlite3"))
    monkeypatch.setenv("MEMA_TWIN_PROMPTS_DIR", str(tmp_path / "prompts"))
    embed.reset_for_tests()
    yield twin_db.connect()
    embed.reset_for_tests()


def _fake_embed(monkeypatch, mapping: dict[str, list[float]], default=None):
    monkeypatch.setattr(embed, "text_vector", lambda t: mapping.get(t, default))


def test_embed_hit_maps_canonical(conn, monkeypatch):
    # "述职汇报" 不在别名表；向量与 "工作汇报" 同向 → embed 档命中
    _fake_embed(monkeypatch, {
        "述职汇报": [1.0, 0.0],
        "工作汇报": [0.99, 0.1],          # cosine ≈ 0.995 >= 0.75
        "软件设计": [0.0, 1.0],
    })
    r = normalize.normalize_value("work_type", "述职汇报", conn)
    assert r["ok"] and r["code"] == "work_report" and r["matched_by"] == "embed"
    assert r["similarity"] >= 0.75


def test_embed_miss_goes_pending(conn, monkeypatch):
    # 与所有候选都近乎正交 → 低于阈值，进 pending
    _fake_embed(monkeypatch, {
        "星际移民白皮书": [1.0, 0.0],
        "工作汇报": [0.0, 1.0],
    }, default=[0.0, 1.0])
    r = normalize.normalize_value("work_type", "星际移民白皮书", conn)
    assert not r["ok"] and r.get("pending_id")


def test_embed_unavailable_falls_back_to_pending(conn, monkeypatch):
    monkeypatch.setattr(embed, "text_vector", lambda t: None)
    r = normalize.normalize_value("work_type", "任意近义说法", conn)
    assert not r["ok"] and r.get("pending_id")


def test_embed_hit_does_not_persist_alias(conn, monkeypatch):
    _fake_embed(monkeypatch, {
        "述职汇报": [1.0, 0.0],
        "工作汇报": [1.0, 0.0],
    })
    normalize.normalize_value("work_type", "述职汇报", conn)
    row = conn.execute(
        "SELECT aliases FROM twin_types WHERE type_kind='work_type' AND code='work_report'"
    ).fetchone()
    assert "述职汇报" not in row["aliases"]


def test_embed_uses_db_governance_rows_too(conn, monkeypatch):
    twin_db.add_canonical(conn, "work_type", "xianxia_doc", "玄幻设定文档",
                          "xianxia setting", "专业服务", ["设定集"])
    _fake_embed(monkeypatch, {
        "门派谱系文档": [1.0, 0.0],
        "玄幻设定文档": [1.0, 0.0],
    })
    r = normalize.normalize_value("work_type", "门派谱系文档", conn)
    assert r["ok"] and r["code"] == "xianxia_doc" and r["matched_by"] == "embed"
