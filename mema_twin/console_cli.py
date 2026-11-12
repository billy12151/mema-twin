"""`mema-twin console` 命令行入口：起本地 HTTP 服务并自动开浏览器。

形态（拆分方案 §3.6）：独立命令，默认 ``127.0.0.1:8766``（避开 8765 MCP 口
与 18876 mema console）；MEMA_TWIN_CONSOLE_HOST / MEMA_TWIN_CONSOLE_PORT
可调，但 host 硬性 loopback（复用 identity.is_loopback_host——console 含
void 等不可逆写操作，非 loopback 暴露=局域网任何人可触发）。自动开浏览器
失败不致命（无头环境常态）；Ctrl-C 干净退出。

多租户（MEMA_TWIN_USERS_DIR）形态不拒启动：服务照常起来、页面照常渲染，
由 API 层返回 multi_tenant_unsupported、页面顶部渲染友好横幅（仅 default
形态支持 console，方案拍板）——拒绝启动反而让云端用户看不到原因。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from typing import Sequence

from .console_http import build_http_server
from .identity import is_loopback_host

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8766


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mema-twin console",
        description="Start the local twin persona ledger console (分身画像治理页).",
    )
    parser.add_argument(
        "--host", default=None,
        help=f"Bind host, loopback only. Default: env MEMA_TWIN_CONSOLE_HOST or {_DEFAULT_HOST}")
    parser.add_argument(
        "--port", type=int, default=None,
        help=f"Bind port. Default: env MEMA_TWIN_CONSOLE_PORT or {_DEFAULT_PORT}")
    parser.add_argument("--no-open", action="store_true", help="Do not open a browser automatically")
    parser.add_argument("--json", action="store_true", help="Print startup line as JSON")
    args = parser.parse_args(list(argv or []))

    host = (args.host or os.environ.get("MEMA_TWIN_CONSOLE_HOST") or _DEFAULT_HOST).strip()
    try:
        port = int(args.port if args.port is not None
                   else os.environ.get("MEMA_TWIN_CONSOLE_PORT") or _DEFAULT_PORT)
    except ValueError:
        print(f"mema-twin console: invalid port"
              f" {os.environ.get('MEMA_TWIN_CONSOLE_PORT')!r}", file=sys.stderr)
        return 2
    if not is_loopback_host(host):
        print(
            f"mema-twin console: host={host!r} 非 loopback 地址被拒绝："
            "console 含 void 等不可逆写操作，仅限本机使用。",
            file=sys.stderr)
        return 2

    try:
        httpd = build_http_server(host, port)
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        else:
            print(f"mema-twin console: {exc}", file=sys.stderr)
        return 2

    bound_host = "127.0.0.1" if host == "localhost" else host
    url = f"http://{bound_host}:{httpd.server_address[1]}"
    if args.json:
        print(json.dumps({"ok": True, "url": url, "local_only": True}, ensure_ascii=False))
    else:
        print("mema-twin console — Twin · 分身画像")
        print(f"Listening on {url}")
        print("Local-only persona ledger console (gates: Host allowlist + X-Console-Request). Do not expose this port publicly.")
        print("Press Ctrl+C to stop.")
        try:
            sys.stdout.flush()
        except OSError:
            pass
    if not args.no_open:
        try:
            # 无头环境（ssh/CI）webbrowser.open 返回 False 或静默失败——不致命。
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        # Ctrl-C 干净退出：不再向终端抛栈。
        print()
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
