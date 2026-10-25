"""v0.4.6 三条改动（「agent 困在流程」调查落地的修复）：step_update 同状态
重复打卡幂等化、in_progress 挂墙 advisory、action 级审计日志。

对应 issue #1384：2026-10-21 案例定性为宿主 agent 行为空转，twin 无
锁死 bug，但防跳步围栏的硬报错客观上延长空转。三条改动都不新增硬门、不改变
既有迁移合法性（除恒等迁移由打回改幂等），全部 advisory/观测向。
"""
import json
import sqlite3

import pytest

from mema_twin import audit, exec_actions, flow, pref_actions, server


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(tmp_path / "twin.sqlite3"))
    monkeypatch.setenv("MEMA_TWIN_PROMPTS_DIR", str(tmp_path / "prompts"))
    monkeypatch.setenv("MEMA_TWIN_DELIVERABLES_DIR", str(tmp_path / "deliverables"))
    flow._schema_ready.clear()
    flow.ensure_schema()
    audit._reset_enabled_cache()
    audit._audit_ready.clear()
    audit._advisory_cooldown.clear()  # 进程内冷却跨用例残留（task id 复用）
    yield tmp_path
    flow._todos_by_session.clear()
    audit._reset_enabled_cache()
    audit._advisory_cooldown.clear()


def _dims():
    return {
        "work_type": {"ok": True, "kind": "work_type", "raw": "工作汇报",
                      "code": "work_report", "label_zh": "工作汇报",
                      "matched_by": "exact_or_alias"},
        "audience": {"ok": True, "kind": "audience", "raw": "高层",
                     "code": "leadership", "label_zh": "高层与决策层",
                     "matched_by": "exact_or_alias"},
        "purpose": {"ok": True, "kind": "purpose", "raw": "同步",
                    "code": "sync_info", "label_zh": "信息同步与知会",
                    "matched_by": "exact_or_alias"},
    }


def _mk_task(**kw):
    return flow.insert_task(brief=kw.pop("brief", "T"), status="planning",
                            dims=_dims(), **kw)


def _plan(task_id, steps=None, questions=None):
    return server._twin_impl("plan_set", {
        "task_id": task_id,
        "steps": steps or [{"title": "s1"},
                           {"title": "s2", "depends_on": [1]},
                           {"title": "s3", "depends_on": [2]}],
        "open_questions": questions or [],
    })


def _step_ids(r):
    return [s["id"] for s in r["steps"]]


# ---- 建议 1：同状态重复打卡幂等 ----


def test_repeat_in_progress_is_idempotent_noop(env):
    """案例复现：步骤 159 已 in_progress，agent 空转轮里再次打卡 → 幂等 ok
    + no_op + 继续执行指引（此前硬报错「不允许从 in_progress 迁到 in_progress」）。"""
    t = _mk_task()
    ids = _step_ids(_plan(t["id"]))
    assert server._twin_impl("step_update", {"step_id": ids[0],
                                            "status": "in_progress"})["ok"]
    r = server._twin_impl("step_update", {"step_id": ids[0],
                                          "status": "in_progress"})
    assert r["ok"] is True
    assert r["no_op"] is True
    assert r["status"] == "in_progress"
    assert r["consecutive_noop"] >= 1
    assert r["guidance"]  # 非空指引（措辞可演进，不断言具体用词）


def test_repeat_done_is_idempotent_noop(env):
    """done→done 同样幂等（收口对账场景：agent 重复确认同一已完成步骤）。"""
    t = _mk_task()
    ids = _step_ids(_plan(t["id"]))
    server._twin_impl("step_update", {"step_id": ids[0], "status": "in_progress"})
    assert server._twin_impl("step_update", {"step_id": ids[0],
                                            "status": "done"})["ok"]
    r = server._twin_impl("step_update", {"step_id": ids[0], "status": "done"})
    assert r["ok"] is True and r["no_op"] is True
    assert r["status"] == "done"
    assert r["guidance"]


def test_idempotent_does_not_touch_decided_at(env):
    """幂等不改 decided_at：挂墙 advisory 的计时基线不被重复打卡刷新
    （否则 agent 每轮打卡就把计时器清零，advisory 永不触发）。"""
    t = _mk_task()
    ids = _step_ids(_plan(t["id"]))
    server._twin_impl("step_update", {"step_id": ids[0], "status": "in_progress"})
    before = exec_actions.get_step(ids[0])["decided_at"]
    server._twin_impl("step_update", {"step_id": ids[0], "status": "in_progress"})
    assert exec_actions.get_step(ids[0])["decided_at"] == before


def test_idempotent_in_progress_bypasses_single_gate(env):
    """幂等 in_progress 不触三门：步骤 1 与 2 都已 in_progress 的病态现场
    （历史遗留/并发），对步骤 1 重复打卡应成功而非被单一 in_progress 门拦
    （门只拦「推进新步骤」，不拦「已在进行的步骤再次确认」）。"""
    t = _mk_task()
    ids = _step_ids(_plan(t["id"]))
    server._twin_impl("step_update", {"step_id": ids[0], "status": "in_progress"})
    # 直接把步骤 2 也推成 in_progress（绕开门：先落 done 再撤销回 in_progress
    # 会触 reason 门，改走 plan_revise.steps_update 同款迁移路径不便，故用
    # 底层 conn 制造病态现场——测试只关心幂等分支不受三门影响）
    conn = flow.db.connect()
    conn.execute("UPDATE twin_plan_steps SET status='in_progress' WHERE id=?",
                 (ids[1],))
    conn.commit()
    conn.close()
    r = server._twin_impl("step_update", {"step_id": ids[0],
                                          "status": "in_progress"})
    assert r["ok"] is True and r["no_op"] is True


def test_non_idempotent_transitions_unchanged(env):
    """除恒等迁移外全部维持原判：pending→pending 仍打回（未列迁移表），
    done→in_progress 仍需 reason（撤销完成审计），failed→done 合法。"""
    t = _mk_task()
    ids = _step_ids(_plan(t["id"]))
    # pending→pending 未列 → 打回
    r = server._twin_impl("step_update", {"step_id": ids[0], "status": "pending"})
    assert r["ok"] is False and r["error"] == "invalid_input"
    # done→pending 需 reason（撤销完成审计）
    server._twin_impl("step_update", {"step_id": ids[0], "status": "in_progress"})
    server._twin_impl("step_update", {"step_id": ids[0], "status": "done"})
    r = server._twin_impl("step_update", {"step_id": ids[0], "status": "pending"})
    assert r["ok"] is False and "reason" in r["reason"]
    # done→in_progress 未列迁移表 → 打回（撤销完成必须先回 pending）
    r = server._twin_impl("step_update", {"step_id": ids[0], "status": "in_progress"})
    assert r["ok"] is False and "不允许" in r["reason"]
    # failed→done 合法（重开后完成）
    server._twin_impl("step_update", {"step_id": ids[0], "status": "in_progress"})
    server._twin_impl("step_update", {"step_id": ids[0], "status": "failed",
                                      "reflection": "为什么失败/下次怎么办"})
    assert server._twin_impl("step_update", {"step_id": ids[0],
                                            "status": "done"})["ok"]


def test_idempotent_requires_reason_when_required(env):
    """reason_required 与 idempotent 不同时出现（迁移表无此组合），但防御：
    若恒等迁移被要求 reason（未来组合），缺 reason 仍须打回而非静默放行。"""
    # 用 done→skipped 需要 reason 验证 required 门在幂等分支之后依然有效
    t = _mk_task()
    ids = _step_ids(_plan(t["id"]))
    server._twin_impl("step_update", {"step_id": ids[0], "status": "in_progress"})
    server._twin_impl("step_update", {"step_id": ids[0], "status": "done"})
    r = server._twin_impl("step_update", {"step_id": ids[0], "status": "skipped"})
    assert r["ok"] is False and "reason" in r["reason"]


# ---- 建议 2：in_progress 挂墙 advisory ----


def test_stall_guidance_absent_when_fresh(env):
    """刚推进的 in_progress 不触发挂墙指引（防误报噪声）。"""
    t = _mk_task()
    ids = _step_ids(_plan(t["id"]))
    server._twin_impl("step_update", {"step_id": ids[0], "status": "in_progress"})
    r = server._twin_impl("task_get", {"task_id": t["id"]})
    assert r["ok"]
    assert "stall_advisory" not in r


def test_stall_guidance_present_when_stale(env, monkeypatch):
    """decided_at 超过阈值 → 后续 ok 响应附出口指引（三选一都在文案里）。"""
    monkeypatch.setenv("MEMA_TWIN_STALL_MINUTES", "30")
    audit._reset_enabled_cache()
    t = _mk_task()
    ids = _step_ids(_plan(t["id"]))
    server._twin_impl("step_update", {"step_id": ids[0], "status": "in_progress"})
    # 回拨 decided_at 到 60 分钟前
    conn = flow.db.connect()
    conn.execute("UPDATE twin_plan_steps SET decided_at=?"
                 " WHERE id=?", ("2020-01-01T00:00:00+00:00", ids[0]))
    conn.commit()
    conn.close()
    r = server._twin_impl("task_get", {"task_id": t["id"]})
    assert r["ok"]
    assert "stall_advisory" in r, r.get("stall_advisory")
    w = r["stall_advisory"]
    assert f"#{ids[0]}" in w
    assert "step_update(failed" in w and "plan_revise" in w
    assert "task_close" in w and "忽略本条" in w


def test_stall_guidance_threshold_configurable(env, monkeypatch):
    """阈值可调：回拨 5 分钟时，阈值 1 分钟→触发、阈值 60 分钟→不触发。
    （用近期时间戳而非极端 2020 年——阈值语义只与相对时长有关，极端值
    同时穿过所有阈值，反而测不出「可调」本身。）"""
    import datetime as _dt
    t = _mk_task()
    ids = _step_ids(_plan(t["id"]))
    server._twin_impl("step_update", {"step_id": ids[0], "status": "in_progress"})
    stale = (_dt.datetime.now(_dt.timezone.utc)
             - _dt.timedelta(minutes=5)).replace(microsecond=0).isoformat()
    conn = flow.db.connect()
    conn.execute("UPDATE twin_plan_steps SET decided_at=? WHERE id=?",
                 (stale, ids[0]))
    conn.commit()
    conn.close()
    monkeypatch.setenv("MEMA_TWIN_STALL_MINUTES", "1")
    assert audit.stall_guidance(t["id"]) is not None
    monkeypatch.setenv("MEMA_TWIN_STALL_MINUTES", "60")
    assert audit.stall_guidance(t["id"]) is None
    # 脏值回落默认 30 分钟（回拨 5 分钟 → 不触发）
    monkeypatch.setenv("MEMA_TWIN_STALL_MINUTES", "abc")
    assert audit.stall_guidance(t["id"]) is None


def test_stall_guidance_dirty_task_id(env):
    """脏 task_id / None 不给 advisory 也不炸（入口不二次校验的兜底）。"""
    assert audit.stall_guidance(None) is None
    assert audit.stall_guidance("abc") is None
    assert audit.stall_guidance(99999) is None


def test_stall_guidance_not_on_error_response(env, monkeypatch):
    """只挂 ok 响应：错误返回不带 advisory（打回本身已是指引，不叠噪）。"""
    monkeypatch.setenv("MEMA_TWIN_STALL_MINUTES", "1")
    audit._reset_enabled_cache()
    t = _mk_task()
    ids = _step_ids(_plan(t["id"]))
    server._twin_impl("step_update", {"step_id": ids[0], "status": "in_progress"})
    conn = flow.db.connect()
    conn.execute("UPDATE twin_plan_steps SET decided_at=? WHERE id=?",
                 ("2020-01-01T00:00:00+00:00", ids[0]))
    conn.commit()
    conn.close()
    r = server._twin_impl("step_update", {"step_id": ids[1], "status": "pending"})
    assert r["ok"] is False
    assert "stall_advisory" not in r


# ---- 建议 3：action 级审计日志 ----


def test_audit_records_success_and_failure(env):
    """每次调用落一行：成功记 ok=True/error=None；失败记 error 码。"""
    t = _mk_task()
    _plan(t["id"])
    server._twin_impl("step_update", {"step_id": 99999, "status": "in_progress"})
    rows = audit.recent(10)
    actions = [r["action"] for r in rows]
    assert "plan_set" in actions and "step_update" in actions
    plan_row = next(r for r in rows if r["action"] == "plan_set")
    assert plan_row["ok"] is True
    assert not plan_row["error"]  # 成功行无错误码（空串表示）
    bad = next(r for r in rows if r["action"] == "step_update")
    assert bad["ok"] is False and bad["error"] == "not_found"
    assert bad["step_id"] == 99999 and bad["task_id"] is None
    assert plan_row["task_id"] == t["id"]


def test_audit_records_unknown_action(env):
    """未知 action 也留痕（此前直接返回，事后无法统计误调用）。"""
    r = server._twin_impl("no_such_action", {})
    assert r["ok"] is False
    rows = audit.recent(5, action="no_such_action")
    assert rows and rows[0]["error"] == "unknown_action"


def test_audit_ring_cap(env, monkeypatch):
    """环形帽：超过上限只留最近 _MAX_ROWS 条（最旧被截）。"""
    monkeypatch.setattr(audit, "_MAX_ROWS", 10)
    for _ in range(25):
        server._twin_impl("help", {})
    rows = audit.recent(100)
    assert len(rows) == 10


def test_audit_disabled_by_env(env, monkeypatch):
    """MEMA_TWIN_AUDIT=0 全关：不记行、recent 空、status 无 audit 键。"""
    monkeypatch.setenv("MEMA_TWIN_AUDIT", "0")
    audit._reset_enabled_cache()
    assert audit.enabled() is False
    server._twin_impl("help", {})
    assert audit.recent(10) == []
    out = server._twin_impl("status", {})
    assert out["plan_stats"]["audit"] == {"enabled": False}


def test_audit_status_summary(env):
    """status 的 audit 键：总数/失败数/近 24h/top 动作/enabled。"""
    server._twin_impl("help", {})
    server._twin_impl("step_update", {"step_id": 1, "status": "in_progress"})
    stats = server._twin_impl("status", {})["plan_stats"]["audit"]
    assert stats["total"] >= 2
    assert stats["failed"] >= 1
    assert stats["last_24h"] >= 2
    assert stats["enabled"] is True
    assert any(a["action"] == "help" for a in stats["top_actions"])


def test_audit_never_records_payload_body(env):
    """隐私边界：日志只记 action/ok/error/耗时/client/task_id/step_id/thread，
    绝不记 brief/deliverable_md/content 等正文（参数原样传入也不泄漏）。"""
    secret = "SECRET-BODY-12345"
    t = _mk_task()
    server._twin_impl("plan_set", {"task_id": t["id"], "steps": [{"title": secret}]})
    rows = audit.recent(5, action="plan_set")
    assert rows
    raw = json.dumps(rows, ensure_ascii=False)
    assert secret not in raw
    # 隐私契约：正文类字段一个都不在（键集可演进，故断言黑名单而非白名单）
    for banned in ("title", "content", "brief", "deliverable_md", "note",
                   "answer", "purpose", "steps", "entries", "todos"):
        assert banned not in rows[0]


def test_audit_failure_does_not_break_tool(env, monkeypatch):
    """观测旁路：写日志炸（DB 只读/磁盘满）不影响工具响应 ok/error 语义。
    只炸审计路径（patch audit.record 与 stall_guidance），flow 的正常连接
    不动——否则测的是「整个 DB 挂了」而非「观测件挂了」。"""
    t = _mk_task()

    def boom(*a, **kw):
        raise sqlite3.OperationalError("attempt to write a readonly database")

    monkeypatch.setattr(audit, "record", boom)
    monkeypatch.setattr(audit, "stall_guidance", boom)
    r = server._twin_impl("task_get", {"task_id": t["id"]})
    assert r["ok"] is True and r["task"]["id"] == t["id"]
    r2 = server._twin_impl("step_update", {"step_id": 99999, "status": "done"})
    assert r2["ok"] is False and r2["error"] == "not_found"


def test_audit_clear(env):
    """clear 清空审计行并返回行数；status 的 audit 键随后报 total=0。"""
    server._twin_impl("help", {})
    server._twin_impl("help", {})
    assert audit.clear() == 2
    assert audit.recent(10) == []
    stats = server._twin_impl("status", {})["plan_stats"]["audit"]
    assert stats["enabled"] is True and stats["total"] == 0


def test_audit_concurrent_writes_serialized(env):
    """并发写入不丢行不炸（模块级锁串行化同进程；读-改-写在锁内）。"""
    import threading
    errors = []

    def worker():
        try:
            for _ in range(5):
                server._twin_impl("help", {})
        except Exception as e:  # pragma: no cover - 失败即测试失败
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert not errors
    assert len(audit.recent(100)) == 20


def test_stall_and_audit_compose_on_submit_path(env, monkeypatch):
    """组合场景：挂墙 advisory 与收口门同场——步骤 stale 时 task_submit
    仍被收口门硬拦（advisory 不削弱硬门），且本次调用进了审计。"""
    monkeypatch.setenv("MEMA_TWIN_STALL_MINUTES", "1")
    audit._reset_enabled_cache()
    t = _mk_task()
    ids = _step_ids(_plan(t["id"]))
    server._twin_impl("step_update", {"step_id": ids[0], "status": "in_progress"})
    conn = flow.db.connect()
    conn.execute("UPDATE twin_plan_steps SET decided_at=? WHERE id=?",
                 ("2020-01-01T00:00:00+00:00", ids[0]))
    conn.commit()
    conn.close()
    r = server._twin_impl("task_submit", {"task_id": t["id"],
                                          "deliverable_md": "交付稿"})
    assert r["ok"] is False  # 收口门硬拦（步骤未闭环）
    assert r["error"] == "invalid_input"
    rows = audit.recent(5, action="task_submit")
    assert rows and rows[0]["ok"] is False


def test_plan_revise_steps_update_idempotent_path(env):
    """plan_revise.steps_update 与 step_update 共用迁移执行器：恒等迁移同样
    幂等（不报错、不重复写），保持两条入口口径一致。"""
    t = _mk_task()
    ids = _step_ids(_plan(t["id"]))
    server._twin_impl("step_update", {"step_id": ids[0], "status": "in_progress"})
    r = server._twin_impl("plan_revise", {
        "task_id": t["id"],
        "steps_update": [{"id": ids[0], "status": "in_progress"}],
        "revision_reason": "确认该步骤仍在进行",
    })
    assert r["ok"] is True
    upd = next(u for u in r["updated"] if u["step_id"] == ids[0])
    assert upd["no_op"] is True
    assert exec_actions.get_step(ids[0])["status"] == "in_progress"


# ---- v0.4.6 评审后修复的回归测试 ----


def test_audit_consecutive_noop_counts_and_warns(env):
    """红队 P1-3：连续 ≥3 次同状态打卡时，consecutive_noop 可读且 guidance
    明示空转与出口（宿主按 ok 判成败，必须显式告知）。"""
    t = _mk_task()
    ids = _step_ids(_plan(t["id"]))
    server._twin_impl("step_update", {"step_id": ids[0], "status": "in_progress"})
    for i in (1, 2, 3):
        r = server._twin_impl("step_update", {"step_id": ids[0],
                                              "status": "in_progress"})
        assert r["consecutive_noop"] == i
    assert "空转" in r["guidance"]
    assert "task_close" in r["guidance"]


def test_audit_noop_count_resets_on_real_transition(env):
    """真实迁移后计数清零：状态变了「连续重复」的语义就断。"""
    t = _mk_task()
    ids = _step_ids(_plan(t["id"]))
    for _ in range(3):
        server._twin_impl("step_update", {"step_id": ids[0],
                                          "status": "in_progress"})
    server._twin_impl("step_update", {"step_id": ids[0], "status": "done"})
    r = server._twin_impl("step_update", {"step_id": ids[0], "status": "done"})
    assert r["consecutive_noop"] == 1


def test_plan_revise_all_noop_reports_no_changes(env):
    """评审 P2-1：steps_update 全是恒等迁移时不得回「计划已修订」的成功
    假信号（v0.4.0 已为「仅 revision_reason」建 no_changes，此处补齐）。"""
    t = _mk_task()
    ids = _step_ids(_plan(t["id"]))
    server._twin_impl("step_update", {"step_id": ids[0], "status": "in_progress"})
    r = server._twin_impl("plan_revise", {
        "task_id": t["id"],
        "steps_update": [{"id": ids[0], "status": "in_progress"}],
        "revision_reason": "确认该步骤仍在进行",
    })
    assert r["ok"] is True
    assert r["no_changes"] is True
    assert "未变更" in r["guidance"]


def test_plan_revise_real_change_still_reports_revised(env):
    """有真实变化的 steps_update 仍报「计划已修订」（no_changes 不误伤）。"""
    t = _mk_task()
    ids = _step_ids(_plan(t["id"]))
    r = server._twin_impl("plan_revise", {
        "task_id": t["id"],
        "steps_update": [{"id": ids[0], "status": "in_progress"}],
        "revision_reason": "开工",
    })
    assert r["ok"] is True and "no_changes" not in r
    assert "已修订" in r["guidance"]


def test_stall_advisory_cooldown_suppresses_repeat(env, monkeypatch):
    """评审 P2-1：同 (task_id, step_id) 的 advisory 有冷却窗，不每轮刷。"""
    monkeypatch.setenv("MEMA_TWIN_STALL_MINUTES", "1")
    t = _mk_task()
    ids = _step_ids(_plan(t["id"]))
    server._twin_impl("step_update", {"step_id": ids[0], "status": "in_progress"})
    conn = flow.db.connect()
    conn.execute("UPDATE twin_plan_steps SET decided_at=? WHERE id=?",
                 ("2020-01-01T00:00:00+00:00", ids[0]))
    conn.commit()
    conn.close()
    first = server._twin_impl("task_get", {"task_id": t["id"]})
    assert "stall_advisory" in first
    second = server._twin_impl("task_get", {"task_id": t["id"]})
    assert "stall_advisory" not in second  # 冷却窗内不重复提示


def test_stall_advisory_own_switch_independent_of_audit(env, monkeypatch):
    """评审 P2-4：advisory 与审计解耦——可以只关 advisory 留审计。"""
    monkeypatch.setenv("MEMA_TWIN_STALL_MINUTES", "1")
    monkeypatch.setenv("MEMA_TWIN_STALL_ADVISORY", "0")
    t = _mk_task()
    ids = _step_ids(_plan(t["id"]))
    server._twin_impl("step_update", {"step_id": ids[0], "status": "in_progress"})
    conn = flow.db.connect()
    conn.execute("UPDATE twin_plan_steps SET decided_at=? WHERE id=?",
                 ("2020-01-01T00:00:00+00:00", ids[0]))
    conn.commit()
    conn.close()
    assert "stall_advisory" not in server._twin_impl(
        "task_get", {"task_id": t["id"]})
    # 审计仍在记（独立开关）
    assert audit.recent(5, action="task_get")


def test_audit_json_buffer_migration(env):
    """评审 P1-4：v0.4.6 首版存在 twin_meta.audit_log 的 JSON 行，换表后
    首次连接应灌进 twin_audit 并删键（一次性、幂等、不丢行）。"""
    from mema_twin import db
    conn = db.connect()
    conn.execute(
        "INSERT INTO twin_meta(key, value) VALUES('audit_log', ?)",
        (json.dumps([{"ts": "2026-10-21T10:00:00+00:00", "action": "help",
                      "ok": True, "error": None, "ms": 3, "client": "kimi",
                      "task_id": 95, "step_id": None},
                     {"ts": "2026-10-21T10:00:01+00:00", "action": "step_update",
                      "ok": False, "error": "not_found", "ms": 2,
                      "client": None, "task_id": None, "step_id": 159}]),
         ))
    conn.commit()
    conn.close()
    rows = audit.recent(10)
    actions = {r["action"] for r in rows}
    assert actions == {"help", "step_update"}
    assert any(r["task_id"] == 95 for r in rows)
    assert any(r["step_id"] == 159 and r["error"] == "not_found" for r in rows)
    # 键已删（幂等：再调一次不会重复灌）
    conn = db.connect()
    assert conn.execute("SELECT 1 FROM twin_meta WHERE key='audit_log'"
                        ).fetchone() is None
    conn.close()
    assert len(audit.recent(10)) == 2


def test_audit_corrupt_json_buffer_dropped_not_resurrected(env):
    """评审 P1-1/P2-5：旧 JSON 缓冲损坏时不静默「重置成空」掩盖证据，
    迁移直接弃键（新表独立计数，运维从 total 落差能看出丢过历史）。"""
    from mema_twin import db
    conn = db.connect()
    conn.execute("INSERT INTO twin_meta(key, value) VALUES('audit_log', ?)",
                 ('{"truncated": ', ))
    conn.commit()
    conn.close()
    assert audit.recent(10) == []
    conn = db.connect()
    assert conn.execute("SELECT 1 FROM twin_meta WHERE key='audit_log'"
                        ).fetchone() is None
    conn.close()


def test_audit_client_attribution_from_connection_header(env, monkeypatch):
    """评审 P2-3：http 模式只带头不传 data.client 时，审计仍能归属宿主
    （此前 client 恒为 null，多宿主部署无法归因）。"""
    monkeypatch.setattr(server.identity, "client_header", lambda: "jingleAI")
    t = _mk_task()
    server._twin_impl("task_get", {"task_id": t["id"]})
    rows = audit.recent(5, action="task_get")
    assert rows[0]["client"] == "jingleAI"


def test_audit_stats_separates_unreachable(env):
    """评审 P2-6：mema 离线期的失败单列 unreachable，不混进空转失败率。"""
    stats = server._twin_impl("status", {})["plan_stats"]["audit"]
    assert "unreachable" in stats
    assert stats["enabled"] is True


def test_audit_record_is_cheap_after_table_switch(env):
    """评审 P1-4 回归：换表后 record 成本与 ring 大小基本无关（单行 INSERT），
    不会随调用量线性恶化。粗粒度断言：灌 300 行后再记一次仍在毫秒级。"""
    import time
    for _ in range(300):
        server._twin_impl("help", {})
    t = _mk_task()
    t0 = time.perf_counter()
    for _ in range(10):
        server._twin_impl("task_get", {"task_id": t["id"]})
    per_call_ms = (time.perf_counter() - t0) * 100
    assert per_call_ms < 10.0, f"record+stall 每次调用 {per_call_ms:.1f}ms"


# ---- 第三轮复审回归（P2-1 advisory 送达 step_update / P2-2 满环成本 / P3-3 字节帽）----


def test_stall_advisory_reaches_step_update_action(env, monkeypatch):
    """复审 P2-1：事故里的空转动作是 step_update，入参只有 step_id 没有
    task_id——advisory 必须能送达（按 step_id 反查任务），否则指路牌挂不到
   最需要的那面墙。"""
    monkeypatch.setenv("MEMA_TWIN_STALL_MINUTES", "1")
    t = _mk_task()
    ids = _step_ids(_plan(t["id"]))
    server._twin_impl("step_update", {"step_id": ids[0], "status": "in_progress"})
    conn = flow.db.connect()
    conn.execute("UPDATE twin_plan_steps SET decided_at=? WHERE id=?",
                 ("2020-01-01T00:00:00+00:00", ids[0]))
    conn.commit()
    conn.close()
    # step_update 自身（无 task_id）就能收到 advisory
    r = server._twin_impl("step_update", {"step_id": ids[0], "status": "in_progress"})
    assert r["ok"] and "stall_advisory" in r
    assert f"#{ids[0]}" in r["stall_advisory"]


def test_audit_task_id_backfilled_from_step_id(env):
    """复审冒烟：step_update 只传 step_id 时审计行反查 task_id——
    否则空转打卡全记 task_id=None，事后无法回答「哪个任务在空转」。"""
    t = _mk_task()
    ids = _step_ids(_plan(t["id"]))
    server._twin_impl("step_update", {"step_id": ids[0], "status": "in_progress"})
    rows = audit.recent(5, action="step_update")
    assert rows[0]["step_id"] == ids[0]
    assert rows[0]["task_id"] == t["id"]


def test_audit_trim_converges_at_cap(env, monkeypatch):
    """复审 P2-2：满环后 _trim 一次调用即收敛到帽值（不渐进泄漏），
    且删法走主键索引（阈值删）而非全表扫（NOT IN）。"""
    monkeypatch.setattr(audit, "_MAX_ROWS", 500)
    for _ in range(520):
        server._twin_impl("help", {})
    rows = audit.recent(100000)
    assert len(rows) == 500
    ids = [r["id"] for r in rows]
    assert ids == sorted(ids, reverse=True)  # recent 新在前（DESC）
    assert ids[-1] == 21  # 最旧保留行：灌了 520 行、留最近 500 → 起点 21


def test_audit_byte_cap_takes_smaller_keep(env, monkeypatch):
    """复审 P3-3：字节帽比行数帽更小时 keep 取小值（min 分支零覆盖的话，
    有人调小 _MAX_BYTES 会让 help 的「近 2 万条」静默失真）。"""
    monkeypatch.setattr(audit, "_MAX_BYTES", 400 * 100)  # keep=100 < 20000
    for _ in range(150):
        server._twin_impl("help", {})
    assert len(audit.recent(100000)) == 100


def test_audit_full_ring_cost_is_bounded(env, monkeypatch):
    """复审 P2-2：满环态单次调用成本有界（阈值删走后个位数 ms 量级；
    此前 NOT IN 全表扫在 20000 行时 p50=8ms 且随 ring 线性增长）。
    阈值取 15ms：宽到不被 CI 噪声误红（实测 2~5ms），紧到能抓住「删法
    退化成全表扫」这类数量级回归。"""
    import time
    monkeypatch.setattr(audit, "_MAX_ROWS", 2000)  # 小帽快速灌满
    for _ in range(2100):
        server._twin_impl("help", {})
    t = _mk_task()
    t0 = time.perf_counter()
    for _ in range(20):
        server._twin_impl("task_get", {"task_id": t["id"]})
    per_call_ms = (time.perf_counter() - t0) * 50
    assert per_call_ms < 15.0, f"满环每次调用 {per_call_ms:.2f}ms"
