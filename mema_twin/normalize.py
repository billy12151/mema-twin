"""三字段写入归一（D3）：精确/别名 → embed 近邻 → pending。

Agent 抽象、产品归一：Agent 给原始值，本模块只负责映射到 canonical；
映射不到进 pending 由用户裁定，绝不自动新建 canonical。
embed 档（M1.1）：别名 miss 后先试语义近邻，相似度 >= EMBED_THRESHOLD
即映射到最近候选（matched_by="embed"，不落别名——别名只能由治理动作追加）；
embedder 禁用或失败时 fail-open 直接走 pending。
"""
from __future__ import annotations

from typing import Any

from . import db, embed, taxonomy

EMBED_THRESHOLD = 0.75


def _embed_nearest(kind: str, value: str, conn: Any) -> tuple[dict, float] | None:
    vec = embed.text_vector(value)
    if vec is None:
        return None
    best_row: dict | None = None
    best_score = 0.0
    for row in db.type_rows(conn, kind):
        for cand in (row["code"], row["label_zh"], row["label_en"], *row["aliases"]):
            if not cand:
                continue
            cv = embed.text_vector(cand)
            if cv is None:
                continue
            score = embed.cosine(vec, cv)
            if score > best_score:
                best_score = score
                best_row = row
    if best_row is not None and best_score >= EMBED_THRESHOLD:
        return best_row, best_score
    return None


def normalize_value(kind: str, raw: str, conn: Any,
                    memory_id: str | None = None,
                    defer_pending: bool = False) -> dict:
    """defer_pending=True 时未命中值不落 pending 表（write 用：mema 写成功后才
    upsert，避免失败重试虚增 hit_count 留下幽灵 pending——对抗 review#14）。"""
    v = (raw or "").strip()
    if not v:
        return {"ok": False, "error": "invalid_input", "kind": kind, "reason": "empty value"}
    hit = taxonomy.match_exact(kind, v)
    if hit:
        return {"ok": True, "kind": kind, "raw": v, "code": hit.code,
                "label_zh": hit.zh, "matched_by": "exact_or_alias"}
    for row in db.type_rows(conn, kind):
        cands = {c.strip().casefold() for c in
                 (row["code"], row["label_zh"], row["label_en"], *row["aliases"]) if c}
        if v.casefold() in cands:
            return {"ok": True, "kind": kind, "raw": v, "code": row["code"],
                    "label_zh": row["label_zh"], "matched_by": "db_alias"}
    near = _embed_nearest(kind, v, conn)
    if near:
        row, score = near
        return {"ok": True, "kind": kind, "raw": v, "code": row["code"],
                "label_zh": row["label_zh"], "matched_by": "embed",
                "similarity": round(score, 4)}
    if defer_pending:
        return {"ok": False, "kind": kind, "raw": v, "code": None,
                "matched_by": None, "deferred_pending": True}
    pid = db.upsert_pending(conn, kind, v, memory_id)
    return {"ok": False, "kind": kind, "raw": v, "code": None,
            "matched_by": None, "pending_id": pid}
