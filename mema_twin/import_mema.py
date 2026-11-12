"""mema 桶导出件导入器（v0.5.0 拆分迁移工具，#1604）。

把 mema 侧导出的 mema-twin 桶 JSONL（scripts/export_twin_bucket.py 产物，
backup envelope 格式）回填进 twin 自持库：

- 已存在同 memory_id 的证据行 → 回填 content + subject 覆盖为 mema 现值
  （status/compiled_version/dims 等 twin 侧生命周期不动）——主体路径；
- 不存在 → 插新行（memory_id=原值，保 `<!-- src: N -->` 溯源不断链），
  三维 code 从 tags 还原（twin:wt:/au:/pu: 前缀；aud- 受众级行用 twin:aud:
  特判 → work_type=aud-{x}；完全无三维 tag 的行跳过并计数，不猜）；
- mema 侧 status 非 active 的行跳过（本体已亡）；
- 幂等：重跑时 content 已一致的行跳过；content 冲突（非空且不同）计数不覆盖；
  新插撞 UNIQUE(memory_id) 计入冲突（与窗口期本地发号撞号的显式归路）。

用法：
    python -m mema_twin.import_mema <export.jsonl> [--tenant <uid>]
                                      [--users-dir <dir>] [--dry-run]

--tenant：多租户形态（云端）显式租户选择——CLI 进程没有请求头，租户解析唯一
通道是 whoami，这里直选 users/<uid>/（目录必须已存在，twin 不代建）。
缺省 = default 自有库形态（MEMA_TWIN_DB_PATH 或包根 twin.sqlite3）。

twin 全程只读 JSONL 文件，绝不打开 memory.sqlite3（C 形态不变量）。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

from . import db, flow, tenant

BUCKET = "mema-twin"
_KIND_TAG_PREFIX = {"twin:wt:": "work_type", "twin:au:": "audience",
                    "twin:pu:": "purpose"}


def _dims_from_tags(tags: list[str]) -> dict | None:
    """tags → normalize 形状的三维 dict；aud- 特判 + 无任何维度 tag 返回 None
    （跳过人工看，不猜——R1：极旧行可能只有 twin:aud: 甚至全缺）。"""
    wt = au = pu = None
    aud_scoped = None
    for t in tags or []:
        if not isinstance(t, str):
            continue
        for prefix, kind in _KIND_TAG_PREFIX.items():
            if t.startswith(prefix):
                code = t[len(prefix):]
                if kind == "work_type":
                    wt = code
                elif kind == "audience":
                    au = code
                else:
                    pu = code
                break
        if t.startswith("twin:aud:"):
            aud_scoped = t[len("twin:aud:"):]
    if aud_scoped is not None:
        # 受众级行（v0.3.6+）：work_type=aud-{x} 合成码（store.py 同构），
        # audience 维取 twin:au:（缺则用 aud 值——旧行 #941 形态）
        return {"work_type": {"ok": True, "code": f"aud-{aud_scoped}"},
                "audience": {"ok": True, "code": au or aud_scoped},
                "purpose": {"ok": pu is not None, "code": pu}}
    if wt is None and au is None and pu is None:
        return None
    return {"work_type": {"ok": wt is not None, "code": wt},
            "audience": {"ok": au is not None, "code": au},
            "purpose": {"ok": pu is not None, "code": pu}}


def _resolve_conn(args) -> sqlite3.Connection:
    if args.tenant:
        users_dir = args.users_dir or os.environ.get(tenant.USERS_DIR_ENV) or ""
        if not users_dir:
            raise SystemExit(
                f"--tenant 需要多租户形态：设 {tenant.USERS_DIR_ENV} 或传 --users-dir")
        os.environ[tenant.USERS_DIR_ENV] = users_dir
        for env in tenant.EXCLUSIVE_ENVS:
            if (os.environ.get(env) or "").strip():
                raise SystemExit(f"--tenant 形态下 {env} 必须清空（互斥 env）")
        root = tenant._users_root() / str(args.tenant)
        if not root.is_dir():
            raise SystemExit(f"用户目录不存在：{root}（twin 不代建）")
        tenant.set_tenant(f"u{args.tenant}")
    return db.connect()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="把 mema mema-twin 桶导出件（backup envelope JSONL）回填进 twin 自持库")
    ap.add_argument("jsonl", help="导出件路径（scripts/export_twin_bucket.py 产物）")
    ap.add_argument("--tenant", type=int, default=None,
                    help="多租户 uid（users/<uid>/twin.sqlite3）；缺省=default 自有库")
    ap.add_argument("--users-dir", default=None,
                    help=f"覆盖 {tenant.USERS_DIR_ENV}（多租户根目录）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只统计不写库")
    args = ap.parse_args(argv)

    rows: list[dict] = []
    skipped_inactive = 0
    seen_canonicals: set[str] = set()
    with open(args.jsonl, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                env = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"跳过第 {lineno} 行（非 JSON: {e}）", file=sys.stderr)
                continue
            rec = env.get("record") or {}
            canonical = str(env.get("workspace_canonical") or rec.get("workspace") or "")
            seen_canonicals.add(canonical)
            # 过滤键 = envelope.workspace_canonical（R1：#976 迁移痕迹行
            # workspace 列可能是 mema-twin 但 canonical 是 mema-twin-dev）
            if canonical != BUCKET:
                continue
            if str(rec.get("status") or "") != "active":
                skipped_inactive += 1
                continue
            rows.append(rec)

    conn = _resolve_conn(args)
    counts = {"bucket_rows_active": len(rows), "skipped_inactive": skipped_inactive,
              "backfilled": 0, "backfill_same": 0,
              "inserted": 0, "skipped_no_dims": 0, "skipped_empty_content": 0,
              "conflicts": 0}
    try:
        for rec in rows:
            mid = rec.get("id")
            content = str(rec.get("content") or "")
            subject = str(rec.get("subject") or "")
            try:
                mid = int(mid)
            except (TypeError, ValueError):
                counts["conflicts"] += 1
                continue
            row = conn.execute(
                "SELECT id, content FROM twin_evidence WHERE memory_id=?",
                (mid,)).fetchone()
            if row is not None:
                # 既有指针行：回填主体路径。content 冲突（行已有不同非空正文——
                # 窗口期本地写入占号的信号）只计数不覆盖（R1 P1-1）。
                if not content.strip():
                    counts["skipped_empty_content"] += 1
                    continue
                old = row["content"] or ""
                if args.dry_run:
                    if old == content:
                        counts["backfill_same"] += 1
                    elif old.strip():
                        counts["conflicts"] += 1
                    else:
                        counts["backfilled"] += 1
                    continue
                outcome = db.backfill_evidence_content(conn, mid, content, subject)
                if outcome == "backfilled":
                    counts["backfilled"] += 1
                elif outcome == "same":
                    counts["backfill_same"] += 1
                else:  # conflict（行已有不同非空正文）或竞态 missing——都不覆盖
                    counts["conflicts"] += 1
                continue
            if not content.strip():
                counts["skipped_empty_content"] += 1
                continue
            dims = _dims_from_tags(rec.get("tags") if isinstance(rec.get("tags"), list) else [])
            if dims is None:
                counts["skipped_no_dims"] += 1
                continue
            if args.dry_run:
                counts["inserted"] += 1
                continue
            try:
                db.record_evidence(conn, mid, dims, subject=subject, content=content)
                counts["inserted"] += 1
            except sqlite3.IntegrityError:
                counts["conflicts"] += 1
        total = conn.execute("SELECT COUNT(*) AS c FROM twin_evidence").fetchone()["c"]
        empty = conn.execute(
            "SELECT COUNT(*) AS c FROM twin_evidence"
            " WHERE (content IS NULL OR content='') AND status!='void'").fetchone()["c"]
        counts["evidence_total"] = int(total)
        counts["evidence_empty_content_alive"] = int(empty)
        if not args.dry_run:
            flow.ensure_schema()
            flow.set_meta("import_done", json.dumps(
                {"at": db.now_iso(), "source": args.jsonl, **counts},
                ensure_ascii=False))
        tenant.reset_tenant()
    finally:
        conn.close()

    print(json.dumps(counts, ensure_ascii=False, indent=2))
    if counts.get("skipped_no_dims") or counts.get("conflicts"):
        print("提示：存在无三维 tag 跳过行或冲突行（不覆盖，本地正文保留），"
              "请人工核对后再继续部署流程；被冲突挡住的 mema 行内容只在导出件里，"
              "处置完（手工 SQL 或保留冷备）前不得删桶。", file=sys.stderr)
        return 2  # 非零退出码：一行式 `import --tenant 1 && import --tenant 2` 不得带冲突继续
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
