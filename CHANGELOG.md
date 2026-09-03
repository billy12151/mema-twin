# Changelog

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
