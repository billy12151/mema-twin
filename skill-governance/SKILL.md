---
name: mema-twin-governance
description: mema-twin 夜间治理/编译会话专用：twin_nightly_compile 两支线执行手册（①执行经验评估→playbook 落版 ②persona/受众画像编译→落版），含 origin=scheduled 口径、编译硬预算自检、同批冲突裁定、验证门被拒处置、晨报汇总（pending 只汇总不代裁）。仅定时任务与编译一次性会话读本 skill；日常工作读 mema-twin skill。
---

# mema-twin 夜间治理手册

> 对齐服务端 **v0.4.6**。流程单一真源是 `mema_twin/scan.py` 的 `SCHEDULED_TASKS_SPEC`
> （建任务时随 scan_notice 的 setup 下发）——与本文件不一致时**以 scan.py 为准**；
> 动作入参与基础语义以 `twin(action="help")` 为准。本文件写执行口径与真源未展开的细节。

## 夜间会话总则

- 无人值守一次性会话（凌晨用户不在场）：**不向用户提问**、不代裁 pending、不处理 mema_notices（只记录进汇总）
- 两支线**顺序执行、各自无素材早退**：①执行经验评估在前（轻且快，防被最重的编译段吃掉会话预算后永远轮不到；水位在，漏一晚次晚自动补）②persona 编译在后
- 工具出错跳过并如实记录，同一项最多重试一次
- 收尾输出汇总：各类型前后版本号与吸收证据数、status 的 pending_count 与未收口任务数各一句、mema_notices 原样记录（留用户在场时分诊）

## ①执行经验评估支线

1. `status`：plan_stats.tasks_unevaluated=0 → 评估支线整体跳过（无素材不空转）
2. `task_evaluate` **必传 `origin="scheduled"`**（唯一合法值；help 未列该参数）：调用成功即刷停转保险丝心跳（`last_scheduled_playbook_at`），与当晚有无新经验无关——**漏传等于 7 天后误报任务停转**。无 task_ids 取未评估收口任务（≤10）；响应 task_count=0 则支线结束（心跳已刷）
3. 素材只含真实执行记录；`playbook_submit`：key 用 key_hint（或覆盖面最大的 work_type；跨类型经验用 `'global'`），source_task_ids=素材包全部任务 id；**编译产物每条必须 `<!-- task: N -->` 溯源，写不出来源的条目不得出现**
4. **无值得沉淀的新经验不提交不落版**

## ②persona 编译支线

1. `status` 定位：uncompiled=0 且无 persona_stale 的类型**不编译不落版**（防版本号空转）；persona_stale（条款被作废）重编剔除对应条款落版；audience_stale（证据数≠画像吸收数或尚无画像）逐受众重抽象落版
2. `compile` 取素材包：该类型全部在世证据（全量投影，每版从头重编），旧版本 prompt **仅编译参考、非执行依据**；与旧版冲突以新证据为准；含义稳定表达自由、变更分级**须归因**（文末按变更分级列出版本间变更）
3. **夜间保守封套**（仅夜间；用户在场的交互式编译不受此限）：无证据动机时不做整体重组——吸收新证据、剔除作废条款、预算合并照做，大重组留给交互式编译
4. **硬预算与落地前自检**：persona 正文 ≤8000 字符或 ≤60 条顶层列表行；受众画像 ≤4000 字符或 ≤40 条（画像留给增量条款的空间很小，长证据要抽象到口径层，别搬格式细节）。超限必须合并或淘汰并写明依据。submit **不校验预算**，超了会静默落版——草稿先写临时文件，`wc -m` 数字符、`grep -c '^- '` 数顶层条目，过线再提交，事后再用 `rm` 清掉临时件。画像（4000 字符）逼近上限时，优先压缩「版本间变更」里的历史日志（按证据分组、逐版一行，归因 id 保留不丢）与合并同源条目，别丢动作/条件/例外
5. **同批证据内部冲突**：常见形态是「后者是对前者的分寸收窄」（如 1016「敬语用麻烦/烦请」被 1018「去 AI 味不等于口语化」收窄）——取更具体/更贴用户定调的一条为准，并在「版本间变更」里显式写「冲突裁定：X 与 Y 冲突，取 Y（理由）」，不要静默择一
6. `submit` **必传 `origin="scheduled"`**（夜间来源标记，验证门 G1/G2 与空转阻尼仅对 scheduled 生效——**漏传等于绕过拦截**）：source_memory_ids=素材包全部在世证据 id；响应回 `supersedes`
7. 受众画像走同一通道：`compile(work_type="aud-<受众>")` 取画像素材包（`aud-` 前缀即画像口径，**无需另传 `audience`**——help 未列该参数；夜间 spec 里带着 `audience:true`，实测省略同样正常返回，带着也无害）。画像**派生不消耗证据**（响应 `derived:true`、`evidence_marked_compiled:0`，对应偏好仍留在原类型队列里）；source_memory_ids 同样报齐素材包全部证据 id（多报/漏报/混入他受众 id 只警告不拦，次晚自愈）；audience_stale 为空则整体跳过
8. compile 响应带 `session_note`：编译会话**用完即弃**——汇总输出末尾提示"后续交付任务换新会话重新开始"（新会话 task_start 会自动注入新版）

## 验证门被拒处置（两支线同口径）

`origin=scheduled` 的 submit / playbook_submit 落版前过确定性检查：素材回声（复述素材包）/ 缺分区标题 → validation_failed；无新证据可吸收 → no_new_evidence（空转阻尼，防版本号空转）。**被拒时如实记录原因并跳过，不要为过门改产物**（active 未变、证据未消耗，次晚自动重试）；连续被拒累计在 status 的 nightly_rejected / playbook_rejected（**成功落版即清**）。

## 治理汇总（夜间只汇总，不处置）

- `pending`：归一门待裁票据非空时取明细（type_kind / raw_value / hit_count=打回次数）列入收尾汇总——这些是三维度打回待用户裁定的长尾（map/canonicalize/reject），**留用户在场时裁定，夜间不代裁**；为空跳过本调用
- `mema_notices`：原样记录进汇总输出，夜间不处理（advisory，留白天分诊）
