---
name: mema-twin
description: 个人分身：工作类偏好沉淀与 persona prompt 编译，经交付任务流注入执行。用户修改/审阅工作产物后，把其中可复用的偏好合并沉淀到 twin.write（禁止再同步本地记忆文档）；开工前 task_start 取分身 prompt 并建档；版本更新仅在用户要求或定时扫描确认后走 compile/submit。
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
- 开始一件工作类产出任务（先于 plan、先于动手）
  → `twin(action="task_start", data={"work_type": ..., "brief": ...})`：
  建档并返回该工作性质的分身 prompt，严格按其中的偏好/结构/前置清单执行；
  材料不齐全按前置清单向用户确认或要求补齐
- 交付稿完成 → `twin(action="task_submit")`；用户审阅后
  → `twin(action="task_review", verdict=approved|changes_requested, notes=...)`
  通过即收口；changes 的意见**合并后** twin.write 沉淀（一轮 review 出少量条目，
  不是每条意见写一条），改稿后**同一任务再 task_submit**
  提交下一轮（轮次递增，评审历史全量可审计）
- 中断/隔日继续 → `twin(action="task_resume", data={"task_id": ...})`
  （进行中、已提交、被搁置、已通过的任务都可续作）；长期不动的开放任务用
  `twin(action="task_close")` 显式关闭（关闭前先经用户确认，不要自行清理）
- **同会话重复注入省 token**：同一会话再次 task_start/task_resume 同 work_type，
  且上文注入返回的 persona_version 仍在场（未被上下文压缩）→ 传
  `have_persona_version=<上文版本号>`，版本未变则服务端不再重复注入全文；
  上文不可见就**不要传**（服务端会照常全文注入，宁可重复不可缺席）
- write 响应提示"有偏好未编译" → **不要**立即 compile，也不要每写一条就播报：
  任务收尾（或用户问起）时非阻塞汇总一句，如"分身积累了 N 条新偏好，要不要现在整理？"；
  是否整理由用户拍板，或等定时扫描统一汇总处理
- 用户明确要求更新分身、或定时扫描汇总后用户确认
  → `twin(action="compile")` 拿素材包 → 当前会话模型编译 → `twin(action="submit")` 落版本。
  用户要求在当前会话整理就直接执行，强模型建议提一次即可，不要反复劝说换会话
- `status` 返回 scan_notice 时：按其 agent_instruction 询问用户是否创建每周扫描任务
  （宿主平台侧建，调用 `twin(action="scan")`）；用户同意后创建，之后不再重复问
- **首次**交付产出物时提醒一次（不是每次）：后续修改尽量交给 Agent 而非手动改，
  每次修改都是一次偏好沉淀机会

## write 的抽象口径

只收可复用抽象：偏好与规则（用语/详略/格式/口吻）、结构习惯、开工前应确认的问题与材料。
必带三字段 work_type / audience / purpose。
事件性内容（做了什么、何时交付）走普通 memory.remember，不要走 twin.write。

## 三字段选码（硬流程：先查清单再写）

- write / task_start 之前**必须**手上有枚举清单：同一会话首次先
  `twin(action="taxonomy")`（按需带 kind=work_type|audience|purpose）查清单，
  从清单里选最贴的 canonical code（或其别名/中文名）作为三字段的值——不要凭记忆造说法；
  已查过的清单会话内直接复用，用户裁定 pending 后才需重查对应 kind
- 清单里确实没有合适项时，给**你认为最合适的原始值**即可：它会自动进
  pending 由用户裁定（map / canonicalize / reject），不要硬凑一个近义枚举
- 不要依赖"我觉得我拿得准"——清单在手是每次必检的条件，不是拿不准时的补救

## compile / submit

- compile 返回素材包：旧版本 prompt（编译参考，非执行依据）+ 未编译证据 + 编译规则；
  **用独立会话执行、做完即弃**（建议强模型）：编译会话内新旧版本同屏，
  勿在同一会话继续交付任务——会话隔离是避免新旧 persona 冲突的唯一硬手段
- submit 时 `data.model` 填当前模型名，`source_memory_ids` 用素材包里的证据 id 列表
- 用户要求撤销/回退某版分身 → `twin(action="rollback", data={work_type, version?})`：
  省略 version 回上一版，传 n 回指定版；零阻力直接执行（不删历史、版本号不回收），
  不要建议重新编译代替回滚

## 治理

`twin(action="pending")` 查看 Agent 给的未识别值；
`twin(action="resolve")` 做映射（map 到既有码）、新建（canonicalize）或拒绝（reject）。
