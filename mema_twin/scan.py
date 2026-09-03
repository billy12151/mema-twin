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
    "Tell the user: mema-twin 建议创建一个定时扫描任务（每周一次 twin scan），"
    "用于发现未编译偏好、pending 治理积压和未收口的交付任务。Ask whether to set "
    "them up now; on consent, create the equivalent task on your own platform from "
    "setup.tasks. The notice disappears by itself once the task has run recently — "
    "no report-back needed."
)

SCHEDULED_TASKS_SPEC: dict = {
    "tasks": [
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
        return _dt.datetime.fromisoformat(ts)
    except ValueError:
        return None


def scan_notice(workspace: str) -> dict | None:
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


def run_scan(workspace: str) -> dict:
    """执行扫描并刷新 last_scan_at。返回给 Agent 的建议素材。"""
    flow.ensure_schema()
    conn = db.connect()
    try:
        uncompiled = db.evidence_stats(conn, workspace)
        pending = db.list_pending(conn)
    finally:
        conn.close()
    open_tasks = [t for t in flow.recent_tasks(workspace, limit=50)
                  if t["status"] in flow._OPEN_STATUSES]

    suggestions: list[str] = []
    total_uncompiled = sum(uncompiled.values())
    if total_uncompiled:
        top = sorted(uncompiled.items(), key=lambda kv: -kv[1])[:3]
        top_s = "、".join(f"{k}（{v} 条）" for k, v in top)
        suggestions.append(
            f"有 {total_uncompiled} 条偏好未编译进 persona prompt（{top_s}）；"
            "建议在强模型会话执行 twin(action=\"compile\") 后 submit 落版本")
    if pending:
        suggestions.append(
            f"pending 治理积压 {len(pending)} 条（三维度未识别值）；"
            "twin(action=\"pending\") 查看后逐条 resolve")
    if open_tasks:
        ids = ", ".join(f"#{t['id']}({t['status']})" for t in open_tasks[:5])
        suggestions.append(
            f"有 {len(open_tasks)} 个未收口的交付任务（{ids}）；"
            "继续执行或评审收口（task_review），长期不动的考虑显式关闭")

    flow.set_meta("last_scan_at", db.now_iso())
    return {
        "ok": True, "workspace": workspace,
        "uncompiled_total": total_uncompiled,
        "uncompiled_by_work_type": uncompiled,
        "pending_count": len(pending),
        "open_tasks": len(open_tasks),
        "suggestions": suggestions or ["没有需要处理的积压——分身状态健康"],
    }
