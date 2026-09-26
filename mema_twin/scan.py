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

# 提醒失效窗口：夜间任务心跳（merged 任务每晚必调 task_evaluate(origin=scheduled)
# 刷 last_scheduled_playbook_at）
# 在该天数内跑过即认为"定时体系在转"；超窗重新提示（停转保险丝）
SCAN_FRESH_DAYS = 7

# 提示抑制窗口（v0.4.3，用户拍板）：每次实际提示时全局盖章（twin_meta 单键），
# 窗口内不再提示，窗口过后心跳仍未跑过则恢复提示。多宿主共接下第一个看到的
# agent 负责向用户提问，其余宿主静默——重复询问压到 3 天最多一次
SCAN_NOTICE_SNOOZE_DAYS = 3
SCAN_NOTICE_SHOWN_KEY = "scan_notice_shown_at"

AGENT_INSTRUCTION = (
    "Tell the user: mema-twin 建议创建一个定时任务：夜间 twin 会话（每天，"
    "专用一次性会话先跑执行经验评估支线 status→task_evaluate(origin=scheduled)"
    "→playbook_submit（把当天收口任务的步骤/反思/工具记录沉淀进 playbook，"
    "无新经验不落版，evaluate 调用即刷新停转保险丝心跳）再跑 persona 编译支线 "
    "pending→compile→submit（把当天未编译偏好自动整理进 persona 并落版，"
    "无证据不编；证据变动的受众画像同场重抽象落版；条款被作废的类型同场重编"
    "落版），两支线各有无素材早退规则）。"
    "Ask whether to set it up now; on consent, create the equivalent "
    "task on your own platform from setup.tasks. The notice disappears by itself "
    "once the task has run recently — no report-back needed."
)

SCHEDULED_TASKS_SPEC: dict = {
    "tasks": [
        {
            "name": "twin_nightly_compile",
            "purpose": ("夜间无人值守的专用一次性会话（凌晨用户不在场，不向用户提问），"
                        "两个支线顺序执行、各自无素材早退：①执行经验评估——把当天收口"
                        "任务的步骤/反思/疑问时序/工具使用记录评估成 playbook 更新"
                        "（失败规避、工具路径、owner 澄清沉淀），playbook_submit 落版，"
                        "无值得沉淀的新经验则不提交不落版；②persona 编译——把各 "
                        "work_type 当天未编译的偏好证据编译进 persona prompt 并 submit "
                        "落版（uncompiled 为 0 且无 persona_stale 的类型不编译不落版，"
                        "避免版本号空转）；把 persona_stale 里条款被作废的类型重编剔除"
                        "对应条款落版；再把 audience_stale 里证据数变动的受众重抽象成"
                        "受众画像落版。评估支线放最前：它轻且快，防被最重的编译段"
                        "吃掉会话预算后永远轮不到（水位在，漏一晚次晚自动补）。"),
            "cadence": "daily",
            "calls": [
                {"tool": "twin", "action": "status",
                 "data": {"note": "uncompiled 是各 work_type 未编译数（只处理 >0 的类型）；"
                                  "audience_stale 是需重抽象的受众（证据数≠画像吸收数或尚无画像）；"
                                  "persona_stale 是条款被作废待重编的类型（同样夜间重编落版）",
                          "rule": "同时看 plan_stats：tasks_unevaluated=0 时评估支线整体"
                                  "跳过（无素材不空转）；两支线都无素材则本次会话到此结束"}},
                {"tool": "twin", "action": "task_evaluate",
                 "data": {"origin": "scheduled",
                          "rule": "origin=scheduled 必传——调用成功即刷新停转保险丝心跳"
                                  "（与当晚有无新经验无关，漏传等于 7 天后误报任务停转）；"
                                  "无 task_ids 取未评估任务（≤10）；响应 task_count=0 "
                                  "则评估支线结束（心跳已刷）；素材只含真实执行记录，"
                                  "编译产物每条必须 <!-- task: N --> 溯源，写不出来源"
                                  "的条目不得出现"}},
                {"tool": "twin", "action": "playbook_submit",
                 "data": {"origin": "scheduled",
                          "rule": "key 用 key_hint（或覆盖面最大的 work_type；跨类型"
                                  "经验用 key='global'）；source_task_ids=素材包全部"
                                  "任务 id；可能被拒：validation_failed（溯源无效/素材"
                                  "回声/缺标题）或 no_new_evidence（空转阻尼）——如实"
                                  "记录原因并跳过，不要为过门改产物（被拒时 active "
                                  "未变，次晚自动重试，连续被拒在 status 的 "
                                  "playbook_rejected 累计）；无新经验则不提交；"
                                  "工具出错跳过并如实记录，同一项最多重试一次"}},
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
                                  "（夜间来源标记，验证门 G1/G2 与空转阻尼仅对 scheduled "
                                  "生效——漏传等于绕过拦截）；submit 可能被拒："
                                  "validation_failed（素材回声/缺分区标题）或 "
                                  "no_new_evidence（空转阻尼）"
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
    """夜间任务停转保险丝（v0.4.1 合并单任务后单键）：心跳键
    last_scheduled_playbook_at 由合并任务的 task_evaluate(origin=scheduled) 每晚
    必调刷新（与当晚有无新经验无关）——编译支线连续无素材也不会误报；老形态
    只建 persona 编译任务、不调 task_evaluate 的宿主永远过不了保险丝，会被持续
    引导升级成合并 spec（对抗评审轮2 P3-13 同源问题的收口）。"""
    flow.ensure_schema()
    now = _dt.datetime.now(_dt.timezone.utc)
    last = _parse_iso(flow.get_meta("last_scheduled_playbook_at"))
    if last is not None and (now - last).days < SCAN_FRESH_DAYS:
        return None
    # 提示抑制（v0.4.3）：盖章在生成时（claim 即 delivered，与 twin_notices 同款
    # 取舍——响应送达前进程崩溃的盲窗内多静默一窗，可接受）；全局单键，多宿主
    # 共享同一抑制窗口
    shown = _parse_iso(flow.get_meta(SCAN_NOTICE_SHOWN_KEY))
    if shown is not None and (now - shown).days < SCAN_NOTICE_SNOOZE_DAYS:
        return None
    flow.set_meta(SCAN_NOTICE_SHOWN_KEY, db.now_iso())
    return {
        "type": "twin_nightly_compile_setup",
        "agent_instruction": AGENT_INSTRUCTION,
        "setup": SCHEDULED_TASKS_SPEC,
        "note": "提醒自消失：夜间任务 7 天内跑过（task_evaluate origin=scheduled "
                "心跳）即不再提示；本次提示后 3 天内不重复（3 天后仍未在转会再提醒）",
    }
