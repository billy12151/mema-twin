"""persona 编译与版本动作（v0.3.8 从 server.py 拆出）：compile / submit /
rollback + 夜间验证门 + 证据取数。

v0.3.8 删双跑（设计文档 D）：compare_hint/compare_offer 及 persona_origin/
compare_prev/compare_offered 三族 meta 退役（rollback 可再滚回，事前确认无
价值）；get 的 version 参数、零阻力 rollback、验证门 G1/G2、空转阻尼、
nightly_rejected 保留，构成质量安全网。
"""
from __future__ import annotations

import json
import re

from . import db, flow, identity, sink, store, taxonomy, templates
from .pref_actions import _bucket, _mark_persona_stale  # _bucket: read 通路存桶标识


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
    """compile 证据（v0.3.7 全量投影）：该类型**全部在世证据**（uncompiled+compiled，
    排除 void）逐条 read——每版从头重编，弱底稿不遗传、作废行天然不在场；与验证门
    G3 期望集同源（alive_evidence）。v0.3.9 删 find 兜底召回（索引落地前存量数据
    引导期已过；无在世证据时正确行为是素材为空——find 语义召回可能不相干的
    mema 记忆属伪造素材。索引真丢失的正解：按 twin:wt:* 等 tags 从 mema 重建
    twin_evidence，一次性脚本，需要时再写）。"""
    rows = db.alive_evidence(conn, code)
    if not rows:
        return [], []
    return _read_evidence_rows(rows, client)


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
        client = identity.effective_client(data)
        if aud is not None:
            # 受众画像素材（AR-2）：该受众全部证据（不分 compiled），逐条 read
            rows = db.audience_evidence(conn, aud)
            evidence, skipped = _read_evidence_rows(rows, client=client)
            audience_profiles = None
            # 已作废条款按 audience 查（评审轮1 P1-2——受众相关行的 work_type 各不
            # 相同）；取数放证据之后（评审轮2 P3-5：并发 void 落在两查询之间时，
            # 「素材含该条+stale 次夜自愈」比「整条消失」安全）
            voided = db.voided_audience_evidence(conn, aud)
        else:
            evidence, skipped = _fetch_evidence(conn, code, client=client)
            audience_profiles = _compile_audience_profiles(conn, code)
            voided = db.voided_evidence(conn, code)
    finally:
        conn.close()
    if aud is not None:
        at = taxonomy.by_code("audience", aud)
        title_zh = at.zh if at else aud
    else:
        title_zh = t.zh if t else code
    material = templates.compile_prompt_material(
        aud or code, title_zh, active, evidence,
        audience_profiles=audience_profiles, audience_mode=aud is not None,
        voided=voided)
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
    """review#10：只收 int / 数字字符串列表；"123" 这类可迭代脏值会拆成 1/2/3。
    上界 2^63-1（对抗轮2 P3-4）：超大 int 到 SQLite 绑定才抛 OverflowError，
    会落成"版本已建、响应 internal_error"的半成品——矫正层直接打回。"""
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
        if n < 0 or n > 2**63 - 1:
            raise ValueError(f"invalid memory id: {i!r}（需在 0..2^63-1 内）")
        out.append(n)
    return out


# ---- 夜间编译验证门（v0.3.7 瘦身版：只拦灾难形态）----
#
# 只保留 G1 素材回声 / G2 分区标题 两条 blocking 检查（误报率近零、必为真故障、
# 次晚自愈），仅对 origin=scheduled 拦截。G3/G4 证据覆盖降为纯警告（漏列由增补
# 注入兜底 + 次晚重编自愈，空转由阻尼兜住）；G5 体积漂移删除（防线 = 编译规则
# 硬预算 + status 体积可见）。配套两条闭环信号：
#   ① 空转阻尼：scheduled 提交不吸收任何新证据且非 stale 触发 → 拒绝落版，
#      防「夜夜 mint 新版空转」；
#   ② nightly_reject 计数：被拒（含阻尼）累计于 twin_meta，status 常显——
#      持续被拒与持续空转两个闭环的唯一出口信号，不复活 hint/三通道体系。
# 交互式 submit 永不拦（用户治理压过自动化），违规只渲染进 warnings。


def _material_echo_marker(text: str) -> str | None:
    """素材回声标记检测（G1）：标题类整串子串；节标题类行首标题匹配——
    复述素材包但把节标题降/升层级（# 编译规则）也命中（对抗轮2 P3-1）。"""
    if not text:
        return None
    hit = next((m for m in templates.MATERIAL_TITLE_MARKERS if m in text), None)
    if hit:
        return hit
    return next((m for m in templates.MATERIAL_SECTION_MARKERS
                 if re.search("(?m)^#{1,6}[ \\t]*" + re.escape(m), text)), None)


def _gate_check(conn, code: str, prompt_md: str,
                source_ids: list[int]) -> tuple[list[dict], list[str]]:
    """验证门主体。返回 (blocking_violations, warnings)：blocking 仅 G1（含自锁
    守卫——active 旧版本身含同标记时新稿沿袭降级为警告，防「交互式落一次含标记
    版本 → 夜夜被拦」的自锁链）/ G2；G3/G4 为警告字符串（G3 期望集 = 该类型全部
    在世证据，超集语义——漏列由增补兜底、次晚自愈，不值得拦）。"""
    violations: list[dict] = []
    warnings: list[str] = []
    old_md = (store.get_active(conn, code) or {}).get("prompt_md") or ""
    echo = _material_echo_marker(prompt_md)
    if echo:
        if _material_echo_marker(old_md):
            warnings.append(f"产物含素材包标记「{echo}」——当前 active 版也含该标记"
                            "（沿袭旧版，非回声证据）；若非有意引用请人工清理")
        else:
            violations.append({"check": "material_echo",
                               "detail": f"产物复述了素材包内容（含标记「{echo}」）——疑似编译失败"})
    # G2 分区标题：ATX 口径（setext 不识别——夜间素材包约定即 ATX，且人工通道不拦）；
    # 与 G1 行首匹配同款宽容（中文输出「##标题」无空格也认，评审轮2 P2-3）
    if not re.search(r"(?m)^#{1,6}[ \t]*\S", prompt_md):
        violations.append({"check": "no_headings",
                           "detail": "产物没有任何 Markdown 标题——编译规则要求按固定分区组织"})
    aud = store.split_audience_profile(code)
    if aud is not None:
        # G4 画像（警告）：集合相等口径——数量对但 id 错会让 stale 假性清零
        expected = {r["memory_id"] for r in db.audience_evidence(conn, aud)}
        actual = set(source_ids)
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            warnings.append(
                f"[验证门:画像证据集不符] source_memory_ids {len(actual)} 条，该受众共 "
                f"{len(expected)} 条证据（漏列 {missing}，多报 {extra}——多报/漏报/混入"
                "他受众 id 都会让画像吸收数失真、audience_stale 随之失真；也可能是素材包"
                "生成后该受众新写入了证据，次晚自愈）")
    else:
        # G3 类型（警告）：超集语义，期望集 = 全部在世证据（与 compile 全量投影同源）
        alive = {r["memory_id"] for r in db.alive_evidence(conn, code)}
        missing = sorted(alive - set(source_ids))
        if missing:
            warnings.append(
                f"[验证门:证据未全覆盖] 该工作性质仍有 {len(missing)} 条在世证据未被本版吸收"
                f"（missing_ids={missing}；可能是编译会话漏列，也可能是素材包生成后新写入"
                "了证据）：它们将继续以增补注入，且夜间任务会因未编译数>0 重复编译本类型")
    return violations, warnings


_GATE_CHECK_NAMES = {"material_echo": "素材回声", "no_headings": "缺分区标题"}


def _render_violation(v: dict) -> str:
    """交互式路径的违规文案（warnings 保持纯字符串列表，既有断言口径不破坏）。"""
    return f"[验证门:{_GATE_CHECK_NAMES.get(v['check'], v['check'])}] {v['detail']}"


def _bump_nightly_reject(code: str, check: str) -> None:
    """scheduled 被拒（验证门/空转阻尼）累计计数：持续被拒闭环的唯一出口信号。"""
    flow.ensure_schema()
    raw = flow.get_meta(f"nightly_reject:{code}")
    try:
        rec = json.loads(raw)
    except (ValueError, TypeError):
        rec = {}
    if not isinstance(rec, dict):
        rec = {}
    rec.update({"count": int(rec.get("count") or 0) + 1,
                "last_check": check, "last_at": db.now_iso()})
    flow.set_meta(f"nightly_reject:{code}", json.dumps(rec, ensure_ascii=False))


def _clear_nightly_reject(code: str) -> None:
    """本 code 成功落版（任意 origin）即清计数——含人工 compile 重出出口。"""
    flow.ensure_schema()
    if flow.get_meta(f"nightly_reject:{code}") is not None:
        flow.delete_meta(f"nightly_reject:{code}")


_REJECT_HINT = ("本次未落版：active 未变、证据未消耗，次晚自动重试；连续被拒不落版会在 "
                "status 的 nightly_rejected 累计显示——如需立即处理请人工 compile 重出后"
                "交互式 submit（不带 origin，不受门限制）")


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
        # v0.3.7 对账（评审轮2 P1-1）：source_ids 与该 code 在世证据集合对账——
        # 去重（重复 id 虚增 evidence_count、画像 stale 永差）；多余 id（幽灵/已
        # 作废/他类型）scheduled 直接拒（确定性错误零误报，次晚自愈），交互式剔除
        # + 警告。不做对账则幽灵 id 会让「基座收缩旁路」永久放行空转、画像侧
        # 夜夜落版 stale 永不清零。
        aud_for_alive = store.split_audience_profile(code)
        if aud_for_alive is not None:
            alive_ids = {r["memory_id"] for r in db.audience_evidence(conn, aud_for_alive)}
        else:
            alive_ids = {r["memory_id"] for r in db.alive_evidence(conn, code)}
        seen: set[int] = set()
        deduped = [i for i in source_ids if not (i in seen or seen.add(i))]
        dup_removed = len(source_ids) - len(deduped)
        extra = sorted(set(deduped) - alive_ids)
        source_ids = deduped
        if extra and origin == "scheduled":
            _bump_nightly_reject(code, "foreign_ids")
            return {"ok": False, "error": "validation_failed", "origin": "scheduled",
                    "work_type": code,
                    "violations": [{"check": "foreign_ids",
                                    "detail": (f"source_memory_ids 含 {len(extra)} 个不在"
                                               f"该类型在世证据集合内的 id（{extra}——"
                                               "幽灵 id/已作废/他类型），拒绝落版")}],
                    "reason": "夜间落版未过验证门：source ids 与在世证据集合不符",
                    "hint": _REJECT_HINT}
        if extra:
            # 交互式：多余 id 剔除后落版（保留会让基座旁路永久放行空转）
            source_ids = [i for i in deduped if i not in set(extra)]
        # v0.3.7 验证门（瘦身版）：G1/G2 拦 scheduled；G3/G4 只警告；空转阻尼
        violations, gate_warnings = _gate_check(conn, code, prompt_md, source_ids)
        if origin == "scheduled":
            flow.ensure_schema()
            if violations:
                _bump_nightly_reject(code, violations[0]["check"])
                return {"ok": False, "error": "validation_failed", "origin": "scheduled",
                        "work_type": code, "violations": violations,
                        "reason": "夜间落版未过验证门：" + "；".join(v["detail"] for v in violations),
                        "hint": _REJECT_HINT}
            active = store.get_active(conn, code)
            if (active is not None and active.get("version") is not None
                    and {str(i) for i in source_ids}
                    <= {str(i) for i in (active.get("source_memory_ids") or [])}
                    and flow.get_meta(f"persona_stale:{code}") is None):
                # 证据基座收缩旁路（评审轮1 P1-1）：void 之后期望集收缩（E ⊊ old），
                # 受众画像/类型的重抽象提交必是旧 source 集的真子集——必须放行，
                # 否则 void 驱动的重编被阻尼永久拦死、stale 永不清零。只有
                # old ⊆ E（基座未缩）且 source ⊆ old 才是真·无新证据空转。
                if aud is not None:
                    expected = {str(r["memory_id"])
                                for r in db.audience_evidence(conn, aud)}
                else:
                    expected = {str(r["memory_id"])
                                for r in db.alive_evidence(conn, code)}
                old = {str(i) for i in (active.get("source_memory_ids") or [])}
                if old <= expected:
                    _bump_nightly_reject(code, "no_new_evidence")
                    return {"ok": False, "error": "no_new_evidence", "origin": "scheduled",
                            "work_type": code,
                            "reason": ("提交的 source_memory_ids 未包含任何 active 版"
                                       "未吸收的新证据（疑似编译会话漏列），拒绝空转落版"),
                            "hint": _REJECT_HINT}
        rec = store.create_version(conn, code,
                                   prompt_md,
                                   source_ids,
                                   model=str(data.get("model") or ""))
        if store.split_audience_profile(code) is None:
            marked = db.mark_compiled(conn, source_ids, rec["version"], code)
        else:
            # 画像派生不消耗证据（AR-2）：aud- 行永远保持 uncompiled 给类型编译
            marked = 0
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
        # 夜间任务在转的信号（v0.3.8：origin 仅服务验证门/阻尼与停转保险丝，
        # 双跑提议已删）——scheduled 落版无条件刷新，空转由阻尼拦
        flow.ensure_schema()
        flow.set_meta("last_scheduled_compile_at", db.now_iso())
    if violations:
        # 交互式落版（scheduled 有违规已在上方 return）：违规只警告不拦
        rec.setdefault("warnings", []).extend(_render_violation(v) for v in violations)
    if extra:
        # 交互式：多余 id 已剔除落版（评审轮2 P1-1——保留会让基座旁路永久放行空转）
        rec.setdefault("warnings", []).append(
            f"[对账] source_memory_ids 含 {len(extra)} 个不在在世证据集合内的 id，"
            f"已剔除：{extra}")
    if dup_removed:
        rec.setdefault("warnings", []).append(
            f"[对账] source_memory_ids 去重剔除 {dup_removed} 个重复 id")
    if gate_warnings:
        # G3/G4 证据覆盖警告（两 origin 同出；信息含原 leftover/aud 吸收数口径）
        rec.setdefault("warnings", []).extend(gate_warnings)
    _clear_nightly_reject(code)  # 成功落版（任意 origin）即清被拒计数（人工重出出口）
    flow.ensure_schema()
    if flow.get_meta(f"persona_stale:{code}") is not None:
        flow.delete_meta(f"persona_stale:{code}")  # 作废条款已被本版吸收剔除
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
        # 回滚复活作废条款守卫（评审轮2 P2-2）：激活的目标版 source 集与该 code
        # 的作废证据有交集 → 重标 persona_stale（夜间重编剔除），否则作废条款
        # 静默常驻旧 active 且无任何信号
        try:
            rolled_ids = {int(i) for i in (out.get("source_memory_ids") or [])}
        except (TypeError, ValueError):
            rolled_ids = set()
        if rolled_ids:
            conn = db.connect()
            try:
                voided = {r["memory_id"] for r in db.voided_evidence(conn, code)}
            finally:
                conn.close()
            revived = sorted(rolled_ids & voided)
            if revived:
                for mid in revived:
                    _mark_persona_stale(code, mid)
                out["warnings"] = [f"回滚目标版的来源含已作废证据 {revived}：已重标 "
                                   "persona_stale，夜间任务将重编剔除该条款"]
    return out
