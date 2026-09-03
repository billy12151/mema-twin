"""embed 近邻归一档（M1.1）：别名 miss 后、pending 之前的语义兜底。

懒加载 mema-core 的 ManagedEmbedder（进程内一次性，共享同一 GGUF 文件）；
模型路径解析顺序：env MEMA_TWIN_EMBED_MODEL → mema 配置 embedding.model_path
（MEMORY_ARBITER_CONFIG 或 ~/.config/memory-arbiter/config.json）→ 禁用。

任何一步失败都 fail-open（返回 None，归一继续走 pending），绝不阻塞写入。
向量按文本进程内缓存；枚举候选文本有限，首次未命中后即全量缓存。
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

# None=未尝试；False=已尝试且不可用（ImportError / 无模型路径 / 加载失败）
_embedder: object | None = None
_cache: dict[str, list[float]] = {}


def _resolve_model_path() -> str | None:
    env = os.environ.get("MEMA_TWIN_EMBED_MODEL")
    if env and env.strip():
        return str(Path(env).expanduser())
    cfg_path = os.environ.get("MEMORY_ARBITER_CONFIG")
    if not cfg_path:
        cfg_path = str(Path.home() / ".config" / "memory-arbiter" / "config.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as fh:
            raw = json.load(fh).get("embedding") or {}
        p = str(raw.get("model_path") or "").strip()
        return str(Path(p).expanduser()) if p else None
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def _get_embedder():
    global _embedder
    if _embedder is False:
        return None
    if _embedder is not None:
        return _embedder
    try:
        from memory_arbiter.embedder import build_embedder
    except ImportError:
        _embedder = False
        return None
    model_path = _resolve_model_path()
    if not model_path:
        _embedder = False
        return None
    try:
        emb, _warnings = build_embedder(model_path)
    except Exception:
        _embedder = False
        return None
    _embedder = emb if emb is not None else False
    return _embedder if _embedder else None


def text_vector(text: str) -> list[float] | None:
    if text in _cache:
        return _cache[text]
    emb = _get_embedder()
    if emb is None:
        return None
    try:
        result = emb.embed_text("", text)
        vec = list(result.embedding)
    except Exception:
        return None
    if not vec:
        return None
    _cache[text] = vec
    return vec


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def reset_for_tests() -> None:
    global _embedder
    _embedder = None
    _cache.clear()
