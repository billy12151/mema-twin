"""本地 console /twin 页的 HTML 组装。

自 mema 仓 console_static.py 迁入（拆分方案 §3.6），裁剪为单页形态：
- 只保留 _doc() 组装器 + _TWIN_BODY + TWIN_HTML（console 外壳/知识库 body
  不迁）；favicon 沿用 mema 品牌徽章（圆角方底 + 白「迷」，twin 页紫色
  #7a5af8）；
- 「返回 Console」链接随 mema 主页一并退役（独立 console 的 / 即 twin 页）。
"""
from __future__ import annotations

from urllib.parse import quote

from .console_script import SHARED_SCRIPT, TWIN_BOOT
from .console_style import KB_EXTRA_CSS, STYLE, TWIN_EXTRA_CSS


def _badge_href(bg: str) -> str:
    """mema 品牌徽章（浏览器标签角标/favicon）：圆角方底 + 白「迷」。
    SVG text 依赖系统 CJK 字体（macOS PingFang / Windows 微软雅黑均有），
    两级 fallback + sans-serif。data URI 内联——单文件 HTML 架构不引外部
    资源；# 须 URL 编码为 %23。"""
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
        f"<rect width='64' height='64' rx='14' fill='{bg}'/>"
        "<text x='32' y='45' font-family='PingFang SC,Hiragino Sans GB,"
        "Microsoft YaHei,sans-serif' font-size='36' font-weight='700' "
        "fill='#fffdf9' text-anchor='middle'>&#36855;</text>"
        "</svg>"
    )
    return "data:image/svg+xml;charset=utf-8," + quote(svg, safe="")


_FAVICON_TWIN_HREF = _badge_href("#7a5af8")

# twin 分身画像治理页 body：独立整页，无 console 外壳；hero/引导卡/数据区块
# 由 TWIN_BOOT 渲进 #twinRoot。
_TWIN_BODY = """
  <main class="kb-page">
    <header class="kb-nav">
      <span class="kb-nav-title" data-i18n="twinTitle">Twin · 分身画像</span>
      <span class="spacer"></span>
      <span class="pill"><i class="dot"></i><span data-i18n="localOnly">仅本地</span></span>
      <div class="lang-toggle"><button id="langZh" type="button">中文</button><button id="langEn" type="button">English</button></div>
    </header>
    <div id="twinRoot" class="twin-scope" style="max-width:1080px;margin:0 auto;padding:18px 20px 40px;width:100%;"><div class="kb-content" style="padding:60px 24px;text-align:center;color:var(--k-text-3);">加载中…</div></div>
  </main>
"""

_HEAD = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <link rel="icon" type="image/svg+xml" href="__FAVICON_HREF__" />
"""


def _doc(title: str, body_class: str, body: str, script: str, favicon: str, extra_css: str = "") -> str:
    """组装一个完整 HTML 文档。"""
    return (
        _HEAD.replace("__FAVICON_HREF__", favicon)
        + "<title>" + title + "</title>"
        + "  <style>" + STYLE + KB_EXTRA_CSS + extra_css + "</style>"
        + "</head>"
        + '<body class="' + body_class + '">' + body + "<script>" + script + "</script>"
        + "</body></html>"
    )


# GET / 与 GET /twin 同页：独立 console 的唯一页面。读 twin 自有账本
# twin.sqlite3（写操作进程内直调 twin 动作，经门卫四件套防 CSRF/DRB）。
TWIN_HTML = _doc(
    "Twin · 分身画像 · mema-twin",
    # twin-body：覆盖 kb-body 的应用式固定高度（100vh+hidden 是知识库三栏
    # 内滚布局的约定；twin 页是普通文档流，类型详情一屏放不下就被裁掉
    # ——owner 2026-11-11「页面超高没有滚动条」，b8af279）。
    "kb-body twin-body",
    _TWIN_BODY,
    SHARED_SCRIPT + TWIN_BOOT,
    _FAVICON_TWIN_HREF,
    extra_css=TWIN_EXTRA_CSS,
)
