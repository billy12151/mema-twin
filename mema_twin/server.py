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

    动作：write / get / compile / submit / status / taxonomy / pending / resolve /
    task_start / task_submit / task_review / task_pending / task_resume / task_revise /
    task_close / task_recent / task_get / todo / scan / help。
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
    for f in ("work_type", "audience", "purpose"):
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
        subject=str(data.get("subject") or f"工作偏好：{dims['work_type'].get('raw')}"),
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
            if dims["work_type"].get("ok"):
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
        for r in rows:
            v = versions.setdefault(r["work_type"],
                                    {"work_type": r["work_type"], "active": None, "versions": []})
            v["versions"].append(dict(r))
            if r["status"] == "active":
                v["active"] = r["version"]
        pending = db.list_pending(conn)
        out = {"ok": True, "prompts": list(versions.values()),
               "pending_count": len(pending),
               "uncompiled": db.evidence_stats(conn)}
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


def _fetch_evidence(conn, code: str, client: str | None = None) -> tuple[list[dict], list[dict]]:
    """compile 证据：优先 twin_evidence 索引 + 按 id 精确 read 取全文（召回
    精确无丢失，M1.3）；索引为空退回 find 兜底。返回 (evidence, skipped)。"""
    rows = db.uncompiled_evidence(conn, code)
    if not rows:
        return _fetch_evidence_find(conn, code), []
    evidence: list[dict] = []
    skipped: list[dict] = []
    for r in rows:
        mid = r["memory_id"]
        try:
            resp = sink.read_memory(mid, _bucket(), client=client)
        except sink.SinkError as e:
            skipped.append({"memory_id": mid, "reason": f"mema read 失败: {e}"})
            continue
        if not resp.get("ok"):
            skipped.append({"memory_id": mid,
                            "reason": (resp.get("error") or "read 未命中")})
            continue
        mem = (resp.get("data") or {}).get("memory") or {}
        if not (mem.get("content") or "").strip():
            # 对抗 review#9②：形状漂移不能产出空证据行进素材包
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


def _is_loopback_host(host: str) -> bool:
    """http 绑定白名单：localhost / 127.x / ::1（core request_identity 同款）。"""
    normalized = str(host or "").strip().strip("[]").casefold()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _action_compile(data: dict) -> dict:
    wt = str(data.get("work_type") or "").strip()
    if not wt:
        return {"ok": False, "error": "invalid_input", "field": "work_type", "reason": "required"}
    conn = db.connect()
    try:
        code = store.resolve_work_type_code(conn, wt)
        if not code:
            return {"ok": False, "error": "invalid_input", "field": "work_type",
                    "reason": "unknown code；先 twin(action=\"taxonomy\") 查码或治理 pending"}
        t = taxonomy.by_code("work_type", code)
        active = store.get_active(conn, code)
        evidence, skipped = _fetch_evidence(conn, code, client=_effective_client(data))
    finally:
        conn.close()
    material = templates.compile_prompt_material(code, t.zh if t else code, active, evidence)
    out: dict = {"ok": True, "work_type": code,
                 "current_version": (active or {}).get("version"),
                 "evidence_count": len(evidence), "material": material,
                 "note": templates.STRONG_MODEL_NOTE,
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
    conn = db.connect()
    try:
        code = store.resolve_work_type_code(conn, str(data["work_type"]))
        if not code:
            return {"ok": False, "error": "invalid_input", "field": "work_type", "reason": "unknown code"}
        rec = store.create_version(conn, code,
                                   str(data["prompt_md"]),
                                   source_ids,
                                   model=str(data.get("model") or ""))
        marked = db.mark_compiled(conn, source_ids, rec["version"], code)
    finally:
        conn.close()
    rec["ok"] = True
    rec["evidence_marked_compiled"] = marked
    return rec


def _action_get(data: dict) -> dict:
    wt = str(data.get("work_type") or "").strip()
    if not wt:
        return {"ok": False, "error": "invalid_input", "field": "work_type", "reason": "required"}
    conn = db.connect()
    try:
        code = store.resolve_work_type_code(conn, wt)
        if not code:
            return {"ok": False, "error": "invalid_input", "field": "work_type", "reason": "unknown code"}
        rec = store.get_active(conn, code)
    finally:
        conn.close()
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


def _action_task_start(data: dict) -> dict:
    brief = str(data.get("brief") or "").strip()
    if not brief:
        return {"ok": False, "error": "invalid_input", "field": "brief", "reason": "required"}
    wt_raw = str(data.get("work_type") or "").strip()
    if not wt_raw:
        return {"ok": False, "error": "invalid_input", "field": "work_type", "reason": "required"}
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
            r = normalize.normalize_value(kind, raw, conn)
            dims[kind] = r
            if not r.get("ok"):
                pendings.append(r)
        wt = dims["work_type"]
        persona = _task_persona(conn, wt.get("code") if wt.get("ok") else None)
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
    out: dict = {
        "ok": True, "task_id": record["id"], "status": "planning",
        "superseded_open_tasks": superseded,
        "dimensions": dims, "pending": pendings,
        "guidance": (
            "任务已建档。按 persona prompt 的偏好/结构/前置清单执行；材料不齐全先向"
            "用户确认或补齐。完成后 twin(action=\"task_submit\") 提交评审。"),
    }
    if persona:
        out["persona_version"] = persona.get("version")
        out["persona_prompt_md"] = persona.get("prompt_md")
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
        persona = _task_persona(conn, record.get("work_type"))
    finally:
        conn.close()
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
    if persona:
        out["persona_version"] = persona.get("version")
        out["persona_prompt_md"] = persona.get("prompt_md")
    else:
        out["hint"] = "该工作性质尚无 persona prompt；可先 compile 生成或按通用标准执行"
    return out


def _action_task_revise(data: dict) -> dict:
    tid = data.get("task_id")
    if tid is None:
        return {"ok": False, "error": "invalid_input", "reason": "需要 task_id"}
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
            "description": "twin 定时扫描任务 spec：Agent 据此在宿主平台创建等价任务。",
            "agent_instruction": scan.AGENT_INSTRUCTION,
            "setup": scan.SCHEDULED_TASKS_SPEC,
            "note": "提醒自消失：twin(action=\"scan\") 在 7 天内跑过即不再提示。",
        }
    return {
        "ok": True,
        "actions": {
            "write": "沉淀一条工作偏好。必填 content/work_type/audience/purpose"
                     "（先 taxonomy 查清单选码；清单无合适项给原始值，进 pending 由用户裁定）；"
                     "可选 subject/tags/source_ref/client（多 Agent 共接时 client 填宿主标识，如 kimi/jinleai）。",
            "status": "查看各 work_type 的 prompt 版本概况与 pending 数量。",
            "compile": "取编译素材包（当前版本 prompt + 未编译偏好证据 + 编译规则），"
                       "由当前会话模型编译。参数 work_type。",
            "submit": "提交编译产物落版本并写文件镜像。必填 work_type/prompt_md；"
                      "建议带 source_memory_ids 与 model。",
            "get": "取某 work_type 的 active persona prompt（DB 优先，文件镜像降级）。参数 work_type。",
            "taxonomy": "列枚举。参数 kind ∈ work_type|audience|purpose。",
            "pending": "列待裁长尾。参数 status（默认 pending）。",
            "resolve": "治理待裁值。pending_id + decision ∈ map(带 code)|canonicalize(带 new_type{code,zh,en,domain})|reject。",
            "task_start": "开工建档（流程注入点）。必填 brief/work_type（audience/purpose 可选，原始值即可）；"
                          "返回该工作性质的 persona prompt 与前置清单，开放任务自动让位。"
                          "client 字段同 write（http 共接时头已带则无需传）。",
            "task_submit": "提交交付稿待评审。必填 task_id/deliverable_md；可带 todos/session。",
            "task_review": "评审裁定（append-only 审计）。task_id + verdict ∈ approved|changes_requested，"
                           "notes 记意见；approved 落交付物文件，changes 走 rejected 并提示沉淀偏好。",
            "task_pending": "评审搁置（中断未决）。task_id。",
            "task_resume": "续作历史任务（可中断可继续）。task_id；恢复 todos、新建 planning 任务并再注入 persona。",
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
