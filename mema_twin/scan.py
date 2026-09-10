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

from . import flow

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
                        "submit 落版（uncompiled 为 0 且无 persona_stale 的类型不编译不"
                        "落版，避免版本号空转）；把 persona_stale 里条款被作废的类型重编"
                        "剔除对应条款落版；再把 audience_stale 里证据数变动的受众重抽象成"
                        "受众画像落版。"),
            "cadence": "daily",
            "calls": [
                {"tool": "twin", "action": "status",
                 "data": {"note": "uncompiled 是各 work_type 未编译数（只处理 >0 的类型）；"
                                  "audience_stale 是需重抽象的受众（证据数≠画像吸收数或尚无画像）；"
                                  "persona_stale 是条款被作废待重编的类型（同样夜间重编落版）"}},
                {"tool": "twin", "action": "pending",
                 "data": {"rule": "归一门待裁票据（v0.3.8）：pending 非空时取明细"
                                  "（type_kind/raw_value/hit_count=打回次数）列入收尾汇总——"
                                  "这些是三维度打回待用户裁定的长尾（map/canonicalize/reject），"
                                  "留待用户在场时裁定，夜间不代裁；为空则跳过本调用"}},
                {"tool": "twin", "action": "compile",
                 "data": {"rule": "严格按素材包编译规则产出新版：素材为该类型全部在世证据"
                                  "（全量投影），与旧版本冲突以新证据为准，含义稳定表达自由，"
                                  "文末按变更分级列出版本间变更；保守封套：无证据动机时"
                                  "不做整体重组（吸收新证据、剔除作废条款、预算合并照做），"
                                  "大重组留给用户在场的交互式编译"}},
                {"tool": "twin", "action": "submit",
                 "data": {"origin": "scheduled",
                          "rule": "source ids 用素材包证据 id；origin=scheduled 必传"
                                  "（夜间来源标记，三重用途：验证门 G1/G2 与空转阻尼仅对 "
                                  "scheduled 生效、刷新 last_scheduled_compile_at 停转保险丝"
                                  "——漏传等于绕过拦截且 7 天后误报体系停转）；submit 可能被拒：validation_failed"
                                  "（素材回声/缺分区标题）或 no_new_evidence（空转阻尼）"
                                  "——如实记录原因并跳过，不要为过门改产物（被拒时 "
                                  "active 未变、证据未消耗，次晚自动重试，连续被拒会在"
                                  " status 的 nightly_rejected 累计）；"
                                  "结束输出各类型前后版本号与吸收证据数汇总，"
                                  "并附 status 里的 pending_count 与未收口任务数各一句；"
                                  "响应带 mema_notices 时原样记录进汇总输出，"
                                  "留待用户在场时分诊（notice 是 advisory），夜间不处理；"
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
    twin_scan 退役后单键判定（last_scan_at 死键 v0.3.9 已从 twin_meta 删除）。"""
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
