# mema-twin（迷码分身）

> **English summary below.** 本文档以中文为主，顶部提供英文摘要。

个人分身：把用户在工作产物（PPT、文档、汇报、设计……）中反复体现的偏好、规则、
结构习惯沉淀为按工作性质分维度的记忆，再编译成**版本化、可溯源的 persona prompt**，
在开工前注入给 Agent——让 Agent 的产出越来越像用户本人。

## English Summary

mema-twin is a personal work twin built on top of mema (memory-arbiter). It extracts
reusable work preferences (style, structure, wording, pre-flight checklists) into
dimension-tagged memories, compiles them into versioned persona prompts per work type
(33 work types / 9 audiences / 8 purposes in v1), and serves the active prompt to
agents before they start a piece of work. Preferences live in mema; compiled prompts
live in twin's own SQLite with a file mirror for fallback and human review.

## 工作原理

```
工作产出/修改 ──twin.write(三字段强制归一)──▶ mema 偏好记忆（带维度标签）
                                                    │
                              compile：取素材包 ────┘    ← 夜间定时任务/手动触发，Agent 提示
                                │
                                ▼
                  当前会话模型编译（建议强模型）
                                │
                                ▼
              twin.submit ──▶ twin.sqlite3 版本表 ──▶ prompts/ 文件镜像（降级+可视化）
                                │
                                ▼
        开工前 twin.get ──▶ 注入 plan / 执行流（严格按分身 prompt 执行）
```

设计要点（详见 `~/ZCodeProject/docs/mema-avatar-design-2026-09-02.md`）：

- **Agent 抽象、产品归一（v0.3.8 归一门）**：三字段（工作性质/受众/用途）未命中
  清单即**整笔打回**（错误附动态清单——从 DB 实时读，治理追加/自建码立刻可见）并落
  pending 裁定票据，Agent **必须问用户**：归一到已有值（resolve map，原值进别名表、
  同一说法终身只问一次）或创建新值（resolve canonicalize，新码即刻入列）。无 other
  杂项桶——真长尾走 canonicalize 立新码。设计公理：阻断只配灾难拦截（验证门）与数据
  入口正确性（归一门），质量类提议交互一律砍。（v0.3.0 移除 embed 近邻档：语义选码
  由 LLM 对清单完成即可。）
- **偏好本体存 mema**：经其 HTTP MCP 读写，复用 mema 的冲突检查、审计与治理；
  twin 自有 `twin.sqlite3` 存枚举注册表、待裁长尾、prompt 版本、证据指针索引
  （twin_evidence 只存 mema 记忆 id 与维度标签，不存正文；compile 按 id 精确
  read 取全文，召回无丢失）。
- **DB 为准、文件镜像降级**：`prompts/<work_type>/vN.md` + `active.md`；
  交付稿在 task_submit 时落 `deliverables/task-N.md`。画像人级全局，本地无 workspace 维度。
- **编译执行者＝宿主 Agent**（twin 不自配模型）：compile 返回素材包，会话模型编译后
  submit 回库，版本记录来源 memory ids 与编译模型。建议在强模型会话中执行编译。
- **交付任务流（机制改造自 plan-mode；v0.3.8 删评审环）**：`planning → submitted
  （终态）` / superseded——task_submit 即交付收口并落盘审计文件，无评审环（评审
  verdict 无下游消费者，用户反馈走对话 → twin.write 主路径）；task_resume（仅
  planning）恢复 todos 续作、task_revise（仅 submitted）返工记 lineage；
  task_start / task_resume 即 persona prompt 注入点。
- **定时任务挂 Agent 端（单一夜间编译任务）**：twin 自身不起调度；status 里的
  scan_notice 提醒 Agent 征询用户后在宿主平台建夜间 persona 编译任务（每天：吸收未编译
  偏好、作废条款重编、受众画像重抽象），近期在转则提醒自消失；夜间任务 7 天没跑过会
  重新出现（停转保险丝，mema 首装提醒同款模式）。
- **新偏好即时生效**：task_start/task_resume 注入 persona 时同步携带该工作性质的
  未编译偏好增补（persona_supplement，冲突以增补为准，上限 10 条）；夜间定时任务把
  增补自动编译进新版本。（v0.3.8 删双跑提议：rollback 免费且可逆，事前确认无价值。）
- **夜间编译验证门（瘦身版）**：夜间（`origin=scheduled`）落版前过确定性检查——只拦灾难
  形态：素材回声（复述素材包标记，标题类子串 + 节标题行首匹配；active 旧版含同标记时
  沿袭降级为警告防自锁）与分区标题（ATX 零标题即畸形稿），未过拒绝落版（validation_failed，
  active 不变、证据未消耗、次晚自动重试）；**空转阻尼**：提交不含任何新证据且非 stale
  触发时拒绝（no_new_evidence，防版本号空转）；证据未全覆盖（类型=
  在世证据超集、画像=集合相等）与交互式违规只警告不拦。连续被拒不落版累计于 status 的
  `nightly_rejected`（成功落版即清）——持续被拒与空转闭环的唯一出口信号。
- **全量投影编译**：compile 素材为该类型**全部在世证据**（不分编译状态，排除作废）——
  每版从头重编，弱底稿不遗传、证据库是唯一事实源；配套编译规则三件套：含义稳定表达自由
  （证据未动的规则不得变含义，合并/条件化/重组自由）、硬预算（类型 8000 字符/60 条、
  画像 4000/40，超限强制合并淘汰并写明依据）、版本间变更分级（语义变更须归因证据 id 或
  预算）；v0.3.9 增简练优先（最小可执行表述，禁为省字符抽象掉动作/条件/例外）与素材包
  受众/用途维度标签。status 附带各版本体积曲线（size_chars）与 active 超预算标记。
  索引若真丢失，正解是按 `twin:wt:*` 等 tags 从 mema 重建 twin_evidence（一次性脚本，
  需要时再写；mema 数据无损）——不做 find 语义兜底召回（v0.3.9 退役：可能召回不相干
  记忆属伪造素材）。
- **冲突链路**：twin.write 透传 mema 的 notice（`mema_notices` + 分诊指引随响应）——
  similar_active_memory 疑似重复静默分诊；语义冲突 notice 分诊后真冲突才问用户三选项
  （都留/新替旧/撤销新写的）。`void` 动作作废证据（行级、全链路排除、不可逆）；曾入编译
  的证据被作废触发 `persona_stale` 夜间自动重编，受众侧经 audience_stale 计数差重抽象；
  素材包常驻「已作废条款」节防旧版参考带回作废条款。status 附 open_conflicts/open_tasks
  治理计数。
- **执行计划层（v0.4）**：建档任务默认走计划（两档路由：无重复执行价值的一次性事务
  才豁免建档，既已建档必须 `plan_set` 后动手）——plan_set 落 `twin_plan_steps` 六态
  步骤（pending/in_progress/done/failed/blocked/skipped；done∪skipped=闭环）与
  open_questions，直接放行无审批门；to=in_progress 过三门（blocking 疑问 → 依赖
  （依赖满足=done∪skipped）→ 单一 in_progress）；pending 直跳 done/failed 自动标
  backfilled（允许补打卡，审计可见）；failed 必带 reflection（服务端硬门）。收口门：
  task_submit 建档任务未闭环步骤整笔打回（附 open_steps 清单），闭环后 outcome=success
  落任务行；task_close 显式关闭时未闭环步骤批量 skipped(reason=closed)。plan_revise
  支持 remove/update/add/answers 四类修订单次提交（固定顺序执行），answers 写回
  open_questions 解锁 blocking；task_resume/task_revise 深拷贝未完结步骤到新 task_id
  （origin_step_id 溯源、depends_on 重映射，旧代已终结依赖剥除）。中断续作历史
  （transitions）只增不删。
- **执行经验 playbook 闭环（v0.4）**：任务闭环后夜间任务 `task_evaluate` 按水位
  （twin_meta 单调推进）打包执行记录（reflection/tool_log/步骤路径）出素材包 → 会话
  模型提炼 playbook（per work_type，独立表 `twin_playbooks`，与 persona 版本互不相扰）
  → `playbook_submit` 落版（`playbooks/<key>/` 镜像；origin=scheduled 过验证门：素材
  回声 G1/无标题 G2/溯源校验/空转阻尼，交互式只警告）；task_start/task_resume 注入
  active playbook（work_type 两级回退到 global）——**连续性分级**：同一宿主
  （last_used_client）重复使用只轻注入全文+近期验证提示，换宿主重注入并要求
  `available_tools`（下次 task_start 传，服务端逐条比对工具面板回 `tool_gap`）；
  playbook 是 advisory 参考，与实际环境冲突按实际执行。`playbook_rollback` 同
  persona rollback 语义。
- **升级提示（v0.4）**：daemon 线程每 24h 拉 GitHub（raw + API 双通道）比对 pyproject
  版本，`twin_notices` 随 ok 响应携带（update_available 7 天/版本抑制；post_upgrade
  首装建基线不发）；版本单源 importlib.metadata（回落仓库 pyproject）。env
  `MEMA_TWIN_UPDATE_CHECK=0` 关闭。

## 工具（单工具动作式）

`twin(action, data)`，动作：

| 动作 | 说明 |
|------|------|
| `write` | 沉淀一条工作偏好。必填 content/work_type/audience/purpose（未命中清单整笔打回+动态清单+裁定票据，问用户 map/canonicalize 后重试）；任务流内沉淀可传 `task_id`——缺省维度沿用该任务的三维度（scope=audience 时不继承 work_type；显式传入优先，响应 `dims_inherited` 列出继承项）；对某受众的通用偏好传 `scope=audience`（work_type 省略），进该受众画像 |
| `get` | 取某工作性质的 persona prompt（开工前调用）；可选 `version` 取历史版本全文；`aud-{受众}` 可读受众画像 |
| `compile` | 取编译素材包（旧版本 prompt 编译参考 + **全部在世证据**（全量投影）+ 已作废条款清单 + 编译规则（稳定律/硬预算/变更分级）），独立会话执行、做完即弃 |
| `submit` | 提交编译产物，落版本并写镜像（返回 `supersedes`），回写证据编译标记；夜间定时任务落版传 `origin=scheduled`（过**验证门**：素材回声/缺分区标题拒绝、无新证据空转阻尼拒绝，均在 status 的 nightly_rejected 累计；证据未全覆盖与交互式违规只警告） |
| `rollback` | 回滚 persona 版本（零阻力）：`version` 省略回上一版，传 n 回指定版；不删历史、版本号不回收 |
| `status` | 版本概况（含体积/超预算标记）、受众画像（audience_profiles）与重抽象队列（audience_stale）、条款作废待重编（persona_stale）、未编译统计、pending 数量（归一门待裁票据）、夜间被拒计数（nightly_rejected）、open 冲突/进行中任务计数、执行计划统计（plan_stats：任务/步骤各态/backfilled/open blocking 疑问/tool_usage/playbooks）、定时任务安装提醒 |
| `taxonomy` | 列枚举清单（动态：含治理追加别名与自建 canonical；kind ∈ work_type/audience/purpose） |
| `pending` / `resolve` | 归一门待裁票据的查看与治理（map=归一并进别名表 / canonicalize=立新码入列 / reject=不入体系且不得重试原值） |
| `task_start` | 开工建档并注入 persona prompt + 未编译增补 + active playbook（连续性分级）；`have_persona_version` 申报同会话已注入版本，未变则省略重复注入；带 audience 时注入受众画像 `audience_profile_md`/雏形；audience/purpose 可选，给了值必须在清单内；可选 `available_tools`（工具名列表，服务端与 playbook 工具面板比对回 `tool_gap`） |
| `plan_set` | 建档任务列步骤计划（v0.4 执行计划层）：steps（title/depends_on）+ open_questions（可标 blocking），直接放行开工；重复调用即重排（未完结步骤标 skipped(replanned)，done 保留） |
| `step_update` | 步骤状态机打卡（六态；to=in_progress 过三门：blocking 疑问→依赖→单一 in_progress）；done/failed 闭环（failed 必带 reflection；pending 直跳标 backfilled 追认） |
| `plan_revise` | 修订单次提交：steps_remove（仅 pending 且不被本代未删步骤依赖）/ steps_update（改 deps/状态走状态机）/ steps_add / answers（open_questions 写回解锁 blocking） |
| `tool_log` | 批量记工具经验（tool/purpose/outcome ∈ success/fail/degraded/skipped，可附 note/skill_digest），task_evaluate 素材来源 |
| `task_submit` | 提交交付稿并收口（**submit 即终点**，v0.3.8 无评审环），落盘 `deliverables/task-N.md`；建过计划的任务收口门拦未闭环步骤；outcome 自动 success；open blocking 疑问仅警告；交付后用户反馈走 twin.write |
| `task_resume` / `task_revise` / `task_close` | 续作进行中任务（仅 planning，恢复 todos + 深拷贝未完结计划到新 task_id）/ 已交付任务修订返工（仅 submitted，子任务回 planning 记 lineage + 同款深拷贝）/ 显式关闭进行中任务（outcome=failed\|superseded，未闭环步骤批量 skipped(closed)） |
| `task_evaluate` | 打包水位后已闭环任务的执行记录（reflection/tool_log/步骤路径）出 playbook 素材包（夜间任务调用；水位单调，可显式补评估） |
| `playbook_submit` / `playbook_rollback` | playbook 落版（origin=scheduled 过验证门：溯源/素材回声 G1/无标题 G2/空转阻尼；交互式只警告）与回滚（省略 version 回上一版） |
| `task_recent` / `task_get` | 任务列表 / 单任务全量 |
| `todo` | 会话 todo 读写（plan-mode 同款语义） |

## 维度枚举 v1

work_type 33 项（六域：规格与执行/分析与复盘/记录与同步/说服与传播/教学与传授/法律与契约——
按产出物目标分类，v0.3.9 重划）、
audience 9 项、purpose 8 项（v0.3.8 删 other 杂项桶）。清单见 `mema_twin/taxonomy.py` 或
`twin(action="taxonomy")`（动态：含自建 canonical）。后续版本可扩展；真长尾经用户
canonicalize 立新码进清单。

## 快速开始（GitHub 安装）

前置：本机已跑 mema HTTP MCP（默认 `http://127.0.0.1:8000/mcp`）——twin 的偏好本体
存在 mema 里。

```bash
uv tool install git+https://github.com/billy12151/mema-twin
```

MCP 客户端配置（Claude Desktop / Cursor / 通用 stdio 均可）：

```json
{
  "mcpServers": {
    "mema-twin": {
      "command": "mema-twin",
      "env": {
        "MEMA_TWIN_MEMA_URL": "http://127.0.0.1:8000/mcp",
        "MEMA_TWIN_CLIENT_ID": "你的客户端名"
      }
    }
  }
}
```

装好 skill 引导：仓库根 `make install-skill` 把 `skill/SKILL.md` 分发到三宿主技能目录
（zcode / jingleAI / workbuddy，cp 保留源文件），Agent 才知道何时调 `twin.write` / `task_start`。

## 开发（本地源码）

```bash
git clone https://github.com/billy12151/mema-twin
cd mema-twin
uv venv && uv pip install -e ".[test]"
.venv/bin/pytest -q          # 跑测试
```

环境变量：

| 变量 | 默认 | 说明 |
|------|------|------|
| `MEMA_TWIN_DB_PATH` | `<项目>/twin.sqlite3` | twin 自有库路径 |
| `MEMA_TWIN_PROMPTS_DIR` | `<项目>/prompts` | prompt 文件镜像目录 |
| `MEMA_TWIN_DELIVERABLES_DIR` | `<项目>/deliverables` | 交付物文件目录 |
| `MEMA_TWIN_MEMA_URL` | `http://127.0.0.1:8000/mcp` | mema HTTP MCP 端点 |
| `MEMA_TWIN_TRANSPORT` | `stdio` | `stdio`（默认，单机零运维）或 `http`（多 Agent 共接，mema 同款形态） |
| `MEMA_TWIN_HTTP_HOST` / `MEMA_TWIN_HTTP_PORT` | `127.0.0.1` / `8765` | http 模式监听地址；端点 `/mcp`，无状态直调（免 initialize） |
| `MEMA_TWIN_CLIENT_ID` | `zcode` | 宿主客户端默认身份（zcode/kimi/...）。http 模式下连接头 `X-Mema-Client` 是权威身份（`data.client` 只能与头一致或省略，不一致打回，堵跨宿主冒充）；stdio 无头时 `data.client` > 本 env。agent_id 与 mema 侧偏好存储桶均写死为 `mema-twin`（子 agent 范式）——按 agent_id 一次查出所有经 twin 落库的偏好，配合 `twin:*` 标签双保险 |
| `MEMA_TWIN_UPDATE_CHECK` | 开启 | `0`/`false`/`off` 关闭版本更新检查（daemon 线程，24h 一次，失败静默） |
| `MEMA_TWIN_UPDATE_STATE_PATH` | `~/.local/share/mema-twin/update_state.json` | 更新检查状态文件（抑制窗口/升级基线） |

## 多 Agent 共接（http 模式）

mema 天然多 Agent，twin 同样支持：一个 http 进程服务所有客户端，偏好汇入同一
人级画像，各宿主用 MCP 配置里的 `X-Mema-Client` 头标识自己（twin 透传给 mema
审计可见，mema 同款形态）：

```bash
MEMA_TWIN_TRANSPORT=http .venv/bin/mema-twin   # 监听 127.0.0.1:8765/mcp
```

```json
{ "mcpServers": { "mema-twin": {
    "type": "http", "url": "http://127.0.0.1:8765/mcp",
    "headers": { "X-Mema-Client": "zcode" } } } }
```

宿主身份规则：http 模式下 `X-Mema-Client` 头是权威（mema `_identity_mismatch`
同款语义——`data.client` 与头不一致直接打回，`data.client` 只在 stdio 无头时
作为显式归属手段，兜底回落 `MEMA_TWIN_CLIENT_ID` env）。脏值/重复头/非字符串
一律打回，不会静默记错归属；http 绑定仅允许 loopback（X-Mema-* 头不是鉴权，
对外暴露等于开放身份伪造）。会话 todo / 任务流按 `session` 参数隔离。stdio 模式
不受影响，仍是单机默认。

## 开发

```bash
.venv/bin/python -m mema_twin.server   # 手动跑 server (stdio)
```

## 路线图

- **阶段 0 原型自用**：表 + 归一 + 手动编译 + 注入，吃自己狗粮验证"越来越像"
- **阶段 1 开源试用**：GitHub 公开仓库分发（`uv tool install git+...`），验证留存与
  "越来越像"结论（许可证已定 Apache-2.0——twin 实现简单，闭源买不到保护，开源是信任
  与采用率的前提；官方 MCP Registry 暂不上，其包类型只认 npm/PyPI/NuGet/Cargo/OCI/MCPB，
  Python 包的最小路径是 PyPI，当前选择跳过）
- **阶段 2 收费拍板**：收费叙事在 mema-team（团队多人画像、组织级治理、托管）——那是
  协调价值而非代码价值；twin 保持开源作为其漏斗顶部，凭留存数据决定形态

## 设计文档

`~/ZCodeProject/docs/mema-avatar-design-2026-09-02.md`（已确认/现状/建议/待确认四分区）
