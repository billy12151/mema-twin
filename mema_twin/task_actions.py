"""交付任务流动作（v0.3.8 从 server.py 拆出）：task_start / task_submit /
task_resume / task_revise / task_close / task_recent / task_get / todo + 注入
helpers（persona 注入 / 未编译增补 / 受众画像）。

v0.3.8：评审环删除（task_submit 即终点并落盘交付物文件，task_review/
task_pending 退役）；task_start 经归一门（三维度未命中整笔打回 + 裁定票据，
建档之前，无半建任务）；双跑提议（persona_compare_offer）删除。
"""
from __future__ import annotations

import json
import sqlite3

from . import db, exec_actions, flow, identity, normalize, store, taxonomy
from .compile_actions import _read_evidence_rows


def _task_persona(conn, code: str | None) -> dict | None:
    if not code:
        return None
    return store.get_active(conn, code)


def _coerce_available_tools(value) -> list[str] | None:
    """task_start 的 available_tools 自报矫正（v3.2-②）：None 透传；非空字符串
    列表收下（≤50 项、每项 ≤100 字符）；脏值打回。存任务行供 tool_gap 计算。"""
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("available_tools 必须是字符串列表")
    out: list[str] = []
    for i, t in enumerate(value):
        s = str(t).strip()
        if not s:
            raise ValueError(f"available_tools[{i}] 不能为空")
        if len(s) > 100:
            raise ValueError(f"available_tools[{i}] 过长（上限 100 字符）")
        out.append(s)
    if len(out) > 50:
        raise ValueError("available_tools 上限 50 项")
    return out


def _coerce_task_id(tid) -> int:
    """task_id 严格矫正（轮2 P3-4）：int / 纯数字串；浮点/bool/脏串打回——
    4.9 不再静默截断成 4。ValueError 由调度器归 invalid_input。上限 2^63-1
    （轮2 对抗 P3：巨整数进 SQLite 绑定会抛 OverflowError 变 internal_error）。"""
    if isinstance(tid, bool) or not isinstance(tid, (int, str)):
        raise ValueError(f"task_id 需是任务号整数: {tid!r}")
    try:
        n = int(tid)
    except ValueError:
        raise ValueError(f"invalid task_id: {tid!r}") from None
    if str(n) != str(tid).strip() and not isinstance(tid, int):
        raise ValueError(f"invalid task_id: {tid!r}")
    if n < 0 or n > 2**63 - 1:
        raise ValueError(f"invalid task_id: {tid!r}（需在 0..2^63-1 内）")
    return n


_BRIEF_MAX = 2000


def _brief_guard(value: str) -> str | None:
    """brief 长度帽（对抗评审轮2 P2-5）：brief 进任务行并进 task_evaluate 素材包，
    无帽会让素材包体积失控。"""
    if len(value) > _BRIEF_MAX:
        return f"过长（上限 {_BRIEF_MAX} 字符）"
    return None


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


def _wt_domain(conn, code: str) -> str:
    """工种域归属双源（F4，方案评审 P2-5）：twin_types 行优先（含 custom 码），
    行缺失回落 taxonomy（老库版本新增内置码无行——is_known_work_type 同款口径）。
    返回值归一（strip+压平空白——轮2 对抗 P2-1：历史脏 domain 不因空格失联）。"""
    row = conn.execute(
        "SELECT domain FROM twin_types WHERE type_kind='work_type' AND code=?",
        (code,)).fetchone()
    if row is not None:
        return " ".join((row["domain"] or "").split())
    t = taxonomy.by_code("work_type", code)
    return (" ".join((t.domain or "").split())) if t else ""


def _wt_label_zh(conn, code: str) -> str:
    row = conn.execute(
        "SELECT label_zh FROM twin_types WHERE type_kind='work_type' AND code=?",
        (code,)).fetchone()
    if row is not None and (row["label_zh"] or "").strip():
        label = row["label_zh"]
    else:
        t = taxonomy.by_code("work_type", code)
        label = (t.zh or code) if t else code
    # 渲染侧 sanitize（轮2 P3-5）：note 契约是「各一行」——压平换行+截断，
    # 治理表 label 可含任意用户裁定文本
    flat = " ".join(label.split())
    return flat[:64] + "…" if len(flat) > 64 else flat


def _fallback_payload(code: str | None) -> dict:
    """F4 同域 fallback 注入（#956 拍板）：本工种无专属 persona 时，注入同 domain
    最成熟工种（活证据最多，tie-break：证据数→版本→code 字典序最大）的 active
    persona 全文 + 其余候选身份摘要 + 降级声明。agent 只裁怎么用不裁要不要
    （硬返回）；专属落版（含 mirror 降级读到）即自动退出——本函数只在
    persona 为 None 的分支被调用。自管连接（照 _supplement_payload 先例）。"""
    if not code:
        return {}
    conn = db.connect()
    try:
        my_domain = _wt_domain(conn, code)
        if not my_domain:
            return {}
        rows = conn.execute(
            "SELECT work_type, version, prompt_md FROM twin_prompt_versions"
            " WHERE status='active' AND work_type NOT LIKE 'aud-%'"
            " ORDER BY work_type, version").fetchall()
        best: dict[str, dict] = {}  # work_type -> 最高版本行（病态双 active 去重）
        for r in rows:
            if store.classify_code(r["work_type"]) != store.CODE_KIND_WORK_TYPE:
                continue
            if not (r["prompt_md"] or "").strip():
                continue  # 空正文 donor 无垫底价值（轮2 P3-2，仅库腐化可达）
            try:
                int(r["version"])  # 病态 TEXT 版本号跳过（轮2 P3-1，仅库腐化可达）
            except (TypeError, ValueError):
                continue
            best[r["work_type"]] = dict(r)
        cands = {w: r for w, r in best.items()
                 if w != code and _wt_domain(conn, w) == my_domain}
        if not cands:
            return {}
        wt_ph = ",".join("?" for _ in cands)
        counts = {r["work_type"]: int(r["n"]) for r in conn.execute(
            f"SELECT work_type, COUNT(*) AS n FROM twin_evidence"
            f" WHERE status IN ('uncompiled','compiled')"
            f" AND work_type IN ({wt_ph}) GROUP BY work_type",
            tuple(cands)).fetchall()}
        donor_w = max(cands,
                      key=lambda w: (counts.get(w, 0), int(cands[w]["version"]), w))
        donor = cands[donor_w]
        donor_n = counts.get(donor_w, 0)
        others = sorted((w for w in cands if w != donor_w),
                        key=lambda w: (-counts.get(w, 0), -int(cands[w]["version"]), w))
        note = (
            f"以下为同域工种「{_wt_label_zh(conn, donor_w)}（{donor_w}）」的 persona"
            f" v{donor['version']}，作为本工种尚无专属规则时的垫底参考"
            f"（活证据 {donor_n} 条，同域最成熟）。\n"
            "此参考仅为本工种无专属规则时的垫底：格式与结构层可参考；若本任务存在"
            "更权威格式来源（用户提供的模板/公司官方规范/行业惯例），以权威来源为准，"
            "本参考降级为风格与详略参考；内容与业务规则不适用。\n"
            "优先级：本类型的增补/画像口径 > 此参考的格式结构。")
        if others:
            lines = "\n".join(
                f"- {_wt_label_zh(conn, w)}（{w}）v{cands[w]['version']}"
                f"，活证据 {counts.get(w, 0)} 条" for w in others)
            note += (f"\n同域其他可参考（各一行）：\n{lines}\n按任务语义判断哪个更相关，可 "
                     f'twin(action="get", data={{"work_type": "<该工种 code>"}}) '
                     "取其全文。本工种攒够证据后会有专属 persona，届时此垫底自动退出。")
        else:
            note += "\n本工种攒够证据后会有专属 persona，届时此垫底自动退出。"
        return {
            "fallback_persona_md": donor["prompt_md"] or "",
            "fallback_from": {"work_type": donor_w, "version": int(donor["version"]),
                              "evidence_count": donor_n},
            "fallback_persona_note": note,
        }
    finally:
        conn.close()


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
    if brief_err := _brief_guard(brief):
        return {"ok": False, "error": "invalid_input", "field": "brief", "reason": brief_err}
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
        available_tools=_coerce_available_tools(data.get("available_tools")),
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
            "任务已建档。凡建档任务默认属于重复执行型工作：开工前先 "
            "twin(action=\"plan_set\") 列出步骤计划，再逐步执行（step_update 打卡，"
            "跳过打卡直接完成的属追认，服务端会标 backfilled，不影响收口）。"
            "仅无重复执行价值的一次性事务（问候、查即时信息、无产出物的一问一答）"
            "不需要建档，既已建档即默认走计划。拿不准要不要建计划时，建。"
            "计划中有没把握、或你确认不了的点，写进 plan_set 的 open_questions"
            "（要紧的标 blocking=true）先与用户澄清——答复经 plan_revise(answers=…)"
            " 写回。完成后 twin(action=\"task_submit\") 交付收口"
            "（未闭环步骤会被拦）。"),
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
        # F4 同域 fallback（#956 拍板）：无专属 persona 时垫底参考注入（硬返回）
        out.update(_fallback_payload(code))
    else:
        out["hint"] = ("该工作性质尚无 persona prompt；可先喂历史产出物或积累偏好后 "
                       "twin(action=\"compile\") 生成，本次按通用标准执行")
        out.update(_fallback_payload(code))
    # playbook 注入（v0.4 P3）：本类型 → global 两级回退，连续性分级
    pb_conn = db.connect()
    try:
        from . import playbook_actions
        playbook_actions.inject_playbook(pb_conn, out, code, data)
    finally:
        pb_conn.close()
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
    if err := _brief_guard(str(data.get("brief") or "").strip()):
        # brief 可选覆盖 update_deliverable，超长同样打回（对抗评审轮2 P2-5）
        return {"ok": False, "error": "invalid_input", "field": "brief", "reason": err}
    # 收口门预检（v0.4 P1）：建过计划且有未闭环步骤 → 拒绝（对账引导，把墙变对账
    # 机会）。此为快筛——真正的门在下方条件 UPDATE 的 NOT EXISTS 里原子复查
    # （对抗评审轮2 P2-1：门查后他宿主 plan_set 插入未闭环步骤的竞窗）
    gate_conn = db.connect()
    try:
        gate = exec_actions.submit_gate(gate_conn, int(tid))
        # 采纳率提醒（v0.4.4 W1）：无计划交付=执行经验静默流失——advisory 警告
        # 把流失变成可见信号；含 replanned 跳过行的任务算有计划（has_plan 行存在性）
        no_plan = not exec_actions.has_plan(gate_conn, int(tid))
    finally:
        gate_conn.close()
    if gate is not None:
        return gate
    # submit 即快照会话 todos 进任务行（plan-mode submit_plan 同款），resume 才有得恢复；
    # 空会话传 None 保留原快照（review#5：空列表会把 COALESCE 当真值清掉 todos）
    session_todos = flow.current_todos(data.get("session"))
    flow.update_deliverable(int(tid), deliverable,
                            brief=str(data.get("brief") or "") or None,
                            todos=session_todos or None)
    # 条件迁移 + outcome 同语句（对抗评审轮2 P2-1）：写入瞬间原子复查收口门，
    # rowcount=0 时区分「状态已被并发迁移」与「门在窗口内失守」
    conn = db.connect()
    try:
        cur = conn.execute(
            "UPDATE twin_tasks SET status='submitted', reason=COALESCE(?, reason),"
            " decided_at=?, outcome='success' WHERE id=? AND status='planning'"
            " AND NOT EXISTS(SELECT 1 FROM twin_plan_steps WHERE task_id=?"
            " AND status IN"
            f" ({','.join('?' * len(exec_actions.STEP_OPEN_STATUSES))}))",
            (str(data.get("note") or "").strip() or None, db.now_iso(), int(tid),
             int(tid), *exec_actions.STEP_OPEN_STATUSES))
        if cur.rowcount == 0:
            row = conn.execute("SELECT status FROM twin_tasks WHERE id=?",
                               (int(tid),)).fetchone()
            if row is None:
                return {"ok": False, "error": "not_found", "reason": f"task id {tid}"}
            if row["status"] != "planning":
                return {"ok": False, "error": "invalid_input",
                        "reason": f"task {tid} 状态为 {row['status']!r}，不可提交"
                                  "（submitted 已是终态；交付后返工走 task_revise）"}
            gate = exec_actions.submit_gate(conn, int(tid))
            return gate if gate is not None else {
                "ok": False, "error": "invalid_input",
                "reason": "收口门并发复查未过：存在未闭环计划步骤，请重读后逐条对账"}
        block_warnings = exec_actions.blocking_questions_warning(conn, int(tid))
        conn.commit()
    finally:
        conn.close()
    out: dict = {"ok": True, "task_id": int(tid), "status": "submitted",
                 "guidance": ("已交付收口（task_submit 即终点）。用户对交付稿的修改与意见是"
                              "偏好信号：有反馈就 twin.write 沉淀（注明来源交付物）；"
                              "需要返工走 task_revise 生成修订任务。")}
    # 落盘在状态迁移之后（评审 P2-A5）：正文已在库，文件只是审计镜像；重读库内
    # 最新防并发写错版本（review#5 同款）；OSError/sqlite 失败降级 warning 不回滚
    fresh = flow.get_task(int(tid)) or {}
    try:
        out["deliverable_path"] = flow.write_deliverable_file(
            int(tid), fresh.get("deliverable_md") or "")
    except (OSError, sqlite3.Error) as e:
        out["warnings"] = [f"交付物文件写入失败（正文已在库）: {e}"]
    if block_warnings:
        # blocking 未解答疑问只警告不拒（v3.3 ⑥：防 owner 口头答复未写回的误伤）
        out.setdefault("warnings", []).extend(block_warnings)
    if no_plan:
        out.setdefault("warnings", []).append(
            "[经验沉淀] 本任务未建执行计划，执行经验（步骤路径/失败反思/工具记录）"
            "不会进入 playbook 沉淀。若属重复执行型工作：下次开工先 "
            "twin(action=\"plan_set\")；若属一次性事务：下次无需 task_start 建档。")
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
    # 计划深拷贝（v3.3 ③）：未完结步骤/疑问带入新任务行，旧代冻结可审计
    copy_conn = db.connect()
    try:
        plan_copied = exec_actions.copy_plan_to_task(copy_conn, int(tid), new_record["id"])
        copy_conn.commit()
    finally:
        copy_conn.close()
    out: dict = {
        "ok": True, "resumed_task_id": int(tid), "new_task_id": new_record["id"],
        "brief": record["brief"],
        "prior_deliverable_md": record.get("deliverable_md") or "",
        "restored_todos": old_todos,
        "guidance": (f"已从任务 #{tid} 续作（新任务 #{new_record['id']}）。"
                     "先核对自上次以来的变化，再继续执行并 task_submit。"),
    }
    if plan_copied["steps"]:
        out["plan_copied"] = plan_copied
        out["guidance"] += (f"上一代计划已带入本代（步骤 {plan_copied['steps']} 个、"
                            f"open 疑问 {plan_copied['questions']} 个），"
                            "从首个未闭环步骤继续（step_update 推进）。")
        if plan_copied["deps_dropped"]:
            out.setdefault("warnings", []).append(
                f"{plan_copied['deps_dropped']} 个依赖指向上一代已终结步骤，已自动解除")
        if plan_copied.get("questions_dropped"):
            out.setdefault("warnings", []).append(
                f"{plan_copied['questions_dropped']} 个 open 疑问因关联步骤已全部闭环，"
                "未带入新代")
    if not old_todos:
        # 轮2 对抗 P3-4：与 deps_dropped/questions_dropped 警告追加式并存，
        # 不再直接赋值覆盖（此前会吞掉刚 append 的深拷贝降级警告）
        out.setdefault("warnings", []).append("原任务没有 todos——可能已全部完成")
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
        out.update(_fallback_payload(resume_code))
    else:
        out["hint"] = "该工作性质尚无 persona prompt；可先 compile 生成或按通用标准执行"
        out.update(_fallback_payload(resume_code))
    # playbook 注入（v0.4 P3）：available_tools 缺省沿用建档时的自报（任务行）
    pb_data = dict(data)
    if pb_data.get("available_tools") is None:
        try:
            stored = json.loads(record.get("available_tools") or "null")
        except (ValueError, TypeError):
            stored = None
        if isinstance(stored, list):
            pb_data["available_tools"] = stored
    pb_conn = db.connect()
    try:
        from . import playbook_actions
        playbook_actions.inject_playbook(pb_conn, out, resume_code, pb_data)
    finally:
        pb_conn.close()
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
    # 计划深拷贝（v3.3 ③）：修订=返工重走，未完结步骤带入子任务
    copy_conn = db.connect()
    try:
        plan_copied = exec_actions.copy_plan_to_task(copy_conn, int(tid), child["id"])
        copy_conn.commit()
    finally:
        copy_conn.close()
    out: dict = {"ok": True, "parent_task_id": int(tid), "task_id": child["id"],
                 "iteration": child["iteration"], "status": child["status"],
                 "guidance": (f"已生成修订版任务 #{child['id']}（第 {child['iteration']} 次修订，"
                              "回到 planning 重走执行）。继续执行后 task_submit。")}
    if plan_copied["steps"]:
        out["plan_copied"] = plan_copied
        if plan_copied["deps_dropped"]:
            out.setdefault("warnings", []).append(
                f"{plan_copied['deps_dropped']} 个依赖指向上一代已终结步骤，已自动解除")
        if plan_copied.get("questions_dropped"):
            out.setdefault("warnings", []).append(
                f"{plan_copied['questions_dropped']} 个 open 疑问因关联步骤已全部闭环，"
                "未带入新代")
    return out


def _action_task_close(data: dict) -> dict:
    tid = data.get("task_id")
    if tid is None:
        return {"ok": False, "error": "invalid_input", "reason": "需要 task_id"}
    tid = _coerce_task_id(tid)
    outcome = data.get("outcome")
    if outcome is not None and outcome not in ("failed", "superseded"):
        return {"ok": False, "error": "invalid_input", "field": "outcome",
                "reason": "outcome 仅接受 failed | superseded（缺省 superseded）"}
    flow.ensure_schema()
    record = flow.get_task(int(tid))
    if not record:
        return {"ok": False, "error": "not_found", "reason": f"task id {tid}"}
    if record["status"] not in flow._OPEN_STATUSES:
        return {"ok": False, "error": "invalid_input",
                "reason": f"task {tid} 状态为 {record['status']!r}，仅进行中（planning）可关闭"}
    conn = db.connect()
    try:
        # 单事务（对抗评审轮2 P2-2）：先条件迁移任务状态（并发失败=整体无副作用，
        # 不会留下已强跳的步骤），成功后再批量跳步骤，一次 commit——原先三段
        # 分离事务在竞争失败后会让调用方误以为 close 未生效，WIP 现场不可逆丢失
        cur = conn.execute(
            "UPDATE twin_tasks SET status='superseded', reason=COALESCE(?, reason),"
            " decided_at=?, outcome=? WHERE id=? AND status IN"
            f" ({','.join('?' * len(flow._OPEN_STATUSES))})",
            ((str(data.get("reason") or "closed").strip() or None), db.now_iso(),
             outcome or "superseded", int(tid), *flow._OPEN_STATUSES))
        if cur.rowcount == 0:
            row = conn.execute("SELECT status FROM twin_tasks WHERE id=?",
                               (int(tid),)).fetchone()
            if row is None:
                return {"ok": False, "error": "not_found", "reason": f"task id {tid}"}
            return {"ok": False, "error": "invalid_input",
                    "reason": f"task {tid} 状态为 {row['status']!r}，仅进行中（planning）可关闭"}
        closed_steps = exec_actions.close_task_steps(conn, int(tid))
        conn.commit()
    finally:
        conn.close()
    out: dict = {"ok": True, "task_id": int(tid), "status": "superseded",
                 "guidance": "任务已显式关闭（历史保留可审计）。"}
    if closed_steps:
        out["warnings"] = [f"{closed_steps} 个未闭环步骤已按 closed 跳过"]
    return out


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
