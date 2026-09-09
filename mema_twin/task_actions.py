"""交付任务流动作（v0.3.8 从 server.py 拆出）：task_start / task_submit /
task_resume / task_revise / task_close / task_recent / task_get / todo + 注入
helpers（persona 注入 / 未编译增补 / 受众画像）。

v0.3.8：评审环删除（task_submit 即终点并落盘交付物文件，task_review/
task_pending 退役）；task_start 经归一门（三维度未命中整笔打回 + 裁定票据，
建档之前，无半建任务）；双跑提议（persona_compare_offer）删除。
"""
from __future__ import annotations

import sqlite3

from . import db, flow, identity, normalize, store, taxonomy
from .compile_actions import _read_evidence_rows


def _task_persona(conn, code: str | None) -> dict | None:
    if not code:
        return None
    return store.get_active(conn, code)


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
        misses: list[dict] = []
        for kind in taxonomy.KINDS:
            raw = str(data.get(kind) or "").strip()
            if not raw:
                if kind == "work_type":
                    return {"ok": False, "error": "invalid_input", "field": kind, "reason": "required"}
                # audience/purpose 可选（v0.3.8 维持）：未提供不归一、不注入画像
                dims[kind] = {"ok": False, "kind": kind, "raw": "", "code": None, "matched_by": None}
                continue
            if len(raw) > 200:
                # 轮2 P3-8：与 write 同款限长，超长 raw 不入库
                return {"ok": False, "error": "invalid_input", "field": kind,
                        "reason": "过长（上限 200 字符）"}
            # 归一门（v0.3.8）：未命中收集后整笔打回（建档之前，无半建任务）
            r = normalize.normalize_value(kind, raw, conn)
            if r.get("error"):
                return {"ok": False, "error": "invalid_input", "field": kind,
                        "reason": r.get("reason")}
            dims[kind] = r
            if not r.get("ok"):
                misses.append(r)
        if misses:
            gate = normalize.gate_reject(misses)
            if gate is not None:
                return gate
            # 全部被并发裁定：dims 已被 gate_reject 就地改写为命中，继续建档
        code = dims["work_type"]["code"]
        persona = _task_persona(conn, code)
    finally:
        conn.close()
    record = flow.insert_task(
        brief=brief, status="planning", dims=dims,
        interpreted_intent=str(data.get("interpreted_intent") or "") or None,
        persona_version=(persona or {}).get("version"),
        client=identity.effective_client(data),
        session_todos=flow.current_todos(data.get("session")),
    )
    superseded = flow.supersede_open_tasks(record["id"])
    # 增补取数放在建档/让位之后（轮2 P3-7）：慢 mema 读不再拉长并发让位竞窗
    supplement = _supplement_payload(code, persona,
                                     client=identity.effective_client(data)) if code else {}
    out: dict = {
        "ok": True, "task_id": record["id"], "status": "planning",
        "superseded_open_tasks": superseded,
        "dimensions": dims,
        "guidance": (
            "任务已建档。按 persona prompt 的偏好/结构/前置清单执行；材料不齐全先向"
            "用户确认或补齐。完成后 twin(action=\"task_submit\") 交付收口（submit 即终点）。"),
    }
    # 受众画像注入：与 persona/增补独立，任何分支都随响应进场（audience 未归一则空）
    aud_dim = dims.get("audience") or {}
    aud_code = aud_dim.get("code") if aud_dim.get("ok") else None
    if aud_code:
        out.update(_audience_payload(aud_code, code, client=identity.effective_client(data)))
    if persona:
        out.update(_persona_injection(persona, have))
        out.update(supplement)
    elif supplement:
        # 空 persona 分支（#905-A 拍板：给）：原始证据当雏形注入，第一天就有分身效果
        out.update(supplement)
        out["hint"] = ("该工作性质尚无编译版 persona，本次按上方已沉淀偏好执行；"
                       "积累后可 twin(action=\"compile\") 生成 v1")
    else:
        out["hint"] = ("该工作性质尚无 persona prompt；可先喂历史产出物或积累偏好后 "
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
    if record["status"] != "planning":
        return {"ok": False, "error": "invalid_input",
                "reason": f"task {tid} 状态为 {record['status']!r}，不可提交"
                          "（submitted 已是终态；交付后返工走 task_revise）"}
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
                              allowed_from=("planning",))
    out: dict = {"ok": True, "task_id": int(tid), "status": "submitted",
                 "guidance": ("已交付收口（task_submit 即终点）。用户对交付稿的修改与意见是"
                              "偏好信号：有反馈就 twin.write 沉淀（注明来源交付物）；"
                              "需要返工走 task_revise 生成修订任务。")}
    # 落盘在状态迁移之后（评审 P2-A5）：正文已在库，文件只是审计镜像；重读库内
    # 最新防并发写错版本（review#5 同款）；OSError/sqlite 失败降级 warning 不回滚
    fresh = flow.get_task(int(tid)) or updated
    try:
        out["deliverable_path"] = flow.write_deliverable_file(
            int(tid), fresh.get("deliverable_md") or "")
    except (OSError, sqlite3.Error) as e:
        out["warnings"] = [f"交付物文件写入失败（正文已在库）: {e}"]
    return out


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
        if record["status"] == "submitted":
            hint = ("已交付（submitted）的返工走 task_revise——注意 revise 不恢复 todos、"
                    "不重注入 persona/增补/受众画像，需要时以新任务视角重新 task_start")
        else:
            hint = "历史遗留状态（v0.3.8 前的评审环终态），无迁移入口；继续该工作请重新 task_start"
        return {"ok": False, "error": "invalid_input",
                "reason": f"task {tid} 状态为 {record['status']!r}，仅 planning 可续作；{hint}"}
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
                                     client=identity.effective_client(data)) if resume_code else {}
    new_record = flow.insert_task(
        brief=record["brief"], status="planning",
        dims=dims, interpreted_intent=record.get("interpreted_intent"),
        deliverable_md=record.get("deliverable_md") or "",
        reason=f"resumed from task #{tid}",
        persona_version=(persona or {}).get("version"),
        parent_task_id=int(tid),
        client=identity.effective_client(data),
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
                                     client=identity.effective_client(data)))
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
        if record["status"] == "planning":
            hint = "进行中（planning）直接继续执行或 task_resume"
        else:
            hint = "历史遗留状态（v0.3.8 前的评审环终态），无迁移入口；继续该工作请重新 task_start"
        return {"ok": False, "error": "invalid_input",
                "reason": f"task {tid} 状态为 {record['status']!r}，仅已交付（submitted）"
                          f"可修订返工；{hint}"}
    dims = {k: {"ok": bool(record.get(k)), "kind": k, "raw": record.get(f"{k}_raw") or "",
                "code": record.get(k), "matched_by": "db_alias" if record.get(k) else None}
            for k in taxonomy.KINDS}
    # 对抗 review#8：修订=返工，子任务一律 planning 重走执行→提交，
    # 不继承终态（否则出现"从未被交付确认的收口"审计伪造）
    child = flow.insert_task(
        brief=new_brief or record["brief"],
        status="planning", dims=dims,
        interpreted_intent=record.get("interpreted_intent"),
        deliverable_md=deliverable or record.get("deliverable_md") or "",
        reason=revision_reason or None,
        persona_version=record.get("persona_version"),
        parent_task_id=int(tid), iteration=int(record.get("iteration") or 0) + 1,
        revision_reason=revision_reason or None,
        client=identity.effective_client(data),
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
                "reason": f"task {tid} 状态为 {record['status']!r}，仅进行中（planning）可关闭"}
    flow.set_status(int(tid), "superseded",
                    reason=str(data.get("reason") or "closed") or None,
                    allowed_from=flow._OPEN_STATUSES)
    return {"ok": True, "task_id": int(tid), "status": "superseded",
            "guidance": "任务已显式关闭（历史保留可审计）。"}


def _action_task_recent(data: dict) -> dict:
    flow.ensure_schema()
    try:
        limit = int(data.get("limit") or 10)
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid_input", "field": "limit",
                "reason": "limit 需是整数"}  # 评审轮2 P3-1：脏类型不再落 internal_error
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
    return {"ok": True, "task": record}


def _action_todo(data: dict) -> dict:
    if data.get("todos") is None:
        return {"ok": True, "todos": flow.current_todos(data.get("session"))}
    return flow.set_session_todos(data.get("session"), data.get("todos"))
