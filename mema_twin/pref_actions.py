"""偏好与治理动作（v0.3.8 从 server.py 拆出）：write / get / taxonomy / pending /
resolve / void / status。

write 经归一门（v0.3.8 B）：三维度值未命中清单即整笔打回（附动态清单 + 裁定
票据），Agent 必须问用户 resolve(map|canonicalize) 后重试——首写即问，不留
杂项桶。taxonomy 动态化：清单从 twin_types 实时读（治理追加/自建码立刻可见）。
"""
from __future__ import annotations

import datetime as _dt
import json

from . import db, flow, identity, normalize, scan, sink, store, taxonomy, templates

_KIND_PREFIX = {"work_type": "wt", "audience": "au", "purpose": "pu"}
_RAW_MAX_CHARS = 200      # 三维度值：短枚举说法
_CONTENT_MAX_CHARS = 8000  # 偏好正文
_BUCKET = "mema-twin"  # mema 侧偏好存储桶：固定值（画像人级全局），0.3.3 起写死、无 env 覆盖


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
    # 用户 tags 剥离 twin: 前缀（对抗 review#13）：维度命名空间只归归一层管
    # （校验先于归一门——轮2 P3-3：miss+脏 tags 不该先制造裁定义务再被打回）
    raw_tags = data.get("tags") or []
    if not isinstance(raw_tags, list):
        return {"ok": False, "error": "invalid_input", "field": "tags",
                "reason": "tags 必须是字符串列表"}
    user_tags = [str(t) for t in raw_tags
                 if not str(t).startswith("twin:") and str(t) != "twin-preference"]
    conn = db.connect()
    dims: dict = {}
    misses: list[dict] = []
    tags = ["twin-preference"]
    try:
        kinds = ("audience", "purpose") if audience_scoped else taxonomy.KINDS
        for kind in kinds:
            # 归一门（v0.3.8）：未命中不写 mema、不进 deferred——收集后整笔打回
            r = normalize.normalize_value(kind, str(data[kind]), conn)
            if r.get("error"):
                return {"ok": False, "error": "invalid_input", "field": kind, "reason": r.get("reason")}
            dims[kind] = r
            if r.get("ok"):
                tags.append(f"twin:{_KIND_PREFIX[kind]}:{r['code']}")
            else:
                misses.append(r)
    finally:
        conn.close()  # 后续是 30s 级 HTTP 调用，连接不能跨调用挂着（review#7）
    if misses:
        gate = normalize.gate_reject(misses)
        if gate is not None:
            return gate
        # 全部被并发裁定：dims 已被 gate_reject 就地改写为命中，补 tags
        for kind in (("audience", "purpose") if audience_scoped else taxonomy.KINDS):
            if dims[kind].get("ok"):
                tags.append(f"twin:{_KIND_PREFIX[kind]}:{dims[kind]['code']}")
    if audience_scoped:
        aud_code = dims["audience"]["code"]
        dims["work_type"] = {"ok": True, "kind": "work_type", "raw": "(受众级偏好)",
                             "code": store.audience_profile_code(aud_code),
                             "label_zh": dims["audience"].get("label_zh"), "matched_by": "audience_scope"}
        tags.append(f"twin:aud:{aud_code}")
    resp = sink.remember(
        content=content,
        subject=str(data.get("subject") or (
            f"受众级偏好：{dims['audience'].get('label_zh') or dims['audience'].get('raw')}"
            if audience_scoped else f"工作偏好：{dims['work_type'].get('raw')}")),
        tags=tags + user_tags,
        workspace=_bucket(),
        source_ref=str(data.get("source_ref") or ""),
        event_time=_today(),
        client=identity.effective_client(data),  # 多 Agent：显式 data.client > 头 > env
    )
    ok = bool(resp.get("ok"))
    out: dict = {"ok": ok, "memory": resp.get("data") if ok else resp,
                 "dimensions": dims}
    if ok:
        mid = _memory_id_of(resp.get("data"))
        conn = db.connect()
        try:
            if mid is None:
                # 对抗 review#9①：id 缺失则证据永不登记，必须显式告警而非静默
                out["warnings"] = ["mema 响应缺记忆 id，本条未入证据索引（compile 不可见），建议重写"]
            else:
                db.record_evidence(conn, mid, dims,
                                   subject=str(data.get("subject") or ""))
                out["evidence_id"] = mid
            if audience_scoped:
                zh = dims["audience"].get("label_zh") or dims["audience"]["raw"]
                out["hint"] = (f"已沉淀为对「{zh}」的受众级通用偏好（不绑定工作类型）；"
                               "该受众有新证据时，夜间任务会重抽象其画像，"
                               "对该受众的任何任务开工时自动带上")
            else:
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
            "SELECT work_type, version, model, status, evidence_count, created_at, prompt_md"
            " FROM twin_prompt_versions ORDER BY work_type, version",
        ).fetchall()
        versions: dict = {}
        audience_profiles: dict = {}
        for r in rows:
            aud = store.split_audience_profile(r["work_type"])
            bucket = audience_profiles if aud is not None else versions
            v = bucket.setdefault(r["work_type"],
                                  {"work_type": r["work_type"], "active": None, "versions": []})
            item = dict(r)
            size = len(item.pop("prompt_md") or "")
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
    try:
        resp = sink.review_conflicts()
        payload = resp.get("data") or resp or {}
        conf = payload.get("conflicts") or []
        out["open_conflicts"] = sum(
            1 for c in conf
            if isinstance(c, dict) and c.get("status") == "open"
            and c.get("workspace_canonical") == _bucket())
    except (sink.SinkError, AttributeError, TypeError, ValueError):
        pass  # 软失败：mema 抖动少一个计数即可，status 是夜间第一步不能被拖垮
    notice = scan.scan_notice()
    if notice:
        out["scan_notice"] = notice
    return out


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
    立刻可见），内置枚举兜底合并——与归一匹配侧同源。"""
    kind = str(data.get("kind") or "work_type")
    if kind not in taxonomy.KINDS:
        return {"ok": False, "error": "invalid_input", "field": "kind",
                "reason": f"expected one of {taxonomy.KINDS}"}
    conn = db.connect()
    try:
        rows = db.type_rows(conn, kind)
    finally:
        conn.close()
    merged: dict = {}
    for r in rows:
        merged[r["code"]] = {"code": r["code"], "zh": r["label_zh"], "en": r["label_en"],
                             "domain": r["domain"], "aliases": r["aliases"],
                             "is_custom": bool(r["is_custom"])}
    for t in taxonomy.all_types(kind):
        merged.setdefault(t.code, {"code": t.code, "zh": t.zh, "en": t.en,
                                   "domain": t.domain, "aliases": list(t.aliases),
                                   "is_custom": False})
    items = list(merged.values())
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
            # 第二个 canonical，归一结果由行序决定而非用户裁定）。多宿主并发同值
            # 票据被先裁定时，后来者的正确动作是直接重试原写入（v0.3.8）
            return {"ok": False, "error": "invalid_input",
                    "reason": f"pending {pid} 已裁定为 {row['status']}（→{row['resolved_code']}），"
                              "不可重复裁定——若他方已替你裁定，直接重试原写入即可"}
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
    finally:
        conn.close()
    out: dict = {"ok": True, "pending_id": int(pid), "decision": decision}
    if decision == "reject":
        out["note"] = ("已裁定不入体系。放弃本次写入，不得再拿原值重试"
                       "（票据会复活成新的裁定义务）")
    return out


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
    notes = ["作废不可逆（内容仍在 mema，需要可重写一条）；mema 本体的 retire/update "
             "按其治理流程另行处理，twin 只管证据索引。"]
    wt = row["work_type"]
    if row["compiled_version"] is not None and wt and store.split_audience_profile(wt) is None:
        _mark_persona_stale(wt, n)
        out["persona_stale"] = True
        notes.append(f"{wt} 已标记 persona_stale：夜间任务将重编剔除该条款"
                     "（成功落版即清标记）")
    if row["audience"]:
        notes.append(f"受众 {row['audience']} 的画像计数已变化，audience_stale 将触发"
                     "夜间重抽象（新画像不含该条款）")
    out["note"] = "；".join(notes)
    return out


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
