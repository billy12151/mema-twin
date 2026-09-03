import pytest

from mema_twin import flow


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(tmp_path / "twin.sqlite3"))
    monkeypatch.setenv("MEMA_TWIN_PROMPTS_DIR", str(tmp_path / "prompts"))
    monkeypatch.setenv("MEMA_TWIN_DELIVERABLES_DIR", str(tmp_path / "deliverables"))
    flow._schema_ready.clear()
    flow.ensure_schema()
    yield tmp_path
    flow._todos_by_session.clear()


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


def test_start_supersedes_open_tasks(env):
    a = flow.insert_task(workspace="ws", brief="A", status="planning", dims=_dims())
    b = flow.insert_task(workspace="ws", brief="B", status="planning", dims=_dims())
    assert flow.supersede_open_tasks("ws", b["id"]) == 1
    assert flow.get_task(a["id"])["status"] == "superseded"
    assert flow.get_task(b["id"])["status"] == "planning"
    # 其他 workspace 不受影响
    c = flow.insert_task(workspace="other", brief="C", status="planning", dims=_dims())
    d = flow.insert_task(workspace="ws", brief="D", status="planning", dims=_dims())
    assert flow.supersede_open_tasks("ws", d["id"]) == 1  # b 仍开放，被 d 让位
    assert flow.get_task(c["id"])["status"] == "planning"


def test_review_lifecycle_and_audit(env):
    t = flow.insert_task(workspace="ws", brief="T", status="planning", dims=_dims())
    flow.update_deliverable(t["id"], "draft v1")
    flow.set_status(t["id"], "submitted")
    r1 = flow.add_review(t["id"], "changes_requested", "结论不突出")
    flow.set_status(t["id"], "rejected", reason="结论不突出")
    flow.update_deliverable(t["id"], "draft v2")
    flow.set_status(t["id"], "submitted")
    r2 = flow.add_review(t["id"], "approved", "通过")
    flow.set_status(t["id"], "approved", reason="通过")
    assert (r1["round"], r2["round"]) == (1, 2)
    reviews = flow.list_reviews(t["id"])
    assert [x["verdict"] for x in reviews] == ["changes_requested", "approved"]
    final = flow.get_task(t["id"])
    assert final["status"] == "approved" and final["decided_at"]


def test_resume_restores_todos_and_creates_new_task(env):
    flow.set_session_todos("s1", [
        {"content": "a", "status": "completed"},
        {"content": "b", "status": "pending"},
    ])
    t = flow.insert_task(workspace="ws", brief="T", status="planning", dims=_dims(),
                         session_todos=flow.current_todos("s1"))
    flow.set_status(t["id"], "approved")
    _t = flow.get_task(t["id"])
    # resume: 恢复 todos + 新建 planning
    _t["workspace"] = "ws"
    nt = flow.insert_task(workspace="ws", brief=_t["brief"], status="planning",
                          dims=_dims(), parent_task_id=t["id"],
                          session_todos=_t["todos"])
    flow.set_session_todos("s2", _t["todos"])
    assert [x["content"] for x in flow.current_todos("s2")] == ["a", "b"]
    assert nt["parent_task_id"] == t["id"]


def test_revise_lineage(env):
    t = flow.insert_task(workspace="ws", brief="T", status="planning", dims=_dims())
    flow.set_status(t["id"], "submitted")
    child = flow.insert_task(workspace="ws", brief="T'", status="submitted",
                             dims=_dims(), parent_task_id=t["id"], iteration=1,
                             revision_reason="用户要求补风险节")
    flow.set_status(t["id"], "superseded", reason=f"revised by task #{child['id']}")
    assert flow.get_task(child["id"])["status"] == "submitted"
    assert flow.get_task(t["id"])["status"] == "superseded"
    assert flow.get_task(child["id"])["iteration"] == 1


def test_todos_validation(env):
    r = flow.set_session_todos("s", [{"content": "x", "status": "in_progress"},
                                     {"content": "y", "status": "pending"}])
    assert r["ok"] and r["count"] == 2
    with pytest.raises(ValueError):
        flow.set_session_todos("s", [{"content": "x", "status": "in_progress"},
                                     {"content": "y", "status": "in_progress"}])
    with pytest.raises(ValueError):
        flow.set_session_todos("s", [{"content": "", "status": "pending"}])


def test_deliverable_file_written_atomically(env):
    t = flow.insert_task(workspace="ws", brief="T", status="planning", dims=_dims())
    p = flow.write_deliverable_file("ws", t["id"], "# 交付稿")
    assert "task-1.md" in p
    from pathlib import Path
    assert Path(p).read_text(encoding="utf-8") == "# 交付稿"
    assert flow.get_task(t["id"])["deliverable_path"] == p


def test_meta_roundtrip(env):
    assert flow.get_meta("last_scan_at") is None
    flow.set_meta("last_scan_at", "2026-09-03T00:00:00+00:00")
    flow.set_meta("last_scan_at", "2026-09-04T00:00:00+00:00")  # upsert
    assert flow.get_meta("last_scan_at") == "2026-09-04T00:00:00+00:00"


def test_resubmit_after_changes_requested(env):
    """打回后同任务再提交（干跑发现的死路修复）：rejected 可再 submit。"""
    from mema_twin import server
    t = flow.insert_task(workspace="ws", brief="T", status="planning", dims=_dims())
    r1 = server.twin("task_submit", {"task_id": t["id"],
                                     "deliverable_md": "v1"})
    assert r1["ok"] and r1["round"] == 1
    server.twin("task_review", {"task_id": t["id"], "verdict": "changes_requested",
                                "notes": "缺数字"})
    assert flow.get_task(t["id"])["status"] == "rejected"
    r2 = server.twin("task_submit", {"task_id": t["id"],
                                     "deliverable_md": "v2 带数字"})
    assert r2["ok"] and r2["round"] == 2
    r3 = server.twin("task_review", {"task_id": t["id"], "verdict": "approved"})
    assert r3["ok"] and "deliverable_path" in r3
    assert flow.get_task(t["id"])["status"] == "approved"


def test_submit_snapshots_session_todos_for_resume(env):
    from mema_twin import server
    flow.set_session_todos("sx", [{"content": "a", "status": "pending"}])
    t = flow.insert_task(workspace="ws", brief="T", status="planning", dims=_dims())
    r = server.twin("task_submit", {"task_id": t["id"], "deliverable_md": "d",
                                    "session": "sx"})
    assert r["ok"]
    # 行内 todos 已随 submit 快照
    assert [x["content"] for x in flow.get_task(t["id"])["todos"]] == ["a"]


def test_supersede_spares_submitted(env):
    """review#6：submitted 在等评审，开新任务不得把它变成不可评审的 superseded。"""
    t1 = flow.insert_task(workspace="ws", brief="待评审", status="submitted", dims=_dims())
    t2 = flow.insert_task(workspace="ws", brief="新任务", status="planning", dims=_dims())
    assert flow.supersede_open_tasks("ws", t2["id"]) == 0  # submitted 不让位
    assert flow.get_task(t1["id"])["status"] == "submitted"
    t3 = flow.insert_task(workspace="ws", brief="又一个", status="planning", dims=_dims())
    assert flow.supersede_open_tasks("ws", t3["id"]) == 1  # planning 照常让位
    assert flow.get_task(t2["id"])["status"] == "superseded"


def test_resubmit_from_empty_session_keeps_todos(env):
    """review#5：空会话重提不清掉任务行里已快照的 todos。"""
    from mema_twin import server
    flow.set_session_todos("s-full", [{"content": "a", "status": "pending"}])
    t = flow.insert_task(workspace="ws", brief="T", status="planning", dims=_dims())
    server.twin("task_submit", {"task_id": t["id"], "deliverable_md": "v1", "session": "s-full"})
    server.twin("task_review", {"task_id": t["id"], "verdict": "changes_requested"})
    r = server.twin("task_submit", {"task_id": t["id"], "deliverable_md": "v2", "session": "s-empty"})
    assert r["ok"]
    assert [x["content"] for x in flow.get_task(t["id"])["todos"]] == ["a"]
