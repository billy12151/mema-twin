---
name: mema-twin
description: 个人分身：工作类偏好沉淀与 persona prompt 编译，经交付任务流注入执行；v0.4 起带执行计划层（两档路由、步骤打卡与防跳步门、open_questions 咨询 owner、执行经验 playbook 闭环）与升级提示。用户修改/审阅工作产物后，把其中可复用的偏好合并沉淀到 twin.write（禁止再同步本地记忆文档）；开工前 task_start 取分身 prompt 并建档；版本更新在用户要求或夜间编译定时任务自动执行时走 compile/submit。
---

# mema-twin 使用引导

> 对齐服务端 **v0.4.4**。动作全集与入参以 `twin(action="help")` 为准；下文的「实测口径」
> 是 help 未覆盖或与实测不符处的补充。

## 分流规则（先判这个，再选工具）

- **事件/事实/进展**（做了什么、何时交付、结论是什么）→ `memory.remember`（mema 本来的用途）
- **可复用的抽象**（用户反复体现的偏好与规则、结构习惯、开工前该问的问题）→ `twin.write`
- 拿不准时问自己："下次干同类活，这条还适用吗？"——适用就是偏好，只发生一次就是事件。
  混进事件记忆不会污染分身（无维度标签进不了 compile），但偏好漏存会让分身学不到。
- **禁止双写**：偏好进了 twin 就是唯一存档——不要再同步到宿主的本地记忆文档/笔记，
  也不要把同一条偏好再写一遍 `memory.remember`。以后干活需要时从 twin 取
  （task_start 自动注入，其他场景 `twin(action="get")`）。

## 何时用

- 用户修改/审阅了工作产物（PPT、文档、汇报、设计……），或显式要求沉淀工作偏好
  → `twin(action="write")`。**合并沉淀**：同一任务的一系列修改汇总为少量高质量条目，
  不要每改一处就写一条；拿不准且复用价值不高的，不写
  - 在任务流内（执行/改稿/返工现场）沉淀时带 `task_id`：缺省维度直接沿用该任务的
    三维度（task_start 已归一，不会写错桶；scope=audience 时不继承 work_type；
    显式传入优先，响应 dims_inherited 列出继承项）
  - 用户对交付稿的**采纳 / 忽略 / 要求修改**等隐式行为同样是偏好信号，一并观察沉淀
    （判据不变："下次干同类活还适用吗"）；一次性异常不写
- 用户表述是「对该受众的通用要求」（如"给领导的东西都要简洁白话"，不限工作类型）
  → `twin(action="write", data={..., "scope": "audience"})`：audience/purpose 必填、
  work_type 省略；落为受众级偏好，夜间自动抽象进该受众画像，对该受众的任何任务
  开工时自动注入（audience_profile_md / 雏形）
- 用户**重复强调**已沉淀过的偏好（同一件事说了第二遍）→ 检查它的 scope：若此前只
  沉淀在单个工作类型下，当场确认"是否对该受众都适用"，是则按上一条补写受众级沉淀
- 开始一件工作类产出任务（先于 plan、先于动手）
  → `twin(action="task_start", data={"work_type": ..., "brief": ...})`：
  建档并返回该工作性质的分身 prompt，严格按其中的偏好/结构/前置清单执行；
  带了 audience 时还会注入该受众的画像（audience_profile_md，通用口径参考，
  格式结构以本类型为准）；材料不齐全按前置清单向用户确认或要求补齐；进行中任务自动让位
  - 响应另带 `persona_supplement`：该 work_type **尚未编译**的偏好增补（上限 10 条），
    与 persona prompt 冲突时**以增补为准**（增补比已落版 prompt 新）
  - 响应带 `playbook_md` 时：它是执行经验参考（**advisory 不是命令**），与实际工具
    环境冲突时按实际环境执行，并把降级/偏差用 tool_log 记录；提示"其他宿主使用过"时
    先逐条核对工具可用性（有 `available_tools_required` 就在下次 task_start 传
    `available_tools`，服务端逐条比对工具面板回 `tool_gap`）
- 响应带 `fallback_persona_md`（v0.4.5 同域 fallback）：本工种尚无专属 persona，
  给的是同域最成熟工种的 persona 当垫底——**格式与结构层可参考**；有更权威的
  格式来源（用户模板/公司规范/行业惯例）以权威为准，此参考降级为风格详略参考；
  **内容与业务规则不适用**。同域其他候选在 note 里，按任务语义判断更相关可
  `twin(action="get", data={"work_type": "<该工种 code>"})` 取全文（`fallback_from` 标注 donor 工种/版本/活证据数）
- 建档后走计划流（凡建档任务默认走计划）：`plan_set` 建步骤 → 执行中 `step_update`
  打卡 → 计划有变 `plan_revise`；会话级 todo 用 `twin(action="todo")` 整体替换读写。
  详见下节「执行流」
- 走了非常规工具路径（失败/重试/降级、首次打通的新路子）→ `twin(action="tool_log")`
  批量记一条（常规重复成功**不记**）
- 查任务 → `twin(action="task_recent", data={"limit": N})`（默认 10）看最近任务，
  单任务全量用 `twin(action="task_get", data={"task_id": ...})`；
  **两者都回 `deliverable_md` 全文**，token 敏感时把 limit 压到 1–3
- 交付稿完成 → `twin(action="task_submit", data={"task_id":…, "deliverable_md":…})`
  即收口（submit 是终点，无评审环；可选带 todos/session/note，交付稿落盘
  `deliverables/task-<id>.md` 留档）。**收口门**：建档任务的未闭环步骤会被整笔打回
  （附 open_steps 清单），逐条对账打卡后再交；用户看过稿子后的修改意见**合并后**
  twin.write 沉淀（一轮反馈出少量条目，不是每条意见写一条）
- 要返工改稿 → `twin(action="task_revise", data={"task_id": ...})` 生成修订任务重走
  （仅 submitted；brief/deliverable_md/revision_reason 至少其一）。注意 revise 不恢复
  todos、不重注入 persona，需要时重新 task_start；子任务回 planning 重走并记 lineage：
  parent_task_id + iteration 递增，未完结步骤深拷贝到新 task_id
- 中断/隔日继续 → `twin(action="task_resume", data={"task_id": ...})`
  （仅进行中 planning 的任务，自动恢复 todos 并再注入分身，同样带 persona_supplement；
  无专属 persona 时同域 fallback 同 task_start）；
  不再做的进行中任务用 `twin(action="task_close")` 显式关闭（关闭前先经用户确认，
  不要自行清理）；可选 `outcome` ∈ failed|superseded（**缺省 superseded**）——
  失败必须显式标 failed，否则结果列与未完成任务无法区分；关闭时未闭环步骤自动按 closed 跳过
- **同会话重复注入省 token**：同一会话再次 task_start/task_resume 同 work_type，
  且上文注入返回的 persona_version 仍在场（未被上下文压缩）→ 传
  `have_persona_version=<上文版本号>`，版本未变则服务端不再重复注入全文；
  上文不可见就**不要传**（服务端会照常全文注入，宁可重复不可缺席）
- write 响应提示"有偏好未编译" → **不要**立即 compile，也不要每写一条就播报：
  任务收尾（或用户问起）时非阻塞汇总一句，如"分身积累了 N 条新偏好，要不要现在整理？"；
  是否整理由用户拍板，或交给夜间编译定时任务统一处理
- **触达 mema 的动作响应可能带 mema_notices**（mema 检出的提示，advisory）：
  `similar_active_memory`
  疑似重复 → 静默分诊（偏好是增量语义，重复由编译期合并吸收；真重复才改走 mema update
  原条目，不必打扰用户）；**语义冲突 notice → 先按 read_call 读完整通知与两侧原文**，
  误报 dismiss；真冲突才问用户三选项：两条都留（编译条件化）/ 新的替旧的（twin void
  旧条）/ 撤销新写的（twin void 本条）
- 响应带 `twin_notices`（**升级提示，独立于 mema_notices**）：按其 agent_instruction 告知
  用户**一次**，不自动升级、不重复提醒（比对 GitHub 版本，同版本 7 天抑制；
  `MEMA_TWIN_UPDATE_CHECK=0` 可关）
- 用户明确要求更新分身、或夜间编译定时任务自动执行
  → `twin(action="compile")` 拿素材包 → 当前会话模型编译 → `twin(action="submit")` 落版本。
  用户要求在当前会话整理就直接执行，强模型建议提一次即可，不要反复劝说换会话
- `twin(action="void", data={"memory_id": N})` 作废一条偏好（冲突裁定"新替旧/撤销新写的"
  的执行动作）：行级作废、全链路排除、不可逆；曾入编译的证据会自动触发该类型夜间重编
  剔除该条款（persona_stale）；受众级证据作废会改变该受众的证据计数 → 触发画像重抽象
  （audience_stale）。mema 本体条目的 retire 按其治理流程另行处理
- `write` 除 content/work_type/audience/purpose 外，可选 `subject` / `tags` /
  `source_ref` / `client`（多 Agent 共接时 client 填宿主标识，如 kimi / zcode；
  HTTP 接入时若头里已带 `X-Mema-Client` 则无需再传）
- `status` 稳定带 `plan_stats`：tasks_total / tasks_planned / steps_total / steps_failed /
  steps_skipped / steps_backfilled / open_blocking_questions / tasks_unevaluated /
  tasks_submitted / submitted_with_plan（采纳率=后者÷前者，分母 0 无读数；历史
  submitted 会稀释，看增量任务） /
  tool_usage_total / tool_usage_fail / playbooks（另 nightly_rejected、playbook_rejected
  为拒绝落版计数，键缺席即 0）。**字段可能整桶缺席**（仅 status 内部软失败时缺席，空库仍返回全零桶），
  判空按「键缺席 或 空容器」等价处理
- `status` 返回 scan_notice 时：按其 agent_instruction 询问用户是否创建/更新夜间任务
  ——**当前 spec 是单一任务**（`twin_nightly_compile`，`setup.tasks` 里对照）：
  同一任务顺序执行两支线，① 执行经验评估（status → `task_evaluate(origin="scheduled")`
  → 有素材才 `playbook_submit`）在前、② persona 编译（pending → compile → submit →
  画像 compile → submit）在后。用户同意后在宿主平台侧建/更新，之后不再重复问
  - **双支线不是可选项**：评估支线会刷心跳（`last_scheduled_playbook_at`），只建
    「纯 persona 编译」旧形态的宿主永远过不了保险丝，会被持续引导升级成合并 spec
    ——正确做法是把现有任务**更新**成合并 spec，不要再建第二个任务
  - 该提醒兼作**停转保险丝**：心跳（由 `task_evaluate(origin="scheduled")` 刷新）
    7 天没跑过会重新出现（心跳新鲜时直接静默，7 天窗口吸收单晚失败）
  - **v0.4.3 加 3 天抑制窗口**：每次实际提示时全局盖章，3 天内**所有宿主**静默，
    窗口过后心跳仍未跑才恢复提示并重盖——多宿主重复询问压到 3 天最多一次，
    由第一个看到的 agent 提问（服务端按 client 识别宿主；换 client/宿主跑同一任务
    可能仍被判"没跑过"）
- **首次**交付产出物时提醒一次（不是每次）：后续修改尽量交给 Agent 而非手动改，
  每次修改都是一次偏好沉淀机会

## write 的抽象口径

只收可复用抽象：偏好与规则（用语/详略/格式/口吻）、结构习惯、开工前应确认的问题与材料。
必带三字段 work_type / audience / purpose。
事件性内容（做了什么、何时交付）走普通 memory.remember，不要走 twin.write。

## 三字段选码（归一门：清单硬约束，未命中必问用户）

- write / task_start 之前**必须**手上有枚举清单：同一会话首次先
  `twin(action="taxonomy")`（按需带 kind=work_type|audience|purpose）查清单，
  从清单里选最贴的 canonical code（或其别名/中文名）作为三字段的值——不要凭记忆造说法；
  已查过的清单会话内直接复用（清单动态：用户裁定后新码/新别名立即生效，届时重查对应 kind）
- 清单里确实没有合适项时，给**你认为最合适的原始值**：写入会被**整笔打回**
  （错误里附完整清单与 pending 票据）——此时**必须问用户**二选一：
  ① 归一到已有值 → `twin(action="resolve", data={"pending_id":…, "decision":"map", "code":…})`
  （原值进别名表，同一说法终身只问这一次）；② 创建新值 →
  `resolve(decision="canonicalize", new_type={code,zh,en,domain})`（新码即刻进清单）。
  用户说"不在意"→ 选最贴近的已有值；用户说"这条不写"→ resolve(reject) 并放弃本次
  （**reject 后不得再拿原值重试**）。裁定完成后用原值重试 write/task_start 即命中；
  resolve 报"已裁定不可重复裁定"说明他方已裁定，直接重试写入即可
- 不要硬凑近义枚举、也不要给"其他"——枚举里没有 other，杂项不配当分类

## 执行流（v0.4：计划、打卡、疑问升级）

建档任务默认走计划——计划通道与 persona 注入是并列的，别只做了 task_start 就动手。

- **两档路由（必须遵守）**：凡 task_start 建档的任务默认属重复执行型工作——开工前
  **必须**先 `twin(action="plan_set")` 列出步骤计划再动手。唯一豁免是无重复执行价值的
  一次性事务（问候、查即时信息、无产出物的一问一答），这类不建档；**既已建档即默认走
  计划，没有"这个任务比较简单"的例外**。拿不准要不要建计划时，建。
- `plan_set` 直接放行开工，无审批门。必填 task_id / steps
  （`[{title, description?, depends_on?}]`，depends_on 引 1-based 序号，不许自引用/成环）；
  计划里没把握、或你确认不了的点写进 open_questions（`[{question, step_ids?, blocking?}]`，
  blocking 缺省 false），**要紧的标 blocking 先与用户澄清再推进关联步骤**（服务端会拦）；
  答复经 `plan_revise(answers=…)` 写回解锁。重复调用 = 重建计划（旧未完结步骤按
  replanned 跳过，done 历史保留）
- `step_update` 打卡：六态 pending/in_progress/done/failed/blocked/skipped；
  skipped/blocked 必填 reason；failed 必填 reflection（失败原因与下次对策，服务端硬门）；
  pending 直跳 done/failed 自动标 backfilled（追认合法）。**三门**（顺序固定）：
  blocking 疑问未解答 → 依赖未闭环 → 已有单一 in_progress 时推进会被拒；
  跳过打卡直接干完的属追认，补 step_update(done) 即可，不影响收口
- `plan_revise`：仅 planning 且已建计划，四类修订 steps_add / steps_update
  （可改 title/description/depends_on/status）/ steps_remove（仅未开工且未被依赖可删）/
  answers / revision_reason **至少其一**。中途换路/砍步骤走这里，不要静默绕过依赖
- `todo`：会话 todo 读写（plan-mode 同款语义），传 todos 即整体替换、至多一条 in_progress；
  不传则读取
- `tool_log`：批量记工具使用，1–50 条/次 `[{tool, purpose, outcome: success|fail|degraded,
  note?, skill_digest?}]`，可选 task_id。**只记失败/重试/降级与首次成功的非常规路径**，
  常规重复成功不记（记多了是噪声）。触发示例：工具超时换备用成功
  （outcome=degraded，note 记换成了什么）；命令报错重试后通过（outcome=success，
  note 记首次失败原因）；彻底失败换路径（outcome=fail，note 记改走了什么）
- task_submit 对未建计划的交付回一条**经验流失提醒**（advisory 不拦）：执行经验
  不会进 playbook 沉淀——重复执行型工作下次开工先 plan_set，一次性事务下次
  不必建档；收到该提醒即说明这次漏走了计划流

## playbook（执行经验沉淀）

- `task_evaluate`：取执行经验评估素材包（playbook 编译入口，夜间任务或手动触发）。
  可选 task_ids 显式指定（补评估），或 limit（默认 10，水位之后的未评估收口任务）；
  响应带 watermark_moved_to。**无新经验不提交**
  - 夜间任务必传 `origin="scheduled"`（唯一合法值；help 文案未列该参数）：调用成功即刷
    心跳，与当晚有无新经验无关——漏传会让停转保险丝误报
- `playbook_submit`：落版。必填 key（work_type code 或 `'global'`，跨类型经验用 global）、
  content_md、**source_task_ids**（溯源任务 id，防幻觉路径——每条产物须能溯源到真实任务，
  写不出来源的条目不得出现）；夜间任务传 `origin="scheduled"`（过验证门：溯源校验/回声/
  标题/空转阻尼，同 submit 口径）；文件镜像 `playbooks/<key>/`
- `playbook_rollback`：回滚 playbook 版本（零阻力，同 persona rollback 语义），key 必填
- task_start / task_resume 注入 active playbook（work_type → global 两级回退）。
  **连续性分级**：同宿主轻注入（全文 + 近期验证提示），换宿主重注入（+ 降级链提示 +
  `available_tools_required`），下次 task_start 传 `available_tools` 服务端逐条比对面板
  回 `tool_gap`。playbook 是 advisory，与实际环境冲突按实际执行

## compile / submit

- compile 返回素材包：旧版本 prompt（编译参考，非执行依据）+ **该类型全部在世偏好证据**
  （全量投影——每版从头重编，已吸收的证据同样在场）+ 已作废条款清单 + 同受众画像参考 +
  编译规则（含义稳定表达自由 / 硬预算 / 变更分级须归因）；**用独立会话执行、做完即弃**
  （建议强模型）：编译会话内新旧版本同屏，勿在同一会话继续交付任务——会话隔离是避免
  新旧 persona 冲突的唯一硬手段
- submit 时 `data.model` 填当前模型名，`source_memory_ids` 用素材包里的证据 id 列表
  （全量口径：素材包里的每条在世证据 id 都应列入）；响应回 `supersedes`（被取代的旧
  active 版本）
- **`origin` 只在夜间定时任务里传** `"scheduled"`：手动/交互式 compile→submit 一律不传
  （传了才会走去重门，交互场景不需要也没有必要）
- **硬预算与落地前自检**：persona 正文 ≤8000 字符或 ≤60 条顶层列表行；受众画像
  ≤4000 字符或 ≤40 条（画像留给增量条款的空间很小，长证据要抽象到口径层，别搬格式细节）。
  超限必须合并或淘汰并写明依据。submit **不校验预算**，超了会静默落版——所以草稿先写临时
  文件，`wc -m` 数字符、`grep -c '^- '` 数顶层条目，过线再提交，事后再用 `rm` 清掉临时件。
  画像（4000 字符）逼近上限时，优先压缩「版本间变更」里的历史日志（按证据分组、逐版一行，
  归因 id 保留不丢）与合并同源条目，别丢动作/条件/例外
- **同批证据内部冲突**：常见形态是「后者是对前者的分寸收窄」（如 1016「敬语用麻烦/烦请」
  被 1018「去 AI 味不等于口语化」收窄）——取更具体/更贴用户定调的一条为准，并在
  「版本间变更」里显式写「冲突裁定：X 与 Y 冲突，取 Y（理由）」，不要静默择一
- **status 空值形态**：`uncompiled` / `audience_stale` 返回 `{}`，而 `persona_stale` /
  `nightly_rejected` 是**键缺席**即空——判空转按「键缺席 或 空容器」等价处理，别因为
  没看到键就以为字段缺失或去查源码
- **受众画像走同一 compile/submit 通道**，差别在两处：取素材包用
  `compile(data={"work_type": "aud-<受众>"})`——**`aud-` 前缀即画像口径**，
  无需另传 `audience`（help 未列该参数；夜间 spec 里带着 `audience:true`，
  实测省略同样正常返回画像素材包，带着也无害）。素材包 = 该受众全部在世证据，
  作废条款清单同样在场；画像**派生不消耗证据**（响应 `derived:true`、
  `evidence_marked_compiled:0`，对应偏好仍留在原类型队列里）。source_memory_ids 同样报齐
  素材包全部在世证据 id（多报/漏报/重复只警告不拦，次晚自愈）
- compile 响应带 `session_note`：编译会话**用完即弃**，收尾时按其中提示告知用户
  "后续交付任务换新会话重新开始"（新会话 task_start 会自动注入新版）
- **夜间落版过验证门**：`origin=scheduled` 的 submit 落版前过确定性检查——素材回声
  （复述素材包）/ 缺分区标题未过会被拒（validation_failed）；无新证据可吸收会被空转阻尼
  拒（no_new_evidence，防版本号空转）。被拒时如实记录跳过、不要为过门改产物：active 未变、
  证据未消耗，次晚自动重试；连续被拒不落版会累计在 status 的 nightly_rejected（成功落版即清）。
  交互式 submit 不受门限制，违规与证据未全覆盖只出警告
- 用户要求撤销/回退某版分身 → `twin(action="rollback", data={work_type, version?})`：
  省略 version 回上一版，传 n 回指定版；零阻力直接执行（不删历史、版本号不回收，
  retired 可再激活），不要建议重新编译代替回滚
- 用户问"现在分身长什么样 / 把 vN 给我看看" → `twin(action="get", data={work_type, version?})`
  读 persona 全文（DB 优先、文件镜像降级）；work_type 传 `aud-<受众>` 读受众画像

## 治理

`twin(action="pending")` 查看归一门打回的待裁票据（字段 id / type_kind / raw_value /
hit_count=打回次数 / status / resolved_code / created_at；可选 `status` 过滤，默认 pending
——夜间任务晨报会汇总，留你裁定，Agent 夜间不代裁）；
`twin(action="resolve")` 做映射（map 到既有码并进别名表）、新建（canonicalize 立新码
即刻入列）或拒绝（reject 不入体系，该值不得再重试）。
