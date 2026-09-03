# Changelog

## [0.1.0] — 2026-09-02

阶段 0 骨架（15 测试绿）：

- 三维度 canonical 枚举 v1：34 work_type（七域）/ 10 audience / 9 purpose，含精确与别名归一、防冲突测试。
- pending 治理：归一未命中的长尾入 `twin_pending_values`，map / canonicalize / reject 三种裁定，治理别名不被内置播种覆盖。
- prompt 版本存储：DB 为准 + `prompts/<workspace>/<work_type>/vN.md`、`active.md` 原子写镜像；`get` 支持 DB→镜像降级读取。
- 单工具 MCP server `twin(action, data)`：write / get / compile / submit / status / taxonomy / pending / resolve / help 九动作；write 缺三字段打回 invalid_input，非阻塞 hint 引导 compile。
- 偏好记忆经本机 mema HTTP MCP（`127.0.0.1:8000/mcp`）读写：无状态 tools/call 直调 + `X-Mema-Client` / `X-Mema-Agent-Id` 身份头，SSE data 帧解析。
