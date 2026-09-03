# mema-twin（迷码分身）

> **English summary below.** 本文档以中文为主，顶部提供英文摘要。

个人分身：把用户在工作产物（PPT、文档、汇报、设计……）中反复体现的偏好、规则、
结构习惯沉淀为按工作性质分维度的记忆，再编译成**版本化、可溯源的 persona prompt**，
在开工前注入给 Agent——让 Agent 的产出越来越像用户本人。

## English Summary

mema-twin is a personal work twin built on top of mema (memory-arbiter). It extracts
reusable work preferences (style, structure, wording, pre-flight checklists) into
dimension-tagged memories, compiles them into versioned persona prompts per work type
(34 work types / 10 audiences / 9 purposes in v1), and serves the active prompt to
agents before they start a piece of work. Preferences live in mema; compiled prompts
live in twin's own SQLite with a file mirror for fallback and human review.

## 工作原理

```
工作产出/修改 ──twin.write(三字段强制归一)──▶ mema 偏好记忆（带维度标签）
                                                    │
                              compile：取素材包 ────┘    ← 定时扫描/手动触发，Agent 提示
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

- **Agent 抽象、产品归一**：三字段（工作性质/受众/用途）写入时强制语义归一，档位为
  精确/别名 → embed 近邻（阈值 0.75，复用 mema-core 的 GGUF embedder，禁用或失败
  自动跳过）→ pending 由用户裁定 map / canonicalize / reject，绝不自动新建。
- **偏好本体存 mema**：经其 HTTP MCP 读写，复用 mema 的冲突检查、审计与治理；
  twin 自有 `twin.sqlite3` 存枚举注册表、待裁长尾、prompt 版本、证据指针索引
  （twin_evidence 只存 mema 记忆 id 与维度标签，不存正文；compile 按 id 精确
  read 取全文，召回无丢失）。
- **DB 为准、文件镜像降级**：`prompts/<workspace>/<work_type>/vN.md` + `active.md`；
  交付任务评审通过后交付稿落 `deliverables/<workspace>/task-N.md`。
- **编译执行者＝宿主 Agent**（twin 不自配模型）：compile 返回素材包，会话模型编译后
  submit 回库，版本记录来源 memory ids 与编译模型。建议在强模型会话中执行编译。
- **交付任务流（机制改造自 plan-mode）**：可审计（任务行不可变追加 + append-only
  评审记录）、可中断（pending 搁置）、可继续（task_resume 恢复 todos 续作）；task_start /
  task_resume 即 persona prompt 注入点。
- **定时扫描挂 Agent 端**：twin 自身不起调度；status 里的 scan_notice 提醒 Agent
  征询用户后在宿主平台建周任务调 `twin(action="scan")`，跑过 7 天内提醒自消失
  （mema 首装提醒同款模式）。

## 工具（单工具动作式）

`twin(action, data)`，动作：

| 动作 | 说明 |
|------|------|
| `write` | 沉淀一条工作偏好。必填 content/work_type/audience/purpose |
| `get` | 取某工作性质的 active persona prompt（开工前调用） |
| `compile` | 取编译素材包（当前版本 + 未编译证据 + 编译规则） |
| `submit` | 提交编译产物，落版本并写镜像，回写证据编译标记 |
| `status` | 版本概况、未编译统计、pending 数量、scan 安装提醒 |
| `taxonomy` | 列枚举（kind ∈ work_type/audience/purpose） |
| `pending` / `resolve` | 待裁长尾的查看与治理 |
| `task_start` | 开工建档并注入 persona prompt（audience/purpose 可选） |
| `task_submit` | 提交交付稿待评审（打回后同任务可再提交，轮次递增） |
| `task_review` | 评审裁定（approved 落交付文件 / changes_requested 走修订），append-only 审计 |
| `task_pending` | 评审搁置（中断未决） |
| `task_resume` / `task_revise` / `task_close` | 续作历史任务（含进行中）/ 修订返工（子任务回 planning 记 lineage）/ 显式关闭开放任务 |
| `task_recent` / `task_get` | 任务列表 / 单任务全量（含评审历史） |
| `todo` | 会话 todo 读写（plan-mode 同款语义） |
| `scan` | 定时扫描：未编译偏好/pending 积压/开放任务汇总与建议 |

## 维度枚举 v1

work_type 34 项（七域：通用职场/方案商务/产品研发/管理制度/培训知识/创意内容/专业服务）、
audience 10 项、purpose 9 项。清单见 `mema_twin/taxonomy.py` 或 `twin(action="taxonomy")`。
后续版本可扩展；长尾进 pending 由用户治理。

## 安装与配置

```bash
cd ~/BillyProject/mema-twin
uv venv && uv pip install -e ".[test]"
.venv/bin/pytest -q          # 跑测试
```

MCP 配置见 `examples/zcode.mcp.json`。环境变量：

| 变量 | 默认 | 说明 |
|------|------|------|
| `MEMA_TWIN_DB_PATH` | `<项目>/twin.sqlite3` | twin 自有库路径 |
| `MEMA_TWIN_PROMPTS_DIR` | `<项目>/prompts` | prompt 文件镜像目录 |
| `MEMA_TWIN_DELIVERABLES_DIR` | `<项目>/deliverables` | 交付物文件目录 |
| `MEMA_TWIN_MEMA_URL` | `http://127.0.0.1:8000/mcp` | mema HTTP MCP 端点 |
| `MEMA_TWIN_CLIENT_ID` / `MEMA_TWIN_AGENT_ID` | `zcode` / `mema-twin` | mema 身份头（缺失会被拒） |
| `MEMA_TWIN_WORKSPACE` | `mema-twin` | 偏好存储桶。画像默认**全局**（能力跟人不跟项目），此值仅用于在 mema 里与项目记忆隔离；一般不改，需单独画像的场景才用 `data.workspace` 显式分桶 |
| `MEMA_TWIN_EMBED_MODEL` | mema 配置的模型 | embed 档 GGUF 路径；解析顺序：本变量 → mema 配置 `embedding.model_path` → 禁用（fail-open 走 pending） |

embed 档依赖：`uv pip install -e ".[core]"`（mema-core ≥0.15.4 提供 ManagedEmbedder；
本地 embedder 还需 llama-cpp-python）。未安装时 embed 档自动跳过，不影响其余功能。

Agent 引导：安装 `skill/SKILL.md` 到各 client 的 skills 目录。

## 开发

```bash
.venv/bin/python -m mema_twin.server   # 手动跑 server (stdio)
```

## 路线图

- **阶段 0 原型自用**：表 + 归一 + 手动编译 + 注入，吃自己狗粮验证"越来越像"
- **阶段 1 免费发布**：registry / PyPI 验证留存
- **阶段 2 收费拍板**：open-core 或托管，凭留存数据决定（许可证亦待此时定）

## 设计文档

`~/ZCodeProject/docs/mema-avatar-design-2026-09-02.md`（已确认/现状/建议/待确认四分区）
