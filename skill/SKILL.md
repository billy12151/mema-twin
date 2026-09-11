---
name: mema-twin
description: 个人分身：工作类偏好沉淀与 persona prompt 编译，经交付任务流注入执行。用户修改/审阅工作产物后，把其中可复用的偏好合并沉淀到 twin.write（禁止再同步本地记忆文档）；开工前 task_start 取分身 prompt 并建档；版本更新在用户要求或夜间编译定时任务自动执行时走 compile/submit。
---

# mema-twin 使用引导

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
  格式结构以本类型为准）；材料不齐全按前置清单向用户确认或要求补齐
- 交付稿完成 → `twin(action="task_submit")` 即收口（v0.3.8：submit 是终点，无评审环，
  交付稿自动落盘留档）；用户看过稿子后的修改意见**合并后** twin.write 沉淀
  （一轮反馈出少量条目，不是每条意见写一条）；要返工改稿 →
  `twin(action="task_revise", data={"task_id": ...})` 生成修订任务重走（注意 revise
  不恢复 todos、不重注入 persona，需要时重新 task_start）
- 中断/隔日继续 → `twin(action="task_resume", data={"task_id": ...})`
  （仅进行中 planning 的任务，自动恢复 todos 并再注入分身）；不再做的进行中任务用
  `twin(action="task_close")` 显式关闭（关闭前先经用户确认，不要自行清理）
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
- 用户明确要求更新分身、或夜间编译定时任务自动执行
  → `twin(action="compile")` 拿素材包 → 当前会话模型编译 → `twin(action="submit")` 落版本。
  用户要求在当前会话整理就直接执行，强模型建议提一次即可，不要反复劝说换会话
- `twin(action="void", data={"memory_id": N})` 作废一条偏好（冲突裁定"新替旧/撤销新写的"
  的执行动作）：行级作废、全链路排除、不可逆；曾入编译的证据会自动触发该类型夜间重编
  剔除该条款（persona_stale）
- `status` 返回 scan_notice 时：按其 agent_instruction 询问用户是否创建定时任务
  ——夜间 persona 编译（每天，无人值守把未编译偏好整理落版、作废条款重编、受众画像
  重抽象），spec 在 `setup.tasks`；用户同意后在宿主平台侧建，之后不再重复问。
  该提醒也兼作**停转保险丝**：夜间任务 7 天没跑过会重新出现
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

## compile / submit

- compile 返回素材包：旧版本 prompt（编译参考，非执行依据）+ **该类型全部在世偏好证据**
  （全量投影——每版从头重编，已吸收的证据同样在场）+ 已作废条款清单 + 同受众画像参考 +
  编译规则（含义稳定表达自由 / 硬预算 / 变更分级须归因）；**用独立会话执行、做完即弃**
  （建议强模型）：编译会话内新旧版本同屏，勿在同一会话继续交付任务——会话隔离是避免
  新旧 persona 冲突的唯一硬手段
- submit 时 `data.model` 填当前模型名，`source_memory_ids` 用素材包里的证据 id 列表
  （全量口径：素材包里的每条在世证据 id 都应列入）
- **夜间落版过验证门**：`origin=scheduled` 的 submit 落版前过确定性检查——素材回声
  （复述素材包）/ 缺分区标题未过会被拒（validation_failed）；无新证据可吸收会被空转阻尼
  拒（no_new_evidence，防版本号空转）。被拒时如实记录跳过、不要为过门改产物：active 未变、
  证据未消耗，次晚自动重试；连续被拒不落版会累计在 status 的 nightly_rejected。交互式
  submit 不受门限制，违规与证据未全覆盖只出警告
- 用户要求撤销/回退某版分身 → `twin(action="rollback", data={work_type, version?})`：
  省略 version 回上一版，传 n 回指定版；零阻力直接执行（不删历史、版本号不回收），
  不要建议重新编译代替回滚

## 治理

`twin(action="pending")` 查看归一门打回的待裁票据（含打回次数——夜间任务晨报会汇总，
留你裁定，Agent 夜间不代裁）；
`twin(action="resolve")` 做映射（map 到既有码并进别名表）、新建（canonicalize 立新码
即刻入列）或拒绝（reject 不入体系，该值不得再重试）。
