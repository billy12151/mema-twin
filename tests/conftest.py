"""全局测试隔离（v0.4.6）：autouse 把每个用例的 twin 库指向 tmp_path。

背景：v0.4.6 起每次 twin 调用都会往审计表落一行，于是**任何**没设
MEMA_TWIN_DB_PATH 的测试都会往生产库 twin.sqlite3 写行（复审实测：全量
pytest 每跑一次污染 3 行，且 git stash 对照确认是 v0.4.6 新引入）。逐用例补
env 是补不完的——用 autouse fixture 兜底，新测试默认即隔离。

已在用例内显式 setenv 的不受影响（monkeypatch 后设者胜出）。
"""
import pytest

from mema_twin import audit, flow


@pytest.fixture(autouse=True)
def _isolated_twin_db(tmp_path, monkeypatch):
    """每个用例独享一个 twin 库 + prompts/deliverables 目录，并清空进程级
    缓存（schema_ready / audit_ready / advisory 冷却 / enabled 探测）——
    否则同 path 的陈旧缓存会让新库静默缺表。"""
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(tmp_path / "twin.sqlite3"))
    monkeypatch.setenv("MEMA_TWIN_PROMPTS_DIR", str(tmp_path / "prompts"))
    monkeypatch.setenv("MEMA_TWIN_DELIVERABLES_DIR", str(tmp_path / "deliverables"))
    flow._schema_ready.clear()
    audit._audit_ready.clear()
    audit._advisory_cooldown.clear()
    audit._reset_enabled_cache()
    flow.ensure_schema()
    yield tmp_path
    flow._todos_by_session.clear()
    audit._advisory_cooldown.clear()
    audit._reset_enabled_cache()
