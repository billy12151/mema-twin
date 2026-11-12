"""本地 console（`mema-twin console`）测试——v0.5.0 拆分方案 §3.6 迁入面。

对象：mema_twin/console_{view,http,static,script,i18n,style,cli}.py。
自 mema 仓 tests/test_console_twin.py 改造迁入：
- 删 spawn/runner_env/看门狗/reaper/locate/find_spec/config 用例（架构简化
  三件套：spawn→进程内直调、mema 联查删、配置面删）；
- store/门卫/HTML/i18n 用例保留并适配（DDL 用 twin 仓真实 schema 建库，
  不再手抄）；
- 新增：multi_tenant_unsupported 拒绝、import 迁移横幅两态、en/zh 键集合
  相等、抽屉直读本地正文、CLI loopback 硬拒。
"""
from __future__ import annotations

import http.client
import json
import re
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from mema_twin import db as twin_db
from mema_twin.console_http import (
    TwinConsoleAPI,
    _limit,
    _offset,
    build_http_server,
)
from mema_twin.console_i18n import I18N_JS
from mema_twin.console_static import TWIN_HTML
from mema_twin.console_style import STYLE, TWIN_EXTRA_CSS
from mema_twin.console_view import TwinStore

_META_DDL = "CREATE TABLE IF NOT EXISTS twin_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);"


# ---- 夹具 -------------------------------------------------------------------


def make_ledger(path: Path, *, partial: bool = False) -> Path:
    """真实 schema 建库 + 种子数据；partial=True 只建 twin_meta（缺表降级用例）。"""
    conn = sqlite3.connect(path)
    if partial:
        conn.executescript(_META_DDL)
        conn.commit()
        conn.close()
        return path
    conn.executescript(twin_db._SCHEMA)
    conn.executescript(_META_DDL)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS twin_audit("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
        " action TEXT NOT NULL DEFAULT '', ok INTEGER NOT NULL DEFAULT 0,"
        " error TEXT, ms INTEGER NOT NULL DEFAULT 0, client TEXT,"
        " task_id INTEGER, step_id INTEGER);"
    )
    conn.execute(
        "INSERT INTO twin_types(type_kind, code, label_zh, label_en, domain, aliases,"
        " is_custom, status, created_at) VALUES"
        "('work_type','code-review','代码评审','Code review','工程','[\"cr\",\"review\"]',1,'active','2026-01-01'),"
        "('work_type','perf-audit','性能审计','Performance audit','工程','[]',1,'active','2026-01-02')"
    )
    conn.execute(
        "INSERT INTO twin_pending_values(type_kind, raw_value, hit_count, status,"
        " resolved_code, created_at) VALUES"
        "('work_type','cr',7,'pending',NULL,'2026-01-03'),"
        "('work_type','review pr',3,'pending',NULL,'2026-01-04'),"
        "('work_type','audit',2,'mapped','perf-audit','2026-01-05')"
    )
    conn.execute(
        "INSERT INTO twin_prompt_versions(work_type, version, prompt_md, status,"
        " evidence_count, created_at, activated_at) VALUES"
        "('code-review',12,?, 'active', 24, '2026-01-06', '2026-01-07'),"
        "('code-review',11,'old prompt','retired', 20, '2026-01-01', '2026-01-02')",
        ("x" * 1234,),
    )
    conn.execute(
        "INSERT INTO twin_evidence(memory_id, work_type, audience, purpose,"
        " work_type_raw, audience_raw, purpose_raw, subject, content, tags, source_ref,"
        " status, compiled_version, created_at) VALUES"
        "(101,'code-review','self','learn','cr','self','learn','subj compiled','正文 A','[\"perf\",\"Linux\"]','src:101','compiled',12,'2026-01-08'),"
        "(102,'code-review',NULL,NULL,'cr','','','subj uncompiled','正文 B','[]','','uncompiled',NULL,'2026-01-09'),"
        "(103,'perf-audit',NULL,NULL,'audit','','','subj void','','[]','','void',NULL,'2026-01-10')"
    )
    conn.execute(
        "INSERT INTO twin_meta(key, value) VALUES"
        "('persona_stale:code-review','1'),('unrelated_key','x')"
    )
    conn.execute(
        "INSERT INTO twin_audit(ts, action, ok, error, ms, client) VALUES"
        "('2026-01-01T00:00:00+00:00','write',1,NULL,12,'zcode'),"
        "('2026-01-02T00:00:00+00:00','void',0,'not_found',5,'console')"
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def ledger(tmp_path: Path) -> Path:
    return make_ledger(tmp_path / "ledger.sqlite3")


# ---- TwinStore 读面 ----------------------------------------------------------


def test_store_overview_aggregates(ledger: Path) -> None:
    ov = TwinStore(str(ledger)).overview()
    assert ov["ok"] is True
    assert ov["degraded"] == []
    assert len(ov["versions"]) == 1  # active only（retired 不进）
    v = ov["versions"][0]
    assert v["work_type"] == "code-review" and v["version"] == 12
    assert v["size_chars"] == 1234
    stats = {(r["work_type"], r["status"]): r["n"] for r in ov["evidence_stats"]}
    assert stats == {("code-review", "compiled"): 1, ("code-review", "uncompiled"): 1,
                     ("perf-audit", "void"): 1}
    assert ov["pending_pending"] == 2
    assert ov["stale_keys"] == ["persona_stale:code-review"]
    assert ov["audit"] == {"total": 2, "ok_n": 1, "last_ts": "2026-01-02T00:00:00+00:00"}


def test_store_overview_import_banner_two_states(tmp_path: Path) -> None:
    """迁移横幅判据（方案 §3.6 R2 P2-4）：twin_meta 无 import_done 且有空
    content 在世行 → pending；有 import_done 标记 → 永不误报（dangling 行）。"""
    p = tmp_path / "imp.sqlite3"
    make_ledger(p)  # 3 行证据（2 alive，均有 content）→ 无标记但无空行
    st = TwinStore(str(p))
    ov = st.overview()
    assert ov["import"] == {"pending": True, "empty_content": 0}  # 无标记但无空行
    conn = sqlite3.connect(p)
    # 存量指针行：在世但 content 空
    conn.execute(
        "INSERT INTO twin_evidence(memory_id, work_type, work_type_raw, subject,"
        " content, status, created_at) VALUES"
        "(201,'code-review','cr','legacy pointer','','uncompiled','2026-01-11')"
    )
    conn.commit()
    conn.close()
    ov2 = st.overview()
    assert ov2["import"] == {"pending": True, "empty_content": 1}
    conn = sqlite3.connect(p)
    conn.execute("INSERT INTO twin_meta(key, value) VALUES('import_done','{}')")
    conn.commit()
    conn.close()
    ov3 = st.overview()  # 标记在 → 即便仍有空 content 行也不提示
    assert ov3["import"] == {"pending": False, "empty_content": 0}


def test_store_evidence_carries_local_content(ledger: Path) -> None:
    """v0.5.0 正文自持：SELECT 自带 content/tags/source_ref（抽屉直读，
    无 mema 联查）。"""
    st = TwinStore(str(ledger))
    ev = st.evidence()
    assert ev["ok"] is True
    assert ev["total"] == 3
    assert [r["id"] for r in ev["items"]] == [3, 2, 1]  # id DESC
    compiled = next(r for r in ev["items"] if r["status"] == "compiled")
    assert compiled["content"] == "正文 A"
    assert compiled["tags"] == '["perf","Linux"]'
    assert compiled["source_ref"] == "src:101"
    assert compiled["subject"] == "subj compiled"
    ev2 = st.evidence(status="compiled")
    assert ev2["total"] == 1 and ev2["items"][0]["memory_id"] == 101
    ev3 = st.evidence(work_type="perf-audit")
    assert ev3["total"] == 1 and ev3["items"][0]["status"] == "void"
    ev4 = st.evidence(limit=2, offset=1)
    assert ev4["total"] == 3 and [r["id"] for r in ev4["items"]] == [2, 1]


def test_store_pending_order_and_status(ledger: Path) -> None:
    st = TwinStore(str(ledger))
    pd = st.pending("pending")
    assert [r["raw_value"] for r in pd["items"]] == ["cr", "review pr"]  # hit_count DESC, id
    hist = st.pending("mapped")
    assert hist["items"][0]["resolved_code"] == "perf-audit"


def test_store_types_aliases_parsed(ledger: Path) -> None:
    tp = TwinStore(str(ledger)).types("work_type")
    assert tp["ok"] is True
    by_code = {r["code"]: r for r in tp["items"]}
    assert by_code["code-review"]["aliases"] == ["cr", "review"]
    assert by_code["perf-audit"]["is_custom"] == 1


def test_store_audit_recent_window(ledger: Path) -> None:
    au = TwinStore(str(ledger)).audit(limit=1)
    assert au["items"][0]["action"] == "void"  # id DESC
    assert au["items"][0]["client"] == "console"


def test_store_persona_returns_active_prompt(ledger: Path) -> None:
    p = TwinStore(str(ledger)).persona("code-review")
    assert p["ok"] is True
    assert p["version"] == 12  # active 行（retired v11 不取）
    assert p["prompt_md"] == "x" * 1234
    assert p["evidence_count"] == 24
    miss = TwinStore(str(ledger)).persona("no-such-type")
    assert miss == {"ok": False, "error": "not_found", "work_type": "no-such-type"}


def test_store_missing_table_degrades_not_raises(tmp_path: Path) -> None:
    st = TwinStore(str(make_ledger(tmp_path / "partial.sqlite3", partial=True)))
    ov = st.overview()
    assert ov["ok"] is True  # 绝不 500/raise：缺表区块降级
    assert set(ov["degraded"]) == {"versions", "evidence_stats", "pending", "audit", "import"}
    assert ov["stale_keys"] == []
    ev = st.evidence()
    assert ev == {"ok": False, "error": "twin_schema_mismatch", "section": "evidence"}
    assert st.pending_count() is None


def test_store_empty_audit_table_sum_null_normalized(tmp_path: Path) -> None:
    """空表 SUM(ok) 为 NULL → 0 归一（mema R2 P2-10 语义保留）。"""
    p = tmp_path / "empty.sqlite3"
    conn = sqlite3.connect(p)
    conn.executescript(twin_db._SCHEMA)
    conn.executescript(_META_DDL)
    conn.execute("CREATE TABLE twin_audit(id INTEGER PRIMARY KEY, ts TEXT, action TEXT DEFAULT '', ok INTEGER DEFAULT 0, error TEXT, ms INTEGER DEFAULT 0, client TEXT, task_id INTEGER, step_id INTEGER)")
    conn.commit()
    conn.close()
    ov = TwinStore(str(p)).overview()
    assert ov["audit"] == {"total": 0, "ok_n": 0, "last_ts": None}


def test_store_unopenable_path_returns_unreadable(tmp_path: Path) -> None:
    st = TwinStore(str(tmp_path))  # 目录不是库文件
    ov = st.overview()
    assert ov["ok"] is False and ov["error"] == "twin_db_unreadable"
    ev = st.evidence()
    assert ev["ok"] is False and ev["error"] == "twin_db_unreadable"


# ---- API 适配层：形态门 / 无库门 / limit 归一 --------------------------------


def test_api_multi_tenant_rejected_everywhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """多租户（USERS_DIR）形态仅 default 形态支持 console：API 一律
    multi_tenant_unsupported；写面绝不触发（证据行不动）。"""
    ledger = make_ledger(tmp_path / "mt.sqlite3")
    monkeypatch.setenv("MEMA_TWIN_USERS_DIR", str(tmp_path / "users"))
    api = TwinConsoleAPI()
    # 读面错误不带 _http_status（HTTP 层 _json_or_error 缺省 400，mema 同款）；
    # 写面（void/resolve）带显式 _http_status=400。
    for call, expect_status in (
        (api.twin_overview, False),
        (lambda: api.twin_evidence(), False),
        (lambda: api.twin_pending(), False),
        (lambda: api.twin_types("work_type"), False),
        (lambda: api.twin_audit(), False),
        (lambda: api.twin_persona("code-review"), False),
        (lambda: api.twin_void(101), True),
        (lambda: api.twin_resolve(1, "reject"), True),
    ):
        r = call()
        assert r["error"] == "multi_tenant_unsupported", r
        if expect_status:
            assert r["_http_status"] == 400
    conn = sqlite3.connect(ledger)
    status = conn.execute("SELECT status FROM twin_evidence WHERE memory_id=101").fetchone()
    conn.close()
    assert status[0] == "compiled"  # 写被门卫拦下，未触发


def test_api_db_not_found_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """库文件不存在：读面 twin_db_not_found；写面前置门拦截（绝不借写操作
    静默造空库，mema R2 P0-1 语义保留）。"""
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(tmp_path / "nope" / "twin.sqlite3"))
    api = TwinConsoleAPI()
    ov = api.twin_overview()
    assert ov == {"ok": False, "error": "twin_db_not_found",
                  "path": str(tmp_path / "nope" / "twin.sqlite3")}
    v = api.twin_void(1)
    assert v["error"] == "twin_db_not_found" and v["_http_status"] == 400
    assert not (tmp_path / "nope").exists()  # 没有静默建库


def test_limit_offset_coercion() -> None:
    assert _limit("abc", 50, 200) == 50
    assert _limit(None, 50, 200) == 50
    assert _limit(9999, 50, 200) == 200
    assert _limit(0, 50, 200) == 1
    assert _offset("x") == 0
    assert _offset(-5) == 0
    assert _offset(7) == 7


def test_api_param_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = make_ledger(tmp_path / "v.sqlite3")
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(ledger))
    api = TwinConsoleAPI()
    assert api.twin_types("bogus")["_http_status"] == 400
    assert api.twin_persona("")["_http_status"] == 400
    assert api.twin_persona("code-review")["ok"] is True
    assert api.twin_void("abc")["error"] == "memory_id must be an integer"
    assert api.twin_resolve(7, "bogus")["_http_status"] == 400
    assert api.twin_resolve(7, "canonicalize")["_http_status"] == 400  # 缺 new_type


# ---- 写面（进程内直调 _twin_impl）--------------------------------------------


def _seed_pending_and_evidence(ledger: Path) -> tuple[int, int]:
    """(pending_id, memory_id)：直插 tmp 库造一张票据 + 一条证据。"""
    conn = sqlite3.connect(ledger)
    kind_row = conn.execute(
        "SELECT type_kind, code FROM twin_types WHERE code='code-review'"
    ).fetchone()
    assert kind_row, "seeded custom type must exist"
    conn.execute(
        "INSERT INTO twin_pending_values(type_kind, raw_value, hit_count, status,"
        " created_at) VALUES ('work_type', 'console-e2e-raw', 1, 'pending', '2026-01-01')"
    )
    pending_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    conn.execute(
        "INSERT INTO twin_evidence(memory_id, work_type, work_type_raw, subject,"
        " content, status, created_at) VALUES"
        " (424242, 'code-review', 'raw-wt', 'e2e evidence', 'e2e 正文',"
        " 'uncompiled', '2026-01-01')"
    )
    conn.commit()
    conn.close()
    return pending_id, 424242


def test_void_direct_call_marks_row_and_audits_console(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """void 进程内直调：行变 void；twin_audit 落 client='console'（归因不丢）；
    再 void → already_void 幂等。"""
    ledger = make_ledger(tmp_path / "void.sqlite3")
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(ledger))
    _pid, mid = _seed_pending_and_evidence(ledger)
    api = TwinConsoleAPI()
    r = api.twin_void(mid)
    assert r.get("ok") is True
    assert r["memory_id"] == mid and r["already_void"] is False
    conn = sqlite3.connect(ledger)
    status = conn.execute("SELECT status FROM twin_evidence WHERE memory_id=?", (mid,)).fetchone()
    clients = conn.execute("SELECT client, action FROM twin_audit WHERE client='console'").fetchall()
    conn.close()
    assert status[0] == "void"
    assert ("console", "void") in clients
    r2 = api.twin_void(mid)
    assert r2.get("ok") is True and r2.get("already_void") is True
    assert "note" in r2  # 幂等提示进 alert（前端只进 alert/textContent）


def test_resolve_direct_call_maps_and_blocks_redecision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = make_ledger(tmp_path / "res.sqlite3")
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(ledger))
    pid, _mid = _seed_pending_and_evidence(ledger)
    api = TwinConsoleAPI()
    r = api.twin_resolve(pid, "map", code="code-review")
    assert r.get("ok") is True and r["decision"] == "map"
    conn = sqlite3.connect(ledger)
    ticket = conn.execute(
        "SELECT status, resolved_code FROM twin_pending_values WHERE id=?", (pid,)
    ).fetchone()
    conn.close()
    assert ticket[0] == "mapped" and ticket[1] == "code-review"
    # 重复裁定 → twin invalid_input 透传 400
    r2 = api.twin_resolve(pid, "reject")
    assert r2.get("error") == "invalid_input" and r2["_http_status"] == 400


def test_resolve_canonicalize_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = make_ledger(tmp_path / "canon.sqlite3")
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(ledger))
    pid, _mid = _seed_pending_and_evidence(ledger)
    api = TwinConsoleAPI()
    r = api.twin_resolve(pid, "canonicalize", new_type={"code": "perf-x", "zh": "性能巡检"})
    assert r.get("ok") is True
    conn = sqlite3.connect(ledger)
    row = conn.execute(
        "SELECT label_zh, is_custom FROM twin_types WHERE code='perf-x'"
    ).fetchone()
    conn.close()
    assert row[0] == "性能巡检" and row[1] == 1


def test_twin_busy_mapping(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """锁竞争归一（mema R2 P1-6 语义保留）：twin internal_error+locked →
    503 twin_busy，不透传 internal_error。"""
    ledger = make_ledger(tmp_path / "busy.sqlite3")
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(ledger))
    import mema_twin.console_http as ch

    def fake_impl(action, data):
        return {"ok": False, "error": "internal_error",
                "reason": "OperationalError: database is locked"}

    monkeypatch.setattr(ch, "_twin_impl", fake_impl)
    r = TwinConsoleAPI().twin_void(1)
    assert r["error"] == "twin_busy" and r["_http_status"] == 503


def test_write_lock_serializes_concurrent_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模块级写锁：并发 void/resolve 串行进 _twin_impl（mema TWIN_RUNNER_LOCK
    的进程内等价物）。"""
    ledger = make_ledger(tmp_path / "lock.sqlite3")
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(ledger))
    import mema_twin.console_http as ch

    inside = 0
    max_inside = 0
    guard = threading.Lock()
    original = ch._twin_impl

    def slow_impl(action, data):
        nonlocal inside, max_inside
        with guard:
            inside += 1
            max_inside = max(max_inside, inside)
        original(action, data)
        with guard:
            inside -= 1

    monkeypatch.setattr(ch, "_twin_impl", slow_impl)
    api = TwinConsoleAPI()
    threads = [threading.Thread(target=api.twin_void, args=(100000 + i,))
               for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert max_inside == 1


# ---- HTTP 门卫四件套 + 页面 ---------------------------------------------------


@pytest.fixture()
def http_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ledger = make_ledger(tmp_path / "http.sqlite3")
    monkeypatch.setenv("MEMA_TWIN_DB_PATH", str(ledger))
    server = build_http_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, server.server_address[1]
    server.shutdown()
    server.server_close()


def _conn(port: int) -> http.client.HTTPConnection:
    return http.client.HTTPConnection("127.0.0.1", port, timeout=3)


def test_http_get_twin_page(http_server) -> None:
    _server, port = http_server
    for path in ("/twin", "/twin/", "/"):
        conn = _conn(port)
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        assert resp.status == 200
        assert resp.headers["Content-Type"].startswith("text/html")
        assert "twinBoot" in body
        conn.close()
    conn = _conn(port)
    conn.request("HEAD", "/twin")
    resp = conn.getresponse()
    assert resp.status == 200
    resp.read()
    conn.close()


def test_http_health_and_404(http_server) -> None:
    _server, port = http_server
    conn = _conn(port)
    conn.request("GET", "/api/health")
    resp = conn.getresponse()
    payload = json.loads(resp.read().decode("utf-8"))
    assert resp.status == 200 and payload["ok"] is True
    conn.close()
    conn = _conn(port)
    conn.request("GET", "/nope")
    resp = conn.getresponse()
    assert resp.status == 404
    resp.read()
    conn.close()


def test_http_post_gate_full(http_server) -> None:
    """门卫四件套全量：403（缺 X-Console-Request）/ 415（Content-Type）/
    Host 门（DNS-rebinding 防线）/ 411（无 Content-Length）/ 413（2MB 帽）/
    400（坏 JSON / 非对象）/ 404（未知路由）。"""
    _server, port = http_server
    headers = {"Content-Type": "application/json", "X-Console-Request": "1"}

    # 缺 X-Console-Request → 403
    conn = _conn(port)
    conn.request("POST", "/api/twin/overview", body="{}",
                 headers={"Content-Type": "application/json"})
    assert conn.getresponse().status == 403
    conn.close()
    # Content-Type 错 → 415
    conn = _conn(port)
    conn.request("POST", "/api/twin/overview", body="{}",
                 headers={"Content-Type": "text/plain", "X-Console-Request": "1"})
    assert conn.getresponse().status == 415
    conn.close()
    # Host 门（唯一 DNS-rebinding 防线，必须 403）
    conn = _conn(port)
    conn.request("POST", "/api/twin/overview", body="{}",
                 headers={**headers, "Host": "evil.example.com"})
    assert conn.getresponse().status == 403
    conn.close()
    # Host 门对 GET 同样生效
    conn = _conn(port)
    conn.request("GET", "/twin", headers={"Host": "evil.example.com"})
    assert conn.getresponse().status == 403
    conn.close()
    # 无 Content-Length（chunked）→ 411：stdlib 不解码 chunked，拒收不错解析
    conn = _conn(port)
    conn.request("POST", "/api/twin/overview", body=iter([b"{}"]),
                 headers={**headers, "Transfer-Encoding": "chunked"})
    assert conn.getresponse().status == 411
    conn.close()
    # 2MB 帽 → 413
    conn = _conn(port)
    conn.request("POST", "/api/twin/overview", body="{}",
                 headers={**headers, "Content-Length": str(2 * 1024 * 1024 + 1)})
    assert conn.getresponse().status == 413
    conn.close()
    # 坏 JSON → 400
    conn = _conn(port)
    conn.request("POST", "/api/twin/overview", body="not json", headers=headers)
    assert conn.getresponse().status == 400
    conn.close()
    # 非对象 JSON → 400
    conn = _conn(port)
    conn.request("POST", "/api/twin/overview", body="[1,2]", headers=headers)
    assert conn.getresponse().status == 400
    conn.close()
    # 未知路由 → 404
    conn = _conn(port)
    conn.request("POST", "/api/twin/locate", body="{}", headers=headers)
    assert conn.getresponse().status == 404
    conn.close()


def test_http_options_never_sends_cors(http_server) -> None:
    """CSRF 防线 pin（mema R2 P1-1 原样迁移）：204 永不带 Access-Control-* 头
    ——preflight 永远失败，跨站简单 POST 带不了自定义头。"""
    _server, port = http_server
    conn = _conn(port)
    conn.request("OPTIONS", "/twin")
    resp = conn.getresponse()
    assert resp.status == 204
    resp.read()
    cors = [k for k, _v in resp.getheaders() if k.lower().startswith("access-control")]
    assert cors == [], f"OPTIONS must never carry CORS headers, got {cors}"
    conn.close()


def test_http_post_roundtrip(http_server) -> None:
    """正常头 → 读面 API 全链路 200（overview 带 import 状态；evidence 带
    content；写面 void 真生效）。"""
    _server, port = http_server
    headers = {"Content-Type": "application/json", "X-Console-Request": "1"}
    conn = _conn(port)
    conn.request("POST", "/api/twin/overview", body="{}", headers=headers)
    resp = conn.getresponse()
    payload = json.loads(resp.read().decode("utf-8"))
    assert resp.status == 200 and payload["ok"] is True
    assert payload["pending_pending"] == 2
    conn.close()
    conn = _conn(port)
    conn.request("POST", "/api/twin/evidence", body="{}", headers=headers)
    resp = conn.getresponse()
    payload = json.loads(resp.read().decode("utf-8"))
    assert resp.status == 200 and payload["ok"] is True
    row = next(r for r in payload["items"] if r.get("status") == "compiled")
    assert row["content"] == "正文 A"  # 正文本地直读
    conn.close()
    conn = _conn(port)
    conn.request("POST", "/api/twin/void", body=json.dumps({"memory_id": 102}),
                 headers=headers)
    resp = conn.getresponse()
    payload = json.loads(resp.read().decode("utf-8"))
    assert resp.status == 200 and payload.get("ok") is True
    conn.close()


def test_http_multi_tenant_page_renders_api_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """多租户形态：页面照常 200（顶部渲染友好横幅），API 一律 400
    multi_tenant_unsupported。"""
    make_ledger(tmp_path / "mt.sqlite3")
    monkeypatch.setenv("MEMA_TWIN_USERS_DIR", str(tmp_path / "users"))
    server = build_http_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        headers = {"Content-Type": "application/json", "X-Console-Request": "1"}
        conn = _conn(port)
        conn.request("GET", "/twin")
        resp = conn.getresponse()
        assert resp.status == 200
        resp.read()
        conn.close()
        conn = _conn(port)
        conn.request("POST", "/api/twin/overview", body="{}", headers=headers)
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 400
        assert payload["error"] == "multi_tenant_unsupported"
        conn.close()
    finally:
        server.shutdown()
        server.server_close()


def test_http_server_rejects_non_loopback_bind() -> None:
    with pytest.raises(ValueError, match="loopback"):
        build_http_server("0.0.0.0", 8766)


# ---- 前端静态断言 -------------------------------------------------------------


def _i18n_keys(lang: str) -> set[str]:
    m = re.search(rf"{lang}: \{{(.*?)\}}\s*(?:,\s*zh: |$)", I18N_JS, re.S)
    assert m, f"i18n {lang} block not found"
    # 值一律以引号开头 → key 的可靠形态是 `identifier:'`
    return set(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*'", m.group(1)))


def test_i18n_en_zh_key_sets_equal() -> None:
    en = _i18n_keys("en")
    zh = _i18n_keys("zh")
    assert en == zh, f"en-only: {sorted(en - zh)}; zh-only: {sorted(zh - en)}"


def test_i18n_used_keys_defined_both_langs() -> None:
    """twin 页用到的 t('key') 与 data-i18n 必须在 en/zh 都有定义。"""
    en = _i18n_keys("en")
    zh = _i18n_keys("zh")
    used = set(re.findall(r"t\('([A-Za-z_][A-Za-z0-9_]*)'\)", TWIN_HTML))
    used |= set(re.findall(r'data-i18n="([A-Za-z_][A-Za-z0-9_]*)"', TWIN_HTML))
    missing_en = sorted(k for k in used if k not in en)
    missing_zh = sorted(k for k in used if k not in zh)
    assert not missing_en, f"missing in en: {missing_en}"
    assert not missing_zh, f"missing in zh: {missing_zh}"
    # 拆分红线：mema 侧 slimming 冲突警示与联查件不迁
    for gone in ("twinWarn", "twinWarnGo", "twinNoPkg", "twinNoPkgHint",
                 "twinMemoryMissing", "twinOpenMemoryPage", "twinMemLoadFail",
                 "backToConsole"):
        assert gone not in en and gone not in zh


def test_twin_html_boot_and_escape_discipline() -> None:
    assert "twinBoot" in TWIN_HTML
    assert "twinRoot" in TWIN_HTML
    assert "twinSetLang" in TWIN_HTML  # 页面自带语言切换
    # XSS 纪律（mema R2 P1-2 正则抽断言）：模板串里不允许裸标识符插值——所有
    # 动态数据（成员访问）必须 esc(...) 包裹；裸标识符只允许「预构建 HTML
    # 片段 / 静态白名单 / 数值参数」：
    #   hero/cards/more/stale/bar/rows/rowHtml/head/body/filterBar/pager/tagChips
    #   —— esc 构建的 HTML 片段变量
    #   s —— statusOpts 静态枚举循环变量；id —— Number(it.id) 数值参数
    #   val/extra —— radio helper 调用点静态字面量（'map'/'checked' 等）
    _SAFE_BARE = {"hero", "cards", "more", "stale", "bar", "rows", "rowHtml",
                  "head", "body", "filterBar", "pager", "tagChips", "s",
                  "id", "val", "extra"}
    boot = TWIN_HTML.split("/* ---- twin 独立页面入口", 1)[-1]
    bare = re.findall(r"\$\{([A-Za-z_$][A-Za-z0-9_$.]*)\}", boot)
    offenders = sorted(set(bare) - _SAFE_BARE)
    assert offenders == [], f"unescaped dynamic interpolations in TWIN_BOOT: {offenders}"
    # 动态数据确有 esc 包裹（抽查高频形态）
    assert "esc(String(it.raw_value||''))" in boot
    assert "esc(String(it.client||''))" in boot
    assert "esc(ref)" in boot


def test_twin_boot_called_functions_are_defined() -> None:
    """TWIN_BOOT 里调用的 twin* 函数必须全部有 function 定义（漏定义即页面卡
    Loading / 写操作后 ReferenceError，字符串存在性断言抓不到）。"""
    boot = TWIN_HTML.split("/* ---- twin 独立页面入口", 1)[-1]
    called = set(re.findall(r"\b(twin[A-Za-z0-9_]+)\s*\(", boot))
    defined = set(re.findall(r"\bfunction\s+(twin[A-Za-z0-9_]+)\s*\(", TWIN_HTML))
    missing = sorted(c for c in called if c not in defined)
    assert missing == [], f"TWIN_BOOT 调用了未定义的函数: {missing}"
    assert "twinLoadOverview" in defined  # 三处调用的具名钉


def test_twin_html_javascript_parses(tmp_path: Path) -> None:
    """TWIN_HTML script 必须过 node 语法门（&&与??混用之类语法错字符串断言
    抓不到；mema 批四实踩教训）。"""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        return
    start = TWIN_HTML.index("<script>") + len("<script>")
    end = TWIN_HTML.rindex("</script>")
    script = tmp_path / "twin_page.js"
    script.write_text(TWIN_HTML[start:end], encoding="utf-8")
    result = subprocess.run(
        [node, "--check", str(script)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"TWIN_HTML script has syntax errors:\n{result.stderr[:600]}"


def test_twin_page_body_scrolls() -> None:
    """b8af279 钉：twin 页是文档流长页，kb-body 的 100vh+hidden 必须被
    twin-body 覆盖——类型详情一屏放不下时整页可滚。"""
    assert '"kb-body twin-body"' in TWIN_HTML
    assert "body.twin-body { height: auto; overflow-y: auto; }" in TWIN_EXTRA_CSS


def test_persona_detail_view_wiring() -> None:
    """批四改向形态保留：版本卡点开类型详情（prompt_md 人话渲染 + 该类型
    证据 + 返回目录）。"""
    assert "function twinOpenPersona" in TWIN_HTML
    assert "function twinClosePersona" in TWIN_HTML
    assert "function twinPersonaView" in TWIN_HTML
    assert "/api/twin/persona" in TWIN_HTML
    assert "renderMd(String(p.prompt_md||''))" in TWIN_HTML  # 人话渲染
    assert "twinOpenPersona('" in TWIN_HTML  # 卡片 onclick
    assert "twinPersonaEvidence" in TWIN_HTML


def test_architecture_simplifications_in_html() -> None:
    """三处架构简化在前端的落点：无 locate、无 mema 联查/跳转、无配置面。"""
    assert "/api/twin/locate" not in TWIN_HTML
    assert "/api/memories/" not in TWIN_HTML
    assert "/#/memory/" not in TWIN_HTML
    assert "memory_missing" not in TWIN_HTML
    assert "backToConsole" not in TWIN_HTML
    assert "/api/settings" not in TWIN_HTML


def test_drawer_reads_local_content() -> None:
    """v0.5.0 证据抽屉：直读行内自带的 content/tags/source_ref（无懒加载、
    无跨库取正文）；撑宽修复保留（.twin-drawer-inner width:0/min-width:100%）。"""
    assert "function twinToggleEvDrawer" in TWIN_HTML
    assert "renderMd(String(it.content||''))" in TWIN_HTML
    assert "JSON.parse(String(it.tags||'[]'))" in TWIN_HTML
    assert "it.source_ref" in TWIN_HTML
    assert "/api/memories/" not in TWIN_HTML  # 无懒加载
    assert 'class="twin-drawer-inner"' in TWIN_HTML
    assert ".twin-drawer-inner { width: 0; min-width: 100%;" in TWIN_EXTRA_CSS


def test_overview_uses_type_labels_and_folds() -> None:
    """可用性批形态保留：版本卡人话 label（aud- 前缀回落 audience 表）、
    >6 折叠、时间戳短格式化、去 chars 技术指标。"""
    assert "function twinTypeLabel" in TWIN_HTML
    assert "ts.types.audience" in TWIN_HTML
    assert "c.slice(4)" in TWIN_HTML
    assert "expandWorkspaces" in TWIN_HTML
    assert "function twinFmtTs" in TWIN_HTML
    assert "size_chars" not in TWIN_HTML


def test_void_button_memory_id_number_normalized() -> None:
    """esc() 不挡 JS 上下文注入（不转义 ( ) ; /）——onclick 数值参数必须
    Number() 归一。"""
    assert 'onclick="twinVoidEvidence(${Number(it.memory_id)})' in TWIN_HTML
    assert 'onclick="twinVoidEvidence(${esc(String(it.memory_id))})' not in TWIN_HTML


def test_guides_render_local_error_states() -> None:
    """引导卡三态（无 locate 的替代）：多租户横幅 / 无账本 / 迁移横幅，
    数据全部来自 overview 响应。"""
    assert "multi_tenant_unsupported" in TWIN_HTML
    assert "twinMultiTenant" in TWIN_HTML
    assert "twin_db_not_found" in TWIN_HTML
    assert "t('twinImportHint').replace('{n}', String(imp.empty_content))" in TWIN_HTML
    assert "twinImportPending" in TWIN_HTML


def test_twin_scope_shares_kb_design_tokens() -> None:
    """可用性批二钉：--k-* 设计变量只定义在 .kb-scope 选择器下——twin 页根
    容器 .twin-scope 必须并入同一变量块，否则 border/surface/text 色全部
    静默失效（页面半残不报错）。"""
    assert ".kb-scope, .twin-scope {" in STYLE
    idx = STYLE.index(".kb-scope, .twin-scope {")
    assert "--k-border:" in STYLE[idx:idx + 1200]


def test_terminology_is_plain_language() -> None:
    """可用性批二钉：tab 名与动作走人话，内部枚举经 twinPendStatusLabel
    映射，每个 tab 顶部一句话说明。"""

    def _i18n_values(lang: str) -> dict[str, str]:
        m = re.search(rf"{lang}: \{{(.*?)\}}\s*(?:,\s*zh: |$)", I18N_JS, re.S)
        assert m
        return dict(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*'((?:[^'\\]|\\.)*)'", m.group(1)))

    en = _i18n_values("en")
    zh = _i18n_values("zh")
    assert en["twinPendingTab"] == "New values" and zh["twinPendingTab"] == "新值归类"
    assert en["twinAuditTab"] == "Activity" and zh["twinAuditTab"] == "操作记录"
    assert zh["twinResolve"] == "归类"
    assert zh["twinWorkType"] == "工作类型" and en["twinWorkType"] == "work type"
    for key in ("twinEvidenceHint", "twinPendingHint", "twinAuditHint",
                "twinPendPending", "twinPendMapped", "twinPendCanonicalized", "twinPendRejected"):
        assert key in en and key in zh
    assert "function twinPendStatusLabel" in TWIN_HTML
    assert "twinTabHint('twinPendingHint')" in TWIN_HTML
    assert "function twinKindLabel" in TWIN_HTML


# ---- CLI ----------------------------------------------------------------------


def test_cli_rejects_non_loopback_host(capsys: pytest.CaptureFixture) -> None:
    from mema_twin.console_cli import main

    rc = main(["--host", "0.0.0.0", "--no-open", "--json"])
    assert rc == 2
    assert "loopback" in capsys.readouterr().err


def test_cli_rejects_bad_port_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    from mema_twin.console_cli import main

    monkeypatch.delenv("MEMA_TWIN_CONSOLE_HOST", raising=False)
    monkeypatch.setenv("MEMA_TWIN_CONSOLE_PORT", "not-a-number")
    rc = main(["--no-open", "--json"])
    assert rc == 2
    assert "invalid port" in capsys.readouterr().err


def test_cli_loopback_env_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """env 形态 host 可调但仅限 loopback：非法值打回（返回码 2，不起服）。"""
    from mema_twin.console_cli import main

    monkeypatch.setenv("MEMA_TWIN_CONSOLE_HOST", "192.168.1.5")
    rc = main(["--no-open", "--json"])
    assert rc == 2
