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


def normalize_value(kind: str, raw: str, conn: Any, content: str = "",
                    client: str = "", source_kind: str = "") -> dict:
    """命中 → ok=True（code/label_zh/matched_by）；空值 → invalid_input；
    未命中 → ok=False 且带 candidates（该 kind 动态清单，DB 实时读）+ content/
    client/source_kind（v0.6.2 D4 与 v0.6.3：经 miss dict 透传给 gate_reject 落
    票据快照、宿主归属与快照来源）。本函数不写 pending——票据由 gate_reject 在
    打回时创建，未打回则无义务。"""
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
            "matched_by": None, "candidates": candidates(kind, conn),
            "content": content, "client": client, "source_kind": source_kind}


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


def all_values(kind: str, conn: Any) -> list[dict]:
    """全量现有值+选码说明（v0.6.2 D2）：code/zh/description——description 取
    DB 列（治理侧可维护）优先，内置码回落 _DESCRIPTIONS 表（R1 P0-1 回填后
    DB 行已带，此处为未回填/新播种形态兜底）。"""
    out: list[dict] = []
    seen: set[str] = set()
    for row in db.type_rows(conn, kind):
        if row["code"] in seen:
            continue
        seen.add(row["code"])
        desc = row["description"] if "description" in row.keys() else ""
        out.append({"code": row["code"], "zh": row["label_zh"],
                    "description": desc or taxonomy.description_for(row["code"])})
    for t in taxonomy.all_types(kind):
        if t.code not in seen:
            seen.add(t.code)
            out.append({"code": t.code, "zh": t.zh,
                        "description": taxonomy.description_for(t.code)})
    return out


def similar_values(raw: str, values: list[dict], threshold: float = 0.82,
                   limit: int = 3) -> list[dict]:
    """近似值标注（v0.6.2 D2/D3）：difflib 字面相似 top3（quick 0.75 预筛，
    任一侧 >2000 字符跳过/长度差 4× 跳过——与 S7 同款护栏）。values 需含
    code/zh。"""
    from difflib import SequenceMatcher
    v = raw or ""
    if not v or len(v) > 2000:
        return []
    scored: list[dict] = []
    for item in values:
        for text in (item.get("zh") or "", item.get("code") or ""):
            if not text or len(text) > 2000:
                continue
            if max(len(text), len(v)) > 4 * min(len(text), len(v)):
                continue
            sm = SequenceMatcher(None, text, v)
            if sm.quick_ratio() < 0.75:
                continue
            ratio = sm.ratio()
            if ratio >= threshold:
                scored.append({"code": item["code"], "zh": item.get("zh", ""),
                               "ratio": round(ratio, 2)})
                break  # 同一条目取最高分即可
    scored.sort(key=lambda x: -x["ratio"])
    return scored[:limit]


GATE_GUIDANCE = (
    "维度值不在清单内，本次写入/建档未执行。全部现有值与说明见 all_values"
    "（近似的已在 candidates 标注）——问 owner 时一次问清：带着这份清单与"
    " candidates 问「归到哪个已有值，还是立新值」："
    "① 归到已有值 → twin(action=\"resolve\", data={\"pending_id\":…, \"decision\":"
    "\"map\", \"code\":…})（原值进别名表，同一说法以后自动命中，只问这一次）；"
    "② 确认没有合适的 → resolve(decision=\"canonicalize\", new_type={code,zh,en,"
    "domain, description?})（新码即刻进入清单，以后自动可选）；"
    "用户不在意 → 选最贴近的已有值；用户说这条不写 → resolve(decision=\"reject\")"
    " 并放弃本次（reject 后不得再拿原值重试，票据会复活）。裁定完成后用原值或 "
    "code 重试本动作；resolve 报「已裁定不可重复裁定」说明他方已裁定，直接重试"
    "本动作即可")


def gate_reject(misses: list[dict]) -> dict | None:
    """归一门打回响应（v0.3.8 B1）：每个未命中值落裁定义务票据（打回次数
    hit_count 续增），响应附动态清单与必须问用户的指引。多维未命中一次性全报。

    落票据前对每个 miss 重查一次（轮2 P3-1 竞窗收窄）：多宿主并发下他方可能刚
    裁定完同值（别名已落库）——重查命中的维度**就地改写为命中结果**（miss dict
    与调用方 dims 里是同一对象，改动直接同步）且不落票据，返回 None 表示全部
    被并发裁定、调用方应按更新后的 dims 继续执行。残余竞窗（重查到 upsert 之间）
    若命中，票据会被复活一次但重试写入即命中别名、自愈。

    v0.6.2 D2：fields 每个未命中维度附 all_values（全量+说明）、candidates
    （全量清单，形状不变零破坏）、similar（difflib top3 近似标注）。similar 在
    本函数算（不进 normalize_value——竞窗重查不重复计算，R2 险-2），且排序在
    重查循环之后（:88 重查会用全量清单覆写 candidates）。"""
    conn = db.connect()
    try:
        remaining: list[dict] = []
        for m in misses:
            r2 = normalize_value(m["kind"], m["raw"], conn,
                                 content=m.get("content") or "",
                                 client=m.get("client") or "",
                                 source_kind=m.get("source_kind") or "")
            if r2.get("ok"):
                m.clear()
                m.update(r2)  # 并发他方已裁定：该维度转为命中，dims 同步
                continue
            if not r2.get("error"):
                m["candidates"] = r2.get("candidates") or m.get("candidates") or []
            m["pending_id"] = db.upsert_pending(
                conn, m["kind"], m["raw"],
                content_snapshot=m.get("content") or "",
                client=m.get("client") or "",
                source_kind=m.get("source_kind") or "")
            remaining.append(m)
    finally:
        conn.close()
    if not remaining:
        return None
    fields = {}
    for m in remaining:
        conn = db.connect()
        try:
            values = all_values(m["kind"], conn)
        finally:
            conn.close()
        fields[m["kind"]] = {"raw": m["raw"],
                             "candidates": values,  # 全量清单（形状加法：+description，零破坏）
                             "similar": similar_values(m["raw"], values),
                             "all_values": values,  # 同清单（含 description 的选码视图）
                             "pending_id": m["pending_id"]}
    return {"ok": False, "error": "unmatched_value",
            "reason": (f"{'/'.join(fields)} 未命中清单（全量与说明见 fields.*.all_values，"
                       "近似见 fields.*.similar，票据 id 在 fields.*.pending_id）"
                       "——按 guidance 问用户裁定后重试"),
            "fields": fields,
            "guidance": GATE_GUIDANCE}
