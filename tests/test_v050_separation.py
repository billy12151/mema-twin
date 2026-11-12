"""v0.5.0 拆分（#1604）行为锁定：偏好本体自持 twin.sqlite3。

覆盖方案 §7 测试计划的 twin 侧新增组：存储层（content 列迁移/发号/去重）、
write 本地化响应、取材本地化（missing_content 跳过）、sink 收缩（运行时调用点
已删的响应级回归）、import 端到端（双模式/幂等/过滤/--dry-run/--tenant 形态）。
数据面 stub（mema HTTP）已不存在——本文件全部走本地行 seed 与 sink.whoami 桩。
"""
import json
import sqlite3

import pytest

from mema_twin import compile_actions, db, flow, server, sink


GOOD_DIMS = {
    "work_type": {"ok": True, "kind": "work_type", "raw": "周报", "code": "work_report",
                  "label_zh": "工作汇报", "matched_by": "exact_or_alias"},
    "audience": {"ok": True, "kind": "audience", "raw": "高层", "code": "leadership",
                 "label_zh": "高层与决策层", "matched_by": "exact_or_alias"},
    "purpose": {"ok": True, "kind": "purpose", "raw": "同步", "code": "sync_info",
                "label_zh": "信息同步与知会", "matched_by": "exact_or_alias"},
}
PPT_DIMS = {**GOOD_DIMS,
            "work_type": {"ok": True, "kind": "work_type", "raw": "PPT",
                          "code": "presentation", "label_zh": "演示文稿",
                          "matched_by": "exact_or_alias"}}


def _row(conn, mid):
    return conn.execute("SELECT * FROM twin_evidence WHERE memory_id=?", (mid,)).fetchone()


# ---- a) add_local_evidence：本地发号 + 词法去重 -----------------------


class TestAddLocalEvidence:
    def test_numbering_is_max_plus_one(self):
        """发号 = MAX(memory_id)+1：显式 id（import 原值）不被复用也不撞号。"""
        conn = db.connect()
        db.record_evidence(conn, 5, GOOD_DIMS, subject="s5", content="旧5")
        db.record_evidence(conn, 9, GOOD_DIMS, subject="s9", content="旧9")
        res = db.add_local_evidence(conn, GOOD_DIMS, subject="新", content="新偏好")
        assert res["duplicate"] is False
        assert res["memory_id"] == 10 and res["evidence_id"] == 10
        conn.close()

    def test_dedupe_same_work_type_same_content(self):
        """同 work_type 同 content：duplicate=True + 既有 id（幂等成功语义）。"""
        conn = db.connect()
        r1 = db.add_local_evidence(conn, GOOD_DIMS, subject="A", content="偏好X")
        assert r1["duplicate"] is False
        r2 = db.add_local_evidence(conn, GOOD_DIMS, subject="B", content="偏好X")
        assert r2["duplicate"] is True
        assert r2["memory_id"] == r1["memory_id"]
        assert r2["evidence_id"] == r1["evidence_id"]
        n = conn.execute("SELECT COUNT(*) AS c FROM twin_evidence").fetchone()["c"]
        assert n == 1  # 不双插
        conn.close()

    def test_dedupe_strips_whitespace(self):
        """strip 后相同（前后空白差异）也命中去重（sha256(content.strip())）。"""
        conn = db.connect()
        r1 = db.add_local_evidence(conn, GOOD_DIMS, subject="A", content="偏好X")
        r2 = db.add_local_evidence(conn, GOOD_DIMS, subject="A",
                                   content="  偏好X  \n")
        assert r2["duplicate"] is True and r2["memory_id"] == r1["memory_id"]
        conn.close()

    def test_no_dedupe_across_work_types(self):
        """跨 work_type 同 content 不互拒（去重键依赖归一后的 work_type code）。"""
        conn = db.connect()
        r1 = db.add_local_evidence(conn, GOOD_DIMS, subject="A", content="同文")
        r2 = db.add_local_evidence(conn, PPT_DIMS, subject="B", content="同文")
        assert r1["duplicate"] is False and r2["duplicate"] is False
        assert r2["memory_id"] == r1["memory_id"] + 1
        conn.close()

    def test_voided_row_not_deduped(self):
        """去重只对 alive 行（uncompiled/compiled）：void 行同文不拦重写。"""
        conn = db.connect()
        r1 = db.add_local_evidence(conn, GOOD_DIMS, subject="A", content="偏好Y")
        db.void_evidence(conn, r1["memory_id"])
        r2 = db.add_local_evidence(conn, GOOD_DIMS, subject="A2", content="偏好Y")
        assert r2["duplicate"] is False
        conn.close()

    def test_tags_and_source_ref_land_in_row(self):
        conn = db.connect()
        db.add_local_evidence(conn, GOOD_DIMS, subject="s", content="c",
                              tags_json='["tag1","tag2"]', source_ref="src://task-1")
        row = _row(conn, 1)
        assert json.loads(row["tags"]) == ["tag1", "tag2"]
        assert row["source_ref"] == "src://task-1"
        assert row["subject"] == "s" and row["content"] == "c"
        conn.close()


# ---- b) 旧库迁移：content/tags/source_ref 列补齐 ---------------------


OLD_EVIDENCE_DDL = """
CREATE TABLE twin_evidence(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id INTEGER NOT NULL,
  work_type TEXT, audience TEXT, purpose TEXT,
  work_type_raw TEXT NOT NULL DEFAULT '',
  audience_raw TEXT NOT NULL DEFAULT '',
  purpose_raw TEXT NOT NULL DEFAULT '',
  subject TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'uncompiled',
  compiled_version INTEGER,
  created_at TEXT NOT NULL,
  UNIQUE(memory_id)
);
"""


def test_old_schema_gets_content_columns(tmp_path, monkeypatch):
    """v0.4.x 指针库（无 content/tags/source_ref 列）→ db.connect() ALTER 补齐，
    旧行 content 为空串（靠 import 回填）。"""
    path = tmp_path / "old.sqlite3"
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(path))
    raw = sqlite3.connect(str(path))
    raw.execute(OLD_EVIDENCE_DDL)
    raw.execute("INSERT INTO twin_evidence(memory_id, work_type, subject, created_at)"
                " VALUES(900, 'work_report', '旧指针行', '2026-01-01T00:00:00+00:00')")
    raw.commit()
    raw.close()
    conn = db.connect()  # 触发 _migrate 的 ALTER 路径
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(twin_evidence)")}
    assert {"content", "tags", "source_ref"} <= cols
    row = _row(conn, 900)
    assert row["content"] == "" and row["tags"] == "[]" and row["source_ref"] == ""
    # 迁移后行可正常走本地写入/去重链路
    res = db.add_local_evidence(conn, GOOD_DIMS, subject="新", content="新偏好")
    assert res["duplicate"] is False and res["memory_id"] == 901
    conn.close()


def test_fresh_schema_has_content_columns():
    conn = db.connect()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(twin_evidence)")}
    assert {"content", "tags", "source_ref"} <= cols  # 新库 DDL 自带
    conn.close()


# ---- d) backfill_evidence_content ------------------------------------


class TestBackfillEvidenceContent:
    def _seed_pointer_row(self, conn):
        db.record_evidence(conn, 400, GOOD_DIMS, subject="twin旧subject", content="")
        conn.execute("UPDATE twin_evidence SET subject='twin旧subject' WHERE memory_id=400")
        conn.commit()

    def test_backfills_content_and_overwrites_subject(self):
        conn = db.connect()
        self._seed_pointer_row(conn)
        assert db.backfill_evidence_content(conn, 400, "mema正文", "mema新subject") == "backfilled"
        row = _row(conn, 400)
        assert row["content"] == "mema正文" and row["subject"] == "mema新subject"
        conn.close()

    def test_idempotent_when_content_same(self):
        conn = db.connect()
        self._seed_pointer_row(conn)
        assert db.backfill_evidence_content(conn, 400, "mema正文", "mema新subject") == "backfilled"
        # 重跑：content 一致 → "same"（幂等跳过），不重复写
        assert db.backfill_evidence_content(conn, 400, "mema正文", "mema新subject") == "same"
        conn.close()

    def test_empty_subject_does_not_overwrite(self):
        conn = db.connect()
        self._seed_pointer_row(conn)
        assert db.backfill_evidence_content(conn, 400, "mema正文", "") == "backfilled"
        row = _row(conn, 400)
        assert row["content"] == "mema正文" and row["subject"] == "twin旧subject"
        conn.close()

    def test_missing_row_returns_false(self):
        conn = db.connect()
        assert db.backfill_evidence_content(conn, 404, "x", "y") == "missing"
        conn.close()

    def test_conflicting_nonempty_content_not_overwritten(self):
        """行已有不同非空正文 → "conflict"（不覆盖，交导入器计数）：窗口期本地
        写入占号的保护（R1 P1-1）。"""
        conn = db.connect()
        self._seed_pointer_row(conn)
        db.backfill_evidence_content(conn, 400, "mema正文", "mema新subject")
        assert db.backfill_evidence_content(conn, 400, "别的正文", "x") == "conflict"
        row = _row(conn, 400)
        assert row["content"] == "mema正文" and row["subject"] == "mema新subject"
        conn.close()

    def test_backfill_preserves_lifecycle_columns(self):
        """回填不动 status/compiled_version/dims（twin 侧生命周期保留）。"""
        conn = db.connect()
        self._seed_pointer_row(conn)
        db.mark_compiled(conn, [400], 3, "work_report")
        db.backfill_evidence_content(conn, 400, "mema正文", "mema新subject")
        row = _row(conn, 400)
        assert row["status"] == "compiled" and row["compiled_version"] == 3
        assert row["work_type"] == "work_report"
        conn.close()


# ---- c) import_mema 端到端 -------------------------------------------


def _jsonl(tmp_path, lines):
    p = tmp_path / "export.jsonl"
    p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
                 encoding="utf-8")
    return str(p)


def _envelope(rec, canonical="mema-twin"):
    return {"workspace_canonical": canonical,
            "record": {"id": rec["id"], "subject": rec.get("subject", ""),
                       "content": rec.get("content", ""),
                       "tags": rec.get("tags", []),
                       "status": rec.get("status", "active")}}


def _import_lines():
    return [
        _envelope({"id": 201, "subject": "mema subj 201", "content": "回填正文201",
                   "tags": ["twin:wt:work_report", "twin:au:leadership",
                            "twin:pu:sync_info"]}),
        _envelope({"id": 301, "subject": "受众级", "content": "对高层要白话",
                   "tags": ["twin:aud:leadership"]}),
        _envelope({"id": 302, "subject": "普通行", "content": "正文302",
                   "tags": ["twin:wt:presentation", "twin:au:team_peers",
                            "twin:pu:drive_action"]}),
        _envelope({"id": 303, "subject": "极旧无tag", "content": "正文303",
                   "tags": ["mema:其他"]}),  # 无任何三维 tag → skip
        _envelope({"id": 304, "subject": "已退", "content": "正文304",
                   "status": "retired"}),  # 非 active → 过滤
        _envelope({"id": 305, "subject": "dev 痕迹", "content": "正文305",
                   "tags": ["twin:wt:work_report"]}, canonical="mema-twin-dev"),
    ]


class TestImportMemaEndToEnd:
    def test_full_run_counts_and_rows(self, tmp_path):
        conn = db.connect()
        db.record_evidence(conn, 201, GOOD_DIMS)  # 指针行（content 空）
        conn.close()
        path = _jsonl(tmp_path, _import_lines())
        rc = __import__("mema_twin.import_mema", fromlist=["main"]).main([path])
        assert rc == 2  # _import_lines 自带无 dims 行（303）→ 非零退出
        conn = db.connect()
        # 回填：201 补正文 + subject 覆盖为 mema 现值
        r201 = _row(conn, 201)
        assert r201["content"] == "回填正文201" and r201["subject"] == "mema subj 201"
        assert r201["status"] == "uncompiled"  # twin 侧生命周期不动
        # 新插：aud- 特判 → work_type=aud-{x}
        r301 = _row(conn, 301)
        assert r301["work_type"] == "aud-leadership" and r301["audience"] == "leadership"
        # 新插：三维 tag 逆映射
        r302 = _row(conn, 302)
        assert (r302["work_type"], r302["audience"], r302["purpose"]) == \
            ("presentation", "team_peers", "drive_action")
        # 三类缺席行
        for absent in (303, 304, 305):
            assert _row(conn, absent) is None, absent
        meta = json.loads(flow.get_meta("import_done"))
        assert meta["backfilled"] == 1 and meta["inserted"] == 2
        assert meta["skipped_no_dims"] == 1 and meta["conflicts"] == 0
        assert meta["bucket_rows_active"] == 4  # 201/301/302/303（304/305 已滤）
        conn.close()

    def test_rerun_idempotent_backfill_same(self, tmp_path):
        conn = db.connect()
        db.record_evidence(conn, 201, GOOD_DIMS)
        conn.close()
        path = _jsonl(tmp_path, _import_lines())
        mod = __import__("mema_twin.import_mema", fromlist=["main"])
        mod.main([path])
        meta1 = json.loads(flow.get_meta("import_done"))
        assert meta1["backfilled"] == 1 and meta1["inserted"] == 2
        mod.main([path])  # 重跑
        meta2 = json.loads(flow.get_meta("import_done"))
        # 三行（201 回填 + 301/302 新插）content 均已一致 → 全部幂等跳过
        assert meta2["backfilled"] == 0 and meta2["backfill_same"] == 3
        assert meta2["inserted"] == 0
        conn = db.connect()
        n = conn.execute("SELECT COUNT(*) AS c FROM twin_evidence").fetchone()["c"]
        conn.close()
        assert n == 3  # 不双插

    def test_window_period_local_row_counted_as_conflict(self, tmp_path):
        """窗口期本地发号已占用原值 id 且带不同非空正文：import 计入 conflicts
        且**不覆盖**本地正文（R1 P1-1——静默覆盖=窗口期新偏好丢失，必须人工核对）；
        空正文本地行则照常回填。"""
        conn = db.connect()
        db.record_evidence(conn, 302, PPT_DIMS, subject="本地行", content="本地窗口期写入")
        db.record_evidence(conn, 306, PPT_DIMS, subject="本地空行", content="")
        conn.close()
        lines = _import_lines() + [
            {"workspace_canonical": "mema-twin",
             "record": {"id": 306, "subject": "普通行306", "content": "正文306",
                        "tags": ["twin:wt:product_doc"], "status": "active"}}]
        path = _jsonl(tmp_path, lines)
        __import__("mema_twin.import_mema", fromlist=["main"]).main([path])
        meta = json.loads(flow.get_meta("import_done"))
        assert meta["conflicts"] == 1 and meta["backfilled"] == 1 and meta["inserted"] == 2
        conn = db.connect()
        r302 = _row(conn, 302)
        r306 = _row(conn, 306)
        conn.close()
        # 冲突行本地正文原样保留
        assert r302["content"] == "本地窗口期写入" and r302["subject"] == "本地行"
        # 空正文本地行照常回填
        assert r306["content"] == "正文306" and r306["subject"] == "普通行306"

    def test_add_local_evidence_retry_after_integrity_error(self, tmp_path, monkeypatch):
        """发号撞 UNIQUE（并发窗口）→ rollback → 重取 MAX 重插成功（R1 P2-10：
        IntegrityError→重试路径此前无用例走到，靠代理连接注入一次假撞号确定性触发）。"""
        real_conn = db.connect()
        state = {"poisoned": False}

        class _FlakyConn:
            """sqlite3.Connection 属性只读，用薄代理注入一次性 IntegrityError。"""

            def execute(self, sql, *a, **kw):
                if (not state["poisoned"] and isinstance(sql, str)
                        and sql.lstrip().upper().startswith("INSERT INTO TWIN_EVIDENCE")):
                    state["poisoned"] = True
                    raise sqlite3.IntegrityError("UNIQUE constraint failed (simulated)")
                return real_conn.execute(sql, *a, **kw)

            def __getattr__(self, name):
                return getattr(real_conn, name)

        res = db.add_local_evidence(_FlakyConn(), PPT_DIMS, subject="s", content="retry body")
        real_conn.close()
        assert res["duplicate"] is False and res["memory_id"] == 1
        conn = db.connect()
        n = conn.execute("SELECT COUNT(*) AS c FROM twin_evidence").fetchone()["c"]
        conn.close()
        assert n == 1  # 重试成功恰好一行，无残留半事务

    def test_conflicts_exit_nonzero(self, tmp_path):
        """conflicts/skipped_no_dims>0 → 退出码 2（R2 P1-2：一行式
        `import 1 && import 2` 不得带冲突继续删桶流程）。"""
        conn = db.connect()
        db.record_evidence(conn, 302, PPT_DIMS, subject="本地行", content="本地窗口期写入")
        conn.close()
        path = _jsonl(tmp_path, _import_lines())  # 302 冲突（不同非空正文）
        rc = __import__("mema_twin.import_mema", fromlist=["main"]).main([path])
        assert rc == 2

    def test_users_dir_without_mema_url_fails_startup(self, tmp_path, monkeypatch):
        """多租户形态 MEMA_TWIN_MEMA_URL 未设 → 启动 SystemExit（R2 P2-4：
        防「服务活着但全租户 gateway_unreachable」的全瞎形态）。"""
        monkeypatch.setenv("MEMA_TWIN_USERS_DIR", str(tmp_path))
        monkeypatch.delenv("MEMA_TWIN_MEMA_URL", raising=False)
        monkeypatch.setenv("MEMA_TWIN_TRANSPORT", "http")
        # conftest 隔离 env（DB_PATH/镜像目录）也在 EXCLUSIVE_ENVS——先清掉，
        # 让校验走到 URL 缺失分支
        for env in ("MEMA_TWIN_DB_PATH", "MEMA_TWIN_PROMPTS_DIR",
                    "MEMA_TWIN_DELIVERABLES_DIR", "MEMA_TWIN_PLAYBOOKS_DIR"):
            monkeypatch.delenv(env, raising=False)
        (tmp_path / "1").mkdir()
        with pytest.raises(SystemExit, match="MEMA_TWIN_MEMA_URL"):
            server.main()

    def test_bad_id_counted_as_conflict_not_raised(self, tmp_path):
        """record.id 非 int → 计入 conflicts，不裸抛（报告提示人工核对）。"""
        lines = _import_lines() + [
            {"workspace_canonical": "mema-twin",
             "record": {"id": "not-an-int", "subject": "脏行",
                        "content": "c", "tags": ["twin:wt:work_report"],
                        "status": "active"}}]
        path = _jsonl(tmp_path, lines)
        __import__("mema_twin.import_mema", fromlist=["main"]).main([path])
        meta = json.loads(flow.get_meta("import_done"))
        assert meta["conflicts"] == 1
        conn = db.connect()
        n = conn.execute("SELECT COUNT(*) AS c FROM twin_evidence").fetchone()["c"]
        conn.close()
        assert n == 3  # 脏行不落

    def test_dry_run_writes_nothing(self, tmp_path):
        path = _jsonl(tmp_path, _import_lines())
        __import__("mema_twin.import_mema", fromlist=["main"]).main([path, "--dry-run"])
        conn = db.connect()
        n = conn.execute("SELECT COUNT(*) AS c FROM twin_evidence").fetchone()["c"]
        conn.close()
        assert n == 0
        assert flow.get_meta("import_done") is None  # dry-run 不落标记

    def test_tenant_flag_requires_users_dir(self, tmp_path, monkeypatch):
        """--tenant 无 USERS_DIR 形态 → 结构化拒绝（SystemExit），不静默落 default 库。"""
        path = _jsonl(tmp_path, _import_lines())
        monkeypatch.delenv("MEMA_TWIN_USERS_DIR", raising=False)
        with pytest.raises(SystemExit, match="MEMA_TWIN_USERS_DIR|--users-dir"):
            __import__("mema_twin.import_mema", fromlist=["main"]).main(
                [path, "--tenant", "1"])

    def test_tenant_flag_lands_in_users_dir(self, tmp_path, monkeypatch):
        users = tmp_path / "users"
        (users / "7").mkdir(parents=True)
        monkeypatch.setenv("MEMA_TWIN_USERS_DIR", str(users))
        for env in ("MEMA_TWIN_DB_PATH", "MEMA_TWIN_PROMPTS_DIR",
                    "MEMA_TWIN_DELIVERABLES_DIR", "MEMA_TWIN_PLAYBOOKS_DIR",
                    "MEMA_TWIN_MEMA_TOKEN"):
            monkeypatch.delenv(env, raising=False)
        path = _jsonl(tmp_path, _import_lines())
        __import__("mema_twin.import_mema", fromlist=["main"]).main(
            [path, "--tenant", "7", "--users-dir", str(users)])
        assert (users / "7" / "twin.sqlite3").exists()
        conn = sqlite3.connect(str(users / "7" / "twin.sqlite3"))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT content FROM twin_evidence WHERE memory_id=302").fetchone()
        conn.close()
        assert row["content"] == "正文302"


# ---- e) 取材本地化：空 content 行 skipped(missing_content) ------------


def test_compile_skips_empty_content_with_missing_content_reason():
    """旧指针行（content 空）进 compile：skipped 如实上报，不伪造素材。"""
    conn = db.connect()
    db.record_evidence(conn, 500, GOOD_DIMS)  # content 默认空
    conn.close()
    r = compile_actions._action_compile({"work_type": "周报"})
    assert r["ok"] and r["evidence_count"] == 0
    assert r["skipped_evidence"] == [{"memory_id": 500, "reason":
        "missing_content（旧指针行待迁移：python -m mema_twin.import_mema 回填）"}]
    assert "[500]" not in r["material"]


def test_compile_mixed_content_rows_partial_skip():
    conn = db.connect()
    db.record_evidence(conn, 501, GOOD_DIMS, subject="s", content="有正文")
    db.record_evidence(conn, 502, GOOD_DIMS, subject="s", content="")
    conn.close()
    r = compile_actions._action_compile({"work_type": "周报"})
    assert r["evidence_count"] == 1 and "[501]" in r["material"]
    assert [s["memory_id"] for s in r["skipped_evidence"]] == [502]


# ---- f) write 本地化响应（键与 duplicate 语义）------------------------


class TestWriteResponse:
    def _write(self, content, **kw):
        data = {"content": content, "work_type": "周报", "audience": "高层",
                "purpose": "同步", **kw}
        return server._twin_impl("write", data)

    def test_response_keys_and_row_landing(self):
        r = self._write("偏好正文X")
        assert r["ok"] is True
        for key in ("memory_id", "evidence_id", "duplicate", "memory", "dimensions"):
            assert key in r, key
        assert r["duplicate"] is False
        assert r["memory"]["id"] == r["memory_id"] == r["evidence_id"]
        assert "偏好正文X" in r["memory"]["subject"] or r["memory"]["subject"]
        conn = db.connect()
        row = _row(conn, r["memory_id"])
        conn.close()
        assert row["content"] == "偏好正文X"
        assert row["work_type"] == "work_report"

    def test_duplicate_semantics_end_to_end(self):
        r1 = self._write("同文偏好")
        assert r1["ok"] and r1["duplicate"] is False
        r2 = self._write("同文偏好")
        assert r2["ok"] and r2["duplicate"] is True
        assert r2["memory_id"] == r1["memory_id"]
        assert "勿重试" in r2["note"]
        conn = db.connect()
        n = conn.execute("SELECT COUNT(*) AS c FROM twin_evidence").fetchone()["c"]
        conn.close()
        assert n == 1  # 幂等：不重复落行
        # 跨类型同文不拒
        r3 = self._write("同文偏好", work_type="PPT")
        assert r3["ok"] and r3["duplicate"] is False

    def test_write_never_reports_mema_unreachable(self):
        """本地写不会 unreachable：mema_unreachable 分支消失的回归钉。"""
        r = self._write("任意偏好")
        assert r["ok"] is True
        assert r.get("error") != "mema_unreachable"


# ---- sink 收缩：运行时调用点已删（响应级回归）------------------------


def test_status_has_no_open_conflicts_key():
    """status 的 open_conflicts 键随 review_conflicts 退役整体移除（无
    AttributeError 僵尸分支——函数级钉在 test_sink_token.py）。"""
    s = server._twin_impl("status", {})
    assert s["ok"] and "open_conflicts" not in s


def test_compile_has_no_conflict_warning_key():
    r = server._twin_impl("compile", {"work_type": "周报"})
    assert r["ok"] and "conflict_warning" not in r


def test_help_wording_updated():
    """help 文案三处改口：write 带 duplicate 语义句、status 无 mema 桶冲突、
    void 叙事改本库归档（无 mema retire 句）。"""
    h = server._twin_impl("help", {})
    actions = h["actions"]
    assert "duplicate" in actions["write"]
    assert "open_conflicts" not in actions["status"]
    assert "冲突" not in actions["status"]
    assert "retire" not in actions["void"] and "mema" not in actions["void"]
    assert "归档" in actions["void"]


def test_no_mema_notices_keys_in_responses():
    """mema_notices/mema_notices_guidance 出口随 notices 机制退役：任何响应
    不再出现这两键。"""
    for resp in (server._twin_impl("write", {"content": "c", "work_type": "周报",
                                             "audience": "高层", "purpose": "同步"}),
                 server._twin_impl("task_start", {"brief": "B", "work_type": "周报"}),
                 server._twin_impl("status", {})):
        assert "mema_notices" not in resp
        assert "mema_notices_guidance" not in resp


# ---- 多租户：whoami 网络不可达 → gateway_unreachable（新 catch）-------


def test_gateway_unreachable_mapped_distinctly(monkeypatch):
    """SinkError（不可达，非鉴权/非半可用）→ gateway_unreachable（v0.5.0 新增
    catch，与 tenant_unavailable 分开分诊）。"""
    from mema_twin import tenant
    monkeypatch.setenv("MEMA_TWIN_USERS_DIR", "/tmp/v050-fake-users")
    monkeypatch.setattr(server, "_request_auth_token", lambda: "Bearer t")
    monkeypatch.setattr(sink, "whoami",
                        lambda t: (_ for _ in ()).throw(
                            sink.SinkError("mema 网关不可达")))
    try:
        r = server._twin_impl("status", {})
        assert r["ok"] is False and r["error"] == "gateway_unreachable"
    finally:
        monkeypatch.delenv("MEMA_TWIN_USERS_DIR")
        tenant.reset_tenant()
