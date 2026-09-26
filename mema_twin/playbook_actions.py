"""playbook 闭环动作（v0.4 P3）：task_evaluate / playbook_submit /
playbook_rollback + PB 验证门（溯源/回声/标题/空转阻尼）+ 连续性分级注入。

playbook 是独立键空间（twin_playbooks 表，key = work_type code 或 'global'），
不复用 twin_prompt_versions——源是执行记录非偏好证据，列语义与验证门均不同构
（评审 A4）。编译链路与 persona 物理隔离：不进 compile 队列、不消耗证据、
不进 status 的 versions 桶。
"""
from __future__ import annotations

import json
import re
import sqlite3

from . import db, exec_actions, flow, identity, store, templates


def _pb_echo_marker(text: str) -> str | None:
    """playbook 素材包回声检测：标题类整串子串 + 节标题类行首标题（G1 同款）。"""
    if not text:
        return None
    hit = next((m for m in templates.PLAYBOOK_TITLE_MARKERS if m in text), None)
    if hit:
        return hit
    return next((m for m in templates.PLAYBOOK_SECTION_MARKERS
                 if re.search("(?m)^#{1,6}[ \\t]*" + re.escape(m), text)), None)


def _dump_ids(ids: list[int]) -> str:
    return json.dumps([int(i) for i in ids])


def _loads_ids(text: str | None) -> list[int]:
    try:
        raw = json.loads(text or "[]")
    except (ValueError, TypeError):
        return []
    return [int(i) for i in raw] if isinstance(raw, list) else []


def get_active_playbook(conn, key: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM twin_playbooks WHERE key=? AND status='active'"
        " ORDER BY version DESC LIMIT 1", (key,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["source_task_ids"] = _loads_ids(d.get("source_task_ids"))
    return d


# ---- action: task_evaluate ----

_EVAL_LIMIT = 10
_EVAL_WATERMARK = "plan_eval_watermark"
_TASK_STEP_CAP = 50
_TASK_TOOL_CAP = 20


def _action_task_evaluate(data: dict) -> dict:
    origin = data.get("origin")
    if origin is not None and origin != "scheduled":
        return {"ok": False, "error": "invalid_input", "field": "origin",
                "reason": "origin 仅接受 scheduled（夜间定时任务心跳/来源标记），交互式不要传"}
    limit_raw = data.get("limit")
    limit = _EVAL_LIMIT
    if limit_raw is not None:
        try:
            limit = int(limit_raw)
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid_input", "field": "limit",
                    "reason": "limit 需是整数"}
        limit = max(1, min(limit, _EVAL_LIMIT))
    explicit_ids: list[int] | None = None
    if data.get("task_ids") is not None:
        raw = data.get("task_ids")
        if not isinstance(raw, list) or any(
                isinstance(x, bool) or not isinstance(x, (int, str)) for x in raw):
            return {"ok": False, "error": "invalid_input", "field": "task_ids",
                    "reason": "必须是任务 id 整数列表"}
        try:
            explicit_ids = [int(x) for x in raw]
        except ValueError:
            return {"ok": False, "error": "invalid_input", "field": "task_ids",
                    "reason": "必须是任务 id 整数列表"}
        if any(n < 0 or n > 2**63 - 1 for n in explicit_ids):
            # 巨整数进 SQLite 绑定会抛 OverflowError 变 internal_error（对抗评审轮2 P3）
            return {"ok": False, "error": "invalid_input", "field": "task_ids",
                    "reason": "task id 需在 0..2^63-1 内"}
    flow.ensure_schema()
    conn = db.connect()
    try:
        if explicit_ids is not None:
            rows = []
            for tid in explicit_ids:
                r = conn.execute(
                    "SELECT * FROM twin_tasks WHERE id=? AND status IN"
                    " ('submitted','superseded')", (tid,)).fetchone()
                if r is not None:
                    rows.append(r)
        else:
            watermark = int(flow.get_meta(_EVAL_WATERMARK) or 0)
            rows = conn.execute(
                "SELECT * FROM twin_tasks WHERE status IN ('submitted','superseded')"
                " AND id > ? ORDER BY id LIMIT ?",
                (watermark, limit)).fetchall()
        tasks: list[dict] = []
        max_id = 0
        for r in rows:
            t = dict(r)
            tid = int(t["id"])
            max_id = max(max_id, tid)
            steps = exec_actions.task_steps(conn, tid)
            if len(steps) > _TASK_STEP_CAP:
                # 保留 reflection 非空的优先（失败对策是评估核心素材）
                keep = [s for s in steps if (s.get("reflection") or "").strip()]
                rest = [s for s in steps if not (s.get("reflection") or "").strip()]
                steps = (keep + rest)[:_TASK_STEP_CAP]
            q_rows = conn.execute(
                "SELECT id, question, step_ids, blocking, status, answer"
                " FROM twin_plan_questions WHERE task_id=? ORDER BY id",
                (tid,)).fetchall()
            questions = [dict(q) for q in q_rows]
            t["steps"] = steps
            t["questions"] = questions
            tasks.append(t)
        tool_rows = conn.execute(
            "SELECT task_id, tool, purpose, outcome, note, client FROM twin_tool_usage"
            " WHERE task_id IN ({}) ORDER BY id".format(
                ",".join("?" for _ in rows) or "NULL"),
            [int(r["id"]) for r in rows]).fetchall() if rows else []
        tool_usage = [dict(u) for u in tool_rows]
        # 体积护栏：每任务 tool_usage 截断
        seen: dict[int, int] = {}
        trimmed: list[dict] = []
        for u in tool_usage:
            tid = int(u.get("task_id") or 0)
            seen[tid] = seen.get(tid, 0) + 1
            if seen[tid] <= _TASK_TOOL_CAP:
                trimmed.append(u)
        # key_hint：本批任务出现最多的 work_type
        wt_count: dict[str, int] = {}
        for t in tasks:
            wt = t.get("work_type")
            if wt:
                wt_count[wt] = wt_count.get(wt, 0) + 1
        key_hint = (max(wt_count, key=wt_count.get) if wt_count else None)
        active = get_active_playbook(conn, key_hint) if key_hint else None
        material = templates.compile_playbook_material(
            key_hint or "global", active, tasks, trimmed)
        # 水位单调推进：显式补评估同样推进、不回拉（规格 §6.2）
        if max_id:
            flow.set_meta(_EVAL_WATERMARK, str(max(
                int(flow.get_meta(_EVAL_WATERMARK) or 0), max_id)))
    finally:
        conn.close()
    if origin == "scheduled":
        # 心跳（v0.4.1 合并单任务）：合并夜间任务每晚必调 task_evaluate(origin=
        # scheduled)，调用成功即证明任务在转——与当晚有无新经验无关，是单键
        # 停转保险丝的依据；编译支线连续无素材也不会误报
        flow.ensure_schema()
        flow.set_meta("last_scheduled_playbook_at", db.now_iso())
    out: dict = {"ok": True, "task_count": len(tasks), "material": material,
                 "key_hint": key_hint,
                 "note": templates.STRONG_MODEL_NOTE,
                 "next": ("按素材包编译规则产出 content_md 后调 twin(action="
                          "\"playbook_submit\", data={key, content_md, "
                          "source_task_ids, model})；无值得沉淀的新经验则不提交")}
    if max_id:
        out["watermark_moved_to"] = max_id
    return out


# ---- playbook 版本存储（照 store.create_version 模式，独立键空间）----

def create_playbook_version(conn, key: str, content_md: str,
                            source_task_ids: list[int], model: str = "",
                            origin: str | None = None) -> dict:
    import os
    from pathlib import Path

    prev = conn.execute(
        "SELECT version FROM twin_playbooks WHERE key=? AND status='active'"
        " ORDER BY version DESC LIMIT 1", (key,)).fetchone()
    superseded_version = int(prev["version"]) if prev else None
    warnings: list[str] = []
    for _attempt in range(3):
        row = conn.execute(
            "SELECT COALESCE(MAX(version),0) AS v FROM twin_playbooks WHERE key=?",
            (key,)).fetchone()
        version = int(row["v"]) + 1
        conn.execute(
            "UPDATE twin_playbooks SET status='retired'"
            " WHERE key=? AND status='active'", (key,))
        try:
            conn.execute(
                "INSERT INTO twin_playbooks"
                "(key, version, content_md, source_task_ids, model, origin,"
                " status, created_at, activated_at) VALUES(?,?,?,?,?,?,'active',?,?)",
                (key, version, content_md, _dump_ids(source_task_ids), model or "",
                 origin, db.now_iso(), db.now_iso()))
            break
        except sqlite3.IntegrityError:
            conn.rollback()
            if _attempt == 2:
                raise
            continue
    conn.commit()
    root = Path(os.environ.get("MEMA_TWIN_PROMPTS_DIR")
                or db.PROJECT_ROOT / "prompts").parent
    d = Path(os.environ.get("MEMA_TWIN_PLAYBOOKS_DIR") or root / "playbooks") / key
    for f in (f"v{version}.md", "active.md"):
        try:
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / (f + ".tmp")
            tmp.write_text(content_md, encoding="utf-8")
            tmp.replace(d / f)
        except OSError as e:
            warnings.append(f"镜像写入失败（{d / f}）: {e}")
    return {"key": key, "version": version, "model": model or "",
            "source_count": len(source_task_ids),
            "superseded_version": superseded_version,
            "mirror": str(d / f"v{version}.md"), "warnings": warnings}


# ---- action: playbook_submit ----

_PB_CONTENT_MAX = 60_000


def _bump_pb_reject(key: str, check: str) -> None:
    flow.ensure_schema()
    raw = flow.get_meta(f"playbook_reject:{key}")
    try:
        rec = json.loads(raw)
    except (ValueError, TypeError):
        rec = {}
    if not isinstance(rec, dict):
        rec = {}
    rec.update({"count": int(rec.get("count") or 0) + 1,
                "last_check": check, "last_at": db.now_iso()})
    flow.set_meta(f"playbook_reject:{key}", json.dumps(rec, ensure_ascii=False))


_PB_REJECT_HINT = ("本次未落版：active 未变，次晚自动重试；连续被拒会在 status 的 "
                   "playbook_rejected 累计显示——如需立即处理请人工评估后交互式 "
                   "playbook_submit（不带 origin，违规只警告不拦）")


def _action_playbook_submit(data: dict) -> dict:
    key = str(data.get("key") or "").strip()
    content_md = str(data.get("content_md") or "").strip()
    if not key or not content_md:
        return {"ok": False, "error": "invalid_input",
                "reason": "需要 key 与 content_md"}
    if key.startswith(("pb-", "aud-")):
        return {"ok": False, "error": "invalid_input", "field": "key",
                "reason": "playbook key 不带前缀：work_type code 或 'global'"}
    if len(content_md) > _PB_CONTENT_MAX:
        return {"ok": False, "error": "invalid_input", "field": "content_md",
                "reason": f"过长（{len(content_md)} 字符，上限 {_PB_CONTENT_MAX}）"}
    origin = data.get("origin")
    if origin is not None and origin != "scheduled":
        return {"ok": False, "error": "invalid_input", "field": "origin",
                "reason": "origin 仅接受 scheduled（夜间定时任务来源标记），交互式不要传"}
    try:
        source_ids = compile_ids_coerce(data.get("source_task_ids"))
    except ValueError as e:
        return {"ok": False, "error": "invalid_input", "field": "source_task_ids",
                "reason": str(e)}
    if not source_ids:
        return {"ok": False, "error": "invalid_input", "field": "source_task_ids",
                "reason": "required（溯源锚：本版经验来源的任务 id，防幻觉路径）"}
    flow.ensure_schema()
    conn = db.connect()
    try:
        if key != "global" and store.resolve_work_type_code(conn, key) is None:
            return {"ok": False, "error": "invalid_input", "field": "key",
                    "reason": f"unknown work_type code: {key!r}；或传 'global'"}
        # 溯源校验（PB 溯源门）：任务存在、已收口、确有执行记录
        foreign: list[int] = []
        valid_ids: list[int] = []
        for tid in source_ids:
            row = conn.execute(
                "SELECT status FROM twin_tasks WHERE id=?", (tid,)).fetchone()
            has_records = conn.execute(
                "SELECT 1 FROM twin_plan_steps WHERE task_id=? UNION ALL"
                " SELECT 1 FROM twin_tool_usage WHERE task_id=? LIMIT 1",
                (tid, tid)).fetchone() is not None
            if row is None or row["status"] not in ("submitted", "superseded") \
                    or not has_records:
                foreign.append(tid)
            else:
                valid_ids.append(tid)
        violations: list[dict] = []
        warnings: list[str] = []
        if foreign:
            msg = (f"source_task_ids 含 {len(foreign)} 个无效溯源（{foreign}——"
                   "不存在/未收口/无执行记录）")
            if origin == "scheduled":
                _bump_pb_reject(key, "foreign_task_ids")
                return {"ok": False, "error": "validation_failed", "origin": "scheduled",
                        "key": key,
                        "violations": [{"check": "foreign_task_ids", "detail": msg}],
                        "reason": "夜间落版未过验证门：" + msg, "hint": _PB_REJECT_HINT}
            warnings.append(f"[对账] source_task_ids 含无效溯源 {foreign}，已剔除")
            if not valid_ids:
                # 溯源锚是拍板语义（防幻觉路径），剔完为空交互也不放行——否则落出
                # source_task_ids=[] 的 active 版，此后该 key 的空转阻尼永久失效
                # （对抗评审轮2 P2-3）
                return {"ok": False, "error": "validation_failed", "key": key,
                        "violations": [{"check": "foreign_task_ids",
                                        "detail": msg + "；剔除后无可溯源任务"}],
                        "reason": msg + "；剔除后无可溯源任务，拒绝落版"
                                        "（先补齐任务的执行记录再提交）"}
        # PB-G2 分区标题
        if not re.search(r"(?m)^#{1,6}[ \t]*\S", content_md):
            violations.append({"check": "no_headings",
                               "detail": "产物没有任何 Markdown 标题——编译规则要求按固定分区组织"})
        # PB-G1 素材回声（含自锁守卫：active 版已含标记 → 降级警告）
        echo = _pb_echo_marker(content_md)
        if echo:
            active_md = (get_active_playbook(conn, key) or {}).get("content_md") or ""
            if _pb_echo_marker(active_md):
                warnings.append(f"产物含素材包标记「{echo}」——现行 active 版也含该标记"
                                "（沿袭旧版）；若非有意引用请人工清理")
            else:
                violations.append({"check": "material_echo",
                                   "detail": f"产物复述了素材包内容（含标记「{echo}」）——"
                                             "疑似编译失败"})
        if violations and origin == "scheduled":
            _bump_pb_reject(key, violations[0]["check"])
            return {"ok": False, "error": "validation_failed", "origin": "scheduled",
                    "key": key, "violations": violations,
                    "reason": "夜间落版未过验证门：" + "；".join(
                        v["detail"] for v in violations), "hint": _PB_REJECT_HINT}
        if violations:
            # 交互式违规只警告不拦（拍板口径）——但必须随响应可见，不得静默丢弃
            # （对抗评审轮2 P2-2：此前 G1/G2 violations 在交互路径无痕消失）
            warnings.extend(v["detail"] for v in violations)
        # 空转阻尼（仅 scheduled；playbook 无基座收缩场景，不需要旁路分支）
        active = get_active_playbook(conn, key)
        if origin == "scheduled" and active is not None:
            if set(valid_ids) <= set(active.get("source_task_ids") or []):
                _bump_pb_reject(key, "no_new_tasks")
                return {"ok": False, "error": "no_new_evidence", "origin": "scheduled",
                        "key": key,
                        "reason": "提交的 source_task_ids 未包含现行 active 版未吸收的"
                                  "新任务（无新执行经验），拒绝空转落版",
                        "hint": _PB_REJECT_HINT}
        rec = create_playbook_version(conn, key, content_md, valid_ids,
                                      model=str(data.get("model") or ""),
                                      origin=origin)
    finally:
        conn.close()
    rec["ok"] = True
    rec["supersedes"] = rec.pop("superseded_version")
    rec["session_note"] = (f"请提醒用户：playbook {key} v{rec['version']} 已生效；"
                           "后续 task_start 自动注入。")
    if warnings:
        rec.setdefault("warnings", []).extend(warnings)
    if origin == "scheduled":
        flow.ensure_schema()
        flow.set_meta("last_scheduled_playbook_at", db.now_iso())
    flow.ensure_schema()
    if flow.get_meta(f"playbook_reject:{key}") is not None:
        flow.delete_meta(f"playbook_reject:{key}")
    return rec


def compile_ids_coerce(value) -> list[int]:
    """source_task_ids 矫正：int/数字串列表，去重，上界 2^63-1（抄 _coerce_source_ids）。"""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("source_task_ids must be a list of task ids")
    out: list[int] = []
    for i in value:
        if isinstance(i, bool) or not isinstance(i, (int, str)):
            raise ValueError(f"invalid task id: {i!r}")
        try:
            n = int(i)
        except ValueError:
            raise ValueError(f"invalid task id: {i!r}") from None
        if str(n) != str(i).strip() and not isinstance(i, int):
            raise ValueError(f"invalid task id: {i!r}")
        if n < 0 or n > 2**63 - 1:
            raise ValueError(f"invalid task id: {i!r}（需在 0..2^63-1 内）")
        out.append(n)
    seen: set[int] = set()
    return [i for i in out if not (i in seen or seen.add(i))]


# ---- action: playbook_rollback ----

def _action_playbook_rollback(data: dict) -> dict:
    key = str(data.get("key") or "").strip()
    if not key:
        return {"ok": False, "error": "invalid_input", "field": "key",
                "reason": "required"}
    version = data.get("version")
    if version is not None:
        if isinstance(version, bool) or not isinstance(version, (int, str)):
            return {"ok": False, "error": "invalid_input", "field": "version",
                    "reason": "version 需是版本号整数（或省略回上一版）"}
        try:
            n = int(version)
        except ValueError:
            return {"ok": False, "error": "invalid_input", "field": "version",
                    "reason": f"invalid version: {version!r}"}
        if n < 1 or n > 2**63 - 1 or (str(n) != str(version).strip()
                                      and not isinstance(version, int)):
            return {"ok": False, "error": "invalid_input", "field": "version",
                    "reason": f"invalid version: {version!r}"}
        version = n
    flow.ensure_schema()
    conn = db.connect()
    try:
        active_row = conn.execute(
            "SELECT version FROM twin_playbooks WHERE key=? AND status='active'"
            " ORDER BY version DESC LIMIT 1", (key,)).fetchone()
        if version is None:
            if active_row is None:
                row = conn.execute(
                    "SELECT * FROM twin_playbooks WHERE key=?"
                    " ORDER BY version DESC LIMIT 1", (key,)).fetchone()
                if row is None:
                    return {"ok": False, "error": "invalid_input",
                            "reason": f"{key} 尚无任何 playbook 版本"}
                version = int(row["version"])
            else:
                row = conn.execute(
                    "SELECT * FROM twin_playbooks WHERE key=? AND version != ?"
                    " ORDER BY version DESC LIMIT 1",
                    (key, int(active_row["version"]))).fetchone()
                if row is None:
                    return {"ok": False, "error": "invalid_input",
                            "reason": (f"{key} 当前 v{active_row['version']} 已是唯一"
                                       "版本，没有可回滚的历史版本")}
                version = int(row["version"])
            target_row = row
        else:
            target_row = conn.execute(
                "SELECT * FROM twin_playbooks WHERE key=? AND version=?",
                (key, int(version))).fetchone()
            if target_row is None:
                avail = [int(r["version"]) for r in conn.execute(
                    "SELECT version FROM twin_playbooks WHERE key=? ORDER BY version",
                    (key,))]
                return {"ok": False, "error": "invalid_input",
                        "reason": f"{key} 不存在版本 v{version}；可用版本：{avail}"}
        target = int(target_row["version"])
        if active_row is not None and target == int(active_row["version"]):
            conn.commit()
            return {"ok": True, "key": key, "version": target,
                    "note": f"v{target} 已是 active，未做变更"}
        ts = db.now_iso()
        conn.execute("UPDATE twin_playbooks SET status='retired'"
                     " WHERE key=? AND status='active'", (key,))
        conn.execute("UPDATE twin_playbooks SET status='active', activated_at=?"
                     " WHERE key=? AND version=?", (ts, key, target))
        conn.commit()
        warnings: list[str] = []
        import os
        from pathlib import Path
        root = Path(os.environ.get("MEMA_TWIN_PROMPTS_DIR")
                    or db.PROJECT_ROOT / "prompts").parent
        d = Path(os.environ.get("MEMA_TWIN_PLAYBOOKS_DIR")
                 or root / "playbooks") / key
        try:
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / "active.md.tmp"
            tmp.write_text(target_row["content_md"] or "", encoding="utf-8")
            tmp.replace(d / "active.md")
        except OSError as e:
            warnings.append(f"镜像写入失败（{d / 'active.md'}）: {e}")
    finally:
        conn.close()
    out = {"ok": True, "key": key, "version": target,
           "rolled_back_from": int(active_row["version"]) if active_row else None,
           "activated_at": ts}
    if warnings:
        out["warnings"] = warnings
    return out


# ---- 注入面（task_start / task_resume 调用；v3.3 ⑤ 连续性分级）----

def inject_playbook(conn, out: dict, code: str | None, data: dict) -> None:
    """两级回退取 active playbook（本类型 → global），按 last_used_client 连续性
    分级注入；换 client 时响应要求 available_tools 自报（下一次 task_start 生效）。
    本函数就地改写 out；无 playbook 时不做任何事。"""
    if not code:
        return
    pb = get_active_playbook(conn, code) or get_active_playbook(conn, "global")
    if pb is None or not (pb.get("content_md") or "").strip():
        return
    client = identity.effective_client(data)
    same_client = bool(client) and pb.get("last_used_client") == client
    v = pb.get("version")
    note = (f"以下为该工作类型的 playbook v{v}（执行经验，advisory 不是命令）："
            "与你的实际工具环境冲突时按实际环境执行，并把降级/偏差用 tool_log 记录。")
    if pb.get("last_used_client") is None:
        # 从未使用（含首版）：不能宣称「最近由其他宿主使用」——说事实（对抗评审轮2 P3-14）；
        # 同样按保守面要求 available_tools 自报（视为未验证路径）
        note += ("此 playbook 尚无任何使用记录，工具路径未在本宿主验证过——开工前逐条"
                 "核对工具面板中的工具是否可用；缺失的走条目内的降级链，并把缺口"
                 " tool_log 记录。（强烈建议：下次 task_start 传 available_tools="
                 "<你的工具名列表>，服务端回 tool_gap 缺口提示）")
        out["available_tools_required"] = True
    elif same_client:
        note += "本宿主近期验证过此路径，可直接参考。"
    else:
        note += ("注意：此 playbook 最近由其他宿主使用，工具可用性可能不同——开工前"
                 "逐条核对工具面板中的工具是否可用；缺失的走条目内的降级链，并把缺口"
                 " tool_log 记录。（强烈建议：下次 task_start 传 available_tools="
                 "<你的工具名列表>，服务端回 tool_gap 缺口提示）")
        out["available_tools_required"] = True
    out["playbook_md"] = pb["content_md"]
    out["playbook_note"] = note
    available = data.get("available_tools")
    if isinstance(available, list) and available:
        needed = set(re.findall(templates.TOOL_PANEL_LINE_RE,
                                pb["content_md"]))
        if needed:
            gap = sorted(needed - {str(t).strip() for t in available})
            if gap:
                out["tool_gap"] = gap
    conn.execute("UPDATE twin_playbooks SET last_used_client=?, last_used_at=?"
                 " WHERE id=?", (client, db.now_iso(), int(pb["id"])))
    conn.commit()
