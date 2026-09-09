"""mema-twin MCP server：单工具 twin(action, data)，动作式紧凑接口（仿 mema/plan-mode 风格）。

v0.3.8 拆分（单文件 ≤1000 行，设计文档 E）：本模块只留工具面——分发与边界、
宿主身份头读取、mema notices 透传、动作注册表、help、传输层。动作实现分散到
pref_actions（偏好与治理）/ compile_actions（编译与版本）/ task_actions（交付
任务流）；身份校验在 identity（contextvar 注入，动作模块调用点形态不变）。
依赖单向：server → actions → (identity, db, flow, store, normalize, taxonomy,
sink, scan, templates)，无环。
"""
from __future__ import annotations

import sqlite3

import anyio.to_thread
from mcp.server.fastmcp import FastMCP

from . import identity, pref_actions, scan, sink, task_actions, templates
from . import compile_actions

mcp = FastMCP("mema-twin", stateless_http=True)  # http 模式免 initialize 直调（mema 同款）


@mcp.tool()
async def twin(action: str, data: dict | None = None) -> dict:
    """个人分身 twin：按工作性质沉淀用户工作偏好，编译版本化 persona prompt，
    经交付任务流注入执行，并提供定时任务建议。

    动作：write / get / compile / submit / rollback / status / taxonomy / pending /
    resolve / void / task_start / task_submit / task_resume / task_revise /
    task_close / task_recent / task_get / todo / help。
    先 twin(action="help") 查看各动作参数与引导。compile 返回素材包，由当前会话模型
    编译（建议在强模型会话中执行），submit 提交回库落版本并写文件镜像。
    """
    # sync 实现体放线程池（评审轮2 P2-4）：FastMCP 对 sync 工具直接在事件循环上
    # 调用，而 compile 全量投影后逐条 read（最坏 30s/条）会独占循环、拖挂其他宿主
    # 的并发调用；anyio.to_thread 传播 contextvars（身份头注入不受影响）
    return await anyio.to_thread.run_sync(lambda: _twin_impl(action, data))


def _twin_impl(action: str, data: dict | None = None) -> dict:
    data = data or {}
    handler = _ACTIONS.get(action)
    if handler is None:
        return {"ok": False, "error": "invalid_input",
                "reason": f"unknown action {action!r}", "actions": sorted(_ACTIONS)}
    try:
        # 入口统一注入请求头身份并校验宿主身份：脏/重复头、脏或不一致 data.client
        # 无论动作立即打回（fail-fast，也避免 handler 先落票据再炸留幽灵行）
        identity.set_client_header(_request_client())
        identity.effective_client(data)
        # mema notice 透传（v0.3.7）：入口清零、出口统一附带——任何触达 mema 的
        # 动作（内部 read/find 也会 claim notice，先到先得）都可能在响应带出
        sink.reset_notices()
        result = handler(data)
        notices = sink.collect_notices()
        # 非 ok 的 dict 响应同样附带（评审轮1 P3-4）：notice 被 claim 即 delivered
        # 不重发，handler 后段出错丢掉等于永久丢失
        if notices and isinstance(result, dict):
            result["mema_notices"] = notices
            result["mema_notices_guidance"] = _notices_guidance(notices)
        return result
    except sink.SinkError as e:
        return {"ok": False, "error": "mema_unreachable", "reason": str(e)}
    except ValueError as e:
        return {"ok": False, "error": "invalid_input", "reason": str(e)}
    except (TypeError, AttributeError, KeyError, OverflowError,
            sqlite3.Error, OSError) as e:
        # 兜底边界（review#2）：畸形参数/存储异常绝不击穿 MCP 工具面
        return {"ok": False, "error": "internal_error",
                "reason": f"{type(e).__name__}: {e}"}


def _notices_guidance(notices: list[dict]) -> str:
    """notice 分诊分层（v0.3.7 拍板）：similar_active_memory 在 twin 写流量上近乎
    必然触发（默认 subject 同类型全同），只做静默分诊；semantic notice 才升级问
    用户三选项。mema notice 是 advisory，不是正式冲突，不能直接 judge。"""
    parts = []
    for n in notices:
        t = str(n.get("type") or "")
        if t == "similar_active_memory":
            parts.append("similar_active_memory（疑似重复偏好）：静默分诊——偏好是增量"
                         "语义、重复由编译期合并吸收；仅当内容真属重复时改走 mema update "
                         "原条目，不必打扰用户")
        elif n.get("action_required") == "read_semantic_notice" or "semantic" in t:
            parts.append(f"语义冲突 notice（notice_id={n.get('notice_id')}）：先按其 "
                         "read_call（形如 memory_repair 的 notice 任务）读完整通知与两侧"
                         "原文分诊，宿主缺该工具时直接 memory(action=\"read\") 按 notice "
                         "相关 memory_id 读两侧原文——误报 dismiss；真冲突才问用户三选项："
                         "两条都留（编译条件化）/ 新的替旧的（twin(action=\"void\") 旧条，"
                         "mema 本体的 retire 是另行一步按其治理流程走）/ 撤销新写的"
                         "（void 本条）")
        else:
            parts.append(f"mema notice（type={t}）：按其自带指引处理")
    return "；".join(parts)


def _request_client() -> str | None:
    """http 模式下读宿主连接自带的 X-Mema-Client 头（mema 同款取头方式）；
    stdio/直调无活跃请求 → None（回落 env 默认）。带头即校验：脏值/重复头
    打回而非静默忽略。读到的值经 contextvar 供动作模块 effective_client 使用。"""
    get_context = getattr(mcp, "get_context", None)
    if not callable(get_context):
        return None
    try:
        request = get_context().request_context.request
        headers = getattr(request, "headers", None)
    except (AttributeError, LookupError, TypeError, ValueError):
        # stdio/无活跃请求（core request_identity 同款兜底）
        return None
    if headers is None:
        return None
    # 与 mema core 一致：重复头打回而非静默取第一个
    getlist = getattr(headers, "getlist", None)
    if callable(getlist):
        values = list(getlist("X-Mema-Client"))
        if len(values) > 1:
            raise ValueError("X-Mema-Client 头必须恰好一个（收到多个）")
    value = headers.get("x-mema-client")
    if value is None:
        # 非 starlette Headers 对象可能大小写敏感，casefold 兜底一次（core 同款）
        for key, candidate in headers.items():
            if str(key).casefold() == "x-mema-client":
                value = candidate
                break
    if value is None:
        return None
    try:
        return identity.validate_client_value(str(value))
    except ValueError as e:
        raise ValueError(f"X-Mema-Client 头非法：{e}") from None


def _action_help(data: dict) -> dict:
    topic = str(data.get("topic") or "").strip()
    if topic == scan.SCHEDULED_TASKS_TOPIC:
        return {
            "ok": True, "topic": scan.SCHEDULED_TASKS_TOPIC,
            "description": "twin 定时任务 spec（单一夜间 persona 编译任务）：Agent 据此在宿主平台创建等价任务。",
            "agent_instruction": scan.AGENT_INSTRUCTION,
            "setup": scan.SCHEDULED_TASKS_SPEC,
            "note": "提醒自消失：夜间编译（scheduled submit）7 天内跑过即不再提示。",
        }
    return {
        "ok": True,
        "actions": {
            "write": "沉淀一条工作偏好。必填 content/work_type/audience/purpose"
                     "（先 taxonomy 查清单选码；清单无合适项的值会被整笔打回并附清单——"
                     "必须问用户：归一到已有值（resolve map）还是创建新值（resolve "
                     "canonicalize，即刻入清单），裁定后重试）；"
                     "可选 subject/tags/source_ref/client（多 Agent 共接时 client 填宿主标识，如 kimi/jinleai）。"
                     "对该受众的通用偏好（不限工作类型）传 scope=audience（此时 work_type 省略）。",
            "status": "查看各 work_type 的 prompt 版本概况（含体积/超预算标记）、受众画像"
                      "（audience_profiles）、需重抽象的受众（audience_stale）、条款作废待重编"
                      "（persona_stale）、pending 数量（归一门待裁票据）、未编译统计、夜间被拒计数"
                      "（nightly_rejected）、open 冲突/进行中任务计数与定时任务安装提醒。",
            "compile": "取编译素材包（旧版本 prompt 编译参考 + **全部在世证据**（全量投影，"
                       "每版从头重编）+ 已作废条款清单 + 同受众画像参考 + 编译规则"
                       "（含义稳定表达自由/变更分级/硬预算）），"
                       "由当前会话模型编译；独立会话执行、收尾即弃（session_note）。参数 work_type；"
                       "传 aud-{受众} 取受众画像素材包（该受众全部证据）。",
            "submit": "提交编译产物落版本并写文件镜像。必填 work_type/prompt_md；"
                      "建议带 source_memory_ids 与 model。返回 supersedes（被取代的旧 active 版本）；"
                      "夜间定时任务落版必传 origin=scheduled（其余场景不要传），落版前过验证门"
                      "（素材回声/分区标题未过拒绝且 active 不变、证据不动；无新证据可吸收时空转阻尼"
                      "拒绝落版——两者都会在 status 的 nightly_rejected 累计显示）；交互式 submit "
                      "只警告不拦；证据未全覆盖只出警告；work_type 传 aud-{受众} 即受众画像落版"
                      "（derived，不消耗证据）。",
            "rollback": "回滚 persona 版本（零阻力：无确认、无警告）。work_type 必填；"
                        "version 省略回上一版本、传 n 回指定版本；不删历史（retired 可再激活），"
                        "版本号不回收（下次 submit 继续 MAX+1）。",
            "get": "取某 work_type 的 persona prompt（DB 优先，文件镜像降级）。参数 work_type；"
                   "可选 version 取指定历史版本全文；"
                   "work_type 传 aud-{受众} 可读受众画像。",
            "taxonomy": "列枚举清单（动态：含治理追加别名与自建 canonical）。参数 kind ∈ work_type|audience|purpose。",
            "pending": "列归一门待裁票据（打回的长尾值，含打回次数）。参数 status（默认 pending）。",
            "resolve": "治理待裁值。pending_id + decision ∈ map(带 code)|canonicalize(带 new_type{code,zh,en,domain})|reject"
                       "（reject=不入体系并放弃本次写入，不得拿原值重试）。",
            "void": "作废一条偏好证据（冲突裁定「新替旧/撤销新写的」的执行机制）。参数 memory_id；"
                    "行级作废、全链路排除、不可逆；曾入编译的证据会触发 persona_stale 夜间重编，"
                    "受众证据计数变化触发画像重抽象。mema 本体的 retire 按其治理流程另行处理。",
            "task_start": "开工建档（流程注入点）。必填 brief/work_type（audience/purpose 可选，"
                          "但给了值必须在清单内——未命中整笔打回，须问用户裁定后重试）；"
                          "返回该工作性质的 persona prompt 与前置清单，进行中任务自动让位。"
                          "可选 have_persona_version：同一会话此前注入过同 work_type 且版本号仍在场时申报，"
                          "版本未变则不再重复注入全文，变了则重注入并附变更说明。"
                          "响应另带 persona_supplement：该 work_type 未编译偏好增补"
                          "（与 prompt 冲突以增补为准；上限 10 条）。"
                          "client 字段同 write（http 共接时头已带则无需传）。",
            "task_submit": "提交交付稿并收口（submit 即终点，v0.3.8 无评审环）：落盘"
                           "deliverables/task-N.md 审计文件。必填 task_id/deliverable_md；"
                           "可带 todos/session/note。交付后用户反馈走 twin.write 沉淀。",
            "task_resume": "续作进行中任务（仅 planning，可中断可继续）。task_id；恢复 todos、"
                           "新建 planning 任务并再注入 persona"
                           "（含未编译增补 persona_supplement，同 task_start）。"
                           "have_persona_version 申报口径同 task_start；"
                           "已交付（submitted）的返工走 task_revise。",
            "task_revise": "已交付任务修订返工（仅 submitted）。task_id + brief/deliverable_md/"
                           "revision_reason 至少其一；子任务回 planning 重走执行并记 lineage"
                           "（不恢复 todos、不重注入 persona，需要时重新 task_start）。",
            "task_close": "显式关闭进行中任务（仅 planning），历史保留可审计。",
            "task_recent": "最近任务列表。参数 limit（默认 10）。",
            "task_get": "取单个任务全量。task_id。",
            "todo": "会话 todo 读写（plan-mode 同款语义：整体替换，至多一条 in_progress）。传 todos 替换，不传读取。",
            "help": "本帮助。",
        },
        "write_guidance": templates.WRITE_GUIDANCE,
        "note": templates.STRONG_MODEL_NOTE,
    }


_ACTIONS = {
    "write": pref_actions._action_write,
    "status": pref_actions._action_status,
    "compile": compile_actions._action_compile,
    "submit": compile_actions._action_submit,
    "rollback": compile_actions._action_rollback,
    "get": pref_actions._action_get,
    "taxonomy": pref_actions._action_taxonomy,
    "pending": pref_actions._action_pending,
    "resolve": pref_actions._action_resolve,
    "void": pref_actions._action_void,
    "task_start": task_actions._action_task_start,
    "task_submit": task_actions._action_task_submit,
    "task_resume": task_actions._action_task_resume,
    "task_revise": task_actions._action_task_revise,
    "task_close": task_actions._action_task_close,
    "task_recent": task_actions._action_task_recent,
    "task_get": task_actions._action_task_get,
    "todo": task_actions._action_todo,
    "help": _action_help,
}


def main() -> None:
    """传输：stdio（默认，单机零运维）或 http（多 Agent 共接，mema 同款形态）。

    MEMA_TWIN_TRANSPORT=stdio|http；http 时 MEMA_TWIN_HTTP_HOST/PORT 可调
    （默认 127.0.0.1:8765，仅允许 loopback 绑定——X-Mema-Client 头不是鉴权，
    非 loopback 暴露=任何人可伪造宿主身份，mema core 同款拒绝）。端点 /mcp
    无状态直调。多宿主各在自己 MCP 配置里带 X-Mema-Client 头（stdio 无头时
    data.client > env）。
    """
    import os
    # 启动即校验 env 兜底身份，脏值清晰报错退出，不留给运行期中途炸
    try:
        sink._env_client()
    except ValueError as e:
        raise SystemExit(f"启动失败：{e}")
    transport = (os.environ.get("MEMA_TWIN_TRANSPORT") or "stdio").strip().lower()
    if transport in ("stdio", ""):
        mcp.run()
    elif transport in ("http", "streamable-http"):
        host = os.environ.get("MEMA_TWIN_HTTP_HOST", "127.0.0.1").strip()
        if not identity.is_loopback_host(host):
            raise SystemExit(
                f"MEMA_TWIN_HTTP_HOST={host!r} 非 loopback 地址被拒绝："
                "X-Mema-Client 头不是鉴权，对外暴露等于开放身份伪造（mema core 同款策略）。"
                "twin 设计为本机单用户服务。")
        mcp.settings.host = host
        mcp.settings.port = int(os.environ.get("MEMA_TWIN_HTTP_PORT", "8765"))
        mcp.run(transport="streamable-http")
    else:
        raise SystemExit(f"unknown MEMA_TWIN_TRANSPORT {transport!r}（期望 stdio|http）")
