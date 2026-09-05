# Changelog

## [0.3.4] — 2026-09-05

- **rollback 动作（persona 版本回滚，零阻力）**：`twin(action="rollback")`，`work_type`
  必填，`version` 省略回上一版本（active 之外号最大的行）、传 n 回指定版本。事务内切换
  active 指针、刷新 activated_at、重写 `active.md` 镜像（v{n}.md 不动；镜像失败降级为
  警告不击穿）。无确认、无警告、无拦截——即使效果是新版本习得的能力从 active 消失；
  报错仅限客观不可能操作（无版本可回滚 / 目标不存在，附可用版本列表）。目标已是 active
  幂等成功。不删历史（retired 可再激活）；版本号永不回收（回滚后 submit 仍 MAX+1）。
- **compile 会话治理**：素材包旧版标题从「当前版本 prompt」改为「旧版本 prompt（编译
  参考，非执行依据）」并注明「新稿 submit 落版后即取代它」；编译规则同步改词（与旧版本
  冲突以新证据为准）。动机：原标题在 submit 落新版后成为永久留在上下文里的假话，且无法
  回收——标签必须出生即经得起时间。`submit` 响应新增 `supersedes`（被取代的旧 active
  版本号）：落版即裁决，不等下一次注入。SKILL.md 引导编译走独立会话、做完即弃（编译会话
  内新旧版本同屏，会话隔离是避免冲突 persona 的唯一硬手段）。

## [0.3.3] — 2026-09-04

- **偏好存储桶写死**：`workspace=mema-twin` 从 env（`MEMA_TWIN_WORKSPACE`）改为代码
  常量，env 一律无效（含坏值，不再打回）。与 0.3.2 的 agent-id 写死对齐：身份类
  配置（agent_id/workspace）产品内定，部署类配置（URL/TRANSPORT/CLIENT_ID）才留 env。
- **client 头透传（http 多宿主归属）**：http 模式下 twin 读宿主连接自带的
  `X-Mema-Client` 头并透传给 mema（取头方式与 mema core `request_identity` 同款）。
  头是权威身份：`data.client` 只能与头一致或省略，不一致打回 invalid_input
  （mema `_identity_mismatch` 同款语义，堵跨宿主冒充）；stdio 无头时
  `data.client` > `MEMA_TWIN_CLIENT_ID` env。脏值/重复头/非字符串/含首尾空白
  一律打回；http 绑定仅允许 loopback（非 loopback 拒绝启动）；
  `MEMA_TWIN_CLIENT_ID` env 脏值启动即报错退出。
- 建档归属修正：`task_start` / `task_resume` / `task_revise` 建（子）任务时 client
  取当前调用者（头/显式），不再恒落 env 默认。resume/revise 子任务记录当前执行者
  而非继承原档。注意归属覆盖建档动作；`task_submit` / `task_review` 行本身不带
  client（评审审计沿任务链关联建档者）。
- `sink.find` / `sink.read_memory` 增加 `client` 透传参数（compile 兜底召回路径同样
  携带真实宿主身份）。
- examples：stdio 模板删 `MEMA_TWIN_WORKSPACE`；新增 `examples/zcode.http.mcp.json`
  （`type=http` + `X-Mema-Client` 头模板）。
- 两轮 review（常规+对抗性）修复：入口统一身份校验（fail-fast）、重复头/空白/类型混淆拒绝、loopback 绑定守卫、env 启动校验、compile 路径身份统一。测试 58→75 全绿。

## [0.3.2] — 2026-09-03

- **agent-id 写死**：`X-Mema-Agent-Id` 从 env（`MEMA_TWIN_AGENT_ID`）改为模块常量
  `mema-twin`（子 agent 范式：client 标识宿主、agent_id 标识写入者）。用户侧配置面
  只剩 client；env 从代码/文档/配置模板全部移除。

## [0.3.1] — 2026-09-03

- **http 传输（多 Agent 共接）**：`MEMA_TWIN_TRANSPORT=stdio|http`（默认 stdio）；
  http 时 `MEMA_TWIN_HTTP_HOST/PORT`（默认 `127.0.0.1:8765`），端点 `/mcp` 无状态
  直调（免 initialize，mema 同款形态）。`twin.write` 的 `data.client` 可覆盖默认
  client，共接宿主归属分离（agent_id 恒 `mema-twin`）。

## [0.3.0] — 2026-09-03

- **移除 embed 近邻归一档**：归一收敛为 精确/别名 → pending 两档。候选枚举仅 53 项，
  语义选码由调用方 LLM 对 `taxonomy` 清单完成（SKILL 硬流程：先查清单再写；清单无
  合适项给原始值，进 pending 由用户裁定）——本地小模型 0.75 阈值静默错归无人工
  检查点、吃掉 pending 治理信号，且 llama-cpp-python + GGUF 是只为 53 个枚举背的
  重依赖。移除后 twin 自身零向量模型，mema-team 集成天然满足单向量模型约束。
- 删除 `mema_twin/embed.py`、`MEMA_TWIN_EMBED_MODEL` env、pyproject `core` extra
  （memory-arbiter-mcp / llama-cpp-python 依赖随之取消）。
- 修复 server.py 各 handler 的 sqlite 连接泄漏（全部 try/finally 收口）；write 的
  `tags` 参数非列表时打回 invalid_input（原会被逐字符拆成 tag）。
- **许可证定为 Apache-2.0**（2026-09-03 拍板）：twin 实现简单，闭源买不到保护、
  开源是信任与采用率的前提；收费叙事保留在 mema-team（协调价值），twin 作其漏斗顶部。
- **仓库转 public 进入阶段 1 开源试用**（2026-09-03）：GitHub 分发（`uv tool install
  git+https://github.com/billy12151/mema-twin`），README 补快速开始（MCP 客户端配置
  模板 + 前置 mema HTTP MCP 说明）；官方 MCP Registry 暂不上（包类型仅认 npm/PyPI/
  NuGet/Cargo/OCI/MCPB，Python 的最小路径是 PyPI，当前选择跳过）。

## [0.2.0] — 2026-09-03

M1 + M2 落地（mema-core 0.15.4 基线），两轮 review（常规+对抗性）修复 28 项发现，61 测试绿：

- **embed 近邻归一档（M1.1）**：别名 miss 后先语义近邻（阈值 0.75），命中映射
  canonical（matched_by=embed，不落别名）；复用 mema-core ManagedEmbedder（懒加载
  共享同一 GGUF），模型路径 env `MEMA_TWIN_EMBED_MODEL` → mema 配置 → 禁用，
  任何失败 fail-open 走 pending。
- **证据指针索引 twin_evidence（M1.3）**：write 成功登记 mema memory id + 三维度；
  compile 按 id 精确 `memory read` 取全文，召回无丢失；submit 回写编译标记；
  索引为空时退回 find 兜底（`include_content=true`，适配 0.15.4 find 索引页化）。
- **交付任务流（M2.1，机制改造自 plan-mode-mcp）**：task_start/submit/review/pending/
  resume/revise/recent/get + 会话 todo。可审计（任务行不可变追加 + append-only 评审表
  + lineage）、可中断（pending）、可继续（resume 恢复 todos）；task_start/resume 注入
  persona prompt；评审通过交付稿落 `deliverables/` 文件。
- **定时扫描（M2.2，模式照搬 mema scan_tasks）**：`scan` 动作汇总未编译偏好/pending
  积压/开放任务并产出建议；status 携带自消失安装提醒（7 天内跑过即不再提示）；
  help(topic="scheduled_tasks") 与提醒同源渲染；twin 自身不起调度。
- SKILL.md/README 更新；SKILL 安装位 `~/.zcode/skills/mema-twin/`；
  pyproject `core` extra 钉 mema-core ≥0.15.4 + llama-cpp-python。
- **画像人级全局（2026-09-03 定案）**：twin 三表（twin_evidence/twin_prompt_versions/
  twin_tasks）删除 workspace 列与 `data.workspace` 覆盖入口，镜像/交付路径降层
  （`prompts/<work_type>/`、`deliverables/task-N.md`），scan 提醒全局唯一；mema 侧
  保留 `mema-twin` 存储桶（治理查全 + 偏好内去重/冲突 + 不污染项目空间），agent-id
  定案为子 agent 范式（client 识宿主、agent_id 识写入者）。

## [0.1.0] — 2026-09-02

阶段 0 骨架（15 测试绿）：

- 三维度 canonical 枚举 v1：34 work_type（七域）/ 10 audience / 9 purpose，含精确与别名归一、防冲突测试。
- pending 治理：归一未命中的长尾入 `twin_pending_values`，map / canonicalize / reject 三种裁定，治理别名不被内置播种覆盖。
- prompt 版本存储：DB 为准 + `prompts/<workspace>/<work_type>/vN.md`、`active.md` 原子写镜像；`get` 支持 DB→镜像降级读取。
- 单工具 MCP server `twin(action, data)`：write / get / compile / submit / status / taxonomy / pending / resolve / help 九动作；write 缺三字段打回 invalid_input，非阻塞 hint 引导 compile。
- 偏好记忆经本机 mema HTTP MCP（`127.0.0.1:8000/mcp`）读写：无状态 tools/call 直调 + `X-Mema-Client` / `X-Mema-Agent-Id` 身份头，SSE data 帧解析。

## [0.3.1] — 2026-09-03

- **HTTP 传输支持（多 Agent 共接）**：`MEMA_TWIN_TRANSPORT=http` 启动 streamable-http
  （默认 `127.0.0.1:8765/mcp`，`MEMA_TWIN_HTTP_HOST/PORT` 可调），端点无状态直调
  （免 initialize，mema 同款）；stdio 仍为默认。
- **多 Agent 身份**：`twin.write` 新增可选 `data.client`，随调用覆盖发往 mema 的
  `X-Mema-Client`（agent_id 仍固定 `mema-twin` 子 agent 范式）——多个宿主 Agent 共接
  一个 http twin 时，写入归属在 mema 审计中可辨。

## [0.3.2] — 2026-09-03

- **agent-id 写死不暴露**：`X-Mema-Agent-Id: mema-twin` 成为产品内部常量（子 agent
  范式的写入者标识），移除 `MEMA_TWIN_AGENT_ID` 环境变量；用户可配置面只剩
  `MEMA_TWIN_CLIENT_ID`（多 Agent 差异只走 client / `data.client`）。
