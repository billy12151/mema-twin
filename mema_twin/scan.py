"""定时任务 spec 与提醒逻辑（M2.2，v0.3.7 收敛为单一夜间编译任务）。

单一真源：AGENT_INSTRUCTION、任务 spec、help topic、提醒都从本模块渲染，防止
漂移。平台无关（mema 2026-09-02 owner decision 同款）：twin 自己不起任何调度，
Agent 拿指令后在宿主平台创建等价任务。twin_scan（每周治理扫描）已随 v0.3.7
退役：其职能被夜间编译任务（吸收证据/画像重抽象/stale 自愈/治理计数汇总）与
status 常显（pending/未收口/冲突计数）覆盖；提醒逻辑瘦身为只看夜间编译在转
信号——夜间任务停转或 mema 长挂超过 7 天时重新提示。
"""
from __future__ import annotations

import datetime as _dt

from . import db, flow

SCHEDULED_TASKS_TOPIC = "scheduled_tasks"

# 提醒失效窗口：夜间编译（scheduled submit 刷 last_scheduled_compile_at）
# 在该天数内跑过即认为"定时体系在转"；超窗重新提示（停转保险丝）
SCAN_FRESH_DAYS = 7

AGENT_INSTRUCTION = (
    "Tell the user: mema-twin 建议创建一个定时任务：夜间 persona 编译（每天，"
    "专用一次性会话跑 status→compile→submit，把当天未编译偏好自动整理进 persona "
    "并落版，无证据不编；证据变动的受众画像同场重抽象落版；条款被作废的类型同场"
    "重编落版）。Ask whether to set it up now; on consent, create the equivalent "
    "task on your own platform from setup.tasks. The notice disappears by itself "
    "once the task has run recently — no report-back needed."
)

SCHEDULED_TASKS_SPEC: dict = {
    "tasks": [
        {
            "name": "twin_nightly_compile",
            "purpose": ("夜间无人值守的专用一次性编译会话（凌晨用户不在场，不向用户提问）："
                        "把各 work_type 当天未编译的偏好证据编译进 persona prompt 并 "
                        "submit 落版（未编译数为 0 的类型不编译不落版，避免版本号空转）；"
                        "再把 audience_stale 里证据数变动的受众重抽象成受众画像落版。"),
            "cadence": "daily",
            "calls": [
                {"tool": "twin", "action": "status",
                 "data": {"note": "uncompiled 是各 work_type 未编译数（只处理 >0 的类型）；"
                                  "audience_stale 是需重抽象的受众（证据数≠画像吸收数或尚无画像）"}},
                {"tool": "twin", "action": "compile",
                 "data": {"rule": "严格按素材包编译规则产出新版：素材为该类型全部在世证据"
                                  "（全量投影），与旧版本冲突以新证据为准，含义稳定表达自由，"
                                  "文末按变更分级列出版本间变更"}},
                {"tool": "twin", "action": "submit",
                 "data": {"origin": "scheduled",
                          "rule": "source ids 用素材包证据 id；origin=scheduled 必传"
                                  "（夜间落版标记，供次日首任务双跑对比提议；漏传等于"
                                  "放弃验证门）；submit 可能被拒：validation_failed"
                                  "（素材回声/缺分区标题）或 no_new_evidence（空转阻尼）"
                                  "——如实记录原因并跳过，不要为过门改产物（被拒时 "
                                  "active 未变、证据未消耗，次晚自动重试，连续被拒会在"
                                  " status 的 nightly_rejected 累计）；"
                                  "结束输出各类型前后版本号与吸收证据数汇总，"
                                  "并附 status 里的 pending_count 与未收口任务数各一句；"
                                  "工具出错跳过并如实记录，同一项最多重试一次"}},
                {"tool": "twin", "action": "compile",
                 "data": {"audience": True,
                          "rule": "对 audience_stale 的每个受众调 compile(work_type="
                                  "\"aud-{audience}\") 取画像素材包（该受众全部证据），"
                                  "按画像规则写出受众画像"}},
                {"tool": "twin", "action": "submit",
                 "data": {"origin": "scheduled",
                          "rule": "受众画像 submit 用 work_type=\"aud-{audience}\"、"
                                  "source_memory_ids=素材包全部证据 id（集合相等才免警告："
                                  "多报/漏报/混入他受众 id 只警告不拦，次晚自愈）；"
                                  "画像派生不消耗证据；"
                                  "audience_stale 为空则整体跳过"}},
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
    """夜间编译停转保险丝：scheduled submit 刷的 last_scheduled_compile_at
    7 天内跑过 → 无提醒（体系在转）；从未跑过或超窗 → 提醒安装/检查夜间任务。
    twin_scan 退役后单键判定（last_scan_at 为历史死键，不再参与）。"""
    flow.ensure_schema()
    now = _dt.datetime.now(_dt.timezone.utc)
    last = _parse_iso(flow.get_meta("last_scheduled_compile_at"))
    if last is not None and (now - last).days < SCAN_FRESH_DAYS:
        return None
    return {
        "type": "twin_nightly_compile_setup",
        "agent_instruction": AGENT_INSTRUCTION,
        "setup": SCHEDULED_TASKS_SPEC,
        "note": "提醒自消失：夜间编译（scheduled submit）7 天内跑过即不再提示",
    }
