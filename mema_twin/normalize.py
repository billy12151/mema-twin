"""三字段写入归一（D3）：精确/别名 → embed 近邻（待接入）→ pending。

Agent 抽象、产品归一：Agent 给原始值，本模块只负责映射到 canonical；
映射不到进 pending 由用户裁定，绝不自动新建 canonical。
"""
from __future__ import annotations

from typing import Any

from . import db, taxonomy

EMBED_THRESHOLD = 0.75


def normalize_value(kind: str, raw: str, conn: Any,
                    memory_id: str | None = None) -> dict:
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
    # TODO(阶段0): embed 近邻档——复用 mema-core embedder，相似度 >= EMBED_THRESHOLD 即映射
    pid = db.upsert_pending(conn, kind, v, memory_id)
    return {"ok": False, "kind": kind, "raw": v, "code": None,
            "matched_by": None, "pending_id": pid}
