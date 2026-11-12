"""本地 console 的 HTTP 服务：门卫四件套 + /twin 页面 + /api/twin/* 路由。

自 mema 仓 memory_arbiter/console_server.py 裁剪迁入（拆分方案 §3.6）：
**门卫四件套整体复制，一条不减**（写面直调含 void 等不可逆动作，无门卫=
本机任意网页可 CSRF/DRB）：

1. Host 白名单门（只认 loopback + 本机端口）——唯一的 DNS-rebinding 防线，
   必须在 body 解析之前；
2. POST Content-Type 必须 application/json → 415；
3. POST 强制 ``X-Console-Request: 1`` 头 → 403（浏览器跨站简单请求带不动
   自定义头）；
4. OPTIONS 永不发 Access-Control-* 头（preflight 永远失败）——**这是设计
   而非缺失**，注释钉死，永不"修复"。

另有 2MB body 帽、Content-Length 缺失拒收（stdlib 不解码 chunked）、
RecursionError 归一 400、500 必落 stderr 栈（review S4）。

写面（拆分红利）：不再 spawn stdio MCP 子进程，进程内直调
``server._twin_impl``——审计落账/身份校验/异常边界全走 twin 现有链路；
data.client 统一注入 "console"（twin_audit 归因，缺了 client 记空串）。
多租户（USERS_DIR）形态：仅 default 形态支持 console，db.db_path() 抛
TenantError → API 返回 {"ok":false,"error":"multi_tenant_unsupported"}
（页面顶部渲染友好横幅）。
"""
from __future__ import annotations

import json
import sys
import threading
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from . import db as twin_db
from . import tenant
from .console_static import TWIN_HTML
from .console_view import TwinStore
from .server import _twin_impl

# 写面模块级锁：mema 侧 TWIN_RUNNER_LOCK 的进程内等价物——并发 void/resolve
# 串行进 _twin_impl，双击/多标签页不会互相顶出 database is locked 的 503。
_TWIN_WRITE_LOCK = threading.Lock()


class ConsoleRequestHandler(BaseHTTPRequestHandler):
    server_version = "mema-twin-console/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
            "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; "
            "form-action 'self'",
        )
        super().end_headers()

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook name
        self._handle(send_body=True)

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib hook name
        self._handle(send_body=False)

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib hook name
        address = self.server.server_address
        port = int(address[1]) if isinstance(address, tuple) and len(address) > 1 else 0
        if not _host_allowed(self.headers.get("Host"), port):
            self._json({"error": "forbidden host"}, status=HTTPStatus.FORBIDDEN, send_body=False)
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        # mema R2 P1-1 pin（原样迁移）：这个 204 故意不带任何 Access-Control-*
        # 头——它就是 POST 面的 CSRF 防线（preflight 永远失败）。永不加 CORS 头。
        self.send_header("Allow", "GET, HEAD, POST, OPTIONS")
        self.end_headers()

    _MAX_POST_BODY = 2 * 1024 * 1024

    def do_POST(self) -> None:  # noqa: N802 - stdlib hook name
        address = self.server.server_address
        port = int(address[1]) if isinstance(address, tuple) and len(address) > 1 else 0
        # 顺序有讲究（mema plan §B.3.2）：Host 门在 body 解析之前——它是唯一
        # 的 DNS-rebinding 防线。
        if not _host_allowed(self.headers.get("Host"), port):
            self._json({"error": "forbidden host"}, status=HTTPStatus.FORBIDDEN)
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            self._json({"error": "content-type must be application/json"}, status=HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        if self.headers.get("X-Console-Request") != "1":
            self._json({"error": "missing X-Console-Request header"}, status=HTTPStatus.FORBIDDEN)
            return
        length_header = self.headers.get("Content-Length")
        try:
            length = int(length_header) if length_header else -1
        except ValueError:
            length = -1
        if length < 0:
            # chunked / 缺长度：stdlib http.server 不解码 chunked body——拒收
            # 而不是错解析。
            self._json({"error": "content-length required"}, status=HTTPStatus.LENGTH_REQUIRED)
            return
        if length > self._MAX_POST_BODY:
            self._json({"error": "body too large"}, status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
            return
        except RecursionError:
            # 深嵌套 JSON 会让 C 解析器抛 RecursionError（不是 ValueError）——
            # 答 400 而不是让 handler 线程死在请求中途（mema 对抗 review P2-1）。
            self._json({"error": "json body nesting too deep"}, status=HTTPStatus.BAD_REQUEST)
            return
        if not isinstance(body, dict):
            self._json({"error": "json body must be an object"}, status=HTTPStatus.BAD_REQUEST)
            return
        try:
            self._route_post(urlparse(self.path).path, body)
        except ValueError as exc:
            # 入参归一抛的 ValueError → 400 invalid_input（不穿透成 500）
            self._json({"ok": False, "error": "invalid_input", "reason": str(exc)},
                       status=HTTPStatus.BAD_REQUEST)
        except Exception:
            # mema review S4：500 必须在 stderr 留栈——本地工具的 stderr 就是
            # 操作者自己的终端。self.path 用 !r 打印：RAW path 可经 ?query 携带
            # 终端转义序列（mema 对抗 review round 2），绝不能裸进终端。
            traceback.print_exc(file=sys.stderr)
            print(f"[mema-twin-console] POST {self.path!r} failed", file=sys.stderr)
            self._json({"error": "internal server error"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _route_post(self, path: str, body: dict[str, Any]) -> None:
        api = TwinConsoleAPI()
        if path == "/api/twin/overview":
            self._json_or_error(api.twin_overview())
            return
        if path == "/api/twin/evidence":
            self._json_or_error(api.twin_evidence(
                status=_optional_str(body.get("status")),
                work_type=_optional_str(body.get("work_type")),
                limit=body.get("limit"),
                offset=body.get("offset"),
            ))
            return
        if path == "/api/twin/pending":
            self._json_or_error(api.twin_pending(
                status=str(body.get("status") or "pending"),
                limit=body.get("limit"),
            ))
            return
        if path == "/api/twin/types":
            self._json_or_error(api.twin_types(type_kind=str(body.get("type_kind") or "")))
            return
        if path == "/api/twin/audit":
            self._json_or_error(api.twin_audit(limit=body.get("limit")))
            return
        if path == "/api/twin/persona":
            self._json_or_error(api.twin_persona(work_type=str(body.get("work_type") or "")))
            return
        if path == "/api/twin/void":
            # memory_id raw 下传：api 层 int 归一（mema knowledge_edit 模式）
            self._json_or_error(api.twin_void(body.get("memory_id")))
            return
        if path == "/api/twin/resolve":
            self._json_or_error(api.twin_resolve(
                pending_id=body.get("pending_id"),
                decision=body.get("decision"),
                code=body.get("code"),
                new_type=body.get("new_type"),
            ))
            return
        self._json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def _handle(self, send_body: bool) -> None:
        address = self.server.server_address
        port = int(address[1]) if isinstance(address, tuple) and len(address) > 1 else 0
        if not _host_allowed(self.headers.get("Host"), port):
            self._json({"error": "forbidden host"}, status=HTTPStatus.FORBIDDEN, send_body=send_body)
            return
        path = urlparse(self.path).path
        try:
            if path in {"/", "/index.html", "/twin", "/twin/"}:
                # 唯一页面：twin 分身画像治理页。/ 与 /twin 同页（独立 console
                # 无 mema 主页可回，/ 直接落 twin 页）。
                self._html(TWIN_HTML, send_body=send_body)
                return
            if path == "/api/health":
                self._json({"ok": True, "service": "mema-twin-console"}, send_body=send_body)
                return
            self._json({"error": "not found"}, status=HTTPStatus.NOT_FOUND, send_body=send_body)
        except Exception:
            # mema review S4：同 do_POST——500 绝不静默；!r 防终端转义。
            traceback.print_exc(file=sys.stderr)
            print(f"[mema-twin-console] GET {self.path!r} failed", file=sys.stderr)
            self._json(
                {"error": "internal server error"},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
                send_body=send_body,
            )

    def _html(self, text: str, send_body: bool = True) -> None:
        body = text.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    def _json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK, send_body: bool = True) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    def _json_or_error(self, payload: dict[str, Any], send_body: bool = True) -> None:
        if "error" not in payload:
            self._json(payload, send_body=send_body)
            return
        status = payload.pop("_http_status", HTTPStatus.BAD_REQUEST)
        try:
            self._json(payload, status=HTTPStatus(status), send_body=send_body)
        except ValueError:
            self._json(payload, status=HTTPStatus.BAD_REQUEST, send_body=send_body)


def _host_allowed(host_header: str | None, bound_port: int) -> bool:
    if not host_header:
        return False
    host = host_header.strip().lower()
    allowed = {
        f"127.0.0.1:{bound_port}",
        f"localhost:{bound_port}",
        "127.0.0.1",
        "localhost",
        # IPv6 loopback（::1 直访时 Host 头可能是裸 ::1 或带端口）
        "::1",
        f"[::1]:{bound_port}",
        f"::1:{bound_port}",
    }
    return host in allowed


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _limit(value: Any, default: int, max_value: int = 100) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, max_value))


def _offset(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


class TwinConsoleAPI:
    """console API 适配层：读面 TwinStore + 写面 _twin_impl 直调。

    自 mema 仓 _console_api_knowledge.py 的 twin 段迁入改造。返回形状与 mema
    版尽量一致（前端少改）；twin_locate 不迁（探测概念随 spawn 退役）。
    """

    def _store(self) -> "tuple[TwinStore | None, dict[str, Any] | None]":
        """读面前置：形态门（多租户拒绝）+ 文件存在门。

        库路径直接用 db.db_path()（单点；default 形态 = MEMA_TWIN_DB_PATH 或
        包根 twin.sqlite3）。文件不存在 → twin_db_not_found（twin 的建库只
        发生在 db.connect()——console 只读面绝不静默造空库，写面前置门同理，
        mema R2 P0-1 语义保留）。
        """
        try:
            path = twin_db.db_path()
        except tenant.TenantError:
            return None, {
                "ok": False, "error": "multi_tenant_unsupported",
                "reason": "多租户（云端）形态不支持本地 console；本页面仅服务 default（本地）形态。",
            }
        if not path.is_file():
            return None, {"ok": False, "error": "twin_db_not_found", "path": str(path)}
        return TwinStore(str(path)), None

    def twin_overview(self) -> dict[str, Any]:
        store, error = self._store()
        if error is not None:
            return error
        assert store is not None
        return store.overview()

    def twin_evidence(
        self, status: str | None = None, work_type: str | None = None,
        limit: Any = 50, offset: Any = 0,
    ) -> dict[str, Any]:
        store, error = self._store()
        if error is not None:
            return error
        assert store is not None
        status_val = str(status or "").strip() or None
        work_type_val = str(work_type or "").strip() or None
        return store.evidence(
            status=status_val, work_type=work_type_val,
            limit=_limit(limit, 50, 200), offset=_offset(offset),
        )

    def twin_pending(self, status: str = "pending", limit: Any = 100) -> dict[str, Any]:
        store, error = self._store()
        if error is not None:
            return error
        assert store is not None
        return store.pending(status=str(status or "pending"), limit=_limit(limit, 100, 200))

    def twin_types(self, type_kind: str = "") -> dict[str, Any]:
        store, error = self._store()
        if error is not None:
            return error
        assert store is not None
        kind = str(type_kind or "").strip()
        if kind not in ("work_type", "audience", "purpose"):
            return {"error": "type_kind must be one of work_type|audience|purpose", "_http_status": 400}
        return store.types(kind)

    def twin_audit(self, limit: Any = 50) -> dict[str, Any]:
        store, error = self._store()
        if error is not None:
            return error
        assert store is not None
        return store.audit(limit=_limit(limit, 50, 200))

    def twin_persona(self, work_type: str = "") -> dict[str, Any]:
        store, error = self._store()
        if error is not None:
            return error
        assert store is not None
        wt = str(work_type or "").strip()
        if not wt:
            return {"error": "work_type is required", "_http_status": 400}
        return store.persona(wt)

    def _twin_write(self, action: str, data: dict[str, Any]) -> dict[str, Any]:
        """写面公共链路（进程内直调版）。

        前置门语义与 mema 版一致（R2 P0-1）：多租户形态/库文件不存在时打回，
        绝不借写操作静默造库。identity 契约：data.client="console"（合法字符
        集 [A-Za-z0-9._:@-]；twin_audit.client 记 "console"，归因面不丢）。
        _twin_impl 自带异常边界（ValueError→invalid_input 等）与审计落账，
        这里只做锁串行 + busy 归一 + HTTP 状态映射。
        """
        store, error = self._store()
        if error is not None:
            return {**error, "_http_status": 400}
        assert store is not None  # 库在读面门之后才可能被写——占位保持形状一致
        payload = dict(data or {})
        payload.setdefault("client", "console")
        with _TWIN_WRITE_LOCK:
            result = _twin_impl(action, payload)
        if not isinstance(result, dict):
            return {"ok": False, "error": "twin_process_error", "reason": "twin returned no object", "_http_status": 500}
        if not result.get("ok"):
            reason = str(result.get("reason") or "")
            # 锁竞争归一（mema R2 P1-6）：宿主 twin 正持 >5s 写事务（夜间编译/
            # submit 时段现实可撞）时 twin 回 internal_error+database is
            # locked——映射 503 twin_busy，不透传 internal_error 吓用户。
            if result.get("error") == "internal_error" and ("locked" in reason.lower() or "busy" in reason.lower()):
                return {"ok": False, "error": "twin_busy", "reason": "宿主 twin 写入中，请稍后重试", "_http_status": 503}
            return {**result, "_http_status": 400}
        return result

    def twin_void(self, memory_id: Any) -> dict[str, Any]:
        try:
            mid = int(memory_id)
        except (TypeError, ValueError):
            return {"error": "memory_id must be an integer", "_http_status": 400}
        return self._twin_write("void", {"memory_id": mid})

    def twin_resolve(
        self, pending_id: Any, decision: Any,
        code: Any = None, new_type: Any = None,
    ) -> dict[str, Any]:
        try:
            pid = int(pending_id)
        except (TypeError, ValueError):
            return {"error": "pending_id must be an integer", "_http_status": 400}
        decision_val = str(decision or "").strip()
        if decision_val not in ("map", "canonicalize", "reject"):
            return {"error": "decision must be one of map|canonicalize|reject", "_http_status": 400}
        data: dict[str, Any] = {"pending_id": pid, "decision": decision_val}
        if decision_val == "map":
            data["code"] = str(code or "").strip()
        elif decision_val == "canonicalize":
            if not isinstance(new_type, dict):
                return {"error": "canonicalize requires new_type {code,zh,en,domain}", "_http_status": 400}
            data["new_type"] = {
                key: str(new_type.get(key) or "").strip() for key in ("code", "zh", "en", "domain")
            }
        return self._twin_write("resolve", data)


def build_http_server(host: str, port: int) -> ThreadingHTTPServer:
    import socket

    from .identity import is_loopback_host

    if not is_loopback_host(host):
        raise ValueError(
            f"host={host!r} 非 loopback 地址被拒绝：console 含 void 等不可逆写操作，"
            "仅限本机使用（X-Console-Request/Host 门防 CSRF/DRB，防不了局域网直连）。")

    class Handler(ConsoleRequestHandler):
        pass

    if ":" in host:
        # IPv6 loopback（::1）：默认 AF_INET 绑不了 v6 地址。
        class Server6(ThreadingHTTPServer):
            address_family = socket.AF_INET6

        return Server6((host, int(port)), Handler)
    return ThreadingHTTPServer((host, int(port)), Handler)
