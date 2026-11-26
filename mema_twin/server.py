"""mema-twin MCP server：单工具 twin(action, data)，动作式紧凑接口（仿 mema/plan-mode 风格）。

v0.3.8 拆分（单文件 ≤1000 行，设计文档 E）：本模块只留工具面——分发与边界、
宿主身份头读取、租户路由、动作注册表、help、传输层。动作实现分散到
pref_actions（偏好与治理）/ compile_actions（编译与版本）/ task_actions（交付
任务流）；身份校验在 identity（contextvar 注入，动作模块调用点形态不变）。
依赖单向：server → actions → (identity, db, flow, store, normalize, taxonomy,
sink, scan, templates)，无环。
v0.5.0 拆分（#1604）：mema notices 透传随 sink 数据面退役——偏好自持本库后
不再有任何 mema 数据通道；sink 只剩 whoami（多租户路由）与宿主身份 env 校验。
"""
from __future__ import annotations

import difflib
import json
import sqlite3
import time
from typing import Annotated, Literal

import anyio.to_thread
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent
from pydantic import Field

from . import audit, identity, playbook_actions, pref_actions, scan, sink, task_actions
from . import templates, update_monitor
from . import compile_actions, exec_actions
from . import tenant


def _action_help(data: dict) -> dict:
    topic = str(data.get("topic") or "").strip()
    if topic == scan.SCHEDULED_TASKS_TOPIC:
        return {
            "ok": True, "topic": scan.SCHEDULED_TASKS_TOPIC,
            "description": "twin 定时任务 spec（单任务双支线：夜间执行经验评估 → 夜间 persona 编译）：Agent 据此在宿主平台创建等价任务。",
            "agent_instruction": scan.AGENT_INSTRUCTION,
            "setup": scan.SCHEDULED_TASKS_SPEC,
            "note": scan.SCAN_NOTICE_NOTE,
        }
    if topic:
        entry = _HELP_ENTRIES.get(topic)
        if entry is None:
            return {"ok": False, "error": "invalid_input",
                    "reason": f"unknown topic {topic!r}",
                    "topics": sorted([*_HELP_ENTRIES, scan.SCHEDULED_TASKS_TOPIC])}
        out = {"ok": True, "topic": topic, "help": entry}
        if topic == "write":
            # write 单查附沉淀规则文本（方案 A1：write 额外附 write_guidance，
            # 单查也自足）
            out["write_guidance"] = templates.WRITE_GUIDANCE
        return out
    return {
        "ok": True,
        "actions": dict(_HELP_ENTRIES),
        "write_guidance": templates.WRITE_GUIDANCE,
        "note": templates.STRONG_MODEL_NOTE,
    }


_HELP_ENTRIES: dict[str, str] = {
    "write": "沉淀一条工作偏好（v0.5.0 起偏好本体自持本库，不经 mema）。"
             "必填 content/work_type/audience/purpose"
             "（先 taxonomy 查清单选码；清单无合适项的值会被整笔打回并附清单——"
             "必须问用户：归一到已有值（resolve map）还是创建新值（resolve "
             "canonicalize，即刻入清单），裁定后重试）；"
             "改稿/返工现场沉淀时可传 task_id：缺省维度沿用该任务的三维度"
             "（task_start 已归一；scope=audience 时不继承 work_type；显式传入优先，"
             "响应 dims_inherited 列出继承项）；"
             "可选 subject/tags/source_ref/client（多 Agent 共接时 client 填宿主标识，如 kimi/jinleai）。"
             "对该受众的通用偏好（不限工作类型）传 scope=audience（此时 work_type 省略）。"
             "上限：content ≤8000 字符，维度各 ≤200 字符。"
             "同类型下内容完全相同的偏好命中词法去重：返回 ok+duplicate=true"
             "（幂等成功），请勿重试也无需改写。"
             "落库后响应可能带 similar_existing（字面相似的既有偏好，ratio≥0.82，"
             "纯词法非语义判定，不拦截）与 orphan_pending_tickets（你此前被拒的"
             "挂票疑似已被本次取代——确认后 resolve(reject) 关闭，勿盲目关不同"
             "维度的票）。维度值撞清单会整笔打回并附 all_values 全量与说明——"
             "可以直接写不预查清单；但先查 taxonomy 再写更优：拥有完整分类视野、"
             "自主对齐已有口径，owner 不需要被打断来裁值。",
    "status": "查看各 work_type 的 prompt 版本概况（v0.6.4 起版本/画像明细"
              "只含**当前行**——active 优先；历史版本经 get(version=) 按需查，"
              "rollback 版本发现免查：省略 version 即回上一版；体积/超预算标记）"
              "、受众画像"
              "（audience_profiles）、需重抽象的受众（audience_stale）、条款作废待重编"
              "（persona_stale）、pending 数量（归一门待裁票据）、未编译统计、夜间被拒计数"
              "（nightly_rejected）、进行中任务计数、执行计划统计（plan_stats："
              "任务/步骤各态/backfilled/open blocking 疑问/tool_usage/playbooks/"
              "playbook_rejected/采纳率 submitted_with_plan÷tasks_submitted——"
              "分母 0 无读数，历史 submitted 会稀释，看增量任务）"
              "与调用审计统计（audit：总调用/失败/近 24h/top 动作，环形缓冲近 2 万条，"
              "MEMA_TWIN_AUDIT=0 关闭）与定时任务安装提醒。",
    "compile": "取编译素材包（旧版本 prompt 编译参考 + **全部在世证据**（全量投影，"
               "每版从头重编，正文本地自取）+ 已作废条款清单 + 同受众画像参考 + 编译规则"
               "（含义稳定表达自由/变更分级/硬预算）），"
               "由当前会话模型编译；独立会话执行、收尾即弃（session_note）。参数 work_type；"
               "传 aud-{受众} 取受众画像素材包（该受众全部证据）。"
               "响应附 evidence_ids/source_ids_suggested/voided_ids 结构化数组"
               "——submit 的 source_memory_ids 直接复制 source_ids_suggested"
               "（在世全集，含缺正文 skipped 行），无需解析素材包正文。",
    "submit": "提交编译产物落版本并写文件镜像。必填 work_type/prompt_md（≤100000 字符）；"
              "建议带 source_memory_ids（从 compile 响应 source_ids_suggested 复制——"
              "不是 evidence_ids，后者缺缺正文 skipped 行，漏列会以增补形式永久注入）"
              "与 model。"
              "返回 supersedes（被取代的旧 active 版本）；"
              "夜间定时任务落版必传 origin=scheduled（其余场景不要传），落版前过验证门"
              "（素材回声/分区标题未过拒绝且 active 不变、证据不动；无新证据可吸收时空转阻尼"
              "拒绝落版——两者都会在 status 的 nightly_rejected 累计显示）；交互式 submit "
              "只警告不拦；证据未全覆盖只出警告；work_type 传 aud-{受众} 即受众画像落版"
              "（derived，不消耗证据）。",
    "rollback": "回滚 persona 版本（零阻力：无确认、无警告）。work_type 必填；"
                "version 省略回上一版本、传 n 回指定版本；不删历史（retired 可再激活），"
                "版本号不回收（下次 submit 继续 MAX+1）。已是 active 版本时返回 "
                "already_active=true（幂等 no-op，非错误）。",
    "get": "取某 work_type 的 persona prompt（DB 优先，文件镜像降级）。参数 work_type；"
           "可选 version 取指定历史版本全文（不带未编译增补）；"
           "work_type 传 aud-{受众} 可读受众画像（不带增补）。"
           "读 active 时响应附 persona_supplement（未编译增补，与 prompt 冲突"
           "以增补为准；软失败不阻断全文）——上下文被压缩/跨会话取回分身时用它。",
    "taxonomy": "列枚举清单（动态：含治理追加别名与自建 canonical）。参数 kind ∈ "
                "work_type|audience|purpose，或数组一次取多类、'all' 取全部"
                "（判别式=响应有无 kinds 键；多类形状顶层 count_total，条目附 "
                "kind 字段；数组传单元素也走多类形状）。compact 仅接受布尔 true"
                "（精简投影 code/zh/aliases 前 5——省 token 拉取用；传 \"true\"/1 "
                "等非布尔会被打回；完整别名面只在非 compact 响应里）。",
    "pending": "列归一门待裁票据（打回的长尾值，含打回次数、宿主归属与快照"
               "来源 source_kind ∈ write|task_start|unknown——task_start 来源"
               "的快照是建档 brief 非偏好正文）。items 带 content_preview 前 "
               "200 字符与 content_chars——全文在 resolve 裁定响应里取。"
               "参数 status ∈ pending|mapped|canonicalized|rejected（默认 pending）。",
    "resolve": "治理待裁值。单条：pending_id + decision ∈ map(带 code)|canonicalize"
               "(带 new_type{code,zh,en,domain,description?})|reject"
               "（reject=不入体系并放弃本次写入，不得拿原值重试）。"
               "响应带 content_snapshot（被拒写入的正文全文，有快照时才附）与 "
               "content_source/content_source_note（v0.6.3 按来源区别引导：write "
               "来源可直接照抄 write；task_start 来源的快照是建档 brief 非偏好——"
               "照抄会产出垃圾证据，仅在含真实偏好时重写后再 write）。跨会话积压 "
               "3 步闭环零丢失。"
               "canonicalize 相似确认门：新值与"
               "现有值 ratio≥0.82 或精确撞别名时打回 require_confirm——对照 "
               "all_values 确认非重复后带 confirm=true 重试。"
               "批量（v0.6）：tickets=[{pending_id, decision, code?|new_type?, "
               "confirm?}]（1..20 条，与单条互斥；confirm 预扫摘出疑似重复票不进"
               "事务，余票单事务全落/全回滚带 failed_index）；裁定后直接重试原写入。",
    "void": "作废一条偏好证据（冲突裁定「新替旧/撤销新写的」的执行机制）。参数 memory_id；"
            "行级作废、全链路排除、不可逆（正文在本库归档保留）；曾入编译的证据会"
            "触发 persona_stale 夜间重编（响应 persona_stale=true 显式标记），受众证据"
            "计数变化触发画像重抽象（响应 audience_stale 列出受众）。"
            "memory_id 未知先调 evidence 动作列索引。",
    "evidence": "列偏好证据索引（只读，定位 memory_id 用；不回正文全文，正文走 console）。"
                "可选 status ∈ uncompiled|compiled|void（缺省全部）、work_type"
                "（canonical code）、limit（默认 50，上限 200）。响应 items 含 "
                "memory_id/work_type/audience/purpose/subject/status/created_at/"
                "content_chars。",
    "task_start": "开工建档（流程注入点）。必填 brief/work_type（audience/purpose 可选，"
                  "但给了值必须在清单内——未命中整笔打回，须问用户裁定后重试）；"
                  "上限：brief ≤2000 字符；available_tools ≤50 项、每项 ≤100 字符。"
                  "返回该工作性质的 persona prompt 与前置清单，进行中任务自动让位。"
                  "该工种尚无专属 persona 时注入同域 fallback（#956 拍板）：同域"
                  "最成熟工种（活证据最多）persona 全文垫底 + 其余同域候选摘要 +"
                  " 降级声明（格式结构可参考、有权威来源以权威为准、内容业务规则"
                  "不适用），专属落版自动退出。"
                  "可选 have_persona_version：同一会话此前注入过同 work_type 且版本号仍在场时申报，"
                  "版本未变则不再重复注入全文，变了则重注入并附变更说明。"
                  "可选 have_audience_versions：对象 {受众码: 版本号}——键=受众裸码"
                  "（如 direct_manager；aud-<合法裸码> 形态会被自动归一；中文名/别名"
                  "不用于申报），值=该受众画像版本号。同一会话此前注入过该受众画像全文且"
                  "版本仍在场时申报，版本未变则不再重复注入全文（只回 "
                  "audience_profile_code / audience_profile_version / "
                  "audience_profile_unchanged 三键），变了则重注入并附变更说明；"
                  "不申报=照常全文注入（宁重复不缺席）；上文已不可见（被上下文压缩）"
                  "就不要申报。申报键不合法不影响本次任务——未识别键会被忽略并在"
                  "响应 have_audience_versions_unknown 警告。"
                  "响应另带 persona_supplement：该 work_type 未编译偏好增补"
                  "（与 prompt 冲突以增补为准；上限 10 条）。"
                  "client 字段同 write（http 共接时头已带则无需传）。",
    "task_submit": "提交交付稿并收口（submit 即终点，v0.3.8 无评审环）：落盘"
                   "deliverables/task-N.md 审计文件。必填 task_id/deliverable_md；"
                   "可带 todos/session/note；可带 steps（v0.4.7，同 step_update "
                   "批量结构且须属本任务）：先补打卡再收口一次往返——steps 已落库"
                   "后被拒时响应带 steps_applied，修正后直接重试，无需重新打卡。"
                   "交付后：用户对产物的反馈走 twin.write；本次执行中没来得及记的"
                   "路径与踩坑走 tool_log(task_id, note)——此时步骤都已 done，"
                   "补 step_update(done) 带 reflection 不会写入。",
    "task_resume": "续作进行中任务（仅 planning，可中断可继续）。task_id；恢复 todos、"
                   "新建 planning 任务并再注入 persona"
                   "（含未编译增补 persona_supplement 与无专属 persona 时的"
                   " 同域 fallback，同 task_start）。"
                   "可选 available_tools 覆盖建档自报值（playbook 工具面板比对用）。"
                   "have_persona_version / have_audience_versions 申报口径同 task_start"
                   "（受众键=受众裸码）；"
                   "已交付（submitted）的返工走 task_revise。",
    "task_revise": "已交付任务修订返工（仅 submitted）。task_id + brief/deliverable_md/"
                   "revision_reason 至少其一；子任务回 planning 重走执行并记 lineage"
                   "（不恢复 todos；v0.4.8 起 persona/未编译增补/受众画像/playbook"
                   " 按 task_resume 同款注入）。可选 available_tools 覆盖沿用值。"
                   "have_persona_version 仅当同一会话"
                   "上文已注入该版本全文时才申报——从任务行抄号申报会短路掉全文注入"
                   "（跨会话返工不传即自动全文重注入）。"
                   "have_audience_versions 申报口径同 task_start（键=受众裸码），"
                   "同款告诫：跨会话返工上文不可见就不要申报——从旧响应抄号申报会"
                   "短路掉全文注入。"
                   "修订反馈中的可复用偏好走 twin.write 沉淀。",
    "task_close": "显式关闭进行中任务（仅 planning），历史保留可审计。"
                  "可选 outcome ∈ failed|superseded（缺省 superseded）——"
                  "失败要显式标 failed，否则结果列与未完成任务无法区分；"
                  "可选 reason 记录关闭缘由；"
                  "关闭时未闭环步骤自动按 closed 跳过。",
    "task_recent": "最近任务列表。参数 limit（默认 10）。",
    "task_get": "取单个任务全量。task_id。",
    "todo": "会话 todo 读写（plan-mode 同款语义：整体替换，至多一条 in_progress）。"
            "session 用于会话 todo/任务流隔离，仅 task_start/task_resume/"
            "task_revise/task_submit/todo 五动作接受；plan/step 族与治理动作"
            "不消费（传了会被未知键门打回）。传 todos 替换，不传读取。",
    "plan_set": "建执行计划（开工第一件事；凡建档任务默认走计划，拿不准时建）。"
                "必填 steps（[{title, description?, depends_on?}]，"
                "task_id 可省略：全局恰一个 planning 任务时自动回退"
                "（响应 task_id_resolved 与 resolved_brief 回显被解析任务，供核对），"
                "0 个或多个 planning 任务时打回并附清单；"
                "depends_on 引用 1-based 序号，不许自引用/成环）；"
                "可选 open_questions（[{question, step_ids?, blocking?}]，"
                "blocking 缺省 false）——没把握、确认不了的点列在这里，要紧的"
                "标 blocking，先与用户澄清，答复经 plan_revise(answers=…) 写回。"
                "注意 open_questions 的 step_ids 入参用**步骤序号**（1-based），"
                "响应里同名键是**全局步骤 id**（同名异义，换算以响应 seq_map 为准）。"
                "可选 start_first=true（v0.6，严格布尔）：计划落库即把无依赖且未被 "
                "open blocking 疑问锚定的首步标 in_progress（省一次 step_update "
                "往返）；全部首步被锚定时响应 start_first_skipped=true 说明原因。"
                "响应即可开工，无审批门。重复调用=重建计划（旧未完结步骤按 "
                "replanned 跳过，done 历史保留）。",
    "step_update": "步骤打卡。单条：step_id/status（六态：pending/in_progress/done/"
                   "failed/blocked/skipped）；skipped/blocked 必填 reason；"
                   "failed 必填 reflection（服务端硬门，≤2000 字符）；"
                   "done 可选带 reflection（≤2000 字符）——这一步的做法值得下次沿用"
                   "时写「下次怎么做」，夜间评估提炼进 playbook；常规步骤、按默认做法"
                   "完成的步骤不写（一个任务通常 0–2 条）；必须在迁 done 那一次带，"
                   "之后补写不生效（后补反思走 tool_log(task_id, note)）；"
                   "执行经验走这里，不要 twin.write；"
                   "可选 task_id：传了即校验步骤归属本任务（步骤 id 全局连发，防"
                   "猜错 id 误标他任务步骤——v0.6.1）；"
                   "pending 直跳 done/failed"
                   " 自动标 backfilled（追认合法）。三门：blocking 疑问未解答、"
                   "依赖未闭环、已有单一 in_progress 时推进会被拒（报错附具体清单"
                   "与下一步做法）。"
                   "同状态重复打卡幂等（in_progress→in_progress 等恒等迁移返回 "
                   "ok + no_op 与继续执行指引，不报错不改库）。"
                   "某步骤 in_progress 超时无推进时，其他 ok 响应会附出口指引"
                   "（advisory：failed+reflection / plan_revise / task_close 三选一，"
                   "阈值 MEMA_TWIN_STALL_MINUTES 分钟，默认 30；提示冷却为固定 "
                   "30 分钟独立常量、不随阈值走；step_update 只传 "
                   "step_id 时按 step_id 反查任务，同样送达）。"
                   "批量（v0.4.7）：steps=[{step_id,status,reason?,reflection?}]"
                   "（1..50 条）+ 顶层 task_id 必填（批内跨任务拒；单事务，"
                   "任一条被拒整笔回滚，错误带 failed_index；applied=实际变更数、"
                   "noop=幂等数分列）——与顶层 step_id/status/reason/reflection "
                   "互斥。",
    "plan_revise": "修订计划（仅 planning 且已建计划）。steps_add / "
                   "steps_update（可改 title/description/depends_on/status，"
                   "状态迁移同 step_update 规则）/ steps_remove（仅未开工且未被"
                   "依赖可删，报错一次列全全部依赖方）/ answers（[{question_id, "
                   "answer}] 写回 owner 答复并解锁 blocking 步骤）/ "
                   "revision_reason 至少其一。",
    "tool_log": "批量记录工具使用（只记失败/重试/降级与首次成功的非常规路径，"
                "常规重复成功不记）。entries 1..50 条：[{tool ≤100 字符, purpose "
                "≤500, outcome: success|fail|degraded, note? ≤500, "
                "skill_digest? ≤500}]；可选 task_id；带 task_id 的记录进夜间评估"
                "（playbook 素材），不带 task_id 的只留痕——工具路径/踩坑类经验写 "
                "note，不要 twin.write。",
    "task_evaluate": "取执行经验评估素材包（playbook 编译入口，夜间任务或手动"
                     "触发）。可选 task_ids 显式指定（补评估），或 limit（默认 "
                     "10，实际 clamp 1..10，水位之后的未评估收口任务）；响应 "
                     "watermark_moved_to。夜间任务必传 origin=scheduled（夜间"
                     "来源标记；调用成功会清除夜编领走的积压提醒章）。"
                     "按素材包编译出 content_md 后 playbook_submit；无新经验不提交。"
                     "分流判据：playbook 只收执行路径/工具选路/踩坑规避，产出物的"
                     "结构/口径/措辞类偏好属 twin.write（persona 侧），不要立条；"
                     "工具自身的参数用法走该工具的技能文档；"
                     "服务端报错已自解释的一次性失误自愈（传错参照报错改对）也不立条，"
                     "值得记录的是报错无法自解释、需额外环境知识的坑。",
    "playbook_submit": "提交 playbook 落版。必填 key（work_type code 或 "
                       "'global'；禁 pb-/aud- 保留前缀）/content_md/"
                       "source_task_ids（溯源任务 id，防幻觉路径）；可选 model（编译"
                       "模型标识，落库审计）；夜间任务 "
                       "origin=scheduled（过验证门：溯源校验/回声/标题/空转阻尼），"
                       "交互式违规只警告。文件镜像 playbooks/<key>/。",
    "playbook_rollback": "回滚 playbook 版本（零阻力，同 rollback 语义）。key 必填；"
                         "version 省略回上一版。已是 active 时响应 already_active="
                         "true（幂等 no-op）。",
    "help": "本帮助。全量模式（无参数）返回全部动作手册 + write_guidance；"
            "topic=<action 名> 单查一条（省上下文，如 data={\"topic\":\"write\"}）；"
            "topic=scheduled_tasks 取定时任务 spec。",
}

_ACTIONS = {
    "write": pref_actions._action_write,
    "status": pref_actions._action_status,
    "compile": compile_actions._action_compile,
    "submit": compile_actions._action_submit,
    "rollback": compile_actions._action_rollback,
    "get": pref_actions._action_get,
    "taxonomy": pref_actions._action_taxonomy,
    "pending": pref_actions._action_pending,
    "resolve": pref_actions._action_resolve,
    "void": pref_actions._action_void,
    "evidence": pref_actions._action_evidence,
    "task_start": task_actions._action_task_start,
    "task_submit": task_actions._action_task_submit,
    "task_resume": task_actions._action_task_resume,
    "task_revise": task_actions._action_task_revise,
    "task_close": task_actions._action_task_close,
    "task_recent": task_actions._action_task_recent,
    "task_get": task_actions._action_task_get,
    "todo": task_actions._action_todo,
    "plan_set": exec_actions._action_plan_set,
    "step_update": exec_actions._action_step_update,
    "plan_revise": exec_actions._action_plan_revise,
    "tool_log": exec_actions._action_tool_log,
    "task_evaluate": playbook_actions._action_task_evaluate,
    "playbook_submit": playbook_actions._action_playbook_submit,
    "playbook_rollback": playbook_actions._action_playbook_rollback,
    "help": _action_help,
}

# B8（v0.6）：逐动作 data 白名单——顶层未知键一律打回（拼写错键静默忽略会把
# required 错误归因误导，宿主无法自纠）。清点口径：handler 实际读取的全部键，
# 含 data[kind] 循环取的 audience/purpose 与各处易漏键（session/interpreted_intent/
# available_tools/task_submit.brief 等）；通用 client（stdio 形态身份，全动作可用，
# http 头权威时仅作回退）。与 _ACTIONS 不同步即启动失败（fail-fast，防漏表动作
# 被全量打回的事故形态）。
_ACTION_KEYS: dict[str, frozenset[str]] = {
    "write": frozenset({"content", "work_type", "audience", "purpose", "scope",
                        "subject", "tags", "source_ref", "task_id", "client"}),
    "status": frozenset({"client"}),
    "compile": frozenset({"work_type", "client"}),
    "submit": frozenset({"work_type", "prompt_md", "source_memory_ids", "model",
                         "origin", "client"}),
    "rollback": frozenset({"work_type", "version", "client"}),
    "get": frozenset({"work_type", "version", "client"}),
    "taxonomy": frozenset({"kind", "compact", "client"}),
    "pending": frozenset({"status", "client"}),
    "resolve": frozenset({"pending_id", "decision", "code", "new_type",
                          "confirm", "tickets", "client"}),
    "void": frozenset({"memory_id", "client"}),
    "evidence": frozenset({"status", "work_type", "limit", "client"}),
    "task_start": frozenset({"brief", "work_type", "audience", "purpose",
                             "available_tools", "have_persona_version",
                             "have_audience_versions", "interpreted_intent",
                             "session", "client"}),
    "task_submit": frozenset({"task_id", "deliverable_md", "todos", "session",
                              "note", "steps", "brief", "client"}),
    "task_resume": frozenset({"task_id", "todos", "session",
                              "have_persona_version", "have_audience_versions",
                              "available_tools", "client"}),
    "task_revise": frozenset({"task_id", "brief", "deliverable_md",
                              "revision_reason", "session",
                              "have_persona_version", "have_audience_versions",
                              "available_tools", "client"}),
    "task_close": frozenset({"task_id", "outcome", "reason", "client"}),
    "task_recent": frozenset({"limit", "client"}),
    "task_get": frozenset({"task_id", "client"}),
    "todo": frozenset({"todos", "session", "client"}),
    "plan_set": frozenset({"task_id", "steps", "open_questions", "start_first",
                           "client"}),
    "step_update": frozenset({"step_id", "status", "reason", "reflection",
                              "task_id", "steps", "client"}),
    "plan_revise": frozenset({"task_id", "steps_add", "steps_update",
                              "steps_remove", "answers", "revision_reason",
                              "client"}),
    "tool_log": frozenset({"entries", "task_id", "client"}),
    "task_evaluate": frozenset({"task_ids", "limit", "origin", "client"}),
    "playbook_submit": frozenset({"key", "content_md", "source_task_ids",
                                  "model", "origin", "client"}),
    "playbook_rollback": frozenset({"key", "version", "client"}),
    "help": frozenset({"topic", "client"}),
}

if set(_ACTION_KEYS) != set(_ACTIONS):
    raise RuntimeError(
        "_ACTION_KEYS 与 _ACTIONS 不同步（漏表动作会被 B8 全量打回）: "
        f"missing={sorted(set(_ACTIONS) - set(_ACTION_KEYS))} "
        f"extra={sorted(set(_ACTION_KEYS) - set(_ACTIONS))}")

mcp = FastMCP("mema-twin", stateless_http=True)  # http 模式免 initialize 直调（mema 同款）


@mcp.tool()
async def twin(
    action: Literal[*tuple(_ACTIONS)],
    data: Annotated[dict | None, Field(
        description="动作参数包；各 action 的参数名/必填/上限见 "
                    "twin(action=\"help\", data={\"topic\":\"<action>\"}) 单查；"
                    "顶层未知键会被打回")] = None,
) -> CallToolResult:
    """个人分身 twin：按工作性质沉淀用户工作偏好，编译版本化 persona prompt，
    经交付任务流注入执行，并提供定时任务建议。

    动作：write / get / compile / submit / rollback / status / taxonomy / pending /
    resolve / void / evidence / task_start / task_submit / task_resume /
    task_revise / task_close / task_recent / task_get / todo / plan_set /
    step_update / plan_revise / tool_log / task_evaluate / playbook_submit /
    playbook_rollback / help。
    先 twin(action="help") 查看全部动作手册；twin(action="help",
    data={"topic":"<action>"}) 单查一条省上下文。compile 返回素材包（响应附
    evidence_ids 供 submit 复制），由当前会话模型编译（建议在强模型会话中执行），
    submit 提交回库落版本并写文件镜像。data 顶层未知键会被打回（附 did_you_mean）。
    """
    # sync 实现体放线程池（评审轮2 P2-4）：FastMCP 对 sync 工具直接在事件循环上
    # 调用，而 compile 全量投影后逐条 read（最坏 30s/条）会独占循环、拖挂其他宿主
    # 的并发调用；anyio.to_thread 传播 contextvars（身份头注入不受影响）
    result = await anyio.to_thread.run_sync(lambda: _twin_impl(action, data))
    return _single_text_result(result)


def _single_text_result(result: dict) -> CallToolResult:
    """content 单写 compact JSON（v0.3.11 拍板，对齐 mema 0.16.5 flip 的
    `_single_text_copy`）。

    FastMCP 对可识别为 dict 的返回值默认双写：compact structuredContent 一份 +
    indent=2 的 content[0].text 副本（mema 实测 2-3x 线膨胀）。twin 此前单写
    纯属侥幸——裸 `-> dict` 注解让 outputSchema 生成失败、SDK 跳过 structured
    分支；谁将来给返回加 TypedDict/BaseModel 注解或 structured_output=True，
    会不惊动任何人地翻回双写。故显式构造 CallToolResult 把单写钉成设计：
    content 是全客户端唯一必读的通道（structuredContent 是 2025-03 后的可选
    字段，只发它老客户端看到空结果——mema/WorkBuddy 实测案例）；响应体语义
    不变（ok/error dict），只收 indent 的线体积。返回注解标 CallToolResult
    是 FastMCP 的 designed 逃生口：FuncMetadata 不带 outputSchema，
    convert_result 对 CallToolResult 实例原样透传。
    """
    return CallToolResult(content=[TextContent(
        type="text",
        # default=str 对齐旧路径容错：FastMCP 的 _convert_to_content 对非 JSON 值
        # 静默 str() 兜底，缺了它这类值会从"宿主拿到 ok 载荷"变成 isError 硬错
        # （评审 P3：当前返回值全 JSON-safe，属防线而非现状修复）
        text=json.dumps(result, ensure_ascii=False, separators=(",", ":"),
                        default=str))])


def _attach_twin_notices(result) -> None:
    """升级提示出口（v0.4 P0）：仅 ok 响应附 twin_notices——notice 按版本抑制键
    存续，无丢失风险，下次成功响应必然再出。本函数任何故障不击穿已成功的工具
    响应。（v0.5.0：mema_notices 通道已随 sink 数据面退役，本键是唯一 notice 面。）"""
    if not isinstance(result, dict) or not result.get("ok"):
        return
    try:
        ns = update_monitor.consume_notices()
    except Exception:  # 更新检查是搭车件，绝不让它把 ok 变 internal_error
        return
    if ns:
        result["twin_notices"] = ns


def _twin_impl(action: str, data: dict | None = None) -> dict:
    handler = _ACTIONS.get(action)
    if handler is None:
        audit.record(action, False, "unknown_action", 0,
                     data if isinstance(data, dict) else {}, None)
        return {"ok": False, "error": "invalid_input",
                "reason": f"unknown action {action!r}", "actions": sorted(_ACTIONS)}
    started = time.perf_counter()
    try:
        # data 归一（v0.6 B8）：None → 空参；非 dict 垃圾（0/""/[]/str…）结构化
        # 打回（MCP 传输面由 arg_model 先拒为 ToolError，此分支达 console 直调与
        # 测试）。归一必须在 identity 之前——effective_client(data) 要读 data。
        if data is None:
            data = {}
        elif not isinstance(data, dict):
            return _audited(action, "invalid_input",
                            f"data 必须是 JSON object（收到 {type(data).__name__}）",
                            started, data)
        # 入口统一注入请求头身份并校验宿主身份：脏/重复头、脏或不一致 data.client
        # 无论动作立即打回（fail-fast，也避免 handler 先落票据再炸留幽灵行）
        identity.set_client_header(_request_client())
        identity.effective_client(data)
        # 多租户（2026-10-25）：入口清零 tenant contextvar，USERS_DIR 形态解析租户
        # 并硬拒矩阵（除 help 外缺头/坏 token 一律打回）。v0.5.0：token 仅服务
        # 租户路由（whoami），不再透传数据面（偏好已自持，#1604）。
        tenant.reset_tenant()
        token = _request_auth_token()
        if tenant.users_dir_mode() and action != "help":
            if not token:
                return _audited(action, "unauthorized",
                                "多租户形态需要 Authorization: Bearer 头（宿主 MCP 配置"
                                "携带自己的 mema token）", started, data)
            try:
                tenant.set_tenant(tenant.resolve(token))
            except sink.SinkAuthError as e:
                return _audited(action, "unauthorized", str(e), started, data)
            except sink.SinkUnavailableError as e:
                return _audited(action, "tenant_unavailable", str(e), started, data)
            except sink.SinkError as e:
                return _audited(action, "gateway_unreachable", str(e), started, data)
        unknown = sorted(set(data) - _ACTION_KEYS[action])
        if unknown:
            # lone surrogate 消毒（v0.6.8 实施后 R2 P1-1 同源兜底）：键含 \udXXX
            # 转义时 reason/did_you_mean 原样回显会在 http 序列化层炸掉响应；
            # encode(replace) 置换单个 '?'（aud 申报警告同款）
            unknown = [u.encode("utf-8", "replace").decode("utf-8") for u in unknown]
            # difflib 近邻提示（ratio≥0.6）；无命中置 null（显式「没有像样的
            # 近邻」，与命中区分——测试钉）
            did_you_mean = {
                k: (m[0] if (m := difflib.get_close_matches(
                    k, sorted(_ACTION_KEYS[action]), n=1, cutoff=0.6)) else None)
                for k in unknown
            }
            return _audited(
                action, "invalid_input",
                f"unknown field(s): {', '.join(unknown)}"
                f"（动作 {action} 的合法参数见 allowed；参数结构 "
                f"help(topic={action}) 单查）",
                started, data,
                extra={"field": "data", "allowed": sorted(_ACTION_KEYS[action]),
                       "did_you_mean": did_you_mean})
        result = handler(data)
        _attach_twin_notices(result)
        _attach_stall_guidance(result, data)
        _record_audit(action, result, started, data)
        return result
    except tenant.TenantError as e:
        # 多租户形态的结构化拒绝（互斥 env/目录缺失/租户未设）——不接住会
        # 裸逃逸出工具面（对抗轮2 P1：裸 RuntimeError + 服务器绝对路径直打
        # 客户端）。审计在无租户时静默跳过（_audited 自吞），属已知观测面。
        return _audited(action, "tenant_unavailable", str(e), started, data)
    except ValueError as e:
        return _audited(action, "invalid_input", str(e), started, data)
    except (TypeError, AttributeError, KeyError, OverflowError) as e:
        # 兜底边界（review#2）：畸形参数绝不击穿 MCP 工具面。参数类异常附
        # 自纠指路（v0.6 A5）；存储类异常（下方）不挂——database is locked
        # 不是「参数结构不合规」，挂了会误导分诊。
        return _audited(action, "internal_error",
                        f"{type(e).__name__}: {e}（参数结构可能不合规，"
                        f"help(topic={action}) 核对）", started, data)
    except (sqlite3.Error, OSError) as e:
        return _audited(action, "internal_error",
                        f"{type(e).__name__}: {e}", started, data)


def _record_audit(action: str, result, started: float, data: dict) -> None:
    """成功/业务失败分支记账（ok 与否按 result 判定）。try 包裹：观测件
    任何异常都不许影响已定型的工具响应。client 取连接身份（http 头权威），
    仅读 data.client 在多宿主模式下恒为 null（评审 P2-3）。"""
    try:
        audit.record(action, _ok_of(result), _error_of(result),
                     _elapsed_ms(started), data, _audit_client(data))
    except Exception:  # noqa: BLE001 —— 观测旁路
        pass


def _audit_client(data: dict) -> str | None:
    """审计归属的宿主身份：http 连接头优先（权威），回落 data.client。"""
    try:
        return identity.client_header() or identity.effective_client(data)
    except Exception:  # noqa: BLE001 —— 身份读取失败不影响记账（记 null）
        return None


def _ok_of(result) -> bool:
    return bool(isinstance(result, dict) and result.get("ok"))


def _error_of(result) -> str | None:
    if isinstance(result, dict) and not result.get("ok"):
        return str(result.get("error") or "unknown_error")[:64]
    return None


def _audited(action: str, error: str, reason: str,
             started: float, data: dict, extra: dict | None = None) -> dict:
    """错误分支统一记账+返回（reason 完整返回给调用方，审计里只存 error 码）。
    extra：附加结构化键（v0.6 B8 的 allowed/did_you_mean 等），不进审计。
    记账包 try：审计异常绝不把已定型的错误响应升级成 internal_error
    （audit.record 自身已吞异常，这里是双保险——测试会 patch 掉它）。"""
    try:
        audit.record(action, False, error, _elapsed_ms(started), data,
                     _audit_client(data))
    except Exception:  # noqa: BLE001 —— 观测旁路，不击穿工具响应
        pass
    resp = {"ok": False, "error": error, "reason": reason}
    if extra:
        resp.update(extra)
    return resp


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _attach_stall_guidance(result: dict, data: dict) -> None:
    """挂墙 advisory（v0.4.6）：仅 ok 响应附；只对带 task_id（或只有 step_id，
    反查其所属任务）且该任务有 in_progress 超长步骤时出口指引一句（advisory
    不拦）。step_id 反查是必须的：事故里的空转动作正是 step_update，入参没有
    task_id——不反查则指路牌挂不到最需要的那面墙（复审 P2-1）。挂独立
    stall_advisory 键而非 warnings——warnings 是真故障通道（交付物落盘
    失败/步骤被跳过/降级提醒），观测提示混进去会让宿主分诊失灵（红队
    P2-1）。本函数任何故障不击穿已成功的工具响应。"""
    if not isinstance(result, dict) or not result.get("ok"):
        return
    try:
        g = audit.stall_guidance(_task_id_of(data), data.get("step_id"))
    except Exception:  # noqa: BLE001 —— advisory 旁路
        return
    if g:
        result["stall_advisory"] = g


def _task_id_of(data: dict):
    """从入参取 task_id 观测键（脏值原样透给 audit.stall_guidance 的
    int() 兜底，不在入口二次校验——动作自身有 _coerce_task_id 门）。"""
    return data.get("task_id")


def _request_client() -> str | None:
    """http 模式下读宿主连接自带的 X-Mema-Client 头（mema 同款取头方式）；
    stdio/直调无活跃请求 → None（回落 env 默认）。带头即校验：脏值/重复头
    打回而非静默忽略。读到的值经 contextvar 供动作模块 effective_client 使用。"""
    get_context = getattr(mcp, "get_context", None)
    if not callable(get_context):
        return None
    try:
        request = get_context().request_context.request
        headers = getattr(request, "headers", None)
    except (AttributeError, LookupError, TypeError, ValueError):
        # stdio/无活跃请求（core request_identity 同款兜底）
        return None
    if headers is None:
        return None
    # 与 mema core 一致：重复头打回而非静默取第一个
    getlist = getattr(headers, "getlist", None)
    if callable(getlist):
        values = list(getlist("X-Mema-Client"))
        if len(values) > 1:
            raise ValueError("X-Mema-Client 头必须恰好一个（收到多个）")
    value = headers.get("x-mema-client")
    if value is None:
        # 非 starlette Headers 对象可能大小写敏感，casefold 兜底一次（core 同款）
        for key, candidate in headers.items():
            if str(key).casefold() == "x-mema-client":
                value = candidate
                break
    if value is None:
        return None
    try:
        return identity.validate_client_value(str(value))
    except ValueError as e:
        raise ValueError(f"X-Mema-Client 头非法：{e}") from None


def _normalize_auth_header(text: str) -> str:
    """Authorization 头规范化（2026-10-25 owner 拍板：配置面不要求写 Bearer
    前缀——MCP 客户端配置里直接填裸 token，减少一步「不知道要加前缀」的坑）。

    语义（与 mema core `_bearer_token` 兼容）：
    - 已有 ``Bearer `` 前缀（任意大小写）→ 原样保留；
    - 无前缀 → 补成 ``Bearer <text>``（网关 `_bearer_token` 硬性要求前缀，
      这里补齐 = 配置面自由，协议面不变）；
    - 前缀在但无体 → 打回（而不是静默当无 token）。
    """
    if text.lower().startswith("bearer "):
        rest = text[7:].strip()
        if not rest:
            raise ValueError("Authorization 头非法（Bearer 前缀无 token 体）")
        return text
    return f"Bearer {text}"


def _request_auth_token() -> str | None:
    """http 请求的 Authorization 头（多租户透传凭证，与 _request_client 同款
    取头方式）：恰好一个、strip 后非空才返回；经 _normalize_auth_header 归一
    （裸 token 自动补 Bearer 前缀，已有前缀原样）。stdio/无活跃请求 → None。
    脏值打回 ValueError 且**绝不回显值**（token 进报错/日志即泄露）。"""
    get_context = getattr(mcp, "get_context", None)
    if not callable(get_context):
        return None
    try:
        request = get_context().request_context.request
        headers = getattr(request, "headers", None)
    except (AttributeError, LookupError, TypeError, ValueError):
        return None
    if headers is None:
        return None
    getlist = getattr(headers, "getlist", None)
    if callable(getlist):
        values = list(getlist("Authorization"))
        if len(values) > 1:
            raise ValueError("Authorization 头必须恰好一个（收到多个）")
    value = headers.get("authorization")
    if value is None:
        for key, candidate in headers.items():
            if str(key).casefold() == "authorization":
                value = candidate
                break
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if any(ord(ch) < 0x20 or ch == "\x7f" for ch in text):
        raise ValueError("Authorization 头非法（含控制字符）")
    return _normalize_auth_header(text)


def main() -> None:
    """传输：stdio（默认，单机零运维）或 http（多 Agent 共接，mema 同款形态）。

    MEMA_TWIN_TRANSPORT=stdio|http；http 时 MEMA_TWIN_HTTP_HOST/PORT 可调
    （默认 127.0.0.1:8765，仅允许 loopback 绑定——X-Mema-Client 头不是鉴权，
    非 loopback 暴露=任何人可伪造宿主身份，mema core 同款拒绝）。端点 /mcp
    无状态直调。多宿主各在自己 MCP 配置里带 X-Mema-Client 头（stdio 无头时
    data.client > env）。
    """
    import os
    # 多租户形态启动校验（对抗评审 v4 P1-2/P1-3/P2）：仅 http（stdio 无请求头
    # 无法解析租户）；token/镜像/DB env 全互斥（解析点还会再强制——这里是快
    # 失败）；目录须像网关 users/（数字子目录），防指错到别的副本安静建库。
    if tenant.users_dir_mode():
        transport_probe = (os.environ.get("MEMA_TWIN_TRANSPORT") or "stdio").strip().lower()
        if transport_probe in ("stdio", ""):
            raise SystemExit(
                "启动失败：MEMA_TWIN_USERS_DIR 多租户形态仅支持 http 传输"
                "（stdio 无请求头，租户无法解析）")
        for env in tenant.EXCLUSIVE_ENVS:
            if (os.environ.get(env) or "").strip():
                raise SystemExit(
                    f"启动失败：{env} 与 MEMA_TWIN_USERS_DIR 互斥"
                    "（多租户形态 token 随请求透传、路径按租户收口）")
        from pathlib import Path as _Path
        if not (os.environ.get("MEMA_TWIN_MEMA_URL") or "").strip():
            # R2 P2-4：多租户唯一 mema 交点是 /whoami（租户路由）；URL 缺失会
            # 回落默认 127.0.0.1:8000 而真实网关常在别口——服务活着但全租户
            # gateway_unreachable 的「全瞎」形态，启动期 fail-fast。
            raise SystemExit(
                "启动失败：多租户形态需 MEMA_TWIN_MEMA_URL（网关 whoami 定位，"
                "如 http://127.0.0.1:8001/mcp）")
        users_root = _Path(os.environ[tenant.USERS_DIR_ENV])
        if not users_root.is_dir():
            raise SystemExit(f"启动失败：MEMA_TWIN_USERS_DIR={users_root} 不存在")
        bad = sorted(c.name for c in users_root.iterdir()
                     if c.is_dir() and not c.name.isdigit())
        if bad:
            raise SystemExit(
                "启动失败：MEMA_TWIN_USERS_DIR 不像网关 users 目录"
                f"（含非数字子目录：{bad[:3]}）")
    # 升级检查（v0.4 P0）：daemon 线程后台跑，不阻塞启动；禁用 env 下内部 no-op
    update_monitor.maybe_start_check_if_due()
    # 启动即校验 env 兜底身份，脏值清晰报错退出，不留给运行期中途炸
    #（v0.5.0：MEMA_TWIN_MEMA_TOKEN 数据面退役，token env 不再校验也不再消费）
    try:
        sink._env_client()
    except ValueError as e:
        raise SystemExit(f"启动失败：{e}")
    transport = (os.environ.get("MEMA_TWIN_TRANSPORT") or "stdio").strip().lower()
    if transport in ("stdio", ""):
        mcp.run()
    elif transport in ("http", "streamable-http"):
        host = os.environ.get("MEMA_TWIN_HTTP_HOST", "127.0.0.1").strip()
        if not identity.is_loopback_host(host):
            raise SystemExit(
                f"MEMA_TWIN_HTTP_HOST={host!r} 非 loopback 地址被拒绝："
                "X-Mema-Client 头不是鉴权，对外暴露等于开放身份伪造（mema core 同款策略）。"
                "twin 设计为本机单用户服务。")
        mcp.settings.host = host
        mcp.settings.port = int(os.environ.get("MEMA_TWIN_HTTP_PORT", "8765"))
        mcp.run(transport="streamable-http")
    else:
        raise SystemExit(f"unknown MEMA_TWIN_TRANSPORT {transport!r}（期望 stdio|http）")
