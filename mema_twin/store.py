"""prompt 版本存储（D2/D5）：DB 为准，文件镜像做降级读取与用户可视化。"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from . import db, taxonomy


def prompts_dir() -> Path:
    return Path(os.environ.get("MEMA_TWIN_PROMPTS_DIR") or db.PROJECT_ROOT / "prompts")


def _mirror_dir(work_type: str) -> Path:
    return prompts_dir() / work_type


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def is_known_work_type(conn: sqlite3.Connection, code: str) -> bool:
    if taxonomy.by_code("work_type", code):
        return True
    return any(r["code"] == code for r in db.custom_types(conn, "work_type"))


def resolve_work_type_code(conn: sqlite3.Connection, value: str) -> str | None:
    """把任意输入解析为 canonical code；解析不到返回 None（不落 pending，由调用方决定）。"""
    v = (value or "").strip()
    if not v:
        return None
    hit = taxonomy.match_exact("work_type", v)
    if hit:
        return hit.code
    for row in db.type_rows(conn, "work_type"):
        cands = {c.strip().casefold() for c in
                 (row["code"], row["label_zh"], row["label_en"], *row["aliases"]) if c}
        if v.casefold() in cands:
            return row["code"]
    return None


def create_version(conn: sqlite3.Connection, work_type: str,
                   prompt_md: str, source_memory_ids: list, model: str = "") -> dict:
    if not (prompt_md or "").strip():
        raise ValueError("prompt_md must not be empty")
    if not is_known_work_type(conn, work_type):
        raise ValueError(f"unknown work_type code: {work_type!r}；先 twin(action=\"taxonomy\") 查码或治理 pending")
    ids = [str(i) for i in (source_memory_ids or [])]
    ts = db.now_iso()
    warnings: list[str] = []
    for _attempt in range(3):  # review#12：并发 submit 撞版本号时重试
        row = conn.execute(
            "SELECT COALESCE(MAX(version),0) AS v FROM twin_prompt_versions WHERE work_type=?",
            (work_type,),
        ).fetchone()
        version = int(row["v"]) + 1
        prev = conn.execute(
            "SELECT version FROM twin_prompt_versions"
            " WHERE work_type=? AND status='active' ORDER BY version DESC LIMIT 1",
            (work_type,),
        ).fetchone()
        superseded_version = int(prev["version"]) if prev else None
        conn.execute(
            "UPDATE twin_prompt_versions SET status='retired'"
            " WHERE work_type=? AND status='active'",
            (work_type,),
        )
        try:
            conn.execute(
                "INSERT INTO twin_prompt_versions"
                "(work_type, version, prompt_md, source_memory_ids, model, status, evidence_count, created_at, activated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (work_type, version, prompt_md, json.dumps(ids), model or "",
                 "active", len(ids), ts, ts),
            )
            break
        except sqlite3.IntegrityError:
            conn.rollback()
            if _attempt == 2:
                raise
            continue
    conn.commit()
    d = _mirror_dir(work_type)
    for f in (f"v{version}.md", "active.md"):
        # 镜像是降级/可视化渠道（D2），写失败降级为警告，不击穿已落库的版本（review#3）
        try:
            _atomic_write(d / f, prompt_md)
        except OSError as e:
            warnings.append(f"镜像写入失败（{d / f}）: {e}")
    out = {"work_type": work_type, "version": version,
           "model": model or "", "source_count": len(ids),
           "superseded_version": superseded_version,
           "mirror": str(d / f"v{version}.md")}
    if warnings:
        out["warnings"] = warnings
    return out


def get_active(conn: sqlite3.Connection, work_type: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM twin_prompt_versions"
        " WHERE work_type=? AND status='active'"
        " ORDER BY version DESC LIMIT 1",
        (work_type,),
    ).fetchone()
    if row:
        d = dict(row)
        d["source_memory_ids"] = json.loads(d["source_memory_ids"])
        d["from_mirror"] = False
        return d
    mirror = _mirror_dir(work_type) / "active.md"
    if mirror.exists():
        return {"work_type": work_type, "version": None,
                "prompt_md": mirror.read_text(encoding="utf-8"),
                "source_memory_ids": [], "model": "", "status": "mirror",
                "from_mirror": True}
    return None


def list_versions(conn: sqlite3.Connection, work_type: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM twin_prompt_versions WHERE work_type=? ORDER BY version DESC",
        (work_type,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["source_memory_ids"] = json.loads(d["source_memory_ids"])
        out.append(d)
    return out
