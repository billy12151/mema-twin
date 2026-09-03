"""全局测试隔离：默认禁用真实 embedder（不加载 313MB GGUF、结果确定性）。

test_embed.py 用假向量显式覆盖 text_vector；涉及 normalize 的其余测试拿到
None → fail-open 走 pending，与"未安装 llama-cpp-python"的产品降级路径同构。
"""
import pytest

from mema_twin import embed


@pytest.fixture(autouse=True)
def _no_real_embedder(monkeypatch):
    monkeypatch.setattr(embed, "text_vector", lambda t: None)
    embed.reset_for_tests()
    yield
    embed.reset_for_tests()
