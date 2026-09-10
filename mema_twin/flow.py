"""交付任务执行流（M2.1；v0.3.8 删评审环）：机制改造自 plan-mode-mcp。

保留的 plan-mode 机制：连接工厂（每次操作新连接）、状态机 + supersede（开放任务
自动让位）、行级审计（任务行不可变追加，状态变更盖 decided_at 时间戳）、
lineage（revise 生成子任务并继承状态）、resume（恢复 todos 并新建 planning 行）、
会话隔离的内存 todos（同一进程多会话互不串）。

v0.3.8（设计文档 mema-twin-v0.3.8-design-2026-09-09.md A）：删除评审环——
task_submit 即终点（planning → submitted 终态），approved/rejected/pending 三态
与 task_review/task_pending/twin_task_reviews 表退役；交付物文件改在 submit 时
落盘。评审 verdict 从无下游消费者，用户真实反应走对话 → twin.write 主路径。
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from . import db, sink

# 状态机（v0.3.8 三态）：planning → submitted（终态，交付即收口）；superseded =
# 被新任务让位/修订/关闭（历史保留）。存量行的 approved/rejected/pending 仅作
# 展示（legacy），不再有任何迁移入口。
STATUSES = ("planning", "submitted", "superseded")
_OPEN_STATUSES = ("planning",)
_SUPERSEDE_STATUSES = ("planning",)  # submitted 是终态无需保护，让位只收 planning
_RESUMABLE_STATUSES = ("planning",)  # 中断续作；已交付（submitted）返工走 task_revise
_REVISABLE_STATUSES = ("submitted",)  # 交付后返工，子任务回 planning 重走

_SCHEMA = """
CREATE TABLE IF NOT EXISTS twin_tasks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  work_type TEXT,
  audience TEXT,
  purpose TEXT,
  work_type_raw TEXT NOT NULL DEFAULT '',
  audience_raw TEXT NOT NULL DEFAULT '',
  purpose_raw TEXT NOT NULL DEFAULT '',
  brief TEXT NOT NULL,
  interpreted_intent TEXT,
  deliverable_md TEXT NOT NULL DEFAULT '',
  todos TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL,
  reason TEXT,
  client TEXT,
  agent_id TEXT,
  persona_version INTEGER,
  deliverable_path TEXT,
  created_at TEXT NOT NULL,
  decided_at TEXT,
  parent_task_id INTEGER,
  iteration INTEGER NOT NULL DEFAULT 0,
  revision_reason TEXT,
  loop_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_twin_tasks_status ON twin_tasks(status);
CREATE TABLE IF NOT EXISTS twin_meta(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

_schema_ready: set[str] = set()  # 已建表的 db 路径（测试会切 MEMA_TWIN_DB_PATH，不能单布尔）


def ensure_schema() -> None:
    """twin_tasks 等表建在 twin.sqlite3（与既有表同库，db.connect 后追加执行）。
    v0.3.8 顺带完成一次性退役清理（幂等）：DROP 评审表、删双跑三族 meta
    （persona_origin/compare_prev/compare_offered；保留 last_scheduled_compile_at，
    勿扩大前缀）；v0.3.9 增删 twin_meta.last_scan_at 死键（twin_scan v0.3.7
    退役后无代码读取）。"""
    path = str(db.db_path())
    if path in _schema_ready:
        return
    conn = db.connect()
    try:
        conn.executescript(_SCHEMA)
        conn.execute("DROP TABLE IF EXISTS twin_task_reviews")
        conn.execute(
            "DELETE FROM twin_meta WHERE key LIKE 'persona_origin:%'"
            " OR key LIKE 'compare_prev:%' OR key LIKE 'compare_offered:%'")
        conn.execute("DELETE FROM twin_meta WHERE key='last_scan_at'")
        conn.commit()
    finally:
        conn.close()
    _schema_ready.add(path)


# ---- 会话 todos（进程内存，plan-mode 同款隔离方式）----

_DEFAULT_SESSION = "default"
_todos_by_session: dict[str, list[dict]] = {}


def _todos_for(session_key: str | None) -> list[dict]:
    return _todos_by_session.get(session_key or _DEFAULT_SESSION, [])


def _set_todos(session_key: str | None, todos: list[dict]) -> None:
    _todos_by_session[session_key or _DEFAULT_SESSION] = todos


def normalize_todos(todos) -> list[dict]:
    if not isinstance(todos, list):
        raise ValueError("todos must be a list")
    out: list[dict] = []
    for i, t in enumerate(todos):
        if not isinstance(t, dict):
            raise ValueError(f"todos[{i}] must be an object")
        content = str(t.get("content") or "").strip()
        status = str(t.get("status") or "pending").strip()
        if not content:
            raise ValueError(f"todos[{i}].content must not be empty")
        if status not in ("pending", "in_progress", "completed"):
            raise ValueError(f"todos[{i}].status invalid: {status!r}")
        out.append({"content": content, "status": status})
    if sum(1 for t in out if t["status"] == "in_progress") > 1:
        raise ValueError("at most one todo may be in_progress")
    return out


def set_session_todos(session_key: str | None, todos) -> dict:
    normalized = normalize_todos(todos)
    _set_todos(session_key, normalized)
    return {
        "ok": True, "count": len(normalized),
        "completed": sum(1 for t in normalized if t["status"] == "completed"),
        "in_progress": sum(1 for t in normalized if t["status"] == "in_progress"),
        "todos": normalized,
    }


def current_todos(session_key: str | None) -> list[dict]:
    return list(_todos_for(session_key))


# ---- 任务生命周期 ----

def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    try:
        d["todos"] = json.loads(d.get("todos") or "[]")
    except (ValueError, TypeError):
        d["todos"] = []
    return d


def insert_task(*, brief: str, status: str,
                dims: dict | None = None, interpreted_intent: str | None = None,
                deliverable_md: str = "", reason: str | None = None,
                persona_version: int | None = None,
                parent_task_id: int | None = None, iteration: int = 0,
                revision_reason: str | None = None, client: str | None = None,
                session_todos: list[dict] | None = None) -> dict:
    dims = dims or {}

    def code(kind: str) -> str | None:
        d = dims.get(kind) or {}
        return d.get("code") if d.get("ok") else None

    conn = db.connect()
    try:
        cur = conn.execute(
            "INSERT INTO twin_tasks(work_type, audience, purpose,"
            " work_type_raw, audience_raw, purpose_raw, brief, interpreted_intent,"
            " deliverable_md, todos, status, reason, client, agent_id,"
            " persona_version, created_at, parent_task_id, iteration, revision_reason)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (code("work_type"), code("audience"), code("purpose"),
             str((dims.get("work_type") or {}).get("raw") or ""),
             str((dims.get("audience") or {}).get("raw") or ""),
             str((dims.get("purpose") or {}).get("raw") or ""),
             brief, interpreted_intent, deliverable_md or "",
             json.dumps(session_todos or [], ensure_ascii=False), status,
             (reason or "").strip() or None,
             client or sink._env_client(),
             "mema-twin",
             persona_version, db.now_iso(), parent_task_id, iteration,
             (revision_reason or "").strip() or None),
        )
        task_id = int(cur.lastrowid)
        conn.commit()
        row = conn.execute("SELECT * FROM twin_tasks WHERE id=?", (task_id,)).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def get_task(task_id: int) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM twin_tasks WHERE id=?", (int(task_id),)).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def set_status(task_id: int, status: str, reason: str | None = None,
               allowed_from: tuple[str, ...] | None = None) -> dict | None:
    """条件状态迁移（对抗 review#4）：跨连接 check-then-act 会让 superseded 任务
    被并发操作复活。allowed_from 给出合法前置状态，UPDATE 按 rowcount 判定成败；
    前置不符抛 ValueError，由边界转为 invalid_input。"""
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}; expected one of {STATUSES}")
    reason = (reason or "").strip() or None
    conn = db.connect()
    try:
        if allowed_from is None:
            cur = conn.execute(
                "UPDATE twin_tasks SET status=?, reason=COALESCE(?, reason), decided_at=?"
                " WHERE id=?",
                (status, reason, db.now_iso(), int(task_id)),
            )
        else:
            cur = conn.execute(
                f"UPDATE twin_tasks SET status=?, reason=COALESCE(?, reason), decided_at=?"
                f" WHERE id=? AND status IN ({','.join('?' for _ in allowed_from)})",
                (status, reason, db.now_iso(), int(task_id), *allowed_from),
            )
            if cur.rowcount == 0:
                row = conn.execute(
                    "SELECT status FROM twin_tasks WHERE id=?", (int(task_id),)
                ).fetchone()
                if row is None:
                    return None
                raise ValueError(
                    f"task {task_id} 当前状态为 {row['status']!r}，不允许迁到 {status!r}"
                    f"（需 {list(allowed_from)}）")
        conn.commit()
        row = conn.execute("SELECT * FROM twin_tasks WHERE id=?", (int(task_id),)).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def supersede_open_tasks(except_id: int) -> int:
    conn = db.connect()
    try:
        cur = conn.execute(
            f"UPDATE twin_tasks SET status='superseded', decided_at=?"
            f" WHERE id!=? AND status IN ({','.join('?' for _ in _SUPERSEDE_STATUSES)})",
            (db.now_iso(), int(except_id), *_SUPERSEDE_STATUSES),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def update_deliverable(task_id: int, deliverable_md: str,
                       brief: str | None = None,
                       interpreted_intent: str | None = None,
                       todos: list[dict] | None = None) -> dict | None:
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE twin_tasks SET deliverable_md=?,"
            " brief=COALESCE(?, brief), interpreted_intent=COALESCE(?, interpreted_intent),"
            " todos=COALESCE(?, todos)"
            " WHERE id=?",
            (deliverable_md, brief, interpreted_intent,
             json.dumps(todos, ensure_ascii=False) if todos is not None else None,
             int(task_id)),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM twin_tasks WHERE id=?", (int(task_id),)).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def set_deliverable_path(task_id: int, path: str) -> None:
    conn = db.connect()
    try:
        conn.execute("UPDATE twin_tasks SET deliverable_path=? WHERE id=?",
                     (path, int(task_id)))
        conn.commit()
    finally:
        conn.close()


def open_tasks(limit: int = 100) -> list[dict]:
    """scan 用：直接按状态查（走 idx_twin_tasks_status），不翻 recent 截断（review#13）。"""
    conn = db.connect()
    try:
        rows = conn.execute(
            f"SELECT * FROM twin_tasks WHERE status IN"
            f" ({','.join('?' for _ in _OPEN_STATUSES)}) ORDER BY id LIMIT ?",
            (*_OPEN_STATUSES, max(1, min(int(limit), 500))),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def recent_tasks(limit: int = 10) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM twin_tasks ORDER BY id DESC LIMIT ?",
            (max(1, min(int(limit), 100)),),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


# ---- 评审（v0.3.8 已删：append-only 评审表随评审环一并退役）----


# ---- 交付物文件（approve 时落盘，审计工件，plan-mode 的 plan_file 同款）----

def deliverables_dir() -> Path:
    root = Path(os.environ.get("MEMA_TWIN_PROMPTS_DIR")
                or db.PROJECT_ROOT / "prompts").parent
    d = Path(os.environ.get("MEMA_TWIN_DELIVERABLES_DIR") or root / "deliverables")
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_deliverable_file(task_id: int, deliverable_md: str) -> str:
    path = deliverables_dir() / f"task-{int(task_id)}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(deliverable_md, encoding="utf-8")
    tmp.replace(path)
    set_deliverable_path(task_id, str(path))
    return str(path)


# ---- twin_meta（scan 等的键值状态）----

def get_meta(key: str) -> str | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT value FROM twin_meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def set_meta(key: str, value: str) -> None:
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO twin_meta(key, value) VALUES(?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def list_meta(prefix: str) -> dict[str, str]:
    """前缀枚举 meta（v0.3.7：nightly_reject:/persona_stale: 等 status 可见面用）。"""
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT key, value FROM twin_meta WHERE key LIKE ?", (prefix + "%",)
        ).fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()


def delete_meta(key: str) -> None:
    """删除 meta 键（v0.3.7 清 nightly_reject:/persona_stale: 用）：删行而非写空值，
    读侧无需过滤脏空键。"""
    conn = db.connect()
    try:
        conn.execute("DELETE FROM twin_meta WHERE key=?", (key,))
        conn.commit()
    finally:
        conn.close()
