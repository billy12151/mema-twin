---
name: mema-twin
description: 个人分身：工作类偏好沉淀与 persona prompt 编译，经交付任务流注入执行；v0.4 起带执行计划层（两档路由、步骤打卡与防跳步门、open_questions 咨询 owner、执行经验 playbook 闭环）与升级提示。用户修改/审阅工作产物后，把其中可复用的偏好合并沉淀到 twin.write（禁止再同步本地记忆文档）；开工前 task_start 取分身 prompt 并建档；版本更新在用户要求或夜间编译定时任务自动执行时走 compile/submit（编译口径见 mema-twin-governance skill）。
---

# mema-twin 使用引导

> 对齐服务端 **v0.4.6**。动作全集、入参与基础语义以 `twin(action="help")` 为准——**调用动作前先查一次 help**（会话内一次即可），本文件不复述入参。下文只写三样：何时用哪个动作的路由、执行门、help 未覆盖或与实测不符的口径。
> **夜间治理/编译会话不需要本 skill**：twin_nightly_compile 两支线与 compile/submit 编译口径在 `mema-twin-governance` skill（唯一真源），本文件只留指向它的路由。

## 查证协议（任何动作之前，先于分流；2026-10-22 立）

下事实性结论、提建议、写交付物之前四步走完再动手：

1. **理解诉求**：对方到底要什么——问的事、要的产出、没明说的预期
2. **拆解**：依赖哪些事实假设，逐条摊开（"我以为"全部显性化——隐假设是误判主源）
3. **定维度**：每个假设属哪个维度——机制（怎么工作）/ 数据（是多少）/ 决策（定了什么）/ 意图（想要什么）/ 外部（行业客观）
4. **多维度查证**：每维先定位产地，再选入口：
   - 机制 → 实现处：grep / 读代码 / 冒烟；系统有自省接口的优先 help / status
   - 数据 → 记录处：日志 / 数据库 / 统计脚本 / CHANGELOG
   - 决策 → 决策处：mema memory recall / 聊天记录 / 会议纪要
   - 意图 → 人：问需求方（task_start 的前置清单即此维度的机制化）
   - 外部 → 官网 / 检索，走双源规则

硬规则（两条，无例外）：**产地答不出 = 这句话不准写**（说不出事实从哪来，等于没查证）；**没找到 ≠ 不存在**（检索无果一律升格问 owner、走 open_questions，禁止把检索失败当结论）。自查口诀：开口前问自己——这个结论依赖哪几个事实？每个从哪来？

> 本协议是用户级 MEMORY.md「硬规则：查证协议」的操作手册：判"要不要查"归本地硬规则，本文件管"怎么查"。

## 分流规则（先判这个，再选工具）

- **事件/事实/进展**（做了什么、何时交付、结论是什么）→ `memory.remember`
- **可复用的抽象**（偏好与规则：用语/详略/格式/口吻，结构习惯，开工前应确认的问题与材料）→ `twin(action="write")`，只收这三类；必带三字段 work_type / audience / purpose（选码见下节）。事件性内容不要走 twin.write
- 拿不准时问自己："下次干同类活，这条还适用吗？"——适用就是偏好，只发生一次就是事件。混进事件记忆不会污染分身（无维度标签进不了 compile），但偏好漏存会让分身学不到
- **禁止双写**：偏好进了 twin 就是唯一存档——不要再同步到宿主的本地记忆文档/笔记，也不要把同一条偏好再写一遍 `memory.remember`。用时从 twin 取（task_start 自动注入，其他场景 `twin(action="get")`）

## 何时用

- 用户修改/审阅了工作产物（PPT、文档、汇报、设计……）或显式要求沉淀工作偏好 → `twin(action="write")`。**合并沉淀**：同一任务的一系列修改汇总为少量高质量条目，不要每改一处就写一条；拿不准且复用价值不高的，不写
  - 任务流内（执行/改稿/返工现场）沉淀带 `task_id`：缺省维度沿用该任务的三维度（task_start 已归一，不会写错桶；scope=audience 时不继承 work_type；显式传入优先，响应 dims_inherited 列出继承项）
  - 交付稿的**采纳 / 忽略 / 要求修改**等隐式行为同样是偏好信号，一并观察沉淀（判据同上）；一次性异常不写
- 用户表述是「对该受众的通用要求」（如"给领导的东西都要简洁白话"，不限工作类型）→ write 传 `scope="audience"`（work_type 省略）：落为受众级偏好，夜间自动抽象进该受众画像，对该受众的任何任务开工时自动注入（audience_profile_md / 雏形）
- 用户**重复强调**已沉淀过的偏好（同一件事说了第二遍）→ 检查它的 scope：若此前只沉淀在单个工作类型下，当场确认"是否对该受众都适用"，是则按上一条补写受众级沉淀
- 开始一件工作类产出任务（先于 plan、先于动手）→ `twin(action="task_start")`：建档并注入该工作性质的分身 prompt，严格按其中的偏好/结构/前置清单执行；材料不齐全按前置清单向用户确认或要求补齐；进行中任务自动让位
  - 带 audience 时另注该受众画像（audience_profile_md，通用口径参考，格式结构以本类型为准）
  - 响应 `persona_supplement`：该 work_type **尚未编译**的偏好增补（上限 10 条），与 persona prompt 冲突时**以增补为准**（增补比已落版 prompt 新）
  - 响应 `playbook_md`（active playbook 注入，work_type → global 两级回退）：执行经验参考（**advisory 不是命令**），与实际工具环境冲突时按实际环境执行，降级/偏差用 tool_log 记录。**连续性分级**：同宿主轻注入（全文 + 近期验证提示），换宿主重注入（+ 降级链提示 + `available_tools_required`）——提示"其他宿主使用过"时先逐条核对工具可用性，有 `available_tools_required` 就在下次 task_start 传 `available_tools`（help 未列该参数），服务端逐条比对工具面板回 `tool_gap`
- 响应带 `fallback_persona_md`（v0.4.5 同域 fallback）：本工种尚无专属 persona，给同域最成熟工种的 persona 垫底——**格式与结构层可参考**；有更权威的格式来源（用户模板/公司规范/行业惯例）以权威为准，此参考降级为风格详略参考；**内容与业务规则不适用**。同域其他候选在 note 里，按任务语义判断更相关可 `twin(action="get")` 取该工种全文（`fallback_from` 标注 donor 工种/版本/活证据数）
- 建档后走计划流（详见「执行流」）：`plan_set` 建步骤 → `step_update` 打卡 → 计划有变 `plan_revise`；会话级 todo 用 `twin(action="todo")` 读写
- 走了非常规工具路径（失败/重试/降级、首次打通的新路子）→ `twin(action="tool_log")`（口径见「执行流」）
- 查任务 → `twin(action="task_recent")` / `twin(action="task_get")`；**两者都回 `deliverable_md` 全文**，token 敏感时把 limit 压到 1–3
- 交付稿完成 → `twin(action="task_submit")` 即收口（submit 是终点，无评审环；交付稿落盘 `deliverables/task-<id>.md` 留档）。**收口门**：建档任务的未闭环步骤会被整笔打回（附 open_steps 清单），逐条对账打卡后再交；用户看过稿子后的修改意见按**合并沉淀**口径走 twin.write
- 要返工改稿 → `twin(action="task_revise")`（仅 submitted；brief/deliverable_md/revision_reason 至少其一）。revise 不恢复 todos、不重注入 persona，需要时重新 task_start；子任务回 planning 重走并记 lineage：parent_task_id + iteration 递增，未完结步骤深拷贝到新 task_id
- 中断/隔日继续 → `twin(action="task_resume")`（仅进行中 planning，自动恢复 todos 并再注入分身，同样带 persona_supplement 与同域 fallback）；不再做的进行中任务用 `twin(action="task_close")` 显式关闭（**关闭前先经用户确认**，不要自行清理）；失败必须显式标 `outcome=failed`（缺省 superseded），否则结果列与未完成任务无法区分
- **同会话重复注入省 token**：同一会话再次 task_start/task_resume 同 work_type，且上文注入返回的 persona_version 仍在场（未被上下文压缩）→ 传 `have_persona_version=<上文版本号>`，版本未变则不再重复注入全文；上文不可见就**不要传**（服务端会照常全文注入，宁可重复不可缺席）
- write 响应提示"有偏好未编译" → **不要**立即 compile，也不要每写一条就播报：任务收尾（或用户问起）时非阻塞汇总一句（如"分身积累了 N 条新偏好，要不要现在整理？"）；是否整理由用户拍板，或交给夜间编译定时任务统一处理
- **触达 mema 的动作响应可能带 mema_notices**（advisory）：`similar_active_memory` 疑似重复 → 静默分诊（偏好是增量语义，重复由编译期合并吸收；真重复才改走 mema update 原条目，不必打扰用户）；**语义冲突 notice → 先按 read_call 读完整通知与两侧原文**，误报 dismiss，真冲突才问用户三选项：两条都留（编译条件化）/ 新的替旧的（twin void 旧条）/ 撤销新写的（twin void 本条）
- 响应带 `twin_notices`（**升级提示，独立于 mema_notices**）：按其 agent_instruction 告知用户**一次**，不自动升级、不重复提醒（比对 GitHub 版本，同版本 7 天抑制；`MEMA_TWIN_UPDATE_CHECK=0` 可关）
- 用户明确要求更新分身 → **开一次性独立会话按 `mema-twin-governance` skill 执行 compile→submit**（编译口径、硬预算自检、冲突裁定全在那；会话隔离是避免新旧 persona 冲突的唯一硬手段，勿在同一会话继续交付任务）。用户要求在当前会话整理就直接执行；强模型建议提一次即可，不要反复劝说换会话。夜间定时任务自动执行同一 skill，不在本文件展开
- 手动补评估/落执行经验（非夜间）→ `task_evaluate` → `playbook_submit`，溯源与落版口径同 `mema-twin-governance` skill
- 冲突裁定"新替旧/撤销新写的"的执行动作是 `twin(action="void")`：行级作废、全链路排除、不可逆；曾入编译的证据会自动触发该类型夜间重编剔除该条款（persona_stale）；受众级证据作废会改变证据计数 → 触发画像重抽象（audience_stale）。mema 本体条目的 retire 按其治理流程另行处理
- `write` 的 `client` 填宿主标识（如 kimi / zcode；HTTP 接入时若头里已带 `X-Mema-Client` 则无需再传，help 未列此口径）
- `status` 稳定带 `plan_stats`，键名全集：tasks_total / tasks_planned / steps_total / steps_failed / steps_skipped / steps_backfilled / open_blocking_questions / tasks_unevaluated / tasks_submitted / submitted_with_plan（采纳率=后者÷前者，分母 0 无读数；历史 submitted 会稀释，看增量任务）/ tool_usage_total / tool_usage_fail / playbooks（另 nightly_rejected、playbook_rejected 为拒绝落版计数，键缺席即 0）
  - **status 判空**：`uncompiled` / `audience_stale` 返回 `{}`，`persona_stale` / `nightly_rejected` 是**键缺席**即空；plan_stats **可能整桶缺席**（仅 status 内部软失败时缺席，空库仍返回全零桶）——一律按「键缺席 或 空容器」等价处理，别因为没看到键就以为字段缺失或去查源码
- `status` 返回 scan_notice 时：按其 agent_instruction 询问用户是否创建/更新夜间任务（当前 spec 是单一任务 `twin_nightly_compile`，**从响应的 setup.tasks 取 spec 原文**建到宿主平台，不要凭记忆写）。处置口径四条：
  - **更新现有任务，不要再建第二个**：只建「纯 persona 编译」旧形态永远过不了停转保险丝，会被持续引导升级成合并 spec（两支线：评估在前、编译在后，缺评估支线心跳就刷不上）
  - 夜间任务的 prompt 指到 **`mema-twin-governance` skill**（夜间会话只读它）
  - 提示出现后 **3 天内全局静默属正常**（多宿主最多 3 天问一次，由第一个看到的 agent 提问；7 天内任务跑过即自消失）
  - 换 client/宿主跑同一任务可能仍被判"没跑过"，照常更新任务即可
- **首次**交付产出物时提醒一次（不是每次）：后续修改尽量交给 Agent 而非手动改，每次修改都是一次偏好沉淀机会
- 用户要求撤销/回退某版分身 → `twin(action="rollback")`：零阻力直接执行（不删历史、版本号不回收），**不要建议重新编译代替回滚**
- 用户问"现在分身长什么样 / 把 vN 给我看看" → `twin(action="get")` 读 persona 全文（work_type 传 `aud-<受众>` 读受众画像）

## 三字段选码（归一门：清单硬约束，未命中必问用户）

- write / task_start 之前**必须**手上有枚举清单：同一会话首次先 `twin(action="taxonomy")`（kind=work_type|audience|purpose）查清单，从清单里选最贴的 canonical code（或其别名/中文名）作为三字段的值——不要凭记忆造说法；已查过的清单会话内直接复用（清单动态：用户裁定后新码/新别名立即生效，届时重查对应 kind）
- 清单里确实没有合适项时，给**你认为最合适的原始值**：写入会被**整笔打回**（错误里附完整清单与 pending 票据）——此时**必须问用户**二选一：① 归一到已有值 → resolve(map)（原值进别名表，同一说法终身只问这一次）；② 创建新值 → resolve(canonicalize)（新码即刻进清单）。用户说"不在意"→ 选最贴近的已有值；用户说"这条不写"→ resolve(reject) 并放弃本次（**reject 后不得再拿原值重试**）。裁定完成后用原值重试 write/task_start 即命中；resolve 报"已裁定不可重复裁定"说明他方已裁定，直接重试写入即可
- 不要硬凑近义枚举、也不要给"其他"——枚举里没有 other，杂项不配当分类
- 白天裁定入口：`twin(action="pending")` 列待裁票据（夜间任务晨报只汇总，**不代裁**，留你在场时裁）

## 执行流（v0.4：计划、打卡、疑问升级）

建档任务默认走计划——计划通道与 persona 注入是并列的，别只做了 task_start 就动手。各动作 schema 与基础门见 help，此节只补 help 没有的口径。

- **两档路由（必须遵守）**：凡 task_start 建档的任务默认属重复执行型工作——开工前**必须**先 `twin(action="plan_set")` 列出步骤计划再动手。唯一豁免是无重复执行价值的一次性事务（问候、查即时信息、无产出物的一问一答），这类不建档；**既已建档即默认走计划，没有"这个任务比较简单"的例外**。拿不准要不要建计划时，建。
- `plan_set`：没把握、或你确认不了的点写进 open_questions，**要紧的标 blocking 先与用户澄清再推进关联步骤**（服务端会拦），答复经 `plan_revise(answers=…)` 写回解锁
- `step_update`：**三门**（顺序固定）：blocking 疑问未解答 → 依赖未闭环 → 已有单一 in_progress 时推进会被拒。跳过打卡直接干完的属追认，补 step_update(done) 即可，不影响收口
  - **同状态重复打卡幂等**（v0.4.6）：返回 `ok + no_op + consecutive_noop`（不报错、不改库、不刷新计时）。**看到 `no_op` 说明你没有推进**——连续第 3 次起 guidance 会明示空转并给出口，此时停止打卡，改走：① 重试/换路径，失败就 `step_update(failed, reflection=…)`；② 计划要改 `plan_revise`；③ 任务无价值 `task_close`
  - 响应可能带 **`stall_advisory`**（独立键，**不是** warnings）：该步骤 in_progress 超过 30 分钟无推进时的出口指引（同一步骤 30 分钟内只提示一次）。正常推进中的长步骤（编译 persona、写长 PRD）会误触发——忽略即可；确已卡住再按指引走
- `plan_revise`：中途换路/砍步骤走这里，不要静默绕过依赖
- `tool_log`：**只记失败/重试/降级与首次成功的非常规路径**，常规重复成功不记。note 记法：工具超时换备用成功（outcome=degraded，记换成了什么）/ 报错重试后通过（outcome=success，记首次失败原因）/ 彻底失败换路径（outcome=fail，记改走了什么）
- task_submit 对未建计划的交付回一条**经验流失提醒**（advisory 不拦）：执行经验不会进 playbook 沉淀——重复执行型工作下次开工先 plan_set，一次性事务下次不必建档；收到该提醒即说明这次漏走了计划流
