"""定时扫描任务 spec 与提醒逻辑（M2.2）：模式照搬 mema-core scan_tasks.py。

单一真源：AGENT_INSTRUCTION、任务 spec、help topic、scan 提醒都从本模块
渲染，防止漂移。平台无关（mema 2026-09-02 owner decision 同款）：twin 自己
不起任何调度，Agent 拿指令后在宿主平台创建等价任务；scan 近期跑过则
提醒不再出现（last_scan_at 自消失，同 mema scan_log 边界逻辑）。
"""
from __future__ import annotations

import datetime as _dt

from . import db, flow

SCHEDULED_TASKS_TOPIC = "scheduled_tasks"

# 提醒失效窗口：scan 在该天数内跑过即认为"定时任务在转"
SCAN_FRESH_DAYS = 7

AGENT_INSTRUCTION = (
    "Tell the user: mema-twin 建议创建两个定时任务：①夜间 persona 编译（每天，"
    "专用一次性会话跑 status→compile→submit，把当天未编译偏好自动整理进 persona "
    "并落版，无证据不编）；②每周治理扫描（twin scan，发现 pending 积压与未收口"
    "交付任务）。Ask whether to set them up now（可只选其一）; on consent, create "
    "the equivalent task(s) on your own platform from setup.tasks. The notice "
    "disappears by itself once the weekly scan has run recently — no report-back "
    "needed."
)

SCHEDULED_TASKS_SPEC: dict = {
    "tasks": [
        {
            "name": "twin_nightly_compile",
            "purpose": ("夜间无人值守的专用一次性编译会话（凌晨用户不在场，不向用户提问）："
                        "把各 work_type 当天未编译的偏好证据编译进 persona prompt 并 "
                        "submit 落版；未编译数为 0 的类型不编译不落版，避免版本号空转。"),
            "cadence": "daily",
            "calls": [
                {"tool": "twin", "action": "status",
                 "data": {"note": "取 uncompiled_by_work_type，只处理数量 >0 的 work_type"}},
                {"tool": "twin", "action": "compile",
                 "data": {"rule": "严格按素材包编译规则产出新版：与旧版本冲突以新证据为准，"
                                  "文末列出版本间变更"}},
                {"tool": "twin", "action": "submit",
                 "data": {"origin": "scheduled",
                          "rule": "source ids 用素材包证据 id；origin=scheduled 必传"
                                  "（夜间落版标记，供次日首任务双跑对比提议）；"
                                  "结束输出各类型前后版本号与吸收证据数汇总；"
                                  "工具出错跳过并如实记录，同一项最多重试一次"}},
            ],
        },
        {
            "name": "twin_scan",
            "purpose": "汇总各 work_type 未编译偏好数、pending 积压与开放交付任务，产出给用户的治理建议。",
            "cadence": "weekly",
            "calls": [
                {"tool": "twin", "action": "scan",
                 "data": {"relay": "将 scan 返回的建议文本转述给用户，询问是否处理"}},
            ],
        },
    ],
}


def _parse_iso(ts: str | None) -> _dt.datetime | None:
    if not ts:
        return None
    try:
        t = _dt.datetime.fromisoformat(ts)
    except ValueError:
        return None
    if t.tzinfo is None:  # naive 一律按 UTC（review#8：naive 与 now(utc) 相减会炸）
        t = t.replace(tzinfo=_dt.timezone.utc)
    return t


def scan_notice() -> dict | None:
    """scan 过期/从未跑过时返回提醒载荷；近期跑过返回 None（提醒自消失）。"""
    flow.ensure_schema()
    last = _parse_iso(flow.get_meta("last_scan_at"))
    if last is not None and (_dt.datetime.now(_dt.timezone.utc) - last).days < SCAN_FRESH_DAYS:
        return None
    return {
        "type": "twin_scan_setup",
        "agent_instruction": AGENT_INSTRUCTION,
        "setup": SCHEDULED_TASKS_SPEC,
        "note": "提醒自消失：scan 在 7 天内跑过即不再提示",
    }


def run_scan() -> dict:
    """执行扫描并刷新 last_scan_at。返回给 Agent 的建议素材。"""
    flow.ensure_schema()
    conn = db.connect()
    try:
        uncompiled = db.evidence_stats(conn)
        pending = db.list_pending(conn)
    finally:
        conn.close()
    open_rows = flow.open_tasks()

    suggestions: list[str] = []
    total_uncompiled = sum(uncompiled.values())
    if total_uncompiled:
        top = sorted(uncompiled.items(), key=lambda kv: -kv[1])[:3]
        top_s = "、".join(f"{k}（{v} 条）" for k, v in top)
        suggestions.append(
            f"有 {total_uncompiled} 条偏好未编译进 persona prompt（{top_s}）；"
            "建议在强模型会话执行 twin(action=\"compile\") 后 submit 落版本，"
            "或由夜间编译定时任务统一处理（help topic scheduled_tasks 有 spec）")
    if pending:
        suggestions.append(
            f"pending 治理积压 {len(pending)} 条（三维度未识别值）；"
            "twin(action=\"pending\") 查看后逐条 resolve")
    if open_rows:
        ids = ", ".join(f"#{t['id']}({t['status']})" for t in open_rows[:5])
        suggestions.append(
            f"有 {len(open_rows)} 个未收口的交付任务（{ids}）；"
            "继续执行或评审收口（task_review），长期不动的用 task_close 显式关闭")

    flow.set_meta("last_scan_at", db.now_iso())
    return {
        "ok": True,
        "uncompiled_total": total_uncompiled,
        "uncompiled_by_work_type": uncompiled,
        "pending_count": len(pending),
        "open_tasks": len(open_rows),
        "suggestions": suggestions or ["没有需要处理的积压——分身状态健康"],
    }
