"""三字段写入归一（D3）：精确/别名 → pending。

SKILL 硬流程要求 Agent 先查 taxonomy 清单选码；本模块只负责把给定的值
映射到 canonical（内置枚举 + 治理追加的别名/自建 canonical），映射不到进
pending 由用户裁定，绝不自动新建 canonical。

v0.3.0 起移除 embed 近邻档（原 M1.1）：候选枚举有限（53 项），调用方本身
是 LLM，语义选码由 Agent 对清单完成即可——本地小模型 0.75 阈值静默错归
没有人工检查点，反而吃掉 pending 治理信号，还让 twin 背上第二份向量模型
依赖（llama-cpp-python + GGUF），与 mema-team 单向量模型约束冲突。
"""
from __future__ import annotations

from typing import Any

from . import db, taxonomy


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
    if defer_pending:
        return {"ok": False, "kind": kind, "raw": v, "code": None,
                "matched_by": None, "deferred_pending": True}
    pid = db.upsert_pending(conn, kind, v, memory_id)
    return {"ok": False, "kind": kind, "raw": v, "code": None,
            "matched_by": None, "pending_id": pid}
