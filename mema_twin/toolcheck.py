"""工具参数片段检测（v0.7.1 沉淀质量治理 W3，方案 §3 W3.1）。

compile 响应标注（tool_clause_flags / 素材包 <!-- tool-flag -->）与 submit
提醒（验证门 warnings：工具条款残留 / 工具条款提醒）共用本模块。设计约束
（开发方案 v1.3，两轮四路评审定稿）：

- 只豁免归因注释 ``<!-- src: … -->``（容忍空白/引号差异）；其余 HTML 注释
  照常扫描——persona 原样注入，整段剥注释等于开后门。
- 先命中先占位，全档按固定类序 url → inner → http → kv → bool →
  kebab_dot → camel：一个片段被先命中的类占位后其余类跳过（否则 bgColor
  被 kv+camel 双报、URL 里的 *.jd.com 被 url+inner 双报）。
- 词边界用环视（中文紧贴参数名时 \\b 不成立）。
- 行号按原文计（豁免注释时以等长空格占位，不移动行列）。
"""

import re

# 归因注释（豁免）：<!-- src: … --> 及空白/等号变体（内容可含 `>`，惰性到 -->）。
# 扫描前以等长空白占位（保留换行，不移动行列）。
_SRC_COMMENT_RE = re.compile(r"<!--\s*src\b(?:(?!-->).)*?-->", re.S)

# 检测类（顺序即占位优先级）。(_CATEGORY_ORDER)
_KV_RE = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]*\s*[:=]\s*"
    r"(?:\"|'|[{\[]|\d+|#[0-9a-fA-F]{3,8})")
_BOOL_RE = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]*\s*[:=]\s*"
    r"(?:true|false|null|True|False|None)\b")
_CAMEL_RE = re.compile(r"(?<![A-Za-z0-9_])[a-z]+[A-Z][A-Za-z]*")
_KEBAB_DOT_RE = re.compile(r"(?<![A-Za-z0-9_])[a-z]+-[a-z]+\.[A-Za-z]+")
_HTTP_RE = re.compile(r"\b(?:GET|POST|PUT|DELETE|PATCH)\b\s*[`/\[{]")
_URL_RE = re.compile(r"https?://[^\s`）)<>]+")
_INNER_RE = re.compile(
    r"[a-z0-9-]+\.jd\.com|apijoyspace|\b\d{1,3}(?:\.\d{1,3}){3}\b|localhost:\d+")

_DATE_VALUE_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?![T ]\d)")   # 带时刻的时间戳是参数值，不豁免
_QTY_VALUE_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:条|字|个|%)")
# kv 数字值分支吃完整数字（\d+）后，日期/数量的剩余尾部从 match 末尾接续判定：
# deadline=2026-12-01 → match 吞到 2026，rest = "-12-01"；count= 3 条 → rest = "条"
_DATE_TAIL_RE = re.compile(r"(?:-\d{2}){2}(?![T\d])")
_QTY_TAIL_RE = re.compile(r"\s*(?:条|字|个|%)")
_YEAR_TAIL_RE = re.compile(r"(?:\d{1,4})?年")   # 中文日期值（2026年12月）豁免；\d+ 已吃掉年份数字时 rest 以「年」开头
_DOC_EXT_RE = re.compile(
    r"\.(?:md|drawio|png|gif|jpe?g|pptx?|docx?|xlsx?|pdf|csv|json|py|sh|ya?ml|toml|html?|css|js|ts)$",
    re.I)
_HTTP_PAIR_TAIL_RE = re.compile(
    r"\s*(?:GET|POST|PUT|DELETE|PATCH)\b")

# 小写开头驼峰的已知非参数词（开放集合；未收录品牌仍会误报，已知边界）
_CAMEL_ALLOW = frozenset({
    "iOS", "iPhone", "iPad", "iPadOS", "macOS", "watchOS", "tvOS",
    "eBay", "iCloud",
})

_SNIPPET_MAX = 40


def _kv_value_allowed(masked: str, end: int) -> bool:
    """kv 值过滤：日期与「条/字/个/%」数量不算参数赋值（方案 W3.1）。
    完整形态（引号场景 rest 从值首起）与数字尾形态（rest 从数字后起）都判。"""
    rest = masked[end:].lstrip()
    if not rest:
        return True
    if _DATE_VALUE_RE.match(rest) or _QTY_VALUE_RE.match(rest):
        return False
    if _DATE_TAIL_RE.match(rest) or _QTY_TAIL_RE.match(rest):
        return False
    if _YEAR_TAIL_RE.match(rest):
        return False
    return True


def scan(text: str) -> list[dict]:
    """扫描文本中的工具参数片段（硬档）与链接/内部域名（软档）。

    返回按 (line, column) 排序的
    ``[{level, kind, snippet, line}]``；snippet 截断到 40 字符。
    """
    if not text:
        return []
    if not isinstance(text, str):
        return []
    # 归因注释以等长空白占位：保留换行（多行注释不并行），行列不变
    masked = _SRC_COMMENT_RE.sub(
        lambda m: "".join(c if c == "\n" else " " for c in m.group(0)), text)
    line_starts = [0] + [m.end() for m in re.finditer("\n", masked)]

    def _line_of(pos: int) -> int:
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    claimed: list[tuple[int, int]] = []
    seen_line_kind: set[tuple[int, str]] = set()
    hits: list[dict] = []

    ordered = (
        ("url", "soft", _URL_RE),
        ("inner", "soft", _INNER_RE),
        ("http", "hard", _HTTP_RE),
        ("kv", "hard", _KV_RE),
        ("bool", "hard", _BOOL_RE),
        ("kebab_dot", "hard", _KEBAB_DOT_RE),
        ("camel", "hard", _CAMEL_RE),
    )
    for kind, level, rx in ordered:
        for m in rx.finditer(masked):
            s, e = m.span()
            snippet_raw = m.group(0)
            if kind == "camel" and snippet_raw in _CAMEL_ALLOW:
                continue
            if kind == "kebab_dot" and _DOC_EXT_RE.search(snippet_raw):
                continue
            if kind == "http" and _HTTP_PAIR_TAIL_RE.match(masked[e:]):
                continue  # GET/POST 并写
            if kind == "kv" and not _kv_value_allowed(masked, e):
                continue
            if any(s < ce and cs < e for cs, ce in claimed):
                continue  # 已被先命中的类占位
            line = _line_of(s)
            key = (line, kind)
            if key in seen_line_kind:
                continue  # 每行每类只报第一个
            seen_line_kind.add(key)
            claimed.append((s, e))
            hits.append({
                "level": level,
                "kind": kind,
                "snippet": snippet_raw[:_SNIPPET_MAX],
                "line": line,
                "start": s,
            })
    hits.sort(key=lambda h: (h["line"], h["start"]))
    return hits
