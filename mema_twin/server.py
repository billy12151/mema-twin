"""mema-twin MCP server：单工具 twin(action, data)，动作式紧凑接口（仿 mema/plan-mode 风格）。

动作分三组：偏好与编译（write/get/compile/submit/status/taxonomy/pending/resolve）、
交付任务流（task_start/task_submit/task_review/task_pending/task_resume/task_revise/
task_recent/task_get/todo，机制改造自 plan-mode：可审计、可中断、可继续）、
定时扫描（scan，提醒挂 Agent 端）。
"""
from __future__ import annotations

import datetime as _dt
import ipaddress
import os
import re
import sqlite3

from mcp.server.fastmcp import FastMCP

from . import db, flow, normalize, scan, sink, store, taxonomy, templates

mcp = FastMCP("mema-twin", stateless_http=True)  # http 模式免 initialize 直调（mema 同款）

_KIND_PREFIX = {"work_type": "wt", "audience": "au", "purpose": "pu"}
_RAW_MAX_CHARS = 200      # 三维度值：短枚举说法
_CONTENT_MAX_CHARS = 8000  # 偏好正文
_BUCKET = "mema-twin"  # mema 侧偏好存储桶：固定值（画像人级全局），0.3.3 起写死、无 env 覆盖
_CLIENT_RE = re.compile(r"^[A-Za-z0-9._:@-]{1,64}$")  # 与 mema X-Mema-Client 同款字符集


@mcp.tool()
def twin(action: str, data: dict | None = None) -> dict:
    """个人分身 twin：按工作性质沉淀用户工作偏好，编译版本化 persona prompt，
    经交付任务流注入执行，并提供定时扫描建议。

    动作：write / get / compile / submit / rollback / status / taxonomy / pending /
    resolve / task_start / task_submit / task_review / task_pending / task_resume /
    task_revise / task_close / task_recent / task_get / todo / scan / help。
    先 twin(action="help") 查看各动作参数与引导。compile 返回素材包，由当前会话模型
    编译（建议在强模型会话中执行），submit 提交回库落版本并写文件镜像。
    """
    data = data or {}
    handler = _ACTIONS.get(action)
    if handler is None:
        return {"ok": False, "error": "invalid_input",
                "reason": f"unknown action {action!r}", "actions": sorted(_ACTIONS)}
    try:
        # 入口统一校验宿主身份：脏/重复头、脏或不一致 data.client 无论动作
        # 立即打回（fail-fast，也避免 handler 先写 pending 再炸留幽灵行）
        _effective_client(data)
        return handler(data)
    except sink.SinkError as e:
        return {"ok": False, "error": "mema_unreachable", "reason": str(e)}
    except ValueError as e:
        return {"ok": False, "error": "invalid_input", "reason": str(e)}
    except (TypeError, AttributeError, KeyError, OverflowError,
            sqlite3.Error, OSError) as e:
        # 兜底边界（review#2）：畸形参数/存储异常绝不击穿 MCP 工具面
        return {"ok": False, "error": "internal_error",
                "reason": f"{type(e).__name__}: {e}"}


def _today() -> str:
    return _dt.date.today().isoformat()


def _memory_id_of(data) -> int | None:
    """mema remember 响应里的记忆 id（data.id；兼容 data.record.id 形态）。"""
    if not isinstance(data, dict):
        return None
    for cand in (data.get("id"), (data.get("record") or {}).get("id") if isinstance(data.get("record"), dict) else None):
        try:
            return int(cand)
        except (TypeError, ValueError):
            continue
    return None


def _bucket() -> str:
    """mema 侧偏好存储桶（0.3.3 起写死，保留函数以免调用点散改）。"""
    return _BUCKET


def _request_client() -> str | None:
    """http 模式下读宿主连接自带的 X-Mema-Client 头（mema 同款取头方式）；
    stdio/直调无活跃请求 → None（回落 env 默认）。带头即校验：脏值/重复头
    打回而非静默忽略。"""
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
    value = str(value)
    if not _CLIENT_RE.fullmatch(value):
        # 未 strip 的值不做修正直接拒（mema 同款：normalized != value 即拒）
        raise ValueError(
            f"X-Mema-Client 头非法（仅 [A-Za-z0-9._:@-]、≤64 字符、无首尾空白）: {value[:32]!r}")
    return value


def _effective_client(data: dict) -> str | None:
    """本次调用的宿主身份。http 头存在时头是权威（连接身份），显式 data.client
    只能与头一致或省略（mema _identity_mismatch 同款语义，堵跨宿主冒充）；
    stdio 无头时 data.client > env。非字符串/带首尾空白一律打回。"""
    header = _request_client()
    raw = data.get("client")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return header
    if not isinstance(raw, str):
        raise ValueError(f"client 必须是字符串，收到 {type(raw).__name__}")
    if raw != raw.strip():
        raise ValueError(f"client 非法（含首尾空白）: {raw[:32]!r}")
    if not _CLIENT_RE.fullmatch(raw):
        raise ValueError(
            f"client 非法（仅 [A-Za-z0-9._:@-]、≤64 字符）: {raw[:32]!r}")
    if header is not None and raw != header:
        raise ValueError(
            f"client 与连接身份不一致：X-Mema-Client 头为 {header!r}，data.client 为 {raw!r}"
            "（http 模式下以连接头为准，请移除 data.client 或保持一致）")
    return raw


def _action_write(data: dict) -> dict:
    content = str(data.get("content") or "").strip()
    if not content:
        return {"ok": False, "error": "invalid_input", "field": "content", "reason": "required"}
    if len(content) > _CONTENT_MAX_CHARS:
        return {"ok": False, "error": "invalid_input", "field": "content",
                "reason": f"超出 {_CONTENT_MAX_CHARS} 字符上限"}
    # 受众级沉淀（v0.3.6 AR-5）：scope=audience 表示"对该受众的通用偏好"，
    # 不属于任何工作类型——work_type 可省略，落到 aud-{audience} 证据行
    scope = data.get("scope")
    if scope is not None and scope != "audience":
        return {"ok": False, "error": "invalid_input", "field": "scope",
                "reason": "scope 仅接受 audience（受众级偏好）；普通类型偏好不要传"}
    audience_scoped = scope == "audience"
    required = ("audience", "purpose") if audience_scoped else ("work_type", "audience", "purpose")
    for f in required:
        if not str(data.get(f) or "").strip():
            return {"ok": False, "error": "invalid_input", "field": f, "reason": "required"}
        if len(str(data[f])) > _RAW_MAX_CHARS:
            return {"ok": False, "error": "invalid_input", "field": f,
                    "reason": f"超出 {_RAW_MAX_CHARS} 字符上限（维度值应是短枚举说法）"}
    conn = db.connect()
    dims: dict = {}
    pendings: list[dict] = []
    tags = ["twin-preference"]
    try:
        if audience_scoped:
            r = normalize.normalize_value("audience", str(data["audience"]), conn,
                                          defer_pending=True)
            if not r.get("ok"):
                # AR-5：受众级沉淀要求 audience 归一成功——落 pending 会造出
                # 无处安放的证据行（work_type/audience 双 NULL），显式打回
                return {"ok": False, "error": "invalid_input", "field": "audience",
                        "reason": "受众级沉淀需 audience 归一成功"
                                  "（先 twin(action=\"taxonomy\", kind=\"audience\") 查清单或治理 pending）"}
            aud_code = r["code"]
            dims["audience"] = r
            dims["work_type"] = {"ok": True, "kind": "work_type", "raw": "(受众级偏好)",
                                 "code": store.audience_profile_code(aud_code),
                                 "label_zh": r.get("label_zh"), "matched_by": "audience_scope"}
            tags.append(f"twin:aud:{aud_code}")
            for kind in ("purpose",):
                r2 = normalize.normalize_value(kind, str(data[kind]), conn, defer_pending=True)
                if r2.get("error"):
                    return {"ok": False, "error": "invalid_input", "field": kind,
                            "reason": r2.get("reason")}
                dims[kind] = r2
                if r2.get("ok"):
                    tags.append(f"twin:{_KIND_PREFIX[kind]}:{r2['code']}")
                else:
                    pendings.append(r2)
                    tags.append(f"twin:{_KIND_PREFIX[kind]}:raw:{r2['raw']}")
        else:
            for kind in taxonomy.KINDS:
                # defer：mema 写成功才 upsert pending，失败重试不留幽灵计数（对抗 review#14）
                r = normalize.normalize_value(kind, str(data[kind]), conn, defer_pending=True)
                if r.get("error"):
                    return {"ok": False, "error": "invalid_input", "field": kind, "reason": r.get("reason")}
                dims[kind] = r
                if r.get("ok"):
                    tags.append(f"twin:{_KIND_PREFIX[kind]}:{r['code']}")
                else:
                    pendings.append(r)
                    tags.append(f"twin:{_KIND_PREFIX[kind]}:raw:{r['raw']}")
    finally:
        conn.close()  # 后续是 30s 级 HTTP 调用，连接不能跨调用挂着（review#7）
    # 用户 tags 剥离 twin: 前缀（对抗 review#13）：维度命名空间只归归一层管
    raw_tags = data.get("tags") or []
    if not isinstance(raw_tags, list):
        return {"ok": False, "error": "invalid_input", "field": "tags",
                "reason": "tags 必须是字符串列表"}
    user_tags = [str(t) for t in raw_tags
                 if not str(t).startswith("twin:") and str(t) != "twin-preference"]
    resp = sink.remember(
        content=content,
        subject=str(data.get("subject") or (
            f"受众级偏好：{dims['audience'].get('label_zh') or dims['audience'].get('raw')}"
            if audience_scoped else f"工作偏好：{dims['work_type'].get('raw')}")),
        tags=tags + user_tags,
        workspace=_bucket(),
        source_ref=str(data.get("source_ref") or ""),
        event_time=_today(),
        client=_effective_client(data),  # 多 Agent：显式 data.client > 头 > env
    )
    ok = bool(resp.get("ok"))
    out: dict = {"ok": ok, "memory": resp.get("data") if ok else resp,
                 "dimensions": dims, "pending": pendings}
    if ok:
        mid = _memory_id_of(resp.get("data"))
        conn = db.connect()
        try:
            if mid is None:
                # 对抗 review#9①：id 缺失则证据永不登记，必须显式告警而非静默
                out["warnings"] = ["mema 响应缺记忆 id，本条未入证据索引（compile 不可见），建议重写"]
            else:
                for p in pendings:
                    p["pending_id"] = db.upsert_pending(conn, p["kind"], p["raw"], mid)
                db.record_evidence(conn, mid, dims,
                                   subject=str(data.get("subject") or ""))
                out["evidence_id"] = mid
            if audience_scoped:
                zh = dims["audience"].get("label_zh") or dims["audience"]["raw"]
                out["hint"] = (f"已沉淀为对「{zh}」的受众级通用偏好（不绑定工作类型）；"
                               "该受众有新证据时，夜间任务会重抽象其画像，"
                               "对该受众的任何任务开工时自动带上")
            elif dims["work_type"].get("ok"):
                active = store.get_active(conn, dims["work_type"]["code"])
                if active and active.get("version") is not None:
                    out["hint"] = (f"{dims['work_type']['label_zh']} 已有 persona prompt v{active['version']}；"
                                   "本次偏好已入池未编译——无需立即整理，"
                                   "由用户决定何时 compile，或等定时扫描统一处理")
                else:
                    out["hint"] = (f"{dims['work_type']['label_zh']} 尚无 persona prompt，"
                                   "可 twin(action=\"compile\") 生成 v1"
                                   f"（{templates.STRONG_MODEL_NOTE}）")
        finally:
            conn.close()
    return out


def _action_status(data: dict) -> dict:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT work_type, version, model, status, evidence_count, created_at"
            " FROM twin_prompt_versions ORDER BY work_type, version",
        ).fetchall()
        versions: dict = {}
        audience_profiles: dict = {}
        for r in rows:
            aud = store.split_audience_profile(r["work_type"])
            bucket = audience_profiles if aud is not None else versions
            v = bucket.setdefault(r["work_type"],
                                  {"work_type": r["work_type"], "active": None, "versions": []})
            v["versions"].append(dict(r))
            if r["status"] == "active":
                v["active"] = r["version"]
        pending = db.list_pending(conn)
        # 受众画像触发器（AR-2）：证据数 ≠ 画像 evidence_count（或尚无画像）→ 需重抽象
        aud_counts = conn.execute(
            "SELECT audience, COUNT(*) AS n FROM twin_evidence"
            " WHERE audience IS NOT NULL AND work_type IS NOT NULL"
            " GROUP BY audience",
        ).fetchall()
        audience_stale: dict = {}
        prof_ev: dict = {}
        for prof in audience_profiles.values():
            if prof["active"] is not None:
                for vr in prof["versions"]:
                    if vr["version"] == prof["active"]:
                        prof_ev[prof["work_type"]] = vr.get("evidence_count")
        for r in aud_counts:
            aud = r["audience"]
            pcode = store.audience_profile_code(aud)
            m = prof_ev.get(pcode)
            if m != r["n"]:
                audience_stale[aud] = {"evidence": r["n"], "profile_evidence": m}
        out: dict = {"ok": True, "prompts": list(versions.values()),
                     "audience_profiles": list(audience_profiles.values()),
                     "audience_stale": audience_stale,
                     "pending_count": len(pending),
                     "uncompiled": db.evidence_stats(conn)}
        # 轮2 P2-3：存量自定义 work_type 若撞上保留前缀 aud-（旧版校验允许过），
        # 升级后会被画像通道劫持——显式告警给迁移提示，不静默
        hijacked = [r["code"] for r in db.type_rows(conn, "work_type")
                    if str(r["code"]).startswith(store.AUD_PREFIX)]
        if hijacked:
            out["warnings"] = [
                f"自定义 work_type 撞上保留前缀：{hijacked}——已被受众画像通道劫持，"
                "请用 twin(action=\"resolve\", decision=\"canonicalize\") 改名迁移"]
    finally:
        conn.close()
    notice = scan.scan_notice()
    if notice:
        out["scan_notice"] = notice
    return out


def _fetch_evidence_find(conn, code: str, client: str | None = None) -> list[dict]:
    """兜底召回：twin_evidence 索引为空时（索引落地前的存量数据）退回
    mema find 语义召回（include_content=true，0.15.4 起默认索引页无正文）。"""
    t = taxonomy.by_code("work_type", code)
    q = f"{t.zh if t else code} 用户偏好 规则 结构"
    resp = sink.find(q, _bucket(), client=client)
    if not resp.get("ok"):
        return []
    payload = resp.get("data") or {}
    results = payload.get("results") or payload.get("matches") or []
    tag = f"twin:wt:{code}"
    compiled = set()
    for v in store.list_versions(conn, code):
        compiled.update(v["source_memory_ids"])
    return [r for r in results
            if tag in (r.get("tags") or []) and str(r.get("id")) not in compiled]


def _read_evidence_rows(rows: list[dict], client: str | None = None,
                        fail_fast: bool = False) -> tuple[list[dict], list[dict]]:
    """按 id 逐条 read 全文（compile 与增补注入共用的读取通路）。fail_fast=True
    （仅增补）时连接级 SinkError 即跳过剩余条目——增补在 task_start 热路径上，
    不能被挂起的 mema 逐条拖慢；compile 保持逐条独立重试（0.3.4 行为，SinkError
    也含单条 JSON-RPC 错误，不能当全局宕机）。单条 not-ok 不触发 fail-fast。"""
    evidence: list[dict] = []
    skipped: list[dict] = []
    mema_down = False
    for r in rows:
        mid = r["memory_id"]
        if mema_down:
            skipped.append({"memory_id": mid, "reason": "mema 不可达，跳过剩余"})
            continue
        try:
            resp = sink.read_memory(mid, _bucket(), client=client)
        except sink.SinkError as e:
            skipped.append({"memory_id": mid, "reason": f"mema read 失败: {e}"})
            if fail_fast:
                mema_down = True
            continue
        if not resp.get("ok"):
            skipped.append({"memory_id": mid,
                            "reason": (resp.get("error") or "read 未命中")})
            continue
        mem = (resp.get("data") or {}).get("memory") or {}
        if not (mem.get("content") or "").strip():
            # 对抗 review#9②：形状漂移不能产出空证据行（compile 素材包/增补同规）
            skipped.append({"memory_id": mid, "reason": "read 响应缺 memory.content"})
            continue
        evidence.append({
            "id": mid,
            "subject": mem.get("subject") or r.get("subject") or "",
            "content": mem.get("content") or "",
            "audience": r.get("audience"),
            "purpose": r.get("purpose"),
        })
    return evidence, skipped


def _fetch_evidence(conn, code: str, client: str | None = None) -> tuple[list[dict], list[dict]]:
    """compile 证据：优先 twin_evidence 索引 + 按 id 精确 read 取全文（召回
    精确无丢失，M1.3）；索引为空退回 find 兜底。返回 (evidence, skipped)。"""
    rows = db.uncompiled_evidence(conn, code)
    if not rows:
        return _fetch_evidence_find(conn, code, client), []
    return _read_evidence_rows(rows, client)


def _is_loopback_host(host: str) -> bool:
    """http 绑定白名单：localhost / 127.x / ::1（core request_identity 同款）。"""
    normalized = str(host or "").strip().strip("[]").casefold()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _compile_audience_profiles(conn, code: str, client: str | None = None) -> list[dict]:
    """compile 素材包的受众画像参考（AR-4）：按该类型证据出现频次取 ≤3 个
    **已有画像**的受众（轮1 P2-4：先过滤有画像再 LIMIT，防无画像受众挤占名额）；
    无画像的受众不塞原始证据（原始通道只有注入雏形一条）。"""
    rows = conn.execute(
        "SELECT e.audience AS audience, COUNT(*) AS n FROM twin_evidence e"
        " WHERE e.work_type=? AND e.audience IS NOT NULL AND e.work_type NOT LIKE 'aud-%'"
        " AND EXISTS (SELECT 1 FROM twin_prompt_versions p"
        "             WHERE p.work_type='aud-'||e.audience AND p.status='active')"
        " GROUP BY e.audience ORDER BY n DESC LIMIT 3",
        (code,),
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        prof = store.get_active(conn, store.audience_profile_code(r["audience"]))
        if prof is None or not (prof.get("prompt_md") or "").strip():
            continue
        t = taxonomy.by_code("audience", r["audience"])
        out.append({"audience": r["audience"],
                    "zh": (t.zh if t else r["audience"]),
                    "version": prof.get("version"),
                    "prompt_md": prof["prompt_md"]})
    return out


def _action_compile(data: dict) -> dict:
    wt = str(data.get("work_type") or "").strip()
    if not wt:
        return {"ok": False, "error": "invalid_input", "field": "work_type", "reason": "required"}
    conn = db.connect()
    try:
        aud = store.split_audience_profile(wt)
        if aud is not None:
            if not store._is_known_audience(conn, aud):
                return {"ok": False, "error": "invalid_input", "field": "work_type",
                        "reason": f"unknown audience code: {aud!r}"}
            code = wt
        else:
            code = store.resolve_work_type_code(conn, wt)
            if not code:
                return {"ok": False, "error": "invalid_input", "field": "work_type",
                        "reason": "unknown code；先 twin(action=\"taxonomy\") 查码或治理 pending"}
        t = taxonomy.by_code("work_type", code)
        active = store.get_active(conn, code)
        client = _effective_client(data)
        if aud is not None:
            # 受众画像素材（AR-2）：该受众全部证据（不分 compiled），逐条 read
            rows = db.audience_evidence(conn, aud)
            evidence, skipped = _read_evidence_rows(rows, client=client)
            audience_profiles = None
        else:
            evidence, skipped = _fetch_evidence(conn, code, client=client)
            audience_profiles = _compile_audience_profiles(conn, code)
    finally:
        conn.close()
    if aud is not None:
        at = taxonomy.by_code("audience", aud)
        title_zh = at.zh if at else aud
    else:
        title_zh = t.zh if t else code
    material = templates.compile_prompt_material(
        aud or code, title_zh, active, evidence,
        audience_profiles=audience_profiles, audience_mode=aud is not None)
    out: dict = {"ok": True, "work_type": code,
                 "current_version": (active or {}).get("version"),
                 "evidence_count": len(evidence), "material": material,
                 "note": templates.STRONG_MODEL_NOTE,
                 "session_note": ("本会话仅用于编译，完成 submit 后即弃。请告知用户："
                                  "后续交付任务建议换新会话重新开始（编译会话内新旧 persona "
                                  "同屏，换会话是唯一硬隔离）；新会话 task_start 会自动注入新版"),
                 "next": "用当前会话模型按素材包编译出 prompt_md 后，调 "
                         "twin(action=\"submit\", data={work_type, prompt_md, source_memory_ids, model})"}
    if skipped:
        out["skipped_evidence"] = skipped
    return out


def _coerce_source_ids(value) -> list[int]:
    """review#10：只收 int / 数字字符串列表；"123" 这类可迭代脏值会拆成 1/2/3。"""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("source_memory_ids must be a list of memory ids")
    out: list[int] = []
    for i in value:
        if isinstance(i, bool) or not isinstance(i, (int, str)):
            raise ValueError(f"invalid memory id: {i!r}")
        try:
            n = int(i)
        except ValueError:
            raise ValueError(f"invalid memory id: {i!r}")
        if str(n) != str(i).strip() and not isinstance(i, int):
            raise ValueError(f"invalid memory id: {i!r}")
        out.append(n)
    return out


def _action_submit(data: dict) -> dict:
    for f in ("work_type", "prompt_md"):
        if not str(data.get(f) or "").strip():
            return {"ok": False, "error": "invalid_input", "field": f, "reason": "required"}
    try:
        source_ids = _coerce_source_ids(data.get("source_memory_ids"))
    except ValueError as e:
        return {"ok": False, "error": "invalid_input", "field": "source_memory_ids",
                "reason": str(e)}
    origin = data.get("origin")
    if origin is not None and origin != "scheduled":
        # AR-7 白名单：origin 只标记夜间定时任务来源，交互式编译不要传
        return {"ok": False, "error": "invalid_input", "field": "origin",
                "reason": "origin 仅接受 scheduled（夜间定时任务来源标记），交互式编译不要传"}
    prompt_md = str(data["prompt_md"])
    if len(prompt_md) > 100_000:
        # 轮2 P3-3：失控的编译会话不配变成每次注入的全额负担
        return {"ok": False, "error": "invalid_input", "field": "prompt_md",
                "reason": f"过长（{len(prompt_md)} 字符，上限 100000）——编译产物应精炼"}
    conn = db.connect()
    try:
        aud = store.split_audience_profile(str(data["work_type"]).strip())
        if aud is not None:
            # 受众画像伪类型（AR-1/AR-6）：不走 work_type 枚举，校验受众段
            if not store._is_known_audience(conn, aud):
                return {"ok": False, "error": "invalid_input", "field": "work_type",
                        "reason": f"unknown audience code: {aud!r}"}
            code = str(data["work_type"])
        else:
            code = store.resolve_work_type_code(conn, str(data["work_type"]))
            if not code:
                return {"ok": False, "error": "invalid_input", "field": "work_type", "reason": "unknown code"}
        rec = store.create_version(conn, code,
                                   prompt_md,
                                   source_ids,
                                   model=str(data.get("model") or ""))
        if store.split_audience_profile(code) is None:
            pre_uncompiled = len(db.uncompiled_evidence(conn, code))
            marked = db.mark_compiled(conn, source_ids, rec["version"], code)
        else:
            # 画像派生不消耗证据（AR-2）：aud- 行永远保持 uncompiled 给类型编译
            pre_uncompiled = marked = 0
    finally:
        conn.close()
    rec["ok"] = True
    rec["supersedes"] = rec.pop("superseded_version")  # 落版即裁决：本版取代的旧 active 版本
    is_profile = store.split_audience_profile(code) is not None
    if is_profile:
        rec["derived"] = True
        rec["note"] = ("受众画像版本：派生自该受众全部证据，不消耗证据"
                       "（对应类型证据仍属各自编译队列）")
        if origin == "scheduled":
            # 画像没有双跑提议，但夜间体系在转的信号要照刷（轮1 P2-3）：
            # 只建受众证据的用户不该被 scan_notice 永久提醒
            flow.ensure_schema()
            flow.set_meta("last_scheduled_compile_at", db.now_iso())
        # 轮2 P2-1：吸收数 ≠ 该受众证据总数（无论多报/漏报/混入他受众 id）
        # → stale 永不清零（夜夜重编），当场点破
        conn = db.connect()
        try:
            total_ev = len(db.audience_evidence(
                conn, store.split_audience_profile(code) or code))
        finally:
            conn.close()
        if len(source_ids) != total_ev:
            rec.setdefault("warnings", []).append(
                f"source_memory_ids {len(source_ids)} 条，该受众共 {total_ev} 条证据"
                "（漏列/多报/混入他受众 id？）：audience_stale 将持续触发夜间重抽象")
    elif origin == "scheduled":
        # #905-④：夜间落版标记来源 + 记对比基线（AR-1：previous_version 只在落版时
        # 可靠，事后推导会被 rollback/多版历史失真）；v1 无旧版可比则不记 → 永不提议
        flow.ensure_schema()
        flow.set_meta(f"persona_origin:{code}:{rec['version']}", "scheduled")
        # 夜间任务在转也算定时体系在转：只建夜间任务的用户不被 scan_notice 永久提醒
        flow.set_meta("last_scheduled_compile_at", db.now_iso())
        if rec["supersedes"] is not None:
            flow.set_meta(f"compare_prev:{code}:{rec['version']}", str(rec["supersedes"]))
    elif rec["supersedes"] is not None:
        # 交互式落版的可发现性（#905-④补充拍板）：告知双跑玩法，是否对比用户自决
        rec["compare_hint"] = (
            f"下一任务如需新旧对比（双跑）：用户同意后调 twin(action=\"get\", data="
            f"{{\"work_type\": \"{code}\", \"version\": {rec['supersedes']}}}) 取旧版 "
            f"v{rec['supersedes']} 全文，新旧各出一稿对比（旧版仅参考），对比后以 "
            f"v{rec['version']} 为执行依据（若此后有更高版本，以更高版本为准）；"
            "是否对比由用户决定，不追问。请把这句转告用户。")
    leftover = pre_uncompiled - marked
    if leftover > 0 and not is_profile:
        # 轮2 P2-1：漏列 source id 的证据会永久以增补在场且夜夜重编——当场点破
        rec.setdefault("warnings", []).append(
            f"该工作性质仍有 {leftover} 条未编译证据未被本版吸收"
            "（source_memory_ids 漏列？）：它们将继续以增补注入，且夜间任务会因"
            "未编译数>0 重复编译本类型")
    rec["evidence_marked_compiled"] = marked
    replaced = f"取代 v{rec['supersedes']}" if rec["supersedes"] is not None else "首个版本"
    rec["session_note"] = (f"请提醒用户：v{rec['version']} 已生效（{replaced}），"
                           "建议后续任务换新会话重新开始（新会话 task_start 自动注入新版 persona）；"
                           f"若用户坚持在本会话继续，下次 task_start 传 have_persona_version={rec['version']}"
                           " 即不再重复注入全文。本编译会话到此收尾即弃。")
    return rec


def _action_rollback(data: dict) -> dict:
    """回滚 persona 版本（零阻力：无确认、无警告、无拦截——即使效果是新版本
    习得的能力从 active 消失）。version 省略回上一版，传 n 回指定版。"""
    wt = str(data.get("work_type") or "").strip()
    if not wt:
        return {"ok": False, "error": "invalid_input", "field": "work_type", "reason": "required"}
    version = data.get("version")
    if version is not None:
        if isinstance(version, bool) or not isinstance(version, (int, str)):
            return {"ok": False, "error": "invalid_input", "field": "version",
                    "reason": "version 需是版本号整数（或省略回上一版）"}
        try:
            n = int(version)
        except ValueError:
            n = None
        # 上界 2^63-1：超范围 int 到 SQLite 绑定会抛 OverflowError（对抗#1），
        # 属调用方参数错误，应在矫正层打回而非落兜底 internal_error
        if (n is None or n < 1 or n > 2**63 - 1
                or (str(n) != str(version).strip() and not isinstance(version, int))):
            return {"ok": False, "error": "invalid_input", "field": "version",
                    "reason": f"invalid version: {version!r}"}
        version = n
    conn = db.connect()
    try:
        aud = store.split_audience_profile(wt)
        if aud is not None:
            # 受众画像同样可回滚（轮1 P1-2）：夜间抽象坏了也要能一键回上一版
            if not store._is_known_audience(conn, aud):
                return {"ok": False, "error": "invalid_input", "field": "work_type",
                        "reason": f"unknown audience code: {aud!r}"}
            code = wt
        else:
            code = store.resolve_work_type_code(conn, wt)
            if not code:
                return {"ok": False, "error": "invalid_input", "field": "work_type",
                        "reason": "unknown code；先 twin(action=\"taxonomy\") 查码或治理 pending"}
        try:
            out = store.activate_version(conn, code, version)
        except ValueError as e:
            return {"ok": False, "error": "invalid_input", "reason": str(e)}
    finally:
        conn.close()
    out["ok"] = True
    if out.pop("already_active", None):
        out["note"] = f"v{out['version']} 已是 active，未做变更"
    else:
        out["rolled_back_from"] = out.pop("superseded_version")
        out["guidance"] = ("已切换 active 版本。旧版本仍在库、可再 rollback 回来"
                           "（不删历史）；版本号不回收，下次 submit 继续 MAX+1。")
    return out


def _coerce_task_id(tid) -> int:
    """task_id 严格矫正（轮2 P3-4）：int / 纯数字串；浮点/bool/脏串打回——
    4.9 不再静默截断成 4。ValueError 由调度器归 invalid_input。"""
    if isinstance(tid, bool) or not isinstance(tid, (int, str)):
        raise ValueError(f"task_id 需是任务号整数: {tid!r}")
    try:
        n = int(tid)
    except ValueError:
        raise ValueError(f"invalid task_id: {tid!r}") from None
    if str(n) != str(tid).strip() and not isinstance(tid, int):
        raise ValueError(f"invalid task_id: {tid!r}")
    return n


def _coerce_version_field(data: dict):
    """version 参数矫正（#905-④，get 用）：int / 数字串，≥1、≤2^63-1；
    缺省返回 None，脏值返回 False 由调用方组 invalid_input（口径同 rollback 内联版）。"""
    version = data.get("version")
    if version is None:
        return None
    if isinstance(version, bool) or not isinstance(version, (int, str)):
        return False
    try:
        n = int(version)
    except ValueError:
        n = None
    if (n is None or n < 1 or n > 2**63 - 1
            or (str(n) != str(version).strip() and not isinstance(version, int))):
        return False
    return n


def _action_get(data: dict) -> dict:
    wt = str(data.get("work_type") or "").strip()
    if not wt:
        return {"ok": False, "error": "invalid_input", "field": "work_type", "reason": "required"}
    version = _coerce_version_field(data)
    if version is False:
        return {"ok": False, "error": "invalid_input", "field": "version",
                "reason": f"invalid version: {data.get('version')!r}"}
    conn = db.connect()
    try:
        aud = store.split_audience_profile(wt)
        if aud is not None:
            # 受众画像只读通道（AR-6）：aud-{受众码} 可查全文；后缀必须是已知受众
            if not store._is_known_audience(conn, aud):
                return {"ok": False, "error": "invalid_input", "field": "work_type",
                        "reason": f"unknown audience code: {aud!r}"}
            code = wt
            if version is not None:
                rec = store.get_version(conn, code, version)
            else:
                rec = store.get_active(conn, code)
        else:
            code = store.resolve_work_type_code(conn, wt)
            if not code:
                return {"ok": False, "error": "invalid_input", "field": "work_type", "reason": "unknown code"}
            if version is not None:
                rec = store.get_version(conn, code, version)
            else:
                rec = store.get_active(conn, code)
    finally:
        conn.close()
    if version is not None and not rec:
        # 镜像降级下无版本身份（AR-6）：明确报不可版本化读取，不猜
        return {"ok": False, "error": "not_found",
                "reason": f"{code} 不存在版本 v{version}；若处于镜像降级状态，"
                          "版本化读取不可用——不带 version 调 get 可取 active 全文；"
                          "twin(action=\"status\") 查看版本概况"}
    if not rec:
        return {"ok": True, "work_type": code, "prompt_md": None,
                "hint": "尚无 persona prompt；可先喂历史产出物或积累偏好后 compile"}
    return {"ok": True, **rec}


def _action_taxonomy(data: dict) -> dict:
    kind = str(data.get("kind") or "work_type")
    if kind not in taxonomy.KINDS:
        return {"ok": False, "error": "invalid_input", "field": "kind",
                "reason": f"expected one of {taxonomy.KINDS}"}
    items = [{"code": t.code, "zh": t.zh, "en": t.en, "domain": t.domain,
              "aliases": list(t.aliases)} for t in taxonomy.all_types(kind)]
    return {"ok": True, "kind": kind, "count": len(items), "types": items}


def _action_pending(data: dict) -> dict:
    conn = db.connect()
    try:
        status = str(data.get("status") or "pending")
        return {"ok": True, "status": status, "items": db.list_pending(conn, status)}
    finally:
        conn.close()


def _action_resolve(data: dict) -> dict:
    pid = data.get("pending_id")
    decision = data.get("decision")
    if pid is None or decision not in ("map", "canonicalize", "reject"):
        return {"ok": False, "error": "invalid_input",
                "reason": "需要 pending_id 与 decision ∈ map|canonicalize|reject"}
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM twin_pending_values WHERE id=?", (pid,)).fetchone()
        if not row:
            return {"ok": False, "error": "not_found", "reason": f"pending id {pid}"}
        if row["status"] != "pending":
            # 对抗 review#7：已裁定的 pending 不得重复裁定（重复 map 会把同一别名挂到
            # 第二个 canonical，归一结果由行序决定而非用户裁定）
            return {"ok": False, "error": "invalid_input",
                    "reason": f"pending {pid} 已裁定为 {row['status']}（→{row['resolved_code']}），不可重复裁定"}
        kind, raw = row["type_kind"], row["raw_value"]
        if decision == "map":
            code = str(data.get("code") or "").strip()
            known = taxonomy.by_code(kind, code) or \
                any(r["code"] == code for r in db.custom_types(conn, kind))
            if not code or not known:
                return {"ok": False, "error": "invalid_input", "field": "code",
                        "reason": "map 需要已有 canonical code"}
            db.append_alias(conn, kind, code, raw)
            db.set_pending(conn, int(pid), "mapped", code)
        elif decision == "canonicalize":
            nt = data.get("new_type") or {}
            if not isinstance(nt, dict):
                return {"ok": False, "error": "invalid_input", "field": "new_type",
                        "reason": "new_type 必须是对象 {code,zh,en,domain}"}
            try:
                db.add_canonical(conn, kind, str(nt.get("code") or ""),
                                 str(nt.get("zh") or ""), str(nt.get("en") or ""),
                                 str(nt.get("domain") or ""), [raw])
            except ValueError as e:
                return {"ok": False, "error": "invalid_input", "reason": str(e)}
            code = str(nt.get("code") or "")
            db.set_pending(conn, int(pid), "canonicalized", code)
        else:
            code = None
            db.set_pending(conn, int(pid), "rejected", None)
        backfilled = 0
        if code:
            # 对抗 review#3：pending 维度写入的证据行 code 为 NULL，对 compile/scan
            # 不可见——按 raw 回填，创始证据不再静默搁浅
            backfilled = db.backfill_evidence_codes(conn, kind, raw, code)
    finally:
        conn.close()
    return {"ok": True, "pending_id": int(pid), "decision": decision,
            "backfilled_evidence": backfilled}


# ---- 交付任务流（M2.1，机制改造自 plan-mode）----

def _task_persona(conn, code: str | None) -> dict | None:
    if not code:
        return None
    return store.get_active(conn, code)


def _coerce_have_version(data: dict) -> int | None:
    """have_persona_version 矫正（#895）：int / 数字串，≥1；脏值打回。
    口径与 rollback version / _coerce_source_ids 一致。"""
    v = data.get("have_persona_version")
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, str)):
        raise ValueError("have_persona_version 需是版本号整数")
    try:
        n = int(v)
    except ValueError:
        n = None
    if n is None or n < 1 or (str(n) != str(v).strip() and not isinstance(v, int)):
        raise ValueError(f"invalid have_persona_version: {v!r}")
    return n


def _persona_injection(persona: dict | None, have: int | None) -> dict:
    """task_start/task_resume 共用的注入分支（#895，agent 申报式短路）。

    服务端看不见会话，「已注入过」由 agent 申报（have_persona_version=上文
    注入返回的版本号）。失败模式全软：忘传/传错/mirror 降级（无版本身份）
    一律回落全文注入，绝不出现分身静默不在场。相等→短路省重复全文；
    不等（中途重编/回滚过）→全文重注入+中性变更说明（覆盖回滚场景）。"""
    if not persona:
        return {}
    cur = persona.get("version")
    declarable = not persona.get("from_mirror") and cur is not None and have is not None
    if declarable and have == cur:
        return {
            "persona_version": cur,
            "persona_unchanged": True,
            "hint": (f"persona v{cur} 与本会话此前注入一致，沿用上文即可；"
                     "若上文不可见（已被压缩），twin(action=\"get\") 取全文"),
        }
    out = {"persona_version": cur, "persona_prompt_md": persona.get("prompt_md")}
    if declarable and have != cur:
        out["note"] = f"persona 版本已从 v{have} 变更为 v{cur}，以本次注入为准"
    return out


# 未编译增补上限（#905-②拍板：10 条）：夜间任务收口后增补 ≤1 天写入量，
# 上限是用户没建夜间任务时的保险丝——截旧留新 + 提醒手动编译，不无限膨胀。
SUPPLEMENT_MAX = 10


def _supplement_payload(code: str, persona: dict | None,
                        client: str | None = None) -> dict:
    """未编译增补（#905-②）：只走 twin_evidence 索引，绝不走 find 兜底
    （兜底会把全量历史证据当增补注入，AR-3）。返回可直接并进 task_start/
    task_resume 响应的键；无可用增补返回 {}（软失败：mema 读挂→空，不影响注入）。
    注意措辞不宣称"编译后新增"——source_memory_ids 漏列时旧证据也会以增补在场，
    优先级声明一律钉死版本号（轮2 P2-1/P2-2，#896 出生标签要经得起时间）。"""
    conn = db.connect()
    try:
        rows = db.uncompiled_evidence(conn, code)
    finally:
        conn.close()
    if not rows:
        return {}
    total = len(rows)
    if total > SUPPLEMENT_MAX:
        rows = rows[-SUPPLEMENT_MAX:]  # ORDER BY id：尾部即最新
    evidence, skipped = _read_evidence_rows(rows, client, fail_fast=True)
    if not evidence:
        # 轮2 P3-6：全部读失败也要如实报 skipped，与"没有未编译证据"可区分
        return {"persona_supplement_skipped": skipped} if skipped else {}
    v = (persona or {}).get("version")
    if persona is not None and v is not None:
        note = (f"以下为尚未被 persona v{v} 吸收的偏好（未编译增补），"
                f"与 v{v} 冲突时以增补为准；若本会话已注入更高版本，以注入版本为准")
    elif persona is not None:  # mirror 降级无版本身份
        note = ("以下为尚未被当前 persona 吸收的偏好（未编译增补），"
                "与 persona prompt 冲突时以增补为准")
    else:
        note = ("尚无编译版 persona，以下为该工作性质已沉淀的偏好，按其执行；"
                "积累后可 twin(action=\"compile\") 生成 v1")
    if total > len(rows):
        note += (f"；另有 {total - len(rows)} 条更早的未编译偏好未带上，"
                 "建议 twin(action=\"compile\") 手动整理")
    out = {"persona_supplement": evidence, "persona_supplement_note": note}
    if skipped:
        out["persona_supplement_skipped"] = skipped
    return out


def _compare_offer(code: str, persona: dict) -> dict:
    """夜间版首任务双跑提议（#905-④）：仅 origin=scheduled、有 compare_prev、
    且未提议过的 active 版本，在 task_start 附加（task_resume 不提议——中途
    换版照常重注入即可）。附加即落 compare_offered（一次性不依赖用户回应，AR-2）。
    不含旧版全文：用户同意双跑后 Agent 才按需 get（防拿错版 + 省 token）。"""
    v = persona.get("version")
    if persona.get("from_mirror") or v is None:
        return {}
    if flow.get_meta(f"persona_origin:{code}:{v}") != "scheduled":
        return {}
    prev = flow.get_meta(f"compare_prev:{code}:{v}")
    if not prev:
        return {}
    try:
        prev_v = int(prev)
    except ValueError:
        return {}  # meta 脏值不让整个 task_start 报错（轮1 review P2）
    if not flow.claim_meta(f"compare_offered:{code}:{v}", db.now_iso()):
        return {}  # 已提议过：原子抢占（轮2 P2-3，多宿主并发只赢一个）
    return {"persona_compare_offer": {
        "current_version": v, "previous_version": prev_v,
        "hint": (f"v{v} 由夜间定时任务自动编译落版，用户未亲审。请询问用户一次："
                 "本任务单用新版跑，还是新旧双跑对比（token 增加）。用户同意双跑时，调 "
                 f"twin(action=\"get\", data={{\"work_type\": \"{code}\", \"version\": {prev_v}}}) "
                 f"按需取旧版 v{prev_v} 全文；旧版仅对比参考、非执行依据，对比后一律以 v{v} "
                 "为执行依据（若此后注入更高版本，以更高版本为准）。"
                 "用户不选或无人回应均照常单稿执行。")}}


# 受众画像雏形上限（#v0.3.6 拍板 A）：画像未编出时跨类型证据摘要垫底，≤5 条。
AUDIENCE_PROTO_MAX = 5


def _audience_payload(audience: str | None, exclude_work_type: str | None,
                      client: str | None = None) -> dict:
    """受众画像注入（v0.3.6）：画像全文优先，未编出时雏形垫底；受众未归一或
    无素材返回 {}（软失败）。优先级链在 note 里显式声明：本类型增补 > 类型
    persona > 受众画像/雏形（类型 persona 编译素材含受众参考，分辨率更高）。"""
    if not audience:
        return {}
    conn = db.connect()
    try:
        profile = store.get_active(conn, store.audience_profile_code(audience))
        if profile is not None and (profile.get("prompt_md") or "").strip():
            v = profile.get("version")
            tag = f"画像 v{v}" if v is not None else "画像（镜像降级读取）"
            out = {"audience_profile_md": profile["prompt_md"],
                   "audience_profile_note": (
                       f"以上为对当前受众的通用口径{tag}：口径/详略/禁忌类可参考，"
                       "格式与结构以本类型为准；冲突时优先级：本类型增补 > 类型 persona > "
                       "受众画像（若会话中后续注入更高版本画像，以更新者为准）")}
            return out
        rows = db.audience_evidence(conn, audience,
                                    exclude_work_type=exclude_work_type)
    finally:
        conn.close()
    if not rows:
        return {}
    if len(rows) > AUDIENCE_PROTO_MAX:
        rows = rows[-AUDIENCE_PROTO_MAX:]  # 最新优先
    evidence, skipped = _read_evidence_rows(rows, client, fail_fast=True)
    if not evidence:
        return {"audience_profile_skipped": skipped} if skipped else {}
    out = {"audience_profile_proto": evidence,
           "audience_profile_note": (
               "尚无该受众画像，以下为对同一受众的其他产出沉淀（含受众级偏好，"
               "可能与增补内容重叠，重叠时以增补为准）：口径/详略/禁忌类可参考，"
               "格式与结构以本任务所属类型为准；冲突时该类型的增补/persona 优先")}
    if skipped:
        out["audience_profile_skipped"] = skipped
    return out


def _action_task_start(data: dict) -> dict:
    brief = str(data.get("brief") or "").strip()
    if not brief:
        return {"ok": False, "error": "invalid_input", "field": "brief", "reason": "required"}
    wt_raw = str(data.get("work_type") or "").strip()
    if not wt_raw:
        return {"ok": False, "error": "invalid_input", "field": "work_type", "reason": "required"}
    try:
        have = _coerce_have_version(data)
    except ValueError as e:
        return {"ok": False, "error": "invalid_input",
                "field": "have_persona_version", "reason": str(e)}
    flow.ensure_schema()
    conn = db.connect()
    try:
        dims: dict = {}
        pendings: list[dict] = []
        for kind in taxonomy.KINDS:
            raw = str(data.get(kind) or "").strip()
            if not raw:
                if kind == "work_type":
                    return {"ok": False, "error": "invalid_input", "field": kind, "reason": "required"}
                dims[kind] = {"ok": False, "kind": kind, "raw": "", "code": None, "matched_by": None}
                continue
            if len(raw) > 200:
                # 轮2 P3-8：与 write 同款限长，超长 pending raw 不入库
                return {"ok": False, "error": "invalid_input", "field": kind,
                        "reason": "过长（上限 200 字符）"}
            # defer（轮2 P3-5）：建档成功才落 pending，读取/插入失败不留幽灵行
            r = normalize.normalize_value(kind, raw, conn, defer_pending=True)
            dims[kind] = r
            if not r.get("ok"):
                pendings.append(r)
        wt = dims["work_type"]
        code = wt.get("code") if wt.get("ok") else None
        persona = _task_persona(conn, code)
    finally:
        conn.close()
    record = flow.insert_task(
        brief=brief, status="planning", dims=dims,
        interpreted_intent=str(data.get("interpreted_intent") or "") or None,
        persona_version=(persona or {}).get("version"),
        client=_effective_client(data),
        session_todos=flow.current_todos(data.get("session")),
    )
    superseded = flow.supersede_open_tasks(record["id"])
    if pendings:
        conn = db.connect()
        try:
            for p in pendings:
                p["pending_id"] = db.upsert_pending(conn, p["kind"], p["raw"], None)
        finally:
            conn.close()
    # 增补取数放在建档/让位之后（轮2 P3-7）：慢 mema 读不再拉长并发让位竞窗
    supplement = _supplement_payload(code, persona,
                                     client=_effective_client(data)) if code else {}
    out: dict = {
        "ok": True, "task_id": record["id"], "status": "planning",
        "superseded_open_tasks": superseded,
        "dimensions": dims, "pending": pendings,
        "guidance": (
            "任务已建档。按 persona prompt 的偏好/结构/前置清单执行；材料不齐全先向"
            "用户确认或补齐。完成后 twin(action=\"task_submit\") 提交评审。"),
    }
    # 受众画像注入：与 persona/增补独立，任何分支都随响应进场（audience 未归一则空）
    aud_dim = dims.get("audience") or {}
    aud_code = aud_dim.get("code") if aud_dim.get("ok") else None
    if aud_code:
        out.update(_audience_payload(aud_code, code, client=_effective_client(data)))
    if persona:
        out.update(_persona_injection(persona, have))
        out.update(_compare_offer(code, persona))
        out.update(supplement)
    elif supplement:
        # 空 persona 分支（#905-A 拍板：给）：原始证据当雏形注入，第一天就有分身效果
        out.update(supplement)
        out["hint"] = ("该工作性质尚无编译版 persona，本次按上方已沉淀偏好执行；"
                       "积累后可 twin(action=\"compile\") 生成 v1")
    else:
        reason = "work_type 未归一（先治理 pending）" if not wt.get("ok") else "该工作性质尚无 persona prompt"
        out["hint"] = (f"{reason}；可先喂历史产出物或积累偏好后 "
                       "twin(action=\"compile\") 生成，本次按通用标准执行")
    return out


def _action_task_submit(data: dict) -> dict:
    tid = data.get("task_id")
    deliverable = str(data.get("deliverable_md") or "").strip()
    if tid is None or not deliverable:
        return {"ok": False, "error": "invalid_input",
                "reason": "需要 task_id 与 deliverable_md"}
    tid = _coerce_task_id(tid)
    flow.ensure_schema()
    record = flow.get_task(int(tid))
    if not record:
        return {"ok": False, "error": "not_found", "reason": f"task id {tid}"}
    if record["status"] not in ("planning", "submitted", "pending", "rejected"):
        return {"ok": False, "error": "invalid_input",
                "reason": f"task {tid} 状态为 {record['status']!r}，不可提交；可 task_resume 续作"}
    if data.get("todos") is not None:
        flow.set_session_todos(data.get("session"), data.get("todos"))
    # submit 即快照会话 todos 进任务行（plan-mode submit_plan 同款），resume 才有得恢复；
    # 空会话传 None 保留原快照（review#5：空列表会把 COALESCE 当真值清掉 todos）
    session_todos = flow.current_todos(data.get("session"))
    flow.update_deliverable(int(tid), deliverable,
                            brief=str(data.get("brief") or "") or None,
                            todos=session_todos or None)
    # 条件迁移（对抗 review#4）：并发 supersede 后这里 rowcount=0 → invalid_input
    updated = flow.set_status(int(tid), "submitted",
                              reason=str(data.get("note") or "") or None,
                              allowed_from=("planning", "submitted", "pending", "rejected"))
    return {"ok": True, "task_id": int(tid), "status": "submitted",
            "round": len(flow.list_reviews(int(tid))) + 1,
            "guidance": "已提交待评审。请用户审阅后 twin(action=\"task_review\") 裁定。"}


def _action_task_review(data: dict) -> dict:
    tid = data.get("task_id")
    verdict = data.get("verdict")
    if tid is None or verdict not in ("approved", "changes_requested"):
        return {"ok": False, "error": "invalid_input",
                "reason": "需要 task_id 与 verdict ∈ approved|changes_requested"}
    tid = _coerce_task_id(tid)
    notes = str(data.get("notes") or "")
    flow.ensure_schema()
    record = flow.get_task(int(tid))
    if not record:
        return {"ok": False, "error": "not_found", "reason": f"task id {tid}"}
    if record["status"] != "submitted":
        return {"ok": False, "error": "invalid_input",
                "reason": f"task {tid} 状态为 {record['status']!r}，仅 submitted 可评审"}
    review = flow.add_review(int(tid), verdict, notes)
    out: dict = {"ok": True, "task_id": int(tid), "review": review}
    if verdict == "approved":
        flow.set_status(int(tid), "approved", reason=notes or None,
                        allowed_from=("submitted",))
        # 对抗 review#5：评审期间可能被并发 resubmit，落盘前重读最新交付稿，
        # 保证 deliverables/ 审计工件与库内一致
        fresh = flow.get_task(int(tid)) or record
        try:
            out["deliverable_path"] = flow.write_deliverable_file(
                int(tid), fresh.get("deliverable_md") or "")
        except OSError as e:
            out["warnings"] = [f"交付物文件写入失败：{e}"]
        out["guidance"] = (
            "评审通过、任务收口。交付后提醒用户：后续修改尽量交给 Agent 而非手动改——"
            "每次修改都是一次偏好沉淀机会（twin.write，注明来源交付物）。")
    else:
        flow.set_status(int(tid), "rejected", reason=notes or None,
                        allowed_from=("submitted",))
        out["guidance"] = (
            "要求修改。评审意见本身是偏好信号：可先把用户的修改要求 twin.write 沉淀"
            "（工作性质/受众/用途照旧），改稿后直接 task_submit 提交下一轮"
            "（同任务轮次递增，评审历史全量可审计）。")
    return out


def _action_task_pending(data: dict) -> dict:
    tid = data.get("task_id")
    if tid is None:
        return {"ok": False, "error": "invalid_input", "reason": "需要 task_id"}
    tid = _coerce_task_id(tid)
    flow.ensure_schema()
    record = flow.get_task(int(tid))
    if not record:
        return {"ok": False, "error": "not_found", "reason": f"task id {tid}"}
    if record["status"] != "submitted":
        return {"ok": False, "error": "invalid_input",
                "reason": f"task {tid} 状态为 {record['status']!r}，仅 submitted 可搁置"}
    flow.set_status(int(tid), "pending", reason=str(data.get("reason") or "") or None,
                    allowed_from=("submitted",))
    return {"ok": True, "task_id": int(tid), "status": "pending",
            "guidance": "评审搁置（中断未决）。用户明确意向后可 task_review 裁定或 task_resume 续作。"}


def _action_task_resume(data: dict) -> dict:
    tid = data.get("task_id")
    if tid is None:
        return {"ok": False, "error": "invalid_input", "reason": "需要 task_id"}
    tid = _coerce_task_id(tid)
    try:
        have = _coerce_have_version(data)
    except ValueError as e:
        return {"ok": False, "error": "invalid_input",
                "field": "have_persona_version", "reason": str(e)}
    flow.ensure_schema()
    record = flow.get_task(int(tid))
    if not record:
        return {"ok": False, "error": "not_found", "reason": f"task id {tid}"}
    if record["status"] not in flow._RESUMABLE_STATUSES:
        return {"ok": False, "error": "invalid_input",
                "reason": f"task {tid} 状态为 {record['status']!r}，仅 "
                          f"{sorted(flow._RESUMABLE_STATUSES)} 可续作"}
    old_todos = record.get("todos") or []
    if old_todos:
        flow.set_session_todos(data.get("session"), old_todos)
    dims = {k: {"ok": bool(record.get(k)), "kind": k, "raw": record.get(f"{k}_raw") or "",
                "code": record.get(k), "matched_by": "db_alias" if record.get(k) else None}
            for k in taxonomy.KINDS}
    conn = db.connect()
    try:
        resume_code = record.get("work_type")
        persona = _task_persona(conn, resume_code)
    finally:
        conn.close()
    supplement = _supplement_payload(resume_code, persona,
                                     client=_effective_client(data)) if resume_code else {}
    new_record = flow.insert_task(
        brief=record["brief"], status="planning",
        dims=dims, interpreted_intent=record.get("interpreted_intent"),
        deliverable_md=record.get("deliverable_md") or "",
        reason=f"resumed from task #{tid}",
        persona_version=(persona or {}).get("version"),
        parent_task_id=int(tid),
        client=_effective_client(data),
        session_todos=flow.current_todos(data.get("session")),
    )
    flow.supersede_open_tasks(new_record["id"])
    out: dict = {
        "ok": True, "resumed_task_id": int(tid), "new_task_id": new_record["id"],
        "brief": record["brief"],
        "prior_deliverable_md": record.get("deliverable_md") or "",
        "restored_todos": old_todos,
        "guidance": (f"已从任务 #{tid} 续作（新任务 #{new_record['id']}）。"
                     "先核对自上次以来的变化，再继续执行并 task_submit。"),
    }
    if not old_todos:
        out["warnings"] = ["原任务没有 todos——可能已全部完成"]
    aud_code = record.get("audience")
    if aud_code:
        out.update(_audience_payload(aud_code, resume_code,
                                     client=_effective_client(data)))
    if persona:
        out.update(_persona_injection(persona, have))
        out.update(supplement)
    elif supplement:
        out.update(supplement)
        out["hint"] = "该工作性质尚无编译版 persona，按上方已沉淀偏好执行；可 compile 生成 v1"
    else:
        out["hint"] = "该工作性质尚无 persona prompt；可先 compile 生成或按通用标准执行"
    return out


def _action_task_revise(data: dict) -> dict:
    tid = data.get("task_id")
    if tid is None:
        return {"ok": False, "error": "invalid_input", "reason": "需要 task_id"}
    tid = _coerce_task_id(tid)
    new_brief = str(data.get("brief") or "").strip()
    revision_reason = str(data.get("revision_reason") or "").strip()
    deliverable = str(data.get("deliverable_md") or "").strip()
    if not (new_brief or deliverable or revision_reason):
        return {"ok": False, "error": "invalid_input",
                "reason": "至少给 brief / deliverable_md / revision_reason 之一"}
    flow.ensure_schema()
    record = flow.get_task(int(tid))
    if not record:
        return {"ok": False, "error": "not_found", "reason": f"task id {tid}"}
    if record["status"] not in flow._REVISABLE_STATUSES:
        return {"ok": False, "error": "invalid_input",
                "reason": f"task {tid} 状态为 {record['status']!r}，仅 "
                          f"{sorted(flow._REVISABLE_STATUSES)} 可修订"}
    dims = {k: {"ok": bool(record.get(k)), "kind": k, "raw": record.get(f"{k}_raw") or "",
                "code": record.get(k), "matched_by": "db_alias" if record.get(k) else None}
            for k in taxonomy.KINDS}
    # 对抗 review#8：修订=返工，子任务一律 planning 重走执行→提交→评审，
    # 不继承 approved（否则出现"从未被评审的已批准"审计伪造）
    child = flow.insert_task(
        brief=new_brief or record["brief"],
        status="planning", dims=dims,
        interpreted_intent=record.get("interpreted_intent"),
        deliverable_md=deliverable or record.get("deliverable_md") or "",
        reason=revision_reason or None,
        persona_version=record.get("persona_version"),
        parent_task_id=int(tid), iteration=int(record.get("iteration") or 0) + 1,
        revision_reason=revision_reason or None,
        client=_effective_client(data),
        session_todos=flow.current_todos(data.get("session")),
    )
    flow.set_status(int(tid), "superseded", reason=f"revised by task #{child['id']}",
                    allowed_from=(record["status"],))
    flow.supersede_open_tasks(child["id"])
    return {"ok": True, "parent_task_id": int(tid), "task_id": child["id"],
            "iteration": child["iteration"], "status": child["status"],
            "guidance": (f"已生成修订版任务 #{child['id']}（第 {child['iteration']} 次修订，"
                         "回到 planning 重走执行）。继续执行后 task_submit。")}


def _action_task_close(data: dict) -> dict:
    tid = data.get("task_id")
    if tid is None:
        return {"ok": False, "error": "invalid_input", "reason": "需要 task_id"}
    tid = _coerce_task_id(tid)
    flow.ensure_schema()
    record = flow.get_task(int(tid))
    if not record:
        return {"ok": False, "error": "not_found", "reason": f"task id {tid}"}
    if record["status"] not in flow._OPEN_STATUSES:
        return {"ok": False, "error": "invalid_input",
                "reason": f"task {tid} 状态为 {record['status']!r}，仅开放任务可关闭"}
    flow.set_status(int(tid), "superseded",
                    reason=str(data.get("reason") or "closed") or None,
                    allowed_from=flow._OPEN_STATUSES)
    return {"ok": True, "task_id": int(tid), "status": "superseded",
            "guidance": "任务已显式关闭（历史保留可审计）。"}


def _action_task_recent(data: dict) -> dict:
    flow.ensure_schema()
    limit = int(data.get("limit") or 10)
    rows = flow.recent_tasks(limit)
    return {"ok": True, "count": len(rows), "tasks": rows}


def _action_task_get(data: dict) -> dict:
    tid = data.get("task_id")
    if tid is None:
        return {"ok": False, "error": "invalid_input", "reason": "需要 task_id"}
    tid = _coerce_task_id(tid)
    flow.ensure_schema()
    record = flow.get_task(int(tid))
    if not record:
        return {"ok": False, "error": "not_found", "reason": f"task id {tid}"}
    record["reviews"] = flow.list_reviews(int(tid))
    return {"ok": True, "task": record}


def _action_todo(data: dict) -> dict:
    if data.get("todos") is None:
        return {"ok": True, "todos": flow.current_todos(data.get("session"))}
    return flow.set_session_todos(data.get("session"), data.get("todos"))


def _action_scan(data: dict) -> dict:
    return scan.run_scan()


def _action_help(data: dict) -> dict:
    topic = str(data.get("topic") or "").strip()
    if topic == scan.SCHEDULED_TASKS_TOPIC:
        return {
            "ok": True, "topic": scan.SCHEDULED_TASKS_TOPIC,
            "description": "twin 定时任务 spec（夜间 persona 编译 + 每周治理扫描）：Agent 据此在宿主平台创建等价任务。",
            "agent_instruction": scan.AGENT_INSTRUCTION,
            "setup": scan.SCHEDULED_TASKS_SPEC,
            "note": "提醒自消失：夜间编译（scheduled submit）或 scan 任一在 7 天内跑过即不再提示。",
        }
    return {
        "ok": True,
        "actions": {
            "write": "沉淀一条工作偏好。必填 content/work_type/audience/purpose"
                     "（先 taxonomy 查清单选码；清单无合适项给原始值，进 pending 由用户裁定）；"
                     "可选 subject/tags/source_ref/client（多 Agent 共接时 client 填宿主标识，如 kimi/jinleai）。"
                     "对该受众的通用偏好（不限工作类型）传 scope=audience（此时 work_type 省略，"
                     "audience 必须归一成功）。",
            "status": "查看各 work_type 的 prompt 版本概况、受众画像（audience_profiles）、"
                      "需重抽象的受众（audience_stale）、pending 数量与未编译统计。",
            "compile": "取编译素材包（旧版本 prompt 编译参考 + 未编译证据 + 同受众画像参考 + 编译规则），"
                       "由当前会话模型编译；独立会话执行、收尾即弃（session_note）。参数 work_type；"
                       "传 aud-{受众} 取受众画像素材包（该受众全部证据）。",
            "submit": "提交编译产物落版本并写文件镜像。必填 work_type/prompt_md；"
                      "建议带 source_memory_ids 与 model。返回 supersedes（被取代的旧 active 版本）；"
                      "夜间定时任务落版必传 origin=scheduled（其余场景不要传）；"
                      "取代旧版的交互式落版返回 compare_hint（双跑对比提示，转告用户；首个版本无）；"
                      "work_type 传 aud-{受众} 即受众画像落版（derived，不消耗证据）。",
            "rollback": "回滚 persona 版本（零阻力：无确认、无警告）。work_type 必填；"
                        "version 省略回上一版本、传 n 回指定版本；不删历史（retired 可再激活），"
                        "版本号不回收（下次 submit 继续 MAX+1）。",
            "get": "取某 work_type 的 persona prompt（DB 优先，文件镜像降级）。参数 work_type；"
                   "可选 version 取指定历史版本全文（双跑对比取旧版用）；"
                   "work_type 传 aud-{受众} 可读受众画像。",
            "taxonomy": "列枚举。参数 kind ∈ work_type|audience|purpose。",
            "pending": "列待裁长尾。参数 status（默认 pending）。",
            "resolve": "治理待裁值。pending_id + decision ∈ map(带 code)|canonicalize(带 new_type{code,zh,en,domain})|reject。",
            "task_start": "开工建档（流程注入点）。必填 brief/work_type（audience/purpose 可选，原始值即可）；"
                          "返回该工作性质的 persona prompt 与前置清单，开放任务自动让位。"
                          "可选 have_persona_version：同一会话此前注入过同 work_type 且版本号仍在场时申报，"
                          "版本未变则不再重复注入全文，变了则重注入并附变更说明。"
                          "夜间自动落版的新版本会附 persona_compare_offer（双跑对比提议，一次性）。"
                          "响应另带 persona_supplement：该 work_type 未编译偏好增补"
                          "（与 prompt 冲突以增补为准；上限 10 条）。"
                          "client 字段同 write（http 共接时头已带则无需传）。",
            "task_submit": "提交交付稿待评审。必填 task_id/deliverable_md；可带 todos/session。",
            "task_review": "评审裁定（append-only 审计）。task_id + verdict ∈ approved|changes_requested，"
                           "notes 记意见；approved 落交付物文件，changes 走 rejected 并提示沉淀偏好。",
            "task_pending": "评审搁置（中断未决）。task_id。",
            "task_resume": "续作历史任务（可中断可继续）。task_id；恢复 todos、新建 planning 任务并再注入 persona"
                           "（含未编译增补 persona_supplement，同 task_start）。"
                           "have_persona_version 申报口径同 task_start。",
            "task_revise": "修订进行中的任务。task_id + brief/deliverable_md/revision_reason 至少其一；"
                           "子任务回 planning 重走执行并记 lineage。",
            "task_close": "显式关闭开放任务（planning/submitted/pending），历史保留可审计。",
            "task_recent": "最近任务列表。参数 limit（默认 10）。",
            "task_get": "取单个任务全量（含评审历史）。task_id。",
            "todo": "会话 todo 读写（plan-mode 同款语义：整体替换，至多一条 in_progress）。传 todos 替换，不传读取。",
            "scan": "执行定时扫描（挂 Agent 端调度）：未编译偏好/pending 积压/开放任务汇总与建议；"
                    "刷新 last_scan_at 使安装提醒自消失。",
            "help": "本帮助。",
        },
        "write_guidance": templates.WRITE_GUIDANCE,
        "note": templates.STRONG_MODEL_NOTE,
    }


_ACTIONS = {
    "write": _action_write,
    "status": _action_status,
    "compile": _action_compile,
    "submit": _action_submit,
    "rollback": _action_rollback,
    "get": _action_get,
    "taxonomy": _action_taxonomy,
    "pending": _action_pending,
    "resolve": _action_resolve,
    "task_start": _action_task_start,
    "task_submit": _action_task_submit,
    "task_review": _action_task_review,
    "task_pending": _action_task_pending,
    "task_resume": _action_task_resume,
    "task_revise": _action_task_revise,
    "task_close": _action_task_close,
    "task_recent": _action_task_recent,
    "task_get": _action_task_get,
    "todo": _action_todo,
    "scan": _action_scan,
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
        if not _is_loopback_host(host):
            raise SystemExit(
                f"MEMA_TWIN_HTTP_HOST={host!r} 非 loopback 地址被拒绝："
                "X-Mema-Client 头不是鉴权，对外暴露等于开放身份伪造（mema core 同款策略）。"
                "twin 设计为本机单用户服务。")
        mcp.settings.host = host
        mcp.settings.port = int(os.environ.get("MEMA_TWIN_HTTP_PORT", "8765"))
        mcp.run(transport="streamable-http")
    else:
        raise SystemExit(f"unknown MEMA_TWIN_TRANSPORT {transport!r}（期望 stdio|http）")


if __name__ == "__main__":
    main()
