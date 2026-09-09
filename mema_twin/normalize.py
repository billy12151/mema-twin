"""三字段写入归一（D3 + v0.3.8 归一门）：精确/别名命中，未命中打回。

v0.3.0 起移除 embed 近邻档（原 M1.1）：候选枚举有限，调用方本身是 LLM，语义
选码由 Agent 对清单完成——本地小模型静默错归没有人工检查点，反而吃掉治理信号。

v0.3.8 归一门（设计文档 mema-twin-v0.3.8-design-2026-09-09.md B）：未命中不再
静默进 pending——normalize_value 返回带动态候选清单的 miss（不落库），调用方
（write / task_start）整笔打回并经 gate_reject 落「裁定义务票据」（pending 行），
Agent 必须问用户 resolve(map|canonicalize) 后重试。设计公理：阻断只配灾难拦截
与数据入口正确性；归一结果当场驱动画像注入，错桶即时伤害，故首写即问。
枚举无 other 杂项桶：真长尾走 canonicalize 立新码，进清单即自动可选。
"""
from __future__ import annotations

from typing import Any

from . import db, taxonomy


def normalize_value(kind: str, raw: str, conn: Any) -> dict:
    """命中 → ok=True（code/label_zh/matched_by）；空值 → invalid_input；
    未命中 → ok=False 且带 candidates（该 kind 动态清单，DB 实时读）。
    本函数不写 pending——票据由 gate_reject 在打回时创建，未打回则无义务。"""
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
    return {"ok": False, "kind": kind, "raw": v, "code": None,
            "matched_by": None, "candidates": candidates(kind, conn)}


def candidates(kind: str, conn: Any) -> list[dict]:
    """动态候选清单（code+中文名）：twin_types 实时读（治理追加的别名与自建
    canonical 立刻可见），内置枚举兜底合并（防未播种形态漏列）。"""
    out: list[dict] = []
    seen: set[str] = set()
    for row in db.type_rows(conn, kind):
        if row["code"] in seen:
            continue
        seen.add(row["code"])
        out.append({"code": row["code"], "zh": row["label_zh"]})
    for t in taxonomy.all_types(kind):
        if t.code not in seen:
            seen.add(t.code)
            out.append({"code": t.code, "zh": t.zh})
    return out


GATE_GUIDANCE = (
    "维度值不在清单内，本次写入/建档未执行。必须先与用户逐个确认归属："
    "① 归一到已有值 → twin(action=\"resolve\", data={\"pending_id\":…, \"decision\":"
    "\"map\", \"code\":…})（原值进别名表，同一说法以后自动命中，只问这一次）；"
    "② 创建新值 → resolve(decision=\"canonicalize\", new_type={code,zh,en,domain})"
    "（新码即刻进入清单，以后自动可选）；用户不在意 → 选最贴近的已有值；"
    "用户说这条不写 → resolve(decision=\"reject\") 并放弃本次（reject 后不得再拿"
    "原值重试，票据会复活）。裁定完成后用原值或 code 重试本动作；resolve 报"
    "「已裁定不可重复裁定」说明他方已裁定，直接重试本动作即可")


def gate_reject(misses: list[dict]) -> dict | None:
    """归一门打回响应（v0.3.8 B1）：每个未命中值落裁定义务票据（打回次数
    hit_count 续增），响应附动态清单与必须问用户的指引。多维未命中一次性全报。

    落票据前对每个 miss 重查一次（轮2 P3-1 竞窗收窄）：多宿主并发下他方可能刚
    裁定完同值（别名已落库）——重查命中的维度**就地改写为命中结果**（miss dict
    与调用方 dims 里是同一对象，改动直接同步）且不落票据，返回 None 表示全部
    被并发裁定、调用方应按更新后的 dims 继续执行。残余竞窗（重查到 upsert 之间）
    若命中，票据会被复活一次但重试写入即命中别名、自愈。"""
    conn = db.connect()
    try:
        remaining: list[dict] = []
        for m in misses:
            r2 = normalize_value(m["kind"], m["raw"], conn)
            if r2.get("ok"):
                m.clear()
                m.update(r2)  # 并发他方已裁定：该维度转为命中，dims 同步
                continue
            if not r2.get("error"):
                m["candidates"] = r2.get("candidates") or m.get("candidates") or []
            m["pending_id"] = db.upsert_pending(conn, m["kind"], m["raw"])
            remaining.append(m)
    finally:
        conn.close()
    if not remaining:
        return None
    fields = {}
    for m in remaining:
        fields[m["kind"]] = {"raw": m["raw"],
                             "candidates": m.get("candidates") or [],
                             "pending_id": m["pending_id"]}
    return {"ok": False, "error": "unmatched_value", "fields": fields,
            "guidance": GATE_GUIDANCE}
