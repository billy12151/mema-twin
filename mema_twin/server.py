"""mema-twin MCP server：单工具 twin(action, data)，动作式紧凑接口（仿 mema/plan-mode 风格）。"""
from __future__ import annotations

import datetime as _dt
import os

from mcp.server.fastmcp import FastMCP

from . import db, normalize, sink, store, taxonomy, templates

mcp = FastMCP("mema-twin")

_KIND_PREFIX = {"work_type": "wt", "audience": "au", "purpose": "pu"}


@mcp.tool()
def twin(action: str, data: dict | None = None) -> dict:
    """个人分身 twin：按工作性质沉淀用户工作偏好，编译版本化 persona prompt 并注入工作流。

    动作：write / status / compile / submit / get / taxonomy / pending / resolve / help。
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
    if ok and dims["work_type"].get("ok"):
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
    return {"ok": True, "workspace": ws, "prompts": list(versions.values()),
            "pending_count": len(pending)}


def _fetch_evidence(conn, ws: str, code: str) -> list[dict]:
    # mema find 暂无 tag 精确过滤：语义召回后按 twin:wt:<code> 标签客户端过滤（阶段 0 已知局限）
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
    evidence = _fetch_evidence(conn, ws, code)
    material = templates.compile_prompt_material(code, t.zh if t else code, active, evidence)
    return {"ok": True, "work_type": code,
            "current_version": (active or {}).get("version"),
            "evidence_count": len(evidence), "material": material,
            "note": templates.STRONG_MODEL_NOTE,
            "next": "用当前会话模型按素材包编译出 prompt_md 后，调 "
                    "twin(action=\"submit\", data={work_type, prompt_md, source_memory_ids, model})"}


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
    rec["ok"] = True
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


def _action_help(data: dict) -> dict:
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
    "help": _action_help,
}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
