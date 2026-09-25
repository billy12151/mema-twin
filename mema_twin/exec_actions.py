"""执行计划层动作（v0.4 P1/P2）：plan_set / step_update / plan_revise / tool_log
+ 步骤六态状态机、三门（blocking 疑问 → 依赖 → 单一 in_progress）、收口门 helper
（task_submit 调用）、task_close 批量跳过、resume/revise 计划深拷贝。

设计定稿见 mema-twin-v0.4-exec-design-2026-09-26.md §4：硬约束只留两个——
reflection 必填（in_progress→failed）与收口门；追认式打卡允许（pending 直跳
done/failed 自动标 backfilled）；「建过计划」由 plan_steps 行存在性判定，不动
任务状态机。
"""
from __future__ import annotations

import json

from . import db, flow, identity

STEP_STATUSES = ("pending", "in_progress", "done", "failed", "blocked", "skipped")
STEP_OPEN_STATUSES = ("pending", "in_progress", "blocked", "failed")
STEP_CLOSED_STATUSES = ("done", "skipped")  # 闭环 = done ∪ skipped；failed 必须重开或跳过
TOOL_OUTCOMES = ("success", "fail", "degraded")

_TITLE_MAX = 200
_DESC_MAX = 2000
_QUESTION_MAX = 500
_NOTE_MAX = 500
_ENTRY_MAX = 50
_REFLECTION_MAX = 2000
_REASON_MAX = 500
_TOOL_NAME_MAX = 100
_PURPOSE_MAX = 500

# 迁移合法性表：key=(from, to)。backfill=True 的迁移自动标追认；reason_required /
# reflection_required 为对应字段必填的迁移。未列出的组合一律打回。
_TRANSITIONS: dict[tuple[str, str], dict] = {}
for _to in ("in_progress", "done", "failed", "blocked", "skipped"):
    _TRANSITIONS[("pending", _to)] = {"backfill": _to in ("done", "failed")}
_TRANSITIONS[("pending", "skipped")] = {"reason_required": True}  # 跳过必带原因（v3.3 ⑧）
_TRANSITIONS[("in_progress", "pending")] = {}
_TRANSITIONS[("in_progress", "done")] = {}
_TRANSITIONS[("in_progress", "failed")] = {"reflection_required": True}
_TRANSITIONS[("in_progress", "blocked")] = {"reason_required": True}
_TRANSITIONS[("in_progress", "skipped")] = {"reason_required": True}
_TRANSITIONS[("done", "pending")] = {"reason_required": True}  # 撤销完成（审计）
_TRANSITIONS[("done", "skipped")] = {"reason_required": True}
_TRANSITIONS[("failed", "in_progress")] = {}  # 原步骤重试
_TRANSITIONS[("failed", "done")] = {}
_TRANSITIONS[("failed", "blocked")] = {"reason_required": True}
_TRANSITIONS[("failed", "skipped")] = {"reason_required": True}
_TRANSITIONS[("blocked", "pending")] = {}
_TRANSITIONS[("blocked", "in_progress")] = {}
_TRANSITIONS[("blocked", "done")] = {}
_TRANSITIONS[("blocked", "failed")] = {}
_TRANSITIONS[("blocked", "skipped")] = {"reason_required": True}
_TRANSITIONS[("skipped", "pending")] = {}  # 撤销跳过
_TRANSITIONS[("skipped", "in_progress")] = {}
_TRANSITIONS[("skipped", "done")] = {}
_TRANSITIONS[("skipped", "failed")] = {}
_TRANSITIONS[("skipped", "blocked")] = {"reason_required": True}

_FAILED_GUIDANCE = (
    "步骤已标记失败（reflection 已记录，夜间评估会挖掘「失败→对策」模式）。"
    "三选一：① 原步骤重试（step_update(in_progress)）；② 换工具/路径重做"
    "（tool_log 记录失败与降级，另开新步骤）；③ 计划本身要改（plan_revise）。"
    "该步骤未闭环，收口前须落到 done 或 skipped。")


def _loads_ids(text: str | None) -> list[int]:
    try:
        raw = json.loads(text or "[]")
    except (ValueError, TypeError):
        return []
    return [int(i) for i in raw] if isinstance(raw, list) else []


def _dump_ids(ids: list[int]) -> str:
    return json.dumps([int(i) for i in ids])


def _step_dict(row) -> dict:
    d = dict(row)
    d["depends_on"] = _loads_ids(d.get("depends_on"))
    return d


def get_step(step_id: int) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM twin_plan_steps WHERE id=?",
                           (int(step_id),)).fetchone()
        return _step_dict(row) if row else None
    finally:
        conn.close()


def task_steps(conn, task_id: int, statuses: tuple[str, ...] | None = None) -> list[dict]:
    if statuses:
        ph = ",".join("?" for _ in statuses)
        rows = conn.execute(
            f"SELECT * FROM twin_plan_steps WHERE task_id=? AND status IN ({ph})"
            f" ORDER BY seq, id", (int(task_id), *statuses)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM twin_plan_steps WHERE task_id=? ORDER BY seq, id",
            (int(task_id),)).fetchall()
    return [_step_dict(r) for r in rows]


def has_plan(conn, task_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM twin_plan_steps WHERE task_id=? LIMIT 1",
        (int(task_id),)).fetchone() is not None


def _task_plan_status(conn, task_id: int) -> str | None:
    row = conn.execute("SELECT status FROM twin_tasks WHERE id=?",
                       (int(task_id),)).fetchone()
    return row["status"] if row else None


# ---- 三门（只在推进 in_progress 时拦；顺序：blocking 疑问 → 依赖 → 单一 in_progress）----

def _gate_blocking_questions(conn, task_id: int, step_id: int) -> dict | None:
    rows = conn.execute(
        "SELECT id, question, step_ids FROM twin_plan_questions"
        " WHERE task_id=? AND status='open' AND blocking=1 ORDER BY id",
        (int(task_id),)).fetchall()
    hits = []
    for r in rows:
        if step_id in _loads_ids(r["step_ids"]):
            hits.append({"id": r["id"], "question": r["question"],
                         "step_ids": _loads_ids(r["step_ids"])})
    if not hits:
        return None
    q = hits[0]
    return {"ok": False, "error": "invalid_input",
            "reason": (f"步骤 #{step_id} 被未解答的 blocking 疑问 #{q['id']} 关联："
                       "先与 owner 澄清，答复后 twin(action=\"plan_revise\", data="
                       f"{{\"task_id\": {int(task_id)}, \"answers\": "
                       f"[{{\"question_id\": {q['id']}, \"answer\": \"…\"}}]}}) 写回并解锁"),
            "blocking_questions": hits}


def _gate_dependencies(conn, step: dict) -> dict | None:
    deps = step.get("depends_on") or []
    if not deps:
        return None
    ph = ",".join("?" for _ in deps)
    rows = conn.execute(
        f"SELECT id, title, status FROM twin_plan_steps WHERE id IN ({ph})",
        deps).fetchall()
    open_deps = [{"id": r["id"], "title": r["title"], "status": r["status"]}
                 for r in rows if r["status"] not in STEP_CLOSED_STATUSES]
    if not open_deps:
        return None
    first = open_deps[0]
    return {"ok": False, "error": "invalid_input",
            "reason": (f"步骤 #{step['id']}「{step['title']}」依赖 #{first['id']}"
                       f"「{first['title']}」未闭环（当前 {first['status']}），不可推进"),
            "open_steps": open_deps}


def _gate_single_in_progress(conn, task_id: int, exclude_step_id: int) -> dict | None:
    rows = conn.execute(
        "SELECT id, title FROM twin_plan_steps"
        " WHERE task_id=? AND status='in_progress' AND id!=? ORDER BY id",
        (int(task_id), int(exclude_step_id))).fetchall()
    if not rows:
        return None
    return {"ok": False, "error": "invalid_input",
            "reason": (f"该任务已有进行中步骤 #{rows[0]['id']}「{rows[0]['title']}」"
                       "（单一 in_progress）；先将其落到 done/failed/skipped 再开新步骤"),
            "in_progress_steps": [{"id": r["id"], "title": r["title"]} for r in rows]}


# ---- 迁移执行（step_update 与 plan_revise.steps_update 共用）----

def _apply_step_transition(conn, step: dict, to: str, reason: str | None,
                           reflection: str | None) -> dict:
    """校验并执行单步迁移。成功返回分支信息 dict（含 guidance），失败抛 ValueError
    （边界转 invalid_input）。不 commit——事务边界由调用方定。"""
    frm = step["status"]
    if to not in STEP_STATUSES:
        raise ValueError(f"invalid status: {to!r}（六态：{'/'.join(STEP_STATUSES)}）")
    rule = _TRANSITIONS.get((frm, to))
    if rule is None:
        raise ValueError(f"步骤 #{step['id']} 不允许从 {frm!r} 迁到 {to!r}")
    reason = (reason or "").strip() or None
    reflection = (reflection or "").strip() or None
    if rule.get("reason_required") and not reason:
        raise ValueError(f"迁移到 {to!r} 需要 reason（写明为什么）")
    if rule.get("reflection_required") and not reflection:
        raise ValueError("失败步骤必须写 reflection（发生了什么/为什么失败/下次怎么办）——"
                         "这是服务端硬约束，夜间评估靠它挖掘「失败→对策」模式")
    # 长度帽（对抗评审轮2 P2-5）：reflection/reason 进 task_evaluate 素材包，无帽失控
    if len(reason or "") > _REASON_MAX:
        raise ValueError(f"reason 过长（上限 {_REASON_MAX} 字符）")
    if len(reflection or "") > _REFLECTION_MAX:
        raise ValueError(f"reflection 过长（上限 {_REFLECTION_MAX} 字符）")
    task_id = int(step["task_id"])
    if to == "in_progress":
        gate = (_gate_blocking_questions(conn, task_id, int(step["id"]))
                or _gate_dependencies(conn, step)
                or _gate_single_in_progress(conn, task_id, int(step["id"])))
        if gate is not None:
            raise _GateReject(gate)
    ts = db.now_iso()
    sql = ("UPDATE twin_plan_steps SET status=?, reason=COALESCE(?, reason),"
           " reflection=COALESCE(?, reflection), backfilled=?,"
           " decided_at=COALESCE(decided_at, ?)"
           " WHERE id=? AND status=?")
    params: list = [to, reason, reflection,
                    1 if rule.get("backfill") else int(step.get("backfilled") or 0),
                    ts, int(step["id"]), frm]
    if to == "in_progress":
        # 单一 in_progress 不变量折进条件 UPDATE：先查后改的进程内门存在跨宿主
        # 竞窗（两宿主并发推进两个不同步骤都能过门），写入瞬间原子复查兜底
        sql += (" AND NOT EXISTS(SELECT 1 FROM twin_plan_steps"
                " WHERE task_id=? AND status='in_progress' AND id!=?)")
        params += [task_id, int(step["id"])]
    cur = conn.execute(sql, params)
    if cur.rowcount == 0:
        fresh = conn.execute("SELECT status FROM twin_plan_steps WHERE id=?",
                             (int(step["id"]),)).fetchone()
        if fresh is None or fresh["status"] != frm:
            raise ValueError(
                f"步骤 #{step['id']} 状态已并发变更为 {fresh['status'] if fresh else '?'}"
                f"（期望 {frm!r}），请重读后重试")
        gate = _gate_single_in_progress(conn, task_id, int(step["id"])) or {
            "ok": False, "error": "invalid_input",
            "reason": (f"步骤 #{step['id']} 推进失败：并发窗口内另一步骤已进入"
                       " in_progress，请重读后重试")}
        raise _GateReject(gate)
    out: dict = {"step_id": int(step["id"]), "task_id": task_id, "status": to,
                 "backfilled": bool(rule.get("backfill"))}
    if to == "failed":
        out["guidance"] = _FAILED_GUIDANCE
    elif to == "done":
        out["guidance"] = "步骤完成。继续下一步，或全部闭环后 task_submit 收口。"
    return out


class _GateReject(Exception):
    """三门拒绝：携带完整响应 dict（区别于普通 ValueError 的 reason-only）。"""

    def __init__(self, payload: dict):
        super().__init__(payload.get("reason") or "gate reject")
        self.payload = payload


def _task_planning_guard(conn, task_id: int, need_plan: bool = False) -> dict | None:
    """task 存在且 planning；need_plan 时另要求建过计划。失败返回打回响应。"""
    status = _task_plan_status(conn, task_id)
    if status is None:
        return {"ok": False, "error": "not_found", "reason": f"task id {task_id}"}
    if status != "planning":
        return {"ok": False, "error": "invalid_input",
                "reason": f"task {task_id} 状态为 {status!r}，仅进行中（planning）可操作计划"}
    if need_plan and not has_plan(conn, task_id):
        return {"ok": False, "error": "invalid_input",
                "reason": (f"任务 #{task_id} 尚未建计划（先 plan_set）——疑问与步骤修订"
                           "都是计划的一部分")}
    return None


# ---- 环检测（节点→依赖邻接表；返回环路径节点序列）----

def _find_cycle(edges: dict[int, list[int]]) -> list[int] | None:
    """迭代 DFS（对抗评审轮2 P3-1：递归版在 ~1000 步正向依赖链上 RecursionError
    抛穿错误边界）。返回环路径节点序列。"""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in edges}
    for start in list(edges):
        if color[start] != WHITE:
            continue
        color[start] = GRAY
        stack = [(start, iter(edges.get(start, ())))]
        path = [start]
        while stack:
            node, children = stack[-1]
            advanced = False
            for m in children:
                if m not in color:
                    continue
                if color[m] == GRAY:
                    return path[path.index(m):] + [m]
                if color[m] == WHITE:
                    color[m] = GRAY
                    stack.append((m, iter(edges.get(m, ()))))
                    path.append(m)
                    advanced = True
                    break
            if not advanced:
                color[node] = BLACK
                stack.pop()
                path.pop()
    return None


def _validate_dep_targets(edges: dict[int, list[int]], valid: set[int],
                          label: str) -> dict | None:
    bad = sorted({d for ds in edges.values() for d in ds if d not in valid})
    if bad:
        return {"ok": False, "error": "invalid_input",
                "reason": f"{label} 引用了不存在的步骤 id: {bad}"}
    cycle = _find_cycle(edges)
    if cycle:
        return {"ok": False, "error": "invalid_input",
                "reason": f"步骤依赖成环: {' -> '.join(f'#{i}' for i in cycle)}"}
    return None


def _coerce_task_id(data: dict) -> int:
    raw = data.get("task_id")
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        raise ValueError(f"invalid task_id: {raw!r}")
    n = int(raw)
    if str(n) != str(raw).strip() and not isinstance(raw, int):
        raise ValueError(f"invalid task_id: {raw!r}")
    return n


# ---- action: plan_set ----

def _action_plan_set(data: dict) -> dict:
    try:
        task_id = _coerce_task_id(data)
    except ValueError as e:
        return {"ok": False, "error": "invalid_input", "reason": str(e)}
    steps_in = data.get("steps")
    if not isinstance(steps_in, list) or not steps_in:
        return {"ok": False, "error": "invalid_input", "field": "steps",
                "reason": "required（非空步骤列表）"}
    parsed: list[dict] = []
    for i, s in enumerate(steps_in):
        if not isinstance(s, dict):
            return {"ok": False, "error": "invalid_input", "field": f"steps[{i}]",
                    "reason": "必须是对象"}
        title = str(s.get("title") or "").strip()
        if not title:
            return {"ok": False, "error": "invalid_input", "field": f"steps[{i}].title",
                    "reason": "required"}
        if len(title) > _TITLE_MAX:
            return {"ok": False, "error": "invalid_input", "field": f"steps[{i}].title",
                    "reason": f"过长（上限 {_TITLE_MAX} 字符）"}
        desc = str(s.get("description") or "").strip()
        if len(desc) > _DESC_MAX:
            return {"ok": False, "error": "invalid_input",
                    "field": f"steps[{i}].description",
                    "reason": f"过长（上限 {_DESC_MAX} 字符）"}
        deps = s.get("depends_on") or []
        if not isinstance(deps, list) or any(isinstance(d, bool) or not isinstance(d, int) for d in deps):
            return {"ok": False, "error": "invalid_input",
                    "field": f"steps[{i}].depends_on",
                    "reason": "必须是步骤序号（1-based）整数列表"}
        if any(d == i + 1 for d in deps):
            return {"ok": False, "error": "invalid_input",
                    "field": f"steps[{i}].depends_on", "reason": "步骤不可依赖自身"}
        if any(not (1 <= d <= len(steps_in)) for d in deps):
            return {"ok": False, "error": "invalid_input",
                    "field": f"steps[{i}].depends_on",
                    "reason": f"序号超出范围 1..{len(steps_in)}"}
        parsed.append({"title": title, "description": desc,
                       "deps_seq": [int(d) for d in deps]})
    edges = {i + 1: parsed[i]["deps_seq"] for i in range(len(parsed))}
    bad = _validate_dep_targets({k: v for k, v in edges.items()},
                                set(edges), "depends_on")
    if bad:
        return bad
    questions_in = data.get("open_questions") or []
    if not isinstance(questions_in, list):
        return {"ok": False, "error": "invalid_input", "field": "open_questions",
                "reason": "必须是列表"}
    parsed_q: list[dict] = []
    for i, q in enumerate(questions_in):
        if not isinstance(q, dict):
            return {"ok": False, "error": "invalid_input",
                    "field": f"open_questions[{i}]", "reason": "必须是对象"}
        text = str(q.get("question") or "").strip()
        if not text:
            return {"ok": False, "error": "invalid_input",
                    "field": f"open_questions[{i}].question", "reason": "required"}
        if len(text) > _QUESTION_MAX:
            return {"ok": False, "error": "invalid_input",
                    "field": f"open_questions[{i}].question",
                    "reason": f"过长（上限 {_QUESTION_MAX} 字符）"}
        qids = q.get("step_ids") or []
        if not isinstance(qids, list) or any(
                isinstance(x, bool) or not isinstance(x, int) for x in qids):
            return {"ok": False, "error": "invalid_input",
                    "field": f"open_questions[{i}].step_ids",
                    "reason": "必须是步骤序号（1-based）整数列表"}
        if any(not (1 <= x <= len(steps_in)) for x in qids):
            return {"ok": False, "error": "invalid_input",
                    "field": f"open_questions[{i}].step_ids",
                    "reason": f"序号超出范围 1..{len(steps_in)}"}
        blocking_raw = q.get("blocking", False)
        if isinstance(blocking_raw, bool):
            blocking = 1 if blocking_raw else 0
        elif isinstance(blocking_raw, int) and blocking_raw in (0, 1):
            blocking = blocking_raw
        else:
            return {"ok": False, "error": "invalid_input",
                    "field": f"open_questions[{i}].blocking",
                    "reason": "需是布尔值 true/false（字符串 \"false\" 不接受——会被误判为 true）"}
        if blocking and not qids:
            # blocking 疑问不锚定步骤就永远拦不住任何推进（对抗评审轮1 P3-7）
            return {"ok": False, "error": "invalid_input",
                    "field": f"open_questions[{i}].step_ids",
                    "reason": "blocking=true 的疑问必须关联至少一个步骤"}
        parsed_q.append({"question": text, "seqs": [int(x) for x in qids],
                         "blocking": blocking})
    flow.ensure_schema()
    conn = db.connect()
    try:
        guard = _task_planning_guard(conn, task_id)
        if guard:
            return guard
        replanned_rows = conn.execute(
            f"UPDATE twin_plan_steps SET status='skipped', reason='replanned',"
            f" decided_at=COALESCE(decided_at, ?)"
            f" WHERE task_id=? AND status IN ({','.join('?' * len(STEP_OPEN_STATUSES))})",
            (db.now_iso(), task_id, *STEP_OPEN_STATUSES))
        replanned = replanned_rows.rowcount
        # 旧 open 疑问随计划重建一并废弃（answered 保留历史）——不设 replanned 条件：
        # 全闭环但有遗留 open 疑问的任务重建计划时同样要清（对抗评审轮2 P3-4）
        conn.execute(
            "DELETE FROM twin_plan_questions WHERE task_id=? AND status='open'",
            (task_id,))
        ts = db.now_iso()
        out_steps = []
        for i, s in enumerate(parsed):
            cur = conn.execute(
                "INSERT INTO twin_plan_steps"
                "(task_id, seq, title, description, depends_on, status, created_at)"
                " VALUES(?,?,?,?,?,'pending',?)",
                (task_id, i + 1, s["title"], s["description"], "[]", ts))
            out_steps.append({"id": int(cur.lastrowid), "seq": i + 1,
                              "title": s["title"], "depends_on_seq": s["deps_seq"]})
        id_by_seq = {s["seq"]: s["id"] for s in out_steps}
        for s in out_steps:
            if s["depends_on_seq"]:
                conn.execute(
                    "UPDATE twin_plan_steps SET depends_on=? WHERE id=?",
                    (_dump_ids([id_by_seq[x] for x in s["depends_on_seq"]]), s["id"]))
                s["depends_on"] = [id_by_seq[x] for x in s["depends_on_seq"]]
            s.pop("depends_on_seq")
        out_q = []
        for q in parsed_q:
            cur = conn.execute(
                "INSERT INTO twin_plan_questions"
                "(task_id, question, step_ids, blocking, status, created_at)"
                " VALUES(?,?,?,?,'open',?)",
                (task_id, q["question"],
                 _dump_ids([id_by_seq[x] for x in q["seqs"]]),
                 q["blocking"], ts))
            out_q.append({"id": int(cur.lastrowid), "question": q["question"],
                          "step_ids": [id_by_seq[x] for x in q["seqs"]],
                          "blocking": bool(q["blocking"]), "status": "open"})
        conn.commit()
    finally:
        conn.close()
    out: dict = {"ok": True, "task_id": task_id, "replanned": replanned > 0,
                 "steps": out_steps, "open_questions": out_q,
                 "guidance": (
                     "计划已建，可直接开工（无需等确认）。步骤推进用 step_update；"
                     "对没把握、或你确认不了的点已列入 open_questions（要紧的标了 "
                     "blocking）先与 owner 澄清，答复经 plan_revise(answers=…) 写回。"
                     "完成后 task_submit 收口（未闭环步骤会被拦）。"
                     + (f"（重建计划：{replanned} 个旧未完结步骤已按 replanned 跳过）"
                        if replanned else ""))}
    if replanned:
        out["replanned_steps"] = replanned
    return out


# ---- action: step_update ----

def _action_step_update(data: dict) -> dict:
    sid = data.get("step_id")
    if isinstance(sid, bool) or not isinstance(sid, (int, str)):
        return {"ok": False, "error": "invalid_input",
                "reason": f"invalid step_id: {sid!r}"}
    try:
        step_id = int(sid)
    except ValueError:
        return {"ok": False, "error": "invalid_input", "reason": f"invalid step_id: {sid!r}"}
    if step_id < 0 or step_id > 2**63 - 1:  # 巨整数进 SQLite 绑定会抛 OverflowError
        return {"ok": False, "error": "invalid_input", "reason": f"invalid step_id: {sid!r}"}
    to = str(data.get("status") or "").strip()
    flow.ensure_schema()
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM twin_plan_steps WHERE id=?", (step_id,)).fetchone()
        if row is None:
            return {"ok": False, "error": "not_found", "reason": f"step id {step_id}"}
        step = _step_dict(row)
        guard = _task_planning_guard(conn, int(step["task_id"]))
        if guard:
            return guard
        try:
            branch = _apply_step_transition(conn, step, to,
                                            str(data.get("reason") or ""),
                                            str(data.get("reflection") or ""))
        except _GateReject as e:
            conn.rollback()
            return e.payload
        except ValueError as e:
            conn.rollback()
            return {"ok": False, "error": "invalid_input", "reason": str(e)}
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, **branch}


# ---- action: plan_revise ----

def _action_plan_revise(data: dict) -> dict:
    try:
        task_id = _coerce_task_id(data)
    except ValueError as e:
        return {"ok": False, "error": "invalid_input", "reason": str(e)}
    steps_add = data.get("steps_add") or []
    steps_update = data.get("steps_update") or []
    steps_remove = data.get("steps_remove") or []
    answers = data.get("answers") or []
    if not (steps_add or steps_update or steps_remove or answers
            or str(data.get("revision_reason") or "").strip()):
        return {"ok": False, "error": "invalid_input",
                "reason": "至少给 steps_add / steps_update / steps_remove / answers /"
                          " revision_reason 之一"}
    for name, lst in (("steps_add", steps_add), ("steps_update", steps_update),
                      ("answers", answers)):
        if not isinstance(lst, list):
            return {"ok": False, "error": "invalid_input", "field": name,
                    "reason": "必须是列表"}
    if not isinstance(steps_remove, list) or any(
            isinstance(x, bool) or not isinstance(x, int) for x in steps_remove):
        return {"ok": False, "error": "invalid_input", "field": "steps_remove",
                "reason": "必须是步骤 id 整数列表"}
    flow.ensure_schema()
    conn = db.connect()
    try:
        guard = _task_planning_guard(conn, task_id, need_plan=True)
        if guard:
            return guard
        removed: list[int] = []
        # ① 删除：仅 pending 可删；不可被任何本代未删步骤依赖（评审 v3.3 ⑧）
        remove_set = {int(x) for x in steps_remove}
        for rid in sorted(remove_set):
            row = conn.execute(
                "SELECT id, status FROM twin_plan_steps WHERE id=? AND task_id=?",
                (rid, task_id)).fetchone()
            if row is None:
                conn.rollback()
                return {"ok": False, "error": "invalid_input",
                        "reason": f"步骤 #{rid} 不存在于任务 #{task_id}"}
            if row["status"] != "pending":
                conn.rollback()
                return {"ok": False, "error": "invalid_input",
                        "reason": (f"步骤 #{rid} 已开工（{row['status']}）不可删除，"
                                   "改走 step_update(skipped, reason=…) 留审计")}
            # 被任何本代未删步骤依赖 → 拒（规格 §4.5：pending 依赖方也拦，
            # 否则删除后引用悬空、依赖门失锚）
            users = conn.execute(
                "SELECT id, depends_on FROM twin_plan_steps"
                " WHERE task_id=? AND id!=?", (task_id, rid)).fetchall()
            for u in users:
                if rid in _loads_ids(u["depends_on"]):
                    conn.rollback()
                    return {"ok": False, "error": "invalid_input",
                            "reason": (f"步骤 #{rid} 被步骤 #{u['id']} 依赖，"
                                       "不可删除（先处理依赖方）")}
            conn.execute("DELETE FROM twin_plan_steps WHERE id=?", (rid,))
            removed.append(rid)
        # ② 更新：逐条按 step_update 语义（含三门/迁移表/reflection 必填）
        updated: list[dict] = []
        for s in steps_update:
            if not isinstance(s, dict) or not isinstance(s.get("id"), int) or isinstance(s.get("id"), bool):
                conn.rollback()
                return {"ok": False, "error": "invalid_input",
                        "field": "steps_update", "reason": "每项需要整数 id"}
            row = conn.execute(
                "SELECT * FROM twin_plan_steps WHERE id=? AND task_id=?",
                (int(s["id"]), task_id)).fetchone()
            if row is None:
                conn.rollback()
                return {"ok": False, "error": "invalid_input",
                        "reason": f"步骤 #{s['id']} 不存在于任务 #{task_id}"}
            step = _step_dict(row)
            if "title" in s:
                title = str(s.get("title") or "").strip()
                if not title or len(title) > _TITLE_MAX:
                    conn.rollback()
                    return {"ok": False, "error": "invalid_input",
                            "field": "steps_update.title",
                            "reason": f"required 且 ≤{_TITLE_MAX} 字符"}
                conn.execute("UPDATE twin_plan_steps SET title=? WHERE id=?",
                             (title, step["id"]))
            if "description" in s:
                desc = str(s.get("description") or "").strip()
                if len(desc) > _DESC_MAX:
                    conn.rollback()
                    return {"ok": False, "error": "invalid_input",
                            "field": "steps_update.description",
                            "reason": f"过长（上限 {_DESC_MAX} 字符）"}
                conn.execute("UPDATE twin_plan_steps SET description=? WHERE id=?",
                             (desc, step["id"]))
            if "depends_on" in s:
                deps = s.get("depends_on") or []
                if not isinstance(deps, list) or any(
                        isinstance(d, bool) or not isinstance(d, int) for d in deps):
                    conn.rollback()
                    return {"ok": False, "error": "invalid_input",
                            "field": "steps_update.depends_on",
                            "reason": "必须是步骤 id 整数列表"}
                conn.execute("UPDATE twin_plan_steps SET depends_on=? WHERE id=?",
                             (_dump_ids(deps), step["id"]))
            if "status" in s and str(s.get("status") or ""):
                fresh = _step_dict(conn.execute(
                    "SELECT * FROM twin_plan_steps WHERE id=?", (step["id"],)).fetchone())
                try:
                    branch = _apply_step_transition(
                        conn, fresh, str(s["status"]),
                        str(s.get("reason") or ""), str(s.get("reflection") or ""))
                except _GateReject as e:
                    conn.rollback()
                    return e.payload
                except ValueError as e:
                    conn.rollback()
                    return {"ok": False, "error": "invalid_input", "reason": str(e)}
                updated.append(branch)
            else:
                updated.append({"step_id": step["id"], "updated": True})
        # ③ 新增：seq 接尾；depends_on 用既有 step_id
        added: list[dict] = []
        if steps_add:
            max_seq = conn.execute(
                "SELECT COALESCE(MAX(seq),0) AS m FROM twin_plan_steps WHERE task_id=?",
                (task_id,)).fetchone()["m"]
            ts = db.now_iso()
            for i, s in enumerate(steps_add):
                if not isinstance(s, dict):
                    conn.rollback()
                    return {"ok": False, "error": "invalid_input",
                            "field": "steps_add", "reason": "每项必须是对象"}
                title = str(s.get("title") or "").strip()
                if not title or len(title) > _TITLE_MAX:
                    conn.rollback()
                    return {"ok": False, "error": "invalid_input",
                            "field": "steps_add.title",
                            "reason": f"required 且 ≤{_TITLE_MAX} 字符"}
                desc = str(s.get("description") or "").strip()
                if len(desc) > _DESC_MAX:
                    conn.rollback()
                    return {"ok": False, "error": "invalid_input",
                            "field": "steps_add.description",
                            "reason": f"过长（上限 {_DESC_MAX} 字符）"}
                deps_in = s.get("depends_on") or []
                if not isinstance(deps_in, list) or any(
                        isinstance(d, bool) or not isinstance(d, int) for d in deps_in):
                    conn.rollback()
                    return {"ok": False, "error": "invalid_input",
                            "field": "steps_add.depends_on",
                            "reason": "必须是步骤 id 整数列表"}
                cur = conn.execute(
                    "INSERT INTO twin_plan_steps"
                    "(task_id, seq, title, description, depends_on, status, created_at)"
                    " VALUES(?,?,?,?,?,'pending',?)",
                    (task_id, int(max_seq) + 1 + i, title, desc, "[]", ts))
                added.append({"id": int(cur.lastrowid),
                              "seq": int(max_seq) + 1 + i, "title": title,
                              "deps_in": [int(d) for d in deps_in]})
        # ④ 环校验（本代全量 depends_on 图，含新增与修改）
        all_rows = conn.execute(
            "SELECT id, depends_on FROM twin_plan_steps WHERE task_id=?",
            (task_id,)).fetchall()
        edges = {int(r["id"]): _loads_ids(r["depends_on"]) for r in all_rows}
        for a in added:
            if a["id"] in a["deps_in"]:  # 与 plan_set 同口径：自依赖打回，不静默剥除
                conn.rollback()
                return {"ok": False, "error": "invalid_input",
                        "field": "steps_add.depends_on", "reason": "步骤不可依赖自身"}
            edges[a["id"]] = list(a["deps_in"])
        valid = set(edges)
        bad = _validate_dep_targets(edges, valid, "depends_on")
        if bad:
            conn.rollback()
            return bad
        for a in added:
            conn.execute("UPDATE twin_plan_steps SET depends_on=? WHERE id=?",
                         (_dump_ids(a["deps_in"]), a["id"]))
            a["depends_on"] = a["deps_in"]
            del a["deps_in"]
        # ⑤ 答疑问：open → answered
        answered: list[int] = []
        for a in answers:
            if not isinstance(a, dict):
                conn.rollback()
                return {"ok": False, "error": "invalid_input", "field": "answers",
                        "reason": "每项必须是对象"}
            qid = a.get("question_id")
            ans = str(a.get("answer") or "").strip()
            if isinstance(qid, bool) or not isinstance(qid, int) or not ans:
                conn.rollback()
                return {"ok": False, "error": "invalid_input", "field": "answers",
                        "reason": "每项需要整数 question_id 与非空 answer"}
            if len(ans) > _QUESTION_MAX:
                conn.rollback()
                return {"ok": False, "error": "invalid_input", "field": "answers.answer",
                        "reason": f"过长（上限 {_QUESTION_MAX} 字符）"}
            row = conn.execute(
                "SELECT id, status FROM twin_plan_questions WHERE id=? AND task_id=?",
                (qid, task_id)).fetchone()
            if row is None:
                conn.rollback()
                return {"ok": False, "error": "invalid_input",
                        "reason": f"疑问 #{qid} 不存在于任务 #{task_id}"}
            if row["status"] != "open":
                conn.rollback()
                return {"ok": False, "error": "invalid_input",
                        "reason": f"疑问 #{qid} 已解答，不可重复解答"}
            conn.execute(
                "UPDATE twin_plan_questions SET status='answered', answer=?,"
                " answered_at=? WHERE id=?",
                (ans, db.now_iso(), qid))
            answered.append(qid)
        conn.commit()
    finally:
        conn.close()
    out: dict = {"ok": True, "task_id": task_id}
    if removed:
        out["removed"] = removed
    if added:
        out["added"] = added
    if updated:
        out["updated"] = updated
    if answered:
        out["answered"] = answered
    if not (removed or added or updated or answered):
        # 仅 revision_reason 的 no-op（对抗评审轮2 P3-5）：如实告知零变更，
        # 不给「计划已修订」的成功假信号
        out["no_changes"] = True
        out["note"] = ("本次仅提交 revision_reason，未产生任何步骤/疑问变更。"
                       "修订说明需伴随 steps_add/steps_update/steps_remove/answers"
                       " 之一才会实际生效。")
        out["guidance"] = "计划未变更。"
        return out
    out["guidance"] = ("计划已修订。被阻塞的步骤现在可推进；全部闭环后 task_submit 收口。")
    return out


# ---- action: tool_log（P2，批量）----

def _action_tool_log(data: dict) -> dict:
    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        return {"ok": False, "error": "invalid_input", "field": "entries",
                "reason": "required（1..50 条）"}
    if len(entries) > _ENTRY_MAX:
        return {"ok": False, "error": "invalid_input", "field": "entries",
                "reason": f"单次最多 {_ENTRY_MAX} 条（拆批传入）"}
    task_id = None
    if data.get("task_id") is not None:
        try:
            task_id = _coerce_task_id(data)
        except ValueError as e:
            return {"ok": False, "error": "invalid_input", "reason": str(e)}
    parsed: list[tuple] = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            return {"ok": False, "error": "invalid_input", "field": f"entries[{i}]",
                    "reason": "必须是对象"}
        tool = str(e.get("tool") or "").strip()
        outcome = str(e.get("outcome") or "").strip()
        if not tool:
            return {"ok": False, "error": "invalid_input", "field": f"entries[{i}].tool",
                    "reason": "required"}
        if outcome not in TOOL_OUTCOMES:
            return {"ok": False, "error": "invalid_input",
                    "field": f"entries[{i}].outcome",
                    "reason": f"须是 {'/'.join(TOOL_OUTCOMES)} 之一"}
        note = str(e.get("note") or "").strip()
        digest = str(e.get("skill_digest") or "").strip()
        purpose = str(e.get("purpose") or "").strip()
        if len(tool) > _TOOL_NAME_MAX:
            return {"ok": False, "error": "invalid_input", "field": f"entries[{i}].tool",
                    "reason": f"过长（上限 {_TOOL_NAME_MAX} 字符）"}
        if len(purpose) > _PURPOSE_MAX:
            return {"ok": False, "error": "invalid_input", "field": f"entries[{i}].purpose",
                    "reason": f"过长（上限 {_PURPOSE_MAX} 字符）"}
        if len(note) > _NOTE_MAX:
            return {"ok": False, "error": "invalid_input", "field": f"entries[{i}].note",
                    "reason": f"过长（上限 {_NOTE_MAX} 字符）"}
        if len(digest) > _NOTE_MAX:
            return {"ok": False, "error": "invalid_input",
                    "field": f"entries[{i}].skill_digest",
                    "reason": f"过长（上限 {_NOTE_MAX} 字符）"}
        parsed.append((tool, purpose, outcome,
                       note, digest))
    flow.ensure_schema()
    conn = db.connect()
    try:
        if task_id is not None:
            row = conn.execute("SELECT 1 FROM twin_tasks WHERE id=?",
                               (task_id,)).fetchone()
            if row is None:
                return {"ok": False, "error": "not_found", "reason": f"task id {task_id}"}
        client = identity.effective_client(data)
        ts = db.now_iso()
        conn.executemany(
            "INSERT INTO twin_tool_usage"
            "(task_id, tool, purpose, outcome, note, skill_digest, client, created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            [(task_id, p[0], p[1], p[2], p[3], p[4], client, ts) for p in parsed])
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "recorded": len(parsed),
            "guidance": "已记录。只记失败/重试/降级与首次成功的非常规路径——常规重复成功不必记。"}


# ---- 收口门 / task_close 批量跳过 / 深拷贝（task_actions 调用）----

def submit_gate(conn, task_id: int) -> dict | None:
    """收口门：未建过计划 → 放行（None）；有未闭环步骤 → 打回响应（v3.3 ⑦：
    附精确未闭环清单 + 对账引导，把墙变对账机会）。"""
    if not has_plan(conn, task_id):
        return None
    open_rows = conn.execute(
        f"SELECT id, title, status, reflection FROM twin_plan_steps"
        f" WHERE task_id=? AND status IN ({','.join('?' * len(STEP_OPEN_STATUSES))})"
        f" ORDER BY seq, id", (int(task_id), *STEP_OPEN_STATUSES)).fetchall()
    if not open_rows:
        return None
    return {"ok": False, "error": "invalid_input",
            "reason": ("任务建过计划且有未闭环步骤，不可交付收口。请逐条对账：实际做完的补 "
                       "step_update(done)（跳过 in_progress 属追认，服务端会标 backfilled，"
                       "不影响收口）；决定不做的 step_update(skipped, reason=…)；"
                       "失败待重试的先处理（重开或 skipped）。"),
            "open_steps": [{"id": r["id"], "title": r["title"], "status": r["status"],
                            "has_reflection": bool((r["reflection"] or "").strip())}
                           for r in open_rows]}


def blocking_questions_warning(conn, task_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT id, question FROM twin_plan_questions"
        " WHERE task_id=? AND status='open' AND blocking=1 ORDER BY id",
        (int(task_id),)).fetchall()
    if not rows:
        return []
    ids = "、".join(f"#{r['id']}" for r in rows)
    return [f"任务仍有未解答的 blocking 疑问 {ids}（owner 已答复？请用 "
            f"plan_revise(answers=…) 写回，保持审计完整）"]


def close_task_steps(conn, task_id: int) -> int:
    """task_close 的死锁出口：未闭环步骤批量 skipped(reason=closed)。"""
    cur = conn.execute(
        f"UPDATE twin_plan_steps SET status='skipped', reason='closed',"
        f" decided_at=COALESCE(decided_at, ?)"
        f" WHERE task_id=? AND status IN ({','.join('?' * len(STEP_OPEN_STATUSES))})",
        (db.now_iso(), int(task_id), *STEP_OPEN_STATUSES))
    return cur.rowcount


def copy_plan_to_task(conn, from_tid: int, to_tid: int) -> dict:
    """resume/revise 的计划深拷贝（v3.3 ③）：未完结步骤复制到新任务行——
    status 重置 pending、reflection/backfilled 清零（新代新开始）、origin_step_id
    溯源；depends_on 重映射（指向旧代已终结步骤的依赖剥除并计数）；open 疑问
    同步拷贝（answered 不拷），step_ids 同款重映射。不 commit。"""
    rows = conn.execute(
        f"SELECT * FROM twin_plan_steps WHERE task_id=?"
        f" AND status IN ({','.join('?' * len(STEP_OPEN_STATUSES))}) ORDER BY seq, id",
        (int(from_tid), *STEP_OPEN_STATUSES)).fetchall()
    ts = db.now_iso()
    id_map: dict[int, int] = {}
    seq_old: list[tuple[int, dict]] = []
    for i, r in enumerate(rows):
        old = dict(r)
        cur = conn.execute(
            "INSERT INTO twin_plan_steps"
            "(task_id, seq, title, description, depends_on, status, origin_step_id,"
            " created_at) VALUES(?,?,?,?,?,'pending',?,?)",
            (int(to_tid), i + 1, old["title"], old["description"],
             "[]", int(old["id"]), ts))
        new_id = int(cur.lastrowid)
        id_map[int(old["id"])] = new_id
        seq_old.append((new_id, old))
    deps_dropped = 0
    for new_id, old in seq_old:
        old_deps = _loads_ids(old.get("depends_on"))
        mapped = [id_map[d] for d in old_deps if d in id_map]
        deps_dropped += len(old_deps) - len(mapped)
        conn.execute("UPDATE twin_plan_steps SET depends_on=? WHERE id=?",
                     (_dump_ids(mapped), new_id))
    q_rows = conn.execute(
        "SELECT * FROM twin_plan_questions WHERE task_id=? AND status='open' ORDER BY id",
        (int(from_tid),)).fetchall()
    q_dropped = 0
    for r in q_rows:
        q = dict(r)
        old_refs = _loads_ids(q.get("step_ids"))
        mapped = [id_map[d] for d in old_refs if d in id_map]
        if not mapped:
            # 关联步骤全部已闭环：疑问属于旧代（对抗评审轮2 P3-3）——照拷会产出
            # step_ids=[] 的 open+blocking 僵尸，永不拦任何步骤却污染统计与警告
            q_dropped += 1
            continue
        conn.execute(
            "INSERT INTO twin_plan_questions"
            "(task_id, question, step_ids, blocking, status, created_at)"
            " VALUES(?,?,?,?, 'open', ?)",
            (int(to_tid), q["question"], _dump_ids(mapped),
             int(q["blocking"] or 0), ts))
    return {"steps": len(rows), "questions": len(q_rows) - q_dropped,
            "questions_dropped": q_dropped, "deps_dropped": deps_dropped}
