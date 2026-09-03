---
name: mema-twin
description: 个人分身：工作类偏好沉淀与 persona prompt 编译，经交付任务流注入执行。修改/产出工作文档后调 twin.write 沉淀偏好；开工前 task_start 取分身 prompt 并建档；版本更新走 compile/submit。
---

# mema-twin 使用引导

## 何时用

- 用户修改/审阅了工作产物（PPT、文档、汇报、设计……），或显式要求沉淀工作偏好
  → `twin(action="write")`
- 开始一件工作类产出任务（先于 plan、先于动手）
  → `twin(action="task_start", data={"work_type": ..., "brief": ...})`：
  建档并返回该工作性质的分身 prompt，严格按其中的偏好/结构/前置清单执行；
  材料不齐全按前置清单向用户确认或要求补齐
- 交付稿完成 → `twin(action="task_submit")`；用户审阅后
  → `twin(action="task_review", verdict=approved|changes_requested, notes=...)`
  通过即收口；changes 的意见先 twin.write 沉淀，改稿后**同一任务再 task_submit**
  提交下一轮（轮次递增，评审历史全量可审计）
- 中断/隔日继续 → `twin(action="task_resume", data={"task_id": ...})`
  （进行中、已提交、被搁置、已通过的任务都可续作）；长期不动的开放任务用
  `twin(action="task_close")` 显式关闭
- write 响应提示"有偏好未编译"、或用户要求更新分身
  → `twin(action="compile")` 拿素材包 → 当前会话模型编译 → `twin(action="submit")` 落版本
- `status` 返回 scan_notice 时：按其 agent_instruction 询问用户是否创建每周扫描任务
  （宿主平台侧建，调用 `twin(action="scan")`）；用户同意后创建，之后不再重复问
- 交付产出物时提醒用户：后续修改尽量交给 Agent 而非手动改，每次修改都是一次偏好沉淀机会

## write 的抽象口径

只收可复用抽象：偏好与规则（用语/详略/格式/口吻）、结构习惯、开工前应确认的问题与材料。
必带三字段 work_type / audience / purpose（原始值即可，产品负责归一）。
事件性内容（做了什么、何时交付）走普通 memory.remember，不要走 twin.write。

## compile / submit

- compile 返回素材包：当前版本 prompt + 未编译证据 + 编译规则；建议在强模型会话中执行
- submit 时 `data.model` 填当前模型名，`source_memory_ids` 用素材包里的证据 id 列表

## 治理

`twin(action="pending")` 查看 Agent 给的未识别值；
`twin(action="resolve")` 做映射（map 到既有码）、新建（canonicalize）或拒绝（reject）。
