"""定时任务 spec 与提醒逻辑（M2.2，v0.3.7 收敛为单一夜间编译任务）。

单一真源：AGENT_INSTRUCTION、任务 spec、help topic、提醒都从本模块渲染，防止
漂移。平台无关（mema 2026-09-02 owner decision 同款）：twin 自己不起任何调度，
Agent 拿指令后在宿主平台创建等价任务。twin_scan（每周治理扫描）已随 v0.3.7
退役：其职能被夜间编译任务（吸收证据/画像重抽象/stale 自愈/治理计数汇总）与
status 常显（pending/未收口计数）覆盖。
v0.6.1：提醒判据从「心跳 7 天停转保险丝」切换为「积压驱动」（owner 2026-11-21
拍板）——心跳的盲区（评估积压的消化是 playbook 落版、不刷 persona 落版时刻）
由逐信号锚修复；夜编会话领走的提醒章由 task_evaluate(origin=scheduled) 清除，
把提醒还给白天的人类会话。
"""
from __future__ import annotations

import datetime as _dt
import json

from . import db, flow

SCHEDULED_TASKS_TOPIC = "scheduled_tasks"

# 积压阈值（v0.6.1，owner 拍板）：任一积压信号的形成/最老未消化时刻距今超过
# 该天数才提醒——手动编译勤的库不打扰，真有积压没人管才响
SCAN_BACKLOG_DAYS = 3

# 提示抑制窗口（v0.4.3，用户拍板）：每次实际提示时全局盖章（twin_meta 单键），
# 窗口内不再提示。多宿主共接下第一个看到的 agent 负责向用户提问，其余宿主
# 静默——重复询问压到 3 天最多一次。夜间任务会话领走的章由
# task_evaluate(origin=scheduled) 清除（夜编清章），保证白天人类会话可见
SCAN_NOTICE_SNOOZE_DAYS = 3
SCAN_NOTICE_SHOWN_KEY = "scan_notice_shown_at"

# 提醒口径文案（help topic=scheduled_tasks 与 scan_notice 共用单一真源，P2-2）
SCAN_NOTICE_NOTE = (
    "积压提醒口径：存在待编译积压（未编译偏好/条款待重编/受众画像待重抽象/"
    "执行经验待评估）且超过 3 天未落版消化时提醒，消化即消失；本次提示后 "
    "3 天内不重复（夜编会话领走的章当晚自动清除）")

AGENT_INSTRUCTION = (
    "Tell the user: mema-twin 检测到待编译积压超过 3 天未消化，建议创建一个"
    "定时任务：夜间 twin 会话（每天，专用一次性会话先跑执行经验评估支线 "
    "status→task_evaluate(origin=scheduled)→playbook_submit（把当天收口任务"
    "的步骤/反思/工具记录沉淀进 playbook，无新经验不落版）再跑 persona 编译"
    "支线 pending→compile→submit（把当天未编译偏好自动整理进 persona 并落版，"
    "无证据不编；证据变动的受众画像同场重抽象落版；条款被作废的类型同场重编"
    "落版），两支线各有无素材早退规则）。"
    "若你本人就是该夜间任务会话（无人值守）：不提问、不建任务、不请求确认，"
    "仅在收尾汇总记一行积压，留待白天处理。"
    "已建夜间任务但积压仍超 3 天未消化（编译连续被拒/评估停转）也会持续提醒"
    "——查 status 的 nightly_rejected/playbook_rejected 排查。"
    "Ask whether to set it up now; on consent, create the equivalent "
    "task on your own platform from setup.tasks. The notice disappears by itself "
    "once the backlog is cleared, not merely because the task ran."
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
            # v0.6 A8-3（对抗 R2 #1）：data 只放真参数（B8 顶层未知键硬拒——说明
            # 文本混进 data 会让夜间会话照抄即被打回）；执行说明一律放 instruction
            # 键（不进 data，照抄无害）。
            "calls": [
                {"tool": "twin", "action": "status", "data": {},
                 "instruction": ("uncompiled 是各 work_type 未编译数（只处理 >0 的类型）；"
                                 "audience_stale 是需重抽象的受众（证据数≠画像吸收数或尚无画像）；"
                                 "persona_stale 是条款被作废待重编的类型（同样夜间重编落版）。"
                                 "同时看 plan_stats：tasks_unevaluated=0 时评估支线整体"
                                 "跳过（无素材不空转）；两支线都无素材则本次会话到此结束")},
                {"tool": "twin", "action": "task_evaluate",
                 "data": {"origin": "scheduled"},
                 "instruction": ("origin=scheduled 必传——夜间来源标记，验证门与"
                                 "空转阻尼仅对 scheduled 生效；调用成功会清除夜间"
                                 "会话领走的积压提醒盖章（把提醒还给白天的人类会话）；"
                                 "无 task_ids 取未评估任务（≤10）；响应 task_count=0 "
                                 "则评估支线结束；素材只含真实执行记录，"
                                 "编译产物每条必须 <!-- task: N --> 溯源，写不出来源"
                                 "的条目不得出现")},
                {"tool": "twin", "action": "playbook_submit",
                 "data": {"origin": "scheduled"},
                 "instruction": ("key 用 key_hint（或覆盖面最大的 work_type；跨类型"
                                 "经验用 key='global'）；source_task_ids=素材包全部"
                                 "任务 id；可能被拒：validation_failed（溯源无效/素材"
                                 "回声/缺标题）或 no_new_evidence（空转阻尼）——如实"
                                 "记录原因并跳过，不要为过门改产物（被拒时 active "
                                 "未变，次晚自动重试，连续被拒在 status 的 "
                                 "playbook_rejected 累计）；无新经验则不提交；"
                                 "工具出错跳过并如实记录，同一项最多重试一次")},
                {"tool": "twin", "action": "pending", "data": {},
                 "instruction": ("归一门待裁票据（v0.3.8）：pending 非空时取明细"
                                 "（type_kind/raw_value/hit_count=打回次数）列入收尾汇总——"
                                 "这些是三维度打回待用户裁定的长尾（map/canonicalize/reject），"
                                 "留待用户在场时裁定，夜间不代裁；为空则跳过本调用")},
                {"tool": "twin", "action": "compile",
                 "data": {"work_type": "<uncompiled>0 或 persona_stale 的每个 work_type code>"},
                 "instruction": ("data.work_type 填实际 code（上例占位）。严格按素材包编译规则"
                                 "产出新版：素材为该类型全部在世证据（全量投影），与旧版本冲突以"
                                 "新证据为准，含义稳定表达自由，文末按变更分级列出版本间变更；"
                                 "保守封套：无证据动机时不做整体重组（吸收新证据、剔除作废条款、"
                                 "预算合并照做），大重组留给用户在场的交互式编译")},
                {"tool": "twin", "action": "submit",
                 "data": {"work_type": "<同 compile 的 code>", "prompt_md": "<编译产物>",
                          "source_memory_ids": "<compile 响应 source_ids_suggested 原样复制>",
                          "origin": "scheduled"},
                 "instruction": ("source_memory_ids 直接复制 compile 响应的 "
                                 "source_ids_suggested（v0.6 起无需解析素材包正文）；"
                                 "origin=scheduled 必传（夜间来源标记，验证门 G1/G2 与空转"
                                 "阻尼仅对 scheduled 生效——漏传等于绕过拦截）；submit 可能"
                                 "被拒：validation_failed（素材回声/缺分区标题）或 "
                                 "no_new_evidence（空转阻尼）——如实记录原因并跳过，不要为"
                                 "过门改产物（被拒时 active 未变、证据未消耗，次晚自动重试，"
                                 "连续被拒会在 status 的 nightly_rejected 累计）；"
                                 "结束输出各类型前后版本号与吸收证据数汇总，"
                                 "并附 status 里的 pending_count 与未收口任务数各一句；"
                                 "工具出错跳过并如实记录，同一项最多重试一次")},
                {"tool": "twin", "action": "compile",
                 "data": {"work_type": "<aud-{受众}>"},
                 "instruction": ("对 audience_stale 的每个受众调 compile(work_type="
                                 "\"aud-{audience}\") 取画像素材包（该受众全部证据），"
                                 "按画像规则写出受众画像")},
                {"tool": "twin", "action": "submit",
                 "data": {"work_type": "<aud-{受众}>", "prompt_md": "<画像产物>",
                          "source_memory_ids": "<画像 compile 响应 source_ids_suggested 原样复制>",
                          "origin": "scheduled"},
                 "instruction": ("受众画像 submit 用 work_type=\"aud-{audience}\"、"
                                 "source_memory_ids 复制画像 compile 响应的 "
                                 "source_ids_suggested（集合相等才免警告："
                                 "多报/漏报/混入他受众 id 只警告不拦，次晚自愈）；"
                                 "画像派生不消耗证据；"
                                 "audience_stale 为空则整体跳过")},
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


def backlog_signals(conn) -> dict:
    """积压四信号 + 各自未消化锚（v0.6.1 S8 逐信号锚，R1 P0-1 修正）。

    返回 {kind: {"anchor": iso|None, "detail": ...}}；anchor=该积压的形成/最老
    未消化时刻，None=无法判定（保守视为不超窗）。设计要点：
    - 评估积压的消化动作是 playbook 落版（不写 prompt_versions）——所以各信号
      用自己的锚，不能用全局 MAX(created_at)（R1 P0-1：全局锚会重新挖瞎旧心跳
      保险丝本职防的「只编 persona 不评估」盲区）。
    - audience_stale 锚=该受众 active 画像版本 created_at（画像派生不消耗证据，
      最老证据可数月龄，MIN 会恒超窗白天误报——R2 炸-1）；无画像分支用受众最老
      在世证据 created_at。"""
    out: dict = {}
    # ① uncompiled
    stats = db.evidence_stats(conn)
    if stats:
        row = conn.execute(
            "SELECT MIN(created_at) AS m FROM twin_evidence WHERE status='uncompiled'"
            " AND work_type IS NOT NULL AND work_type NOT LIKE 'aud-%'"
        ).fetchone()  # aud- 行永不消化（画像派生不消耗），不入类型积压锚（R2 险-1）
        out["uncompiled"] = {"anchor": row["m"], "detail": dict(stats)}
    # ② persona_stale（meta rec.at 已有记账）
    stale = flow.list_meta("persona_stale:")
    if stale:
        ats = []
        parsed = {}
        for key, raw in stale.items():
            try:
                rec = json.loads(raw)
            except (ValueError, TypeError):
                rec = {}
            at = rec.get("at") if isinstance(rec, dict) else None
            ats.append(at)
            parsed[key[len("persona_stale:"):]] = rec.get("voided") or []
        ats = [a for a in ats if a]
        out["persona_stale"] = {"anchor": min(ats) if ats else None,
                                "detail": parsed}
    # ③ audience_stale（与 pref_actions._action_status 同源口径）
    aud_counts = conn.execute(
        "SELECT audience, COUNT(*) AS n, MIN(created_at) AS oldest"
        " FROM twin_evidence WHERE audience IS NOT NULL AND work_type IS NOT NULL"
        " AND status != 'void' GROUP BY audience").fetchall()
    stale_auds: dict = {}
    for r in aud_counts:
        aud = r["audience"]
        pcode = f"aud-{aud}"
        prof = conn.execute(
            "SELECT created_at, evidence_count FROM twin_prompt_versions"
            " WHERE work_type=? AND status='active' ORDER BY version DESC LIMIT 1",
            (pcode,)).fetchone()
        if prof is None or prof["evidence_count"] != int(r["n"]):
            stale_auds[aud] = {"evidence": int(r["n"]),
                               "profile_evidence": (prof["evidence_count"]
                                                    if prof else None)}
            out.setdefault("audience_stale", {"anchor": None, "detail": {}})
            # 锚：有画像用画像版本时刻（画像落后多久），无画像用最老证据时刻
            anchor = prof["created_at"] if prof is not None else r["oldest"]
            old = out["audience_stale"]["anchor"]
            if old is None or (anchor or "") < old:
                out["audience_stale"]["anchor"] = anchor
            out["audience_stale"]["detail"][aud] = stale_auds[aud]
    # ④ tasks_unevaluated（水位口径与 _plan_stats 同源）
    wm = int(flow.get_meta("plan_eval_watermark") or 0)
    row = conn.execute(
        "SELECT COUNT(*) AS c, MIN(decided_at) AS oldest FROM twin_tasks"
        " WHERE status IN ('submitted','superseded') AND id > ?",
        (wm,)).fetchone()
    if int(row["c"]) > 0:
        out["tasks_unevaluated"] = {"anchor": row["oldest"],
                                    "detail": {"count": int(row["c"])}}
    return out


def overdue_signals(signals: dict, now: _dt.datetime | None = None) -> list[str]:
    """从 backlog_signals 结果算超窗信号清单（锚脏值/缺失视为不超窗，保守）。"""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    out = []
    for k, s in signals.items():
        t = _parse_iso(s.get("anchor"))
        if t is not None and (now - t).days >= SCAN_BACKLOG_DAYS:
            out.append(k)
    return out


def scan_notice() -> dict | None:
    """积压提醒（v0.6.1 积压驱动，替代 v0.4.1 心跳停转保险丝）：任一积压信号
    （未编译/条款待重编/画像待重抽象/执行经验待评估）超 3 天未消化 → 提醒建
    夜间任务；消化即自消失。夜编会话领走的章由 task_evaluate(origin=scheduled)
    清除（R2 炸-2），保证白天人类会话可见。"""
    flow.ensure_schema()
    now = _dt.datetime.now(_dt.timezone.utc)
    conn = db.connect()
    try:
        signals = backlog_signals(conn)
    finally:
        conn.close()
    # 锚逐个解析：脏时间戳（旧 meta rec.at 可能是任意文本）→ 解析失败视为不超窗
    # （保守），不炸提醒面
    overdue = overdue_signals(signals, now)
    if not overdue:
        return None
    # 提示抑制（v0.4.3）：盖章在生成时（claim 即 delivered，与 twin_notices 同款
    # 取舍——响应送达前进程崩溃的盲窗内多静默一窗，可接受）；全局单键，多宿主
    # 共享同一抑制窗口；夜编领走的章由 task_evaluate(origin=scheduled) 清除
    shown = _parse_iso(flow.get_meta(SCAN_NOTICE_SHOWN_KEY))
    if shown is not None and (now - shown).days < SCAN_NOTICE_SNOOZE_DAYS:
        return None
    flow.set_meta(SCAN_NOTICE_SHOWN_KEY, db.now_iso())
    return {
        "type": "twin_nightly_compile_setup",
        "agent_instruction": AGENT_INSTRUCTION,
        "setup": SCHEDULED_TASKS_SPEC,
        "overdue_signals": overdue,
        "backlog": {k: s["detail"] for k, s in signals.items()},
        "note": SCAN_NOTICE_NOTE,
    }
