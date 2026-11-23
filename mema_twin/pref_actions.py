"""偏好与治理动作（v0.3.8 从 server.py 拆出）：write / get / taxonomy / pending /
resolve / void / status。

write 经归一门（v0.3.8 B）：三维度值未命中清单即整笔打回（附动态清单 + 裁定
票据），Agent 必须问用户 resolve(map|canonicalize) 后重试——首写即问，不留
杂项桶。taxonomy 动态化：清单从 twin_types 实时读（治理追加/自建码立刻可见）。
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3

from . import db, flow, identity, normalize, scan, store, taxonomy, templates

_RAW_MAX_CHARS = 200      # 三维度值：短枚举说法
_CONTENT_MAX_CHARS = 8000  # 偏好正文

# S6（v0.6.1）：write hint 同 client+work_type+版本 第二次起省略——指引非契约，
# 重复沉淀场景每次都带同一句是纯 token 税（实测卡点 6）。duplicate=true 的
# note 不受影响（幂等提示必须保留）。进程内记忆，重启重现，容量帽防灌爆。
_WRITE_HINT_SEEN: dict[tuple[str, str], int] = {}
_WRITE_HINT_CAP = 4096

# S7（v0.6.1）：相似提示阈值——difflib 字面相似度（纯词法，非语义判定）。
# 0.82 实测校准（方案附记）：同义改写样例 0.93、同条增补样例 0.84（须命中）、
# 无关样例 ~0.30（须不命中）——0.85 会漏增补型，0.82 三点全覆盖；
# quick_ratio 0.75 预筛（quick_ratio≥ratio 恒成立，数学上无漏报）
_SIM_THRESHOLD = 0.82
_SIM_QUICK = 0.75
_SIM_MAX_CHARS = 2000  # 任一侧超长跳过（防 8000² ratio 阻塞请求路径）
_SIM_MAX_ROWS = 100    # 比对集帽：最近 100 行


def _today() -> str:
    return _dt.date.today().isoformat()


def _seen_hint(client: str, code: str, version: int | None) -> bool:
    """S6：同 client+code+active 版本 已提示过 hint → True（本次省略）。
    键含租户前缀（多租户形态同宿主跨租户不串扰——R2 险-3）。"""
    from . import tenant as _tenant
    key = (f"{_tenant.current_tenant() or ''}:{client or ''}", code or "")
    if len(_WRITE_HINT_SEEN) >= _WRITE_HINT_CAP and key not in _WRITE_HINT_SEEN:
        _WRITE_HINT_SEEN.clear()
    ver = int(version) if version is not None else 0
    if _WRITE_HINT_SEEN.get(key) == ver:
        return True
    _WRITE_HINT_SEEN[key] = ver
    return False


def _rank_similar(new_text: str, items: list[dict], text_key: str,
                  threshold: float, project: tuple[str, ...] | None = None) -> list[dict]:
    """公共相似比对核心（v0.6.2 D5 抽取，S7/D5/normalize.similar_values 共用
    护栏：任一侧 >2000 字符跳过、长度差 >4 倍跳过、quick 0.75 预筛、
    ratio≥threshold、最多 3 条、按 ratio 降序）。items 需含 text_key 字段；
    project 指定响应白名单键（None=原条目全字段——调用方必须显式投影，防
    快照/正文全文经相似通道泄漏进响应，R1 P1-2/P1-3）。"""
    from difflib import SequenceMatcher
    if len(new_text) > _SIM_MAX_CHARS:
        return []
    sim: list[dict] = []
    for item in items:
        old = item.get(text_key) or ""
        if not old or len(old) > _SIM_MAX_CHARS:
            continue
        if max(len(old), len(new_text)) > 4 * min(len(old), len(new_text)):
            continue
        sm = SequenceMatcher(None, old, new_text)
        if sm.quick_ratio() < _SIM_QUICK:
            continue
        ratio = sm.ratio()
        if ratio >= threshold:
            out = ({k: item[k] for k in project if k in item}
                   if project else dict(item))
            out["ratio"] = round(ratio, 2)
            sim.append(out)
            if len(sim) >= 3:
                break
    sim.sort(key=lambda x: -x["ratio"])
    return sim


def _similar_existing(conn, wt_code: str, exclude_id: int, new_text: str) -> list[dict]:
    """S7 相似提示（软通道）：difflib 字面相似度——纯词法非语义判定，不拦截
    不落票，落库后把「库里有长得像的」递给 Agent 自裁（是否 void 新写的）。
    护栏：任一侧 >2000 字符跳过（防 8000² ratio 阻塞）、长度差 >4 倍跳过、
    quick_ratio 预筛、最多 3 条、比对集最近 100 行、排除自身。核心循环已抽
    _rank_similar（D5 孤儿票共用）。"""
    rows = conn.execute(
        "SELECT memory_id, subject, content FROM twin_evidence"
        " WHERE work_type=? AND status IN ('uncompiled','compiled')"
        " AND memory_id != ? ORDER BY memory_id DESC LIMIT ?",
        (wt_code, exclude_id, _SIM_MAX_ROWS)).fetchall()
    items = [{"memory_id": int(r["memory_id"]),
              "subject": r["subject"] or "",
              "content": r["content"] or ""} for r in rows]
    return _rank_similar(new_text, items, "content", _SIM_THRESHOLD,
                         project=("memory_id", "subject"))


def _orphan_tickets(conn, client: str, new_text: str) -> list[dict]:
    """D5 孤儿票提示（v0.6.2）：同宿主此前被拒写入的挂票（有快照）与本次正文
    相似 → 疑似已被本次取代（维度合法化后重试成功），提示 Agent 显式 reject。
    关联口径=同 client+正文相似；mapped/canonicalized 票不进扫描（已裁定）；
    服务端不自动关票（作废动作归 Agent——R2 险-6 guidance 提醒核维度）。
    client 空串（stdio 无头）参与匹配：单机形态全部调用天然同宿主（R2 险-3
    的跨宿主串扰只存在于带头 http 形态，那里 client 非空正常区分）。"""
    if len(new_text) > _SIM_MAX_CHARS:
        return []
    rows = conn.execute(
        "SELECT id AS pending_id, type_kind, raw_value, content_snapshot"
        " FROM twin_pending_values WHERE client=? AND status='pending'"
        " AND content_snapshot != '' ORDER BY id DESC LIMIT ?",
        (client, _SIM_MAX_ROWS)).fetchall()
    items = [{"pending_id": int(r["pending_id"]), "type_kind": r["type_kind"],
              "raw_value": r["raw_value"],
              "content_snapshot": r["content_snapshot"]} for r in rows]
    return _rank_similar(new_text, items, "content_snapshot", _SIM_THRESHOLD,
                         project=("pending_id", "type_kind", "raw_value"))


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
    # 任务上下文维度继承（#959，v0.3.10）：改稿/返工现场沉淀偏好时，缺省维度沿用
    # 所属任务行的三维度——task_start 建档时已过归一门（canonical code），重复重判
    # 属多余打扰。显式传入优先于继承；只补必填维度（scope=audience 不继承
    # work_type）；裸写不传 task_id 照旧全显式走归一门（#951 数据入口正确性不动）。
    inherited: list[str] = []
    task_ctx: dict | None = None
    raw_tid = data.get("task_id")
    if raw_tid is not None:
        if isinstance(raw_tid, bool) or not isinstance(raw_tid, (int, str)):
            return {"ok": False, "error": "invalid_input", "field": "task_id",
                    "reason": f"invalid task_id: {raw_tid!r}（任务整数 id，task_recent/task_get 可查）"}
        try:
            tid = int(raw_tid)
        except ValueError:
            return {"ok": False, "error": "invalid_input", "field": "task_id",
                    "reason": f"invalid task_id: {raw_tid!r}（任务整数 id，task_recent/task_get 可查）"}
        if str(tid) != str(raw_tid).strip() and not isinstance(raw_tid, int):
            return {"ok": False, "error": "invalid_input", "field": "task_id",
                    "reason": f"invalid task_id: {raw_tid!r}（任务整数 id，task_recent/task_get 可查）"}
        flow.ensure_schema()  # fresh 库直传 task_id 也走 not_found 判定，而非 no such table
        record = flow.get_task(tid)
        if not record:
            return {"ok": False, "error": "invalid_input", "field": "task_id",
                    "reason": f"任务 #{tid} 不存在（task_recent/task_get 查 id）"}
        task_ctx = record
        for f in required:
            if not str(data.get(f) or "").strip():
                v = str(record.get(f) or "").strip()
                if v:
                    data[f] = v
                    inherited.append(f)
    for f in required:
        if not str(data.get(f) or "").strip():
            reason = ("required"
                      if task_ctx is None
                      else f"required——任务 #{task_ctx.get('id')} 的 {f} 为空，继承不到，请显式传入")
            return {"ok": False, "error": "invalid_input", "field": f, "reason": reason}
        if len(str(data[f])) > _RAW_MAX_CHARS:
            return {"ok": False, "error": "invalid_input", "field": f,
                    "reason": f"超出 {_RAW_MAX_CHARS} 字符上限（维度值应是短枚举说法）"}
    # 用户 tags 剥离 twin: 前缀（对抗 review#13）：维度命名空间只归归一层管
    # （校验先于归一门——轮2 P3-3：miss+脏 tags 不该先制造裁定义务再被打回）
    raw_tags = data.get("tags") or []
    if not isinstance(raw_tags, list):
        return {"ok": False, "error": "invalid_input", "field": "tags",
                "reason": "tags 必须是字符串列表"}
    user_tags = [str(t) for t in raw_tags
                 if not str(t).startswith("twin:") and str(t) != "twin-preference"]
    # v0.6.2 D4（R2 炸-2）：client 提取前置到归一门之前——miss 经 normalize_value
    # 带进票据（client 列），否则票据宿主归属落空串、D5 孤儿匹配上线即死。
    # 副作用声明：脏 client + 归一 miss 的错误优先级从 unmatched_value 变
    # invalid_input（client 校验先行），测试钉住。
    client = identity.effective_client(data) or ""
    conn = db.connect()
    dims: dict = {}
    misses: list[dict] = []
    try:
        kinds = ("audience", "purpose") if audience_scoped else taxonomy.KINDS
        for kind in kinds:
            # 归一门（v0.3.8）：未命中不落库——收集后整笔打回
            r = normalize.normalize_value(kind, str(data[kind]), conn,
                                          content=content, client=client,
                                          source_kind="write")
            if r.get("error"):
                return {"ok": False, "error": "invalid_input", "field": kind, "reason": r.get("reason")}
            dims[kind] = r
            if not r.get("ok"):
                misses.append(r)
    finally:
        conn.close()
    if misses:
        gate = normalize.gate_reject(misses)
        if gate is not None:
            return gate
        # 全部被并发裁定：dims 已被 gate_reject 就地改写为命中
    if audience_scoped:
        aud_code = dims["audience"]["code"]
        dims["work_type"] = {"ok": True, "kind": "work_type", "raw": "(受众级偏好)",
                             "code": store.audience_profile_code(aud_code),
                             "label_zh": dims["audience"].get("label_zh"), "matched_by": "audience_scope"}
    # v0.5.0 本地化（#1604）：偏好本体直写 twin_evidence（词法去重 + 本地发号，
    # 事务在 db.add_local_evidence 内）。tags 前缀家族（twin:/twin-preference）
    # 是 mema 桶行的维度外化物，自持库维度就在行上不再需要；user_tags 与
    # source_ref 原样落行（API 契约保留）。
    tags_json = json.dumps(user_tags, ensure_ascii=False)
    source_ref = str(data.get("source_ref") or "")
    subject = str(data.get("subject") or (
        f"受众级偏好：{dims['audience'].get('label_zh') or dims['audience'].get('raw')}"
        if audience_scoped else f"工作偏好：{dims['work_type'].get('raw')}"))
    # v0.5.1：调用方宿主标识随证据行落库（此前只喂审计，证据行没有归因；
    # 页面「写入方」显示的就是这个值）。http 头存在时头是权威，见 identity。
    conn = db.connect()
    try:
        res = db.add_local_evidence(conn, dims, subject=subject, content=content,
                                    tags_json=tags_json, source_ref=source_ref,
                                    client=client)
        mid = res["memory_id"]
        out: dict = {"ok": True, "duplicate": res["duplicate"],
                     "memory_id": mid, "evidence_id": mid,
                     "memory": {"id": mid, "subject": subject},
                     "dimensions": dims}
        if inherited:
            # 继承透明面：哪些维度来自任务行（显式传入的不在此列）
            out["dims_inherited"] = inherited
        # v0.6.2 D5 孤儿票提示（插桩在 dup 分支前，两路共用——R1 P2-6）：同宿主
        # 此前被拒的写入若与本次正文相似，疑似已被本次（维度合法化后）取代——
        # 提示 Agent 显式 reject 关票，服务端不自动关（作废动作归 Agent）
        orphans = _orphan_tickets(conn, client, content)
        if orphans:
            out["orphan_pending_tickets"] = orphans
            out["orphan_guidance"] = (
                "以上挂票疑似已被本次写入取代（维度已合法化）——确认后 "
                "resolve(reject) 关闭。先核对 type_kind/raw_value 是否属本次被拒"
                "写入的维度：不同维度的挂票可能是另一笔未完成写入，勿盲目 reject")
        if res["duplicate"]:
            out["note"] = ("同类型已存在内容完全相同的偏好（词法去重命中），本次未重复"
                           "写入；本响应即幂等成功，请勿重试、也无需改写")
            return out
        # S7 相似提示（软通道，不拦截）：库里有字面相似的既有偏好时递给 Agent
        # 自裁——相似≠重复，服务端不判语义（v0.5.0 拍板），是否 void 新写的由
        # Agent/用户裁定
        sim = _similar_existing(conn, dims["work_type"]["code"], mid, content)
        if sim:
            out["similar_existing"] = sim
        client_id = client or ""
        wt_code = dims["work_type"]["code"]
        if audience_scoped:
            zh = dims["audience"].get("label_zh") or dims["audience"]["raw"]
            hint = (f"已沉淀为对「{zh}」的受众级通用偏好（不绑定工作类型）；"
                    "该受众有新证据时，夜间任务会重抽象其画像，"
                    "对该受众的任何任务开工时自动带上")
            if not _seen_hint(client_id, wt_code, None):
                out["hint"] = hint
        else:
            active = store.get_active(conn, dims["work_type"]["code"])
            ver = active.get("version") if active else None
            if ver is not None:
                hint = (f"{dims['work_type']['label_zh']} 已有 persona prompt v{ver}；"
                        "本次偏好已入池未编译——无需立即整理，"
                        "由用户决定何时 compile，或等定时扫描统一处理")
            else:
                hint = (f"{dims['work_type']['label_zh']} 尚无 persona prompt，"
                        "可 twin(action=\"compile\") 生成 v1"
                        f"（{templates.STRONG_MODEL_NOTE}）")
            if not _seen_hint(client_id, wt_code, ver):
                out["hint"] = hint
    finally:
        conn.close()
    return out


def _plan_stats(conn) -> dict:
    """执行统计面（v0.4 P2，现用现算）：口径=分母为全部任务（不剔 superseded），
    用于趋势观察不作考核（help/status 条目同款声明）。"""
    q = conn.execute
    tasks_total = q("SELECT COUNT(*) AS c FROM twin_tasks").fetchone()["c"]
    tasks_planned = q(
        "SELECT COUNT(DISTINCT task_id) AS c FROM twin_plan_steps").fetchone()["c"]
    steps = q(
        "SELECT status, COUNT(*) AS c FROM twin_plan_steps GROUP BY status").fetchall()
    by = {r["status"]: int(r["c"]) for r in steps}
    wm = int(flow.get_meta("plan_eval_watermark") or 0)
    unevaluated = q(
        "SELECT COUNT(*) AS c FROM twin_tasks WHERE status IN"
        " ('submitted','superseded') AND id > ?", (wm,)).fetchone()["c"]
    # 采纳率观测（v0.4.4 W2）：交付任务里带计划的比例——分母只算 submitted
    # （close/被让位不进），旧库历史 submitted 会稀释读数，看增量任务为主
    tasks_submitted = q("SELECT COUNT(*) AS c FROM twin_tasks"
                        " WHERE status='submitted'").fetchone()["c"]
    submitted_with_plan = q(
        "SELECT COUNT(*) AS c FROM twin_tasks t WHERE t.status='submitted'"
        " AND EXISTS(SELECT 1 FROM twin_plan_steps WHERE task_id=t.id)"
    ).fetchone()["c"]
    tool = q("SELECT COUNT(*) AS c,"
             " SUM(CASE WHEN outcome!='success' THEN 1 ELSE 0 END) AS f"
             " FROM twin_tool_usage").fetchone()
    playbooks: dict = {}
    for r in q(
            "SELECT key, version, last_used_client, last_used_at FROM twin_playbooks"
            " WHERE status='active' ORDER BY key").fetchall():
        playbooks[r["key"]] = {"active_version": int(r["version"]),
                               "last_used_client": r["last_used_client"],
                               "last_used_at": r["last_used_at"]}
    pb_rejects = []
    for key, raw in sorted(flow.list_meta("playbook_reject:").items()):
        try:
            rec = json.loads(raw)
        except (ValueError, TypeError):
            rec = {}
        if not isinstance(rec, dict):
            rec = {}
        pb_rejects.append({"key": key[len("playbook_reject:"):],
                           "count": rec.get("count"), "last_check": rec.get("last_check"),
                           "last_at": rec.get("last_at")})
    out = {
        "tasks_total": tasks_total,
        "tasks_planned": tasks_planned,
        "tasks_submitted": tasks_submitted,
        "submitted_with_plan": submitted_with_plan,
        "steps_total": sum(by.values()),
        "steps_failed": by.get("failed", 0),
        "steps_skipped": by.get("skipped", 0),
        "steps_backfilled": int(q(
            "SELECT COUNT(*) AS c FROM twin_plan_steps WHERE backfilled=1"
        ).fetchone()["c"]),
        "open_blocking_questions": int(q(
            "SELECT COUNT(*) AS c FROM twin_plan_questions"
            " WHERE status='open' AND blocking=1").fetchone()["c"]),
        "tasks_unevaluated": unevaluated,
        "tool_usage_total": int(tool["c"] or 0),
        "tool_usage_fail": int(tool["f"] or 0),
        "playbooks": playbooks,
    }
    if pb_rejects:
        out["playbook_rejected"] = pb_rejects
    # 审计面（v0.4.6）：调用计数与失败率——观测「agent 困在流程」类循环的直接
    # 指标（step_update/plan_* 高频 + ok 率低即空转信号）。环形缓冲近 2 万条，
    # 计数是近期窗口非全史。审计关闭（MEMA_TWIN_AUDIT=0）时返回 {"enabled": False}。
    stats = _audit_stats()
    if stats:
        out["audit"] = stats
    return out


def _audit_stats() -> dict:
    """审计日志摘要（v0.4.6）：现用现算（SQL 聚合，不全量解析 JSON）。
    unreachable 单列：mema 服务离线期间的失败不该混进「agent 空转失败率」
    （评审 P2-6）。"""
    from . import audit
    stats = audit.stats()
    if stats.get("enabled"):
        stats["last_24h"] = audit.recent_24h()
    return stats


def _action_status(data: dict) -> dict:
    # v0.6.4（owner 拍板）：版本/画像明细**永远只回当前行**（active；无 active
    # 回最高版本）——全量历史没有真实消费方（夜间任务只看 uncompiled/stale
    # 信号；宿主只要 active+体积；人工增长曲线走 console 直连；rollback 版本
    # 发现有兜底：省略 version 回上一版、报错自带可用列表、get(version=) 按需）。
    # 响应 17KB→KB 级；历史明细经 get(version=) 按需取。数组缩短对按键消费的
    # 宿主是安全变化。
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT work_type, version, model, status, evidence_count, created_at, prompt_md"
            " FROM twin_prompt_versions ORDER BY work_type, version",
        ).fetchall()
        # v0.6.4：每工种只留当前行（active 优先，无 active 回最高版本）——
        # Python 后滤，实现简单性优先（86 行上限无性能压力）
        keep: dict[str, dict] = {}
        order: list[str] = []
        for r in rows:
            wt = r["work_type"]
            cur = keep.get(wt)
            if cur is None:
                keep[wt] = dict(r)
                order.append(wt)
                continue
            better = ((r["status"] == "active" and cur["status"] != "active")
                      or (r["status"] == cur["status"]
                          and r["version"] > cur["version"]))
            if better:
                keep[wt] = dict(r)
        rows = [keep[wt] for wt in order]
        versions: dict = {}
        audience_profiles: dict = {}
        for r in rows:
            aud = store.split_audience_profile(r["work_type"])
            bucket = audience_profiles if aud is not None else versions
            v = bucket.setdefault(r["work_type"],
                                  {"work_type": r["work_type"], "active": None, "versions": []})
            item = dict(r)
            size = len(item.pop("prompt_md") or "")
            item["size_chars"] = size  # v0.6.4：当前行体积；完整增长曲线走 console 直连
            v["versions"].append(item)
            if r["status"] == "active":
                v["active"] = r["version"]
                # 硬预算可见面（v0.3.7）：active 正文体积与超预算标记（常显不打扰；
                # 预算的强制力在编译规则，机器不拦）
                limit = (templates.BUDGET_AUD_CHARS if aud is not None
                         else templates.BUDGET_TYPE_CHARS)
                v["size_chars"] = size
                v["over_budget"] = size > limit
        pending = db.list_pending(conn)
        # 受众画像触发器（AR-2）：证据数 ≠ 画像 evidence_count（或尚无画像）→ 需重抽象
        aud_counts = conn.execute(
            "SELECT audience, COUNT(*) AS n FROM twin_evidence"
            " WHERE audience IS NOT NULL AND work_type IS NOT NULL"
            " AND status != 'void'"  # v0.3.7：作废行不进画像触发计数
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
    finally:
        conn.close()
    # v0.3.7 夜间被拒计数（持续被拒/空转闭环的唯一出口信号，成功落版即清）
    flow.ensure_schema()
    rejects = []
    for key, raw in sorted(flow.list_meta("nightly_reject:").items()):
        try:
            rec = json.loads(raw)
        except (ValueError, TypeError):
            rec = {}
        if not isinstance(rec, dict):
            rec = {}
        rejects.append({"work_type": key[len("nightly_reject:"):],
                        "count": rec.get("count"), "last_check": rec.get("last_check"),
                        "last_at": rec.get("last_at")})
    if rejects:
        out["nightly_rejected"] = rejects
    # v0.3.7 条款作废待重编清单（夜间自动重编，成功落版即清）
    stale_list = []
    for key, raw in sorted(flow.list_meta("persona_stale:").items()):
        try:
            rec = json.loads(raw)
        except (ValueError, TypeError):
            rec = {}
        if not isinstance(rec, dict):
            rec = {}
        voided = rec.get("voided")
        stale_list.append({"work_type": key[len("persona_stale:"):],
                           "at": rec.get("at"),
                           "voided": voided if isinstance(voided, list) else []})
    if stale_list:
        out["persona_stale"] = stale_list
    # 治理计数（v0.3.8：open_tasks 仅数 planning——submitted 已是终态）
    out["open_tasks"] = len(flow.open_tasks())
    # 执行统计面（v0.4 P2）：现用现算，趋势观察用不作考核
    try:
        stats_conn = db.connect()
        try:
            out["plan_stats"] = _plan_stats(stats_conn)
        finally:
            stats_conn.close()
    except sqlite3.Error:
        pass  # 软失败：统计面缺失不影响其余 status 输出
    # v0.5.0：mema 桶冲突计数（open_conflicts）随 sink 数据面退役整体移除
    #（拆分后偏好不在 mema，写时语义冲突检测亦不服务 twin——见方案 §3.4）
    try:
        notice = scan.scan_notice()
    except Exception:  # noqa: BLE001 —— 提醒是观测件，任何故障不击穿 status（twin_notices 同款）
        notice = None
    if notice:
        out["scan_notice"] = notice
    return out


def _action_get(data: dict) -> dict:
    # v0.4.8：get 复用 _supplement_payload（读通道补未编译增补）。函数内
    # 延迟 import——顶层会成环（pref → task → compile → pref，2026-11-09 实测）
    from . import task_actions
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
        is_aud = aud is not None
        if is_aud:
            # 受众画像只读通道（AR-6）：aud-{受众码} 可查全文；后缀必须是已知受众
            if not store._is_known_audience(conn, aud):
                return {"ok": False, "error": "invalid_input", "field": "work_type",
                        "reason": f"unknown audience code: {aud!r}"}
            code = wt
        else:
            code = store.resolve_work_type_code(conn, wt)
            if not code:
                return {"ok": False, "error": "invalid_input", "field": "work_type",
                        "reason": "unknown code（先 twin(action=\"taxonomy\") 查码；"
                                  "治理追加值见 pending 票据）"}
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
        out: dict = {"ok": True, "work_type": code, "prompt_md": None,
                     "hint": "尚无 persona prompt；可先喂历史产出物或积累偏好后 compile"}
        # v0.4.8 B：读通道补未编译增补（逃生口完整性）——get 是 revise 等链路
        # 「上文不可见」时的取回口，此前只回全文不带增补。仅 work_type 读
        # active 时附；aud- 画像与 versioned 历史读不附（增补锚定 active 语义）
        if not is_aud and version is None:
            supp = task_actions._supplement_payload(code, None)
            if supp:
                out.update(supp)
                out["hint"] = ("该工作性质尚无编译版 persona，以上增补即已沉淀偏好，"
                               "按其执行；积累后可 twin(action=\"compile\") 生成 v1")
        return out
    out = {"ok": True, **rec}
    if not is_aud and version is None:
        # 同上：active 读取附未编译增补（本地自取，软失败不阻断全文返回）；
        # 不注 _fallback_payload——读通道与执行通道的有意不对称（方案 §1.3）
        out.update(task_actions._supplement_payload(
            code, {"version": rec.get("version")}))
    return out


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


def _action_taxonomy(data: dict) -> dict:
    """动态清单（v0.3.8 B3）：twin_types 实时读（治理追加别名/自建 canonical
    立刻可见），内置枚举兜底合并——与归一匹配侧同源。
    v0.4.7：kind 可传数组一次取多类或 'all' 取全部（判别式=响应有无 kinds 键，
    数组传 1 个元素也走 kinds 形状）；compact=true 精简投影（code/zh/aliases 前 5，
    砍 domain/en/is_custom——归一引导靠 aliases，完整版仍是唯一全量面）。"""
    kind_raw = data.get("kind")
    compact = data.get("compact") is True  # 严格布尔："false" 字符串不当作 true
    multi = False
    if isinstance(kind_raw, list):
        kinds = [str(k).strip() for k in kind_raw if str(k).strip()]
        multi = True
    elif kind_raw is None or (isinstance(kind_raw, str) and not kind_raw.strip()):
        kinds = ["work_type"]
    else:
        k = str(kind_raw).strip()
        if k == "all":
            kinds = list(taxonomy.KINDS)
            multi = True
        else:
            kinds = [k]
    bad = [k for k in kinds if k not in taxonomy.KINDS]
    if bad or not kinds:
        return {"ok": False, "error": "invalid_input", "field": "kind",
                "reason": f"expected one of {taxonomy.KINDS}（或数组/'all'），收到 {bad or kind_raw!r}"}
    seen: set = set()
    kinds = [k for k in kinds if not (k in seen or seen.add(k))]

    def _load(kind: str) -> list[dict]:
        conn = db.connect()
        try:
            rows = db.type_rows(conn, kind)
        finally:
            conn.close()
        merged: dict = {}
        for r in rows:
            desc = r["description"] if "description" in r.keys() else ""
            merged[r["code"]] = {"code": r["code"], "zh": r["label_zh"], "en": r["label_en"],
                                 "domain": r["domain"], "aliases": r["aliases"],
                                 "description": desc or taxonomy.description_for(r["code"]),
                                 "is_custom": bool(r["is_custom"])}
        for t in taxonomy.all_types(kind):
            merged.setdefault(t.code, {"code": t.code, "zh": t.zh, "en": t.en,
                                       "domain": t.domain, "aliases": list(t.aliases),
                                       "description": taxonomy.description_for(t.code),
                                       "is_custom": False})
        items = list(merged.values())
        if compact:
            out_items = []
            for it in items:
                raw = it.get("aliases")
                if isinstance(raw, str):
                    try:
                        import json as _json
                        raw = _json.loads(raw)
                    except ValueError:
                        raw = []
                aliases = list(raw)[:5] if isinstance(raw, list) else []
                out_items.append({"code": it["code"], "zh": it["zh"],
                                  "aliases": aliases})
            return out_items
        return items

    if not multi:
        # 单 kind：形状与历史版本逐键一致（既有断言钉死）
        items = _load(kinds[0])
        return {"ok": True, "kind": kinds[0], "count": len(items), "types": items}
    kinds_out: dict = {}
    total = 0
    for k in kinds:
        items = _load(k)
        for it in items:
            it["kind"] = k  # 防客户端拍平三类后错配（code 仅 kind 内唯一）
        kinds_out[k] = {"count": len(items), "types": items}
        total += len(items)
    return {"ok": True, "kinds": kinds_out, "count_total": total}


_PENDING_STATUSES = ("pending", "mapped", "canonicalized", "rejected")


def _action_pending(data: dict) -> dict:
    status = str(data.get("status") or "pending")
    if status not in _PENDING_STATUSES:
        # v0.6 A8-4：此前未知值静默返回 ok+空 items——归因误导（看起来「没有
        # 票据」其实是「查错状态」）。打回列合法值。
        return {"ok": False, "error": "invalid_input", "field": "status",
                "reason": f"status 仅接受 {'|'.join(_PENDING_STATUSES)}，收到 {status!r}"}
    conn = db.connect()
    try:
        rows = db.list_pending(conn, status)
    finally:
        conn.close()
    # v0.6.2 D4：显式投影（防 SELECT * 泄漏全文快照，R1 P1-3）——列表带预览与
    # 字数，全文在 resolve 响应按需取（积压批量全带会 72KB 级爆炸）
    items = []
    for r in rows:
        snap = r.get("content_snapshot") or ""
        items.append({"id": r["id"], "type_kind": r["type_kind"],
                      "raw_value": r["raw_value"],
                      "hit_count": r["hit_count"], "status": r["status"],
                      "resolved_code": r["resolved_code"],
                      "created_at": r["created_at"],
                      "client": r.get("client") or "",
                      "source_kind": r.get("source_kind") or "unknown",
                      "content_preview": snap[:200],
                      "content_chars": len(snap)})
    return {"ok": True, "status": status, "items": items}


def _resolve_ticket(conn, pid, decision, code, new_type,
                    commit: bool = True, confirm: bool = False) -> dict:
    """单票校验+落库（v0.6 B4 从 _action_resolve 抽出，单条/批量共用）。
    事务边界归调用方：commit=True 单条模式即时提交；commit=False 批量模式
    外层统一 commit/rollback（db helper 的 commit 参数随之联动）。

    v0.6.2 D3：canonicalize 相似确认门（新 code/zh 与既有值 ratio≥0.82 或精确
    撞别名 → 无 confirm 打回 require_confirm）；响应附 content_snapshot 全文
    （D4：裁定后 Agent 直接照抄 write，跨会话 3 步闭环零丢失）。"""
    if pid is None or decision not in ("map", "canonicalize", "reject"):
        return {"ok": False, "error": "invalid_input",
                "reason": "需要 pending_id 与 decision ∈ map|canonicalize|reject"}
    row = conn.execute("SELECT * FROM twin_pending_values WHERE id=?", (pid,)).fetchone()
    if not row:
        return {"ok": False, "error": "not_found",
                "reason": f"pending id {pid}"
                          "（pending_id 见写入打回响应 fields.*.pending_id，"
                          "或调 pending 动作查票据）"}
    if row["status"] != "pending":
        # 对抗 review#7：已裁定的 pending 不得重复裁定（重复 map 会把同一别名挂到
        # 第二个 canonical，归一结果由行序决定而非用户裁定）。多宿主并发同值
        # 票据被先裁定时，后来者的正确动作是直接重试原写入（v0.3.8）
        return {"ok": False, "error": "invalid_input",
                "reason": f"pending {pid} 已裁定为 {row['status']}（→{row['resolved_code']}），"
                          "不可重复裁定——若他方已替你裁定，直接重试原写入即可"}
    kind, raw = row["type_kind"], row["raw_value"]
    snap = row["content_snapshot"] or ""
    if decision == "map":
        code = str(code or "").strip()
        known = taxonomy.by_code(kind, code) or \
            any(r["code"] == code for r in db.custom_types(conn, kind))
        if not code or not known:
            return {"ok": False, "error": "invalid_input", "field": "code",
                    "reason": "map 需要已有 canonical code——全部现有值见 all_values",
                    "all_values": normalize.all_values(kind, conn)}
        db.append_alias(conn, kind, code, raw, commit=commit)
        db.set_pending(conn, int(pid), "mapped", code, commit=commit)
    elif decision == "canonicalize":
        nt = new_type or {}
        if not isinstance(nt, dict):
            return {"ok": False, "error": "invalid_input", "field": "new_type",
                    "reason": "new_type 必须是对象 {code,zh,en,domain,description?}"}
        desc = str(nt.get("description") or "")
        if len(desc) > 200:
            return {"ok": False, "error": "invalid_input", "field": "new_type.description",
                    "reason": f"description 超出 200 字符上限（收到 {len(desc)}）"}
        # v0.6.2 D3 相似确认门：与批量预扫共用同一 helper（R1 P1-4：单条/预扫
        # 两份逻辑漂移导致批量别名撞检破洞——预扫放行、门内命中=整批回滚）
        new_code = str(nt.get("code") or "").strip()
        new_zh = str(nt.get("zh") or "").strip()
        gate = _canonicalize_similar_gate(conn, kind, new_code, new_zh,
                                          confirm=confirm)
        if gate is not None:
            gate["all_values"] = normalize.all_values(kind, conn)
            return gate
        try:
            db.add_canonical(conn, kind, new_code, new_zh, str(nt.get("en") or ""),
                             str(nt.get("domain") or ""), [raw], commit=commit)
            if desc:
                conn.execute(
                    "UPDATE twin_types SET description=? WHERE type_kind=? AND code=?",
                    (desc, kind, new_code))
        except ValueError as e:
            return {"ok": False, "error": "invalid_input", "reason": str(e)}
        code = new_code
        db.set_pending(conn, int(pid), "canonicalized", code, commit=commit)
    else:
        code = None
        db.set_pending(conn, int(pid), "rejected", None, commit=commit)
    out: dict = {"ok": True, "pending_id": int(pid), "decision": decision}
    if code is not None:
        out["resolved_code"] = code
    # v0.6.2 D4：响应附快照全文（reject 也附——裁定者关票前最后一次看到内容）
    # v0.6.3：按 source_kind 区分引导（R2 险-1 二义性——task_start 的快照是建档
    # brief 非偏好正文，照抄 write 会产出垃圾证据）
    source_kind = row["source_kind"] if "source_kind" in row.keys() else ""
    if snap:
        out["content_snapshot"] = snap
        out["content_chars"] = len(snap)
        out["content_source"] = source_kind or "unknown"
        if decision in ("map", "canonicalize", "reject"):
            # task_start 来源的提醒在 reject 时同样附（R2 P2-1：关票前最后一次
            # 看到内容，brief 非偏好的认知在此刻同样有价值）
            if source_kind == "task_start":
                out["content_source_note"] = (
                    "此快照来自 task_start 建档 brief，是任务描述不是偏好——"
                    "照抄 write 会产出垃圾证据；仅当其中确有可复用的偏好时，"
                    "重写为偏好形式再 write（维度已合法化）")
            elif source_kind == "write" and decision != "reject":
                out["content_source_note"] = ("此快照是偏好正文，可直接照抄 write"
                                              "（维度已合法化）")
    if decision == "reject":
        out["note"] = ("已裁定不入体系。放弃本次写入，不得再拿原值重试"
                       "（票据会复活成新的裁定义务）")
    return out


def _canonicalize_similar_gate(conn, kind: str, new_code: str, new_zh: str,
                               confirm: bool) -> dict | None:
    """D3 相似确认门（单条与批量预扫共用，R1 P1-4）：新 code/zh 对既有值
    difflib（quick 0.75/ratio≥0.82，S7 同款常量）+ 既有正名/别名精确撞检
    （短串 difflib 不可靠——「老板」vs「直属上级」ratio 0.28，R1 P2-2）。
    命中且未 confirm → 打回 validation_failed（require_confirm+similar）；
    None=放行。注意 similar 列出的是**被撞的既有码**（非新值）。"""
    existing = normalize.all_values(kind, conn)
    probe = new_zh or new_code
    fuzzy = normalize.similar_values(probe, existing)
    fuzzy_codes = {v["code"] for v in fuzzy}
    exact_codes = [v["code"] for v in existing
                   if (new_code and new_code == v["code"])
                   or (new_zh and (new_zh == v["zh"]
                       or new_zh.casefold() in _kind_aliases(conn, kind, v["code"])))]
    similar = fuzzy + [{"code": c,
                        "zh": next((v["zh"] for v in existing if v["code"] == c), ""),
                        "ratio": 1.0}
                       for c in exact_codes if c not in fuzzy_codes]
    if not similar:
        return None
    if confirm is True:
        return None  # Agent 已对照 similar 清单确认非重复（承诺语义）
    return {"ok": False, "error": "validation_failed",
            "require_confirm": True,
            "similar": similar,
            "reason": (f"新维度值与现有值疑似重复（{new_code or new_zh!r}）——对照 "
                       "all_values 确认：确属新概念则带 confirm=true 重试（commit 语义："
                       "已对照清单确认非重复）；与现有重复请改走 map")}


def _kind_aliases(conn, kind: str, code: str) -> set[str]:
    """既有码的别名集（D3 精确撞检用）：DB 行优先，内置兜底。"""
    names: set[str] = set()
    row = conn.execute(
        "SELECT label_zh, aliases FROM twin_types WHERE type_kind=? AND code=?",
        (kind, code)).fetchone()
    if row is not None:
        names.add(row["label_zh"] or "")
        try:
            names.update(json.loads(row["aliases"] or "[]"))
        except (ValueError, TypeError):
            pass
    else:
        t = taxonomy.by_code(kind, code)
        if t:
            names.add(t.zh or "")
            names.update(t.aliases)
    return {n.strip().casefold() for n in names if n}


def _resolve_batch(tickets) -> dict:
    """批量裁定（v0.6 B4）：两段式（v0.6.2 D3，R2 炸-1 状态机）——
    ①confirm 预扫：相似命中且未 confirm 的票摘出为 confirm_required[] 条目
    （带 require_confirm+all_values），不进事务；②余票进现有单事务（硬失败仍
    整笔回滚+failed_index，对齐 step_update 批量范式）。响应三键并存：
    resolved[] + confirm_required[] + error——混合结果如实表达。"""
    if not isinstance(tickets, list) or not (1 <= len(tickets) <= 20):
        return {"ok": False, "error": "invalid_input", "field": "tickets",
                "reason": "tickets 需为 1..20 条的列表（每条 {pending_id, decision,"
                          " code?|new_type?, confirm?}）"}
    conn = db.connect()
    try:
        # ① confirm 预扫（只读）：canonicalize 且相似命中且未 confirm 的票摘出
        confirm_required: list[dict] = []
        runnable: list[tuple[int, dict]] = []
        for i, t in enumerate(tickets):
            if not isinstance(t, dict):
                conn.rollback()
                return {"ok": False, "error": "invalid_input", "field": "tickets",
                        "reason": f"tickets[{i}] 必须是对象", "failed_index": i}
            if t.get("decision") == "canonicalize":
                row = conn.execute(
                    "SELECT type_kind FROM twin_pending_values WHERE id=?",
                    (t.get("pending_id"),)).fetchone()
                kind = row["type_kind"] if row else None
                if kind is not None:
                    nt = t.get("new_type") or {}
                    # 与单条模式共用相似门 helper（R1 P1-4：预扫放行/门内命中
                    # 的漂移会让批量别名撞检整批回滚）
                    gate = _canonicalize_similar_gate(conn, kind,
                                                      str(nt.get("code") or "").strip(),
                                                      str(nt.get("zh") or "").strip(),
                                                      confirm=t.get("confirm") is True)
                    if gate is not None:
                        gate.pop("all_values", None)  # 批量条目体积：全量在 required 顶层附一份
                        gate.update({"failed_index": i,
                                     "pending_id": t.get("pending_id"),
                                     "require_confirm": True})
                        confirm_required.append(gate)
                        continue
            runnable.append((i, t))
        # ② 余票单事务
        resolved: list[dict] = []
        for i, t in runnable:
            fail = {"failed_index": i}
            try:
                r = _resolve_ticket(conn, t.get("pending_id"), t.get("decision"),
                                    t.get("code"), t.get("new_type"),
                                    commit=False,
                                    confirm=t.get("confirm") is True)
            except ValueError as e:
                conn.rollback()
                return {"ok": False, "error": "invalid_input",
                        "reason": str(e), **fail}
            except sqlite3.Error as e:
                conn.rollback()
                return {"ok": False, "error": "internal_error",
                        "reason": str(e), **fail}
            if not r.get("ok"):
                conn.rollback()
                r.update(fail)
                return r
            resolved.append(r)
        conn.commit()
    finally:
        conn.close()
    out: dict = {"ok": True, "resolved": resolved, "applied": len(resolved),
                 "guidance": "全部裁定完成，直接重试原写入即可"}
    if confirm_required:
        out["confirm_required"] = confirm_required
        out["guidance"] = ("部分票疑似与现有维度重复（见 confirm_required）——对照 "
                           "all_values 后带 confirm=true 重试该票，其余已裁定完成")
    return out


def _action_resolve(data: dict) -> dict:
    tickets = data.get("tickets")
    if tickets is not None:
        if "pending_id" in data or "decision" in data:
            return {"ok": False, "error": "invalid_input",
                    "reason": "tickets 批量与单条 pending_id/decision 互斥，二选一"}
        return _resolve_batch(tickets)
    conn = db.connect()
    try:
        return _resolve_ticket(conn, data.get("pending_id"), data.get("decision"),
                               data.get("code"), data.get("new_type"),
                               commit=True,
                               confirm=data.get("confirm") is True)
    finally:
        conn.close()


def _action_void(data: dict) -> dict:
    """作废一条偏好证据（v0.3.7 冲突裁定「新替旧 / 撤销新写的」的执行机制）。

    行级 status='void'，全链路排除（compile 全量集合/增补注入/画像投影/统计）；
    单向不可逆（作废错了：内容仍在 mema，重写一条即可）。曾入编译的证据作废后
    标记 persona_stale → 夜间自动重编剔除该条款；受众侧由 audience_stale 计数差
    触发重抽象。mema 本体的 retire/update 属其治理流程（需用户授权），twin 只管
    自己的证据索引。"""
    mid = data.get("memory_id")
    if isinstance(mid, bool) or not isinstance(mid, (int, str)):
        return {"ok": False, "error": "invalid_input", "field": "memory_id",
                "reason": "memory_id 需是记忆 id 整数"}
    try:
        n = int(mid)
    except ValueError:
        return {"ok": False, "error": "invalid_input", "field": "memory_id",
                "reason": f"invalid memory_id: {mid!r}"}
    if str(n) != str(mid).strip() and not isinstance(mid, int):
        return {"ok": False, "error": "invalid_input", "field": "memory_id",
                "reason": f"invalid memory_id: {mid!r}"}
    if n < 0 or n > 2**63 - 1:
        return {"ok": False, "error": "invalid_input", "field": "memory_id",
                "reason": f"invalid memory_id: {mid!r}"}
    conn = db.connect()
    try:
        row = db.void_evidence(conn, n)
    finally:
        conn.close()
    if row is None:
        return {"ok": False, "error": "not_found",
                "reason": f"memory_id {n} 不在 twin 证据索引中"}
    out: dict = {"ok": True, "memory_id": n, "work_type": row["work_type"],
                 "was_compiled": row["compiled_version"] is not None,
                 "already_void": bool(row.get("already_void"))}
    if out["already_void"]:
        out["note"] = "该证据已是作废状态，幂等无变更"
        return out
    # v0.6 B7：副作用从 note 长句拼接提为显式键（persona_stale/audience_stale），
    # note 收敛单句——句内「；」+段间「；」双层拼接机器不可拆（评估 P2-3）。
    # note 仍保留人读摘要；键为加法，旧宿主读 note 语义不破。
    wt = row["work_type"]
    if (row["compiled_version"] is not None and wt
            and store.classify_code(wt) == store.CODE_KIND_WORK_TYPE):
        _mark_persona_stale(wt, n)
        out["persona_stale"] = True
    if row["audience"]:
        out["audience_stale"] = [row["audience"]]
    out["note"] = ("作废完成（行保留、全链路排除，不可逆；正文已归档，需要可重写一条"
                   "新偏好）")
    return out


def _action_evidence(data: dict) -> dict:
    """列偏好证据索引（v0.6 B3）：void 死胡同的自纠通道——此前 memory_id 未知
    时 Agent 无任何列举手段（status 只有聚合）。只读、不回正文（content_chars
    代替，控体积；全文走 console）。status/work_type 过滤，limit 默认 50 上限
    200（超限打回附上限，不静默 clamp）。"""
    status = data.get("status")
    if status is not None and status not in ("uncompiled", "compiled", "void"):
        return {"ok": False, "error": "invalid_input", "field": "status",
                "reason": "status 仅接受 uncompiled|compiled|void，收到 "
                          f"{status!r}"}
    limit_raw = data.get("limit")
    limit = 50
    if limit_raw is not None:
        if isinstance(limit_raw, bool) or not isinstance(limit_raw, (int, str)):
            return {"ok": False, "error": "invalid_input", "field": "limit",
                    "reason": f"invalid limit: {limit_raw!r}"}
        try:
            limit = int(limit_raw)
        except ValueError:
            return {"ok": False, "error": "invalid_input", "field": "limit",
                    "reason": f"invalid limit: {limit_raw!r}"}
        if limit < 1:
            return {"ok": False, "error": "invalid_input", "field": "limit",
                    "reason": f"limit 需 ≥1，收到 {limit}"}
        if limit > 200:
            return {"ok": False, "error": "invalid_input", "field": "limit",
                    "reason": f"limit 上限 200，收到 {limit}"}
    conn = db.connect()
    try:
        where, args = [], []
        if status is not None:
            where.append("status=?")
            args.append(status)
        wt = str(data.get("work_type") or "").strip()
        if wt:
            if wt.startswith(store.AUD_PREFIX):
                # 受众伪类型证据行（work_type=aud-%）不过 work_type 码解析
                #（resolve 只收 work_type 类），直接列匹配——受众证据也能按类型过滤
                where.append("work_type=?")
                args.append(wt)
            else:
                code = store.resolve_work_type_code(conn, wt)
                if not code:
                    return {"ok": False, "error": "invalid_input", "field": "work_type",
                            "reason": "unknown code（先 twin(action=\"taxonomy\") 查码）"}
                where.append("work_type=?")
                args.append(code)
        sql = ("SELECT memory_id, work_type, audience, purpose, subject, status,"
               " created_at, length(content) AS content_chars FROM twin_evidence")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY memory_id LIMIT ?"
        args.append(limit)
        rows = conn.execute(sql, args).fetchall()
        # R2 对抗 R5：count 是 LIMIT 后行数，total 是过滤后总数——证据 >limit 时
        # 宿主能看出被截断（total > count）
        count_sql = "SELECT COUNT(*) AS c FROM twin_evidence"
        if where:
            count_sql += " WHERE " + " AND ".join(where)
        total = int(conn.execute(count_sql, args[:-1]).fetchone()["c"])
    finally:
        conn.close()
    items = []
    for r in rows:
        item = dict(r)
        item["memory_id"] = int(item["memory_id"])
        item["content_chars"] = int(item["content_chars"] or 0)
        items.append(item)
    return {"ok": True, "count": len(items), "total": total, "items": items}


def _mark_persona_stale(code: str, memory_id: int) -> None:
    """条款作废命中「曾入编译」的证据 → 标记该类型待重编（保守触发：宁可多编
    一次，无 rollback 边界洞）。清除=该 code 成功落版（夜间自动重编为主路径）。"""
    flow.ensure_schema()
    raw = flow.get_meta(f"persona_stale:{code}")
    try:
        rec = json.loads(raw)
    except (ValueError, TypeError):
        rec = {}
    if not isinstance(rec, dict):
        rec = {}
    voided = rec.get("voided")
    if not isinstance(voided, list):
        voided = []
    if memory_id not in voided:
        voided.append(memory_id)
    rec.update({"at": db.now_iso(), "voided": voided})
    flow.set_meta(f"persona_stale:{code}", json.dumps(rec, ensure_ascii=False))
