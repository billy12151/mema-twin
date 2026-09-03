"""mema-twin MCP server：单工具 twin(action, data)，动作式紧凑接口（仿 mema/plan-mode 风格）。

动作分三组：偏好与编译（write/get/compile/submit/status/taxonomy/pending/resolve）、
交付任务流（task_start/task_submit/task_review/task_pending/task_resume/task_revise/
task_recent/task_get/todo，机制改造自 plan-mode：可审计、可中断、可继续）、
定时扫描（scan，提醒挂 Agent 端）。
"""
from __future__ import annotations

import datetime as _dt
import os

from mcp.server.fastmcp import FastMCP

from . import db, flow, normalize, scan, sink, store, taxonomy, templates

mcp = FastMCP("mema-twin")

_KIND_PREFIX = {"work_type": "wt", "audience": "au", "purpose": "pu"}


@mcp.tool()
def twin(action: str, data: dict | None = None) -> dict:
    """个人分身 twin：按工作性质沉淀用户工作偏好，编译版本化 persona prompt，
    经交付任务流注入执行，并提供定时扫描建议。

    动作：write / get / compile / submit / status / taxonomy / pending / resolve /
    task_start / task_submit / task_review / task_pending / task_resume / task_revise /
    task_recent / task_get / todo / scan / help。
    先 twin(action="help") 查看各动作参数与引导。compile 返回素材包，由当前会话模型
    编译（建议在强模型会话中执行），submit 提交回库落版本并写文件镜像。
    """
    data = data or {}
    handler = _ACTIONS.get(action)
    if handler is None:
        return {"ok": False, "error": "invalid_input",
                "reason": f"unknown action {action!r}", "actions": sorted(_ACTIONS)}
    try:
        return handler(data)
    except sink.SinkError as e:
        return {"ok": False, "error": "mema_unreachable", "reason": str(e)}
    except ValueError as e:
        return {"ok": False, "error": "invalid_input", "reason": str(e)}


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


def _workspace(data: dict) -> str:
    return str(data.get("workspace") or os.environ.get("MEMA_TWIN_WORKSPACE", "memory-arbiter-mcp"))


def _action_write(data: dict) -> dict:
    for f in ("content", "work_type", "audience", "purpose"):
        if not str(data.get(f) or "").strip():
            return {"ok": False, "error": "invalid_input", "field": f, "reason": "required"}
    conn = db.connect()
    dims: dict = {}
    pendings: list[dict] = []
    tags = ["twin-preference"]
    for kind in taxonomy.KINDS:
        r = normalize.normalize_value(kind, str(data[kind]), conn)
        if r.get("error"):
            return {"ok": False, "error": "invalid_input", "field": kind, "reason": r.get("reason")}
        dims[kind] = r
        if r.get("ok"):
            tags.append(f"twin:{_KIND_PREFIX[kind]}:{r['code']}")
        else:
            pendings.append(r)
            tags.append(f"twin:{_KIND_PREFIX[kind]}:raw:{r['raw']}")
    resp = sink.remember(
        content=str(data["content"]),
        subject=str(data.get("subject") or f"工作偏好：{dims['work_type'].get('raw')}"),
        tags=tags + [str(t) for t in (data.get("tags") or [])],
        workspace=_workspace(data),
        source_ref=str(data.get("source_ref") or ""),
        event_time=_today(),
    )
    ok = bool(resp.get("ok"))
    out: dict = {"ok": ok, "memory": resp.get("data") if ok else resp,
                 "dimensions": dims, "pending": pendings}
    if ok:
        ws = _workspace(data)
        mid = _memory_id_of(resp.get("data"))
        if mid is not None:
            db.record_evidence(conn, ws, mid, dims,
                               subject=str(data.get("subject") or ""))
            out["evidence_id"] = mid
        if dims["work_type"].get("ok"):
            conn2 = db.connect()
            active = store.get_active(conn2, _workspace(data), dims["work_type"]["code"])
            if active and active.get("version") is not None:
                out["hint"] = (f"{dims['work_type']['label_zh']} 已有 persona prompt v{active['version']}；"
                               "本次偏好已入池未编译，可 twin(action=\"compile\") 生成新版本"
                               f"（{templates.STRONG_MODEL_NOTE}）")
            else:
                out["hint"] = (f"{dims['work_type']['label_zh']} 尚无 persona prompt，"
                               "可 twin(action=\"compile\") 生成 v1"
                               f"（{templates.STRONG_MODEL_NOTE}）")
    return out


def _action_status(data: dict) -> dict:
    conn = db.connect()
    ws = _workspace(data)
    rows = conn.execute(
        "SELECT work_type, version, model, status, evidence_count, created_at"
        " FROM twin_prompt_versions WHERE workspace=? ORDER BY work_type, version",
        (ws,),
    ).fetchall()
    versions: dict = {}
    for r in rows:
        v = versions.setdefault(r["work_type"],
                                {"work_type": r["work_type"], "active": None, "versions": []})
        v["versions"].append(dict(r))
        if r["status"] == "active":
            v["active"] = r["version"]
    pending = db.list_pending(conn)
    out = {"ok": True, "workspace": ws, "prompts": list(versions.values()),
           "pending_count": len(pending),
           "uncompiled": db.evidence_stats(conn, ws)}
    notice = scan.scan_notice(ws)
    if notice:
        out["scan_notice"] = notice
    return out


def _fetch_evidence_find(conn, ws: str, code: str) -> list[dict]:
    """兜底召回：twin_evidence 索引为空时（索引落地前的存量数据）退回
    mema find 语义召回（include_content=true，0.15.4 起默认索引页无正文）。"""
    t = taxonomy.by_code("work_type", code)
    q = f"{t.zh if t else code} 用户偏好 规则 结构"
    resp = sink.find(q, ws)
    if not resp.get("ok"):
        return []
    payload = resp.get("data") or {}
    results = payload.get("results") or payload.get("matches") or []
    tag = f"twin:wt:{code}"
    compiled = set()
    for v in store.list_versions(conn, ws, code):
        compiled.update(v["source_memory_ids"])
    return [r for r in results
            if tag in (r.get("tags") or []) and str(r.get("id")) not in compiled]


def _fetch_evidence(conn, ws: str, code: str) -> tuple[list[dict], list[dict]]:
    """compile 证据：优先 twin_evidence 索引 + 按 id 精确 read 取全文（召回
    精确无丢失，M1.3）；索引为空退回 find 兜底。返回 (evidence, skipped)。"""
    rows = db.uncompiled_evidence(conn, ws, code)
    if not rows:
        return _fetch_evidence_find(conn, ws, code), []
    evidence: list[dict] = []
    skipped: list[dict] = []
    for r in rows:
        mid = r["memory_id"]
        try:
            resp = sink.read_memory(mid)
        except sink.SinkError as e:
            skipped.append({"memory_id": mid, "reason": f"mema read 失败: {e}"})
            continue
        if not resp.get("ok"):
            skipped.append({"memory_id": mid,
                            "reason": (resp.get("error") or "read 未命中")})
            continue
        mem = (resp.get("data") or {}).get("memory") or {}
        evidence.append({
            "id": mid,
            "subject": mem.get("subject") or r.get("subject") or "",
            "content": mem.get("content") or "",
            "audience": r.get("audience"),
            "purpose": r.get("purpose"),
        })
    return evidence, skipped


def _action_compile(data: dict) -> dict:
    wt = str(data.get("work_type") or "").strip()
    if not wt:
        return {"ok": False, "error": "invalid_input", "field": "work_type", "reason": "required"}
    conn = db.connect()
    code = store.resolve_work_type_code(conn, wt)
    if not code:
        return {"ok": False, "error": "invalid_input", "field": "work_type",
                "reason": "unknown code；先 twin(action=\"taxonomy\") 查码或治理 pending"}
    ws = _workspace(data)
    t = taxonomy.by_code("work_type", code)
    active = store.get_active(conn, ws, code)
    evidence, skipped = _fetch_evidence(conn, ws, code)
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


def _action_submit(data: dict) -> dict:
    for f in ("work_type", "prompt_md"):
        if not str(data.get(f) or "").strip():
            return {"ok": False, "error": "invalid_input", "field": f, "reason": "required"}
    conn = db.connect()
    code = store.resolve_work_type_code(conn, str(data["work_type"]))
    if not code:
        return {"ok": False, "error": "invalid_input", "field": "work_type", "reason": "unknown code"}
    rec = store.create_version(conn, _workspace(data), code,
                               str(data["prompt_md"]),
                               data.get("source_memory_ids") or [],
                               model=str(data.get("model") or ""))
    marked = db.mark_compiled(conn, _workspace(data),
                              [int(i) for i in (data.get("source_memory_ids") or [])],
                              rec["version"])
    rec["ok"] = True
    rec["evidence_marked_compiled"] = marked
    return rec


def _action_get(data: dict) -> dict:
    wt = str(data.get("work_type") or "").strip()
    if not wt:
        return {"ok": False, "error": "invalid_input", "field": "work_type", "reason": "required"}
    conn = db.connect()
    code = store.resolve_work_type_code(conn, wt)
    if not code:
        return {"ok": False, "error": "invalid_input", "field": "work_type", "reason": "unknown code"}
    rec = store.get_active(conn, _workspace(data), code)
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
    status = str(data.get("status") or "pending")
    return {"ok": True, "status": status, "items": db.list_pending(conn, status)}


def _action_resolve(data: dict) -> dict:
    pid = data.get("pending_id")
    decision = data.get("decision")
    if pid is None or decision not in ("map", "canonicalize", "reject"):
        return {"ok": False, "error": "invalid_input",
                "reason": "需要 pending_id 与 decision ∈ map|canonicalize|reject"}
    conn = db.connect()
    row = conn.execute("SELECT * FROM twin_pending_values WHERE id=?", (pid,)).fetchone()
    if not row:
        return {"ok": False, "error": "not_found", "reason": f"pending id {pid}"}
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
        try:
            db.add_canonical(conn, kind, str(nt.get("code") or ""),
                             str(nt.get("zh") or ""), str(nt.get("en") or ""),
                             str(nt.get("domain") or ""), [raw])
        except ValueError as e:
            return {"ok": False, "error": "invalid_input", "reason": str(e)}
        db.set_pending(conn, int(pid), "canonicalized", str(nt.get("code") or ""))
    else:
        db.set_pending(conn, int(pid), "rejected", None)
    return {"ok": True, "pending_id": int(pid), "decision": decision}


# ---- 交付任务流（M2.1，机制改造自 plan-mode）----

def _task_persona(conn, ws: str, code: str | None) -> dict | None:
    if not code:
        return None
    return store.get_active(conn, ws, code)


def _action_task_start(data: dict) -> dict:
    brief = str(data.get("brief") or "").strip()
    if not brief:
        return {"ok": False, "error": "invalid_input", "field": "brief", "reason": "required"}
    wt_raw = str(data.get("work_type") or "").strip()
    if not wt_raw:
        return {"ok": False, "error": "invalid_input", "field": "work_type", "reason": "required"}
    flow.ensure_schema()
    conn = db.connect()
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
    ws = _workspace(data)
    wt = dims["work_type"]
    persona = _task_persona(conn, ws, wt.get("code") if wt.get("ok") else None)
    record = flow.insert_task(
        workspace=ws, brief=brief, status="planning", dims=dims,
        interpreted_intent=str(data.get("interpreted_intent") or "") or None,
        persona_version=(persona or {}).get("version"),
        session_todos=flow.current_todos(data.get("session")),
    )
    superseded = flow.supersede_open_tasks(ws, record["id"])
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
    # submit 即快照会话 todos 进任务行（plan-mode submit_plan 同款），resume 才有得恢复
    flow.update_deliverable(int(tid), deliverable,
                            brief=str(data.get("brief") or "") or None,
                            todos=flow.current_todos(data.get("session")))
    updated = flow.set_status(int(tid), "submitted",
                              reason=str(data.get("note") or "") or None)
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
        flow.set_status(int(tid), "approved", reason=notes or None)
        try:
            out["deliverable_path"] = flow.write_deliverable_file(
                record["workspace"], int(tid), record["deliverable_md"])
        except OSError as e:
            out["warnings"] = [f"交付物文件写入失败：{e}"]
        out["guidance"] = (
            "评审通过、任务收口。交付后提醒用户：后续修改尽量交给 Agent 而非手动改——"
            "每次修改都是一次偏好沉淀机会（twin.write，注明来源交付物）。")
    else:
        flow.set_status(int(tid), "rejected", reason=notes or None)
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
    flow.set_status(int(tid), "pending", reason=str(data.get("reason") or "") or None)
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
    conn = db.connect()
    dims = {k: {"ok": bool(record.get(k)), "kind": k, "raw": record.get(f"{k}_raw") or "",
                "code": record.get(k), "matched_by": "db_alias" if record.get(k) else None}
            for k in taxonomy.KINDS}
    persona = _task_persona(conn, record["workspace"], record.get("work_type"))
    new_record = flow.insert_task(
        workspace=record["workspace"], brief=record["brief"], status="planning",
        dims=dims, interpreted_intent=record.get("interpreted_intent"),
        deliverable_md=record.get("deliverable_md") or "",
        reason=f"resumed from task #{tid}",
        persona_version=(persona or {}).get("version"),
        parent_task_id=int(tid),
        session_todos=flow.current_todos(data.get("session")),
    )
    flow.supersede_open_tasks(record["workspace"], new_record["id"])
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
    child = flow.insert_task(
        workspace=record["workspace"], brief=new_brief or record["brief"],
        status=record["status"], dims=dims,
        interpreted_intent=record.get("interpreted_intent"),
        deliverable_md=deliverable or record.get("deliverable_md") or "",
        reason=revision_reason or None,
        persona_version=record.get("persona_version"),
        parent_task_id=int(tid), iteration=int(record.get("iteration") or 0) + 1,
        revision_reason=revision_reason or None,
        session_todos=flow.current_todos(data.get("session")),
    )
    flow.set_status(int(tid), "superseded", reason=f"revised by task #{child['id']}")
    flow.supersede_open_tasks(record["workspace"], child["id"])
    return {"ok": True, "parent_task_id": int(tid), "task_id": child["id"],
            "iteration": child["iteration"], "status": child["status"],
            "guidance": (f"已生成修订版任务 #{child['id']}（第 {child['iteration']} 次修订，"
                         "继承原状态）。继续执行后 task_submit。")}


def _action_task_recent(data: dict) -> dict:
    flow.ensure_schema()
    limit = int(data.get("limit") or 10)
    rows = flow.recent_tasks(_workspace(data), limit)
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
    return scan.run_scan(_workspace(data))


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
            "write": "沉淀一条工作偏好。必填 content/work_type/audience/purpose（原始值即可，产品归一）；"
                     "可选 subject/tags/source_ref/workspace。",
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
                          "返回该工作性质的 persona prompt 与前置清单，开放任务自动让位。",
            "task_submit": "提交交付稿待评审。必填 task_id/deliverable_md；可带 todos/session。",
            "task_review": "评审裁定（append-only 审计）。task_id + verdict ∈ approved|changes_requested，"
                           "notes 记意见；approved 落交付物文件，changes 走 rejected 并提示沉淀偏好。",
            "task_pending": "评审搁置（中断未决）。task_id。",
            "task_resume": "续作历史任务（可中断可继续）。task_id；恢复 todos、新建 planning 任务并再注入 persona。",
            "task_revise": "修订进行中的任务。task_id + brief/deliverable_md/revision_reason 至少其一；"
                           "子任务继承状态并记 lineage。",
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
    "task_recent": _action_task_recent,
    "task_get": _action_task_get,
    "todo": _action_todo,
    "scan": _action_scan,
    "help": _action_help,
}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
