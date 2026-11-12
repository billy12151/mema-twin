"""本地 console /twin 页的样式。

自 mema 仓 console_style.py 迁入（拆分方案 §3.6），三段结构：
- STYLE：mema 共享样式里 **twin 页实际引用的最小子集**（:root 设计变量、
  基础排版、card/pill/mini/empty/callout/md-body/minibtn、
  ``.kb-scope, .twin-scope`` --k-* 变量块、body 点阵纹理）——console 外壳
  （topbar/sidebar/hero/metric/表单/模态）与知识库三栏不迁；
- KB_EXTRA_CSS：仅 twin 页用到的 kb 骨架段（kb-body/kb-page/kb-nav），
  知识库专属（.kb-back 返回链接等）不迁；
- TWIN_EXTRA_CSS：b8af279 终态整块平移（hero/版本卡/表格/抽屉/归类表单，
  含「页面超高可滚」修复与「抽屉不撑宽表格」修复）。

红线（mema 可用性批二教训）：--k-* 设计变量只定义在 ``.kb-scope, .twin-scope``
选择器下——twin 页根容器 .twin-scope 必须与 .kb-scope 并入同一变量块，
否则 border/surface/text 色全部静默失效（页面半残不报错）。
"""

STYLE = """
    :root {
      /* Console：中性石板灰 + 单点强调色，安静、专业、工具感。 */
      --c-bg: #f1f2f5;
      --c-surface: #fafbfc;
      --c-surface-2: #f3f4f6;
      --c-border: #dde1e6;
      --c-border-strong: #c6ccd4;
      --c-text: #262b34;
      --c-text-2: #525a68;
      --c-text-3: #737d8a;
      --c-accent: #2f6bff;
      --c-accent-soft: #eaf0ff;
      --c-ok: #0f9960; --c-ok-soft: #e6f5ee;
      --c-warn: #b8760a; --c-warn-soft: #fcf3e3;
      --c-err: #cf3f3c; --c-err-soft: #fcecec;
      --c-violet: #6d5ddb; --c-violet-soft: #efecfd;
      --c-radius: 10px; --c-radius-sm: 7px;
      --font: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
              "Hiragino Sans GB", "Microsoft YaHei", "Source Han Sans SC", sans-serif;
      --mono: "SF Mono", "JetBrains Mono", ui-monospace, Menlo, Monaco, Consolas, monospace;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; color: var(--c-text); background-color: var(--c-bg);
      font-family: var(--font); font-size: 14px; line-height: 1.6;
      -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility;
      min-height: 100vh; overflow-x: hidden;
    }
    /* 全局点阵 + 极慢色雾：可感知但不抢注意力（mema console 同款底纹） */
    body::before {
      content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 0;
      background-image: radial-gradient(rgba(16,24,40,.085) 1px, transparent 1px);
      background-size: 18px 18px;
    }
    body::after {
      content: ""; position: fixed; inset: -18%; pointer-events: none; z-index: 0;
      background:
        radial-gradient(34% 30% at 20% 20%, rgba(47,107,255,.20), transparent 62%),
        radial-gradient(30% 26% at 80% 32%, rgba(34,211,238,.17), transparent 62%),
        radial-gradient(36% 32% at 58% 86%, rgba(109,93,219,.14), transparent 64%);
      animation: cMist 30s ease-in-out infinite alternate;
      will-change: transform;
    }
    @keyframes cMist {
      0%   { transform: translate3d(0, 0, 0) scale(1); }
      50%  { transform: translate3d(2.6%, -1.8%, 0) scale(1.05); }
      100% { transform: translate3d(-2.2%, 2.4%, 0) scale(1.025); }
    }
    @media (prefers-reduced-motion: reduce) {
      body::after { animation: none !important; }
    }
    .num, code, .mono { font-variant-numeric: tabular-nums; }
    code, .mono { font-family: var(--mono); }
    code { background: var(--c-surface-2); padding: 2px 6px; border-radius: 5px; color: #3a455c; overflow-wrap: anywhere; font-size: 12.5px; }

    .pill, .mini, .id-badge {
      display: inline-flex; align-items: center; gap: 6px; border-radius: 6px; white-space: nowrap;
    }
    .pill {
      padding: 5px 10px; border: 1px solid var(--c-border); background: var(--c-surface);
      color: var(--c-text-2); font-size: 12px; font-weight: 600; font-variant-numeric: tabular-nums;
    }
    .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--c-ok); box-shadow: 0 0 0 4px rgba(15,153,96,.12); flex: 0 0 auto; }
    .lang-toggle { border: 1px solid var(--c-border); background: var(--c-surface-2); border-radius: 7px; padding: 2px; display: flex; gap: 2px; }
    .lang-toggle button { border: 0; background: transparent; border-radius: 5px; padding: 4px 9px; font-weight: 700; color: var(--c-text-3); cursor: pointer; font-size: 12px; font-family: inherit; }
    .lang-toggle button.active { background: var(--c-surface); color: var(--c-accent); box-shadow: 0 1px 2px rgba(16,24,40,.06); }

    /* ---- 卡片 ---- */
    .card {
      border-radius: var(--c-radius); background: var(--c-surface);
      border: 1px solid var(--c-border); box-shadow: 0 1px 2px rgba(16,24,40,.04); overflow: hidden;
    }
    .card-head { padding: 14px 16px 0; display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
    .card h3 { margin: 0; font-size: 14px; letter-spacing: -.01em; font-weight: 700; }
    .card .desc { color: var(--c-text-3); margin: 4px 0 0; font-size: 12.5px; line-height: 1.55; }
    .card-body { padding: 14px 16px 16px; }

    .id-badge { font-size: 11.5px; color: var(--c-text-2); background: var(--c-surface); border: 1px solid var(--c-border); padding: 4px 8px; font-weight: 700; font-variant-numeric: tabular-nums; }
    .mini { padding: 4px 8px; font-size: 11.5px; background: var(--c-surface); color: var(--c-text-2); font-weight: 700; border: 1px solid var(--c-border); border-radius: 5px; }
    .empty { color: var(--c-text-2); padding: 16px; border-radius: var(--c-radius-sm); background: var(--c-surface-2); border: 1px dashed var(--c-border-strong); font-size: 13px; }

    /* ---- 正文渲染 ---- */
    .md-body { white-space: normal; }
    .md-body h1 { font-size: 18px; margin: 16px 0 8px; letter-spacing: -.02em; color: var(--c-text); }
    .md-body h2 { font-size: 16px; margin: 15px 0 7px; color: var(--c-text); }
    .md-body h3 { font-size: 14.5px; margin: 13px 0 6px; color: var(--c-text); }
    .md-body h4 { font-size: 13.5px; margin: 12px 0 6px; color: var(--c-text); }
    .md-body p { margin: 8px 0; }
    .md-body ul, .md-body ol { margin: 8px 0; padding-left: 22px; }
    .md-body li { margin: 4px 0; }
    .md-body blockquote { margin: 10px 0; padding: 8px 14px; border-left: 3px solid var(--c-accent); background: var(--c-accent-soft); border-radius: 0 7px 7px 0; color: var(--c-text-2); }
    .md-body pre { margin: 10px 0; padding: 12px 14px; background: #1f2733; color: #dfe5ee; border-radius: var(--c-radius-sm); overflow: auto; font-size: 12.5px; }
    .md-body pre code { background: transparent; color: inherit; padding: 0; }
    .md-body table { border-collapse: collapse; margin: 10px 0; width: 100%; font-size: 12.5px; }
    .md-body th, .md-body td { border: 1px solid var(--c-border); padding: 7px 10px; text-align: left; }
    .md-body th { background: var(--c-surface-2); font-weight: 700; color: var(--c-text); }
    .md-body hr { border: 0; border-top: 1px solid var(--c-border); margin: 14px 0; }
    mark { background: rgba(184,118,10,.18); color: #8a560f; padding: 0 3px; border-radius: 4px; }

    .callout {
      border-radius: var(--c-radius-sm); padding: 13px 14px; margin-bottom: 12px;
      background: var(--c-warn-soft); border: 1px solid rgba(184,118,10,.18);
      display: flex; gap: 11px; align-items: flex-start;
    }
    .callout-icon {
      width: 26px; height: 26px; border-radius: var(--c-radius-sm); display: grid; place-items: center;
      background: var(--c-warn); color: #fff; font-weight: 800; flex: 0 0 auto; font-size: 13px;
    }
    .callout strong { display: block; font-size: 13px; margin-bottom: 4px; }
    .callout p { margin: 0; color: var(--c-text-2); font-size: 12.5px; line-height: 1.6; }

    .support-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 13px; align-items: center; }
    .minibtn { font-size: 12.5px; padding: 6px 11px; border-radius: 5px; border: 1px solid var(--c-border-2); background: transparent; color: var(--c-text-2); cursor: pointer; font-family: inherit; }
    .minibtn:hover { border-color: var(--c-accent); color: var(--c-accent); }
    .minibtn.solid { background: var(--c-accent); border-color: var(--c-accent); color: #fff; font-weight: 700; }

    /* ============ 纸白知识库设计令牌（.kb-scope / .twin-scope 作用域） ============ */
    /* 暖调纸白（v3）：① 发光屏上纯白底+高对比=眩光，用带暖调低饱和纸白模拟纸张；
       ② 文字不用纯黑（最强对比致疲劳），用深灰褐；③ 对比 7:1~13:1；
       ④ 强调色赭石（蓝在暖纸白上对比生硬显脏）；⑤ 纸纹纤维几乎不可见。 */
    .kb-scope, .twin-scope {
      --k-bg: #f5f3ee; --k-surface: #fffdf9; --k-surface-2: #eeeae1;
      --k-border: #cfc7b7; --k-border-2: #bdb5a4;
      --k-text: #2c2a26; --k-text-2: #4f4b43; --k-text-3: #665f54;
      --k-accent: #a8551f; --k-accent-2: #2e7d6b; --k-accent-glow: rgba(168,85,31,.10);
      /* 选中态专用深赭石：#a8551f 在自身 14% 底色上只有 4.23:1，提深到 #8f4416 后 4.87:1 达 AA */
      --k-accent-deep: #8f4416;
      --k-accent-soft: #f0e4d8;
      --k-ok: #1f7a4d; --k-ok-soft: #e2f0e8;
      --k-warn: #9a6209; --k-warn-soft: #f6ecd8;
      --k-err: #b8322f; --k-err-soft: #f7e3e1;
      --k-violet: #5b45b8; --k-violet-soft: #eae5f8;
      --k-mono: "SF Mono", "JetBrains Mono", ui-monospace, Menlo, Consolas, monospace;
    }
    /* twin 页加载骨架占位（mema 的 .kb-content 三栏容器不迁，只留内边距语义） */
    .kb-content { display: flex; flex-direction: column; min-width: 0; padding: 18px 26px 0; }
"""

KB_EXTRA_CSS = """
    /* twin 独立页面：整页铺满。骨架段自 mema KB_EXTRA_CSS 裁剪（仅 twin 页
       引用的选择器；.kb-back 返回链接随「返回 Console」一并退役）。 */
    body.kb-body { background-color: var(--k-bg); }
    .kb-page { width: 100%; min-height: 100vh; display: flex; flex-direction: column; }
    #twinRoot { flex: 1; min-height: 0; display: flex; flex-direction: column; }
    /* 独立页极简顶栏：标题 + 仅本地徽章 + 语言切换 */
    .kb-nav {
      display: flex; align-items: center; gap: 12px; flex: 0 0 auto;
      padding: 0 18px; height: 44px;
      background: var(--k-surface); border-bottom: 1px solid var(--k-border);
      color: var(--k-text-2); font-size: 12.5px;
    }
    .kb-nav .spacer { flex: 1; }
    .kb-nav-title { color: var(--k-text); font-weight: 700; font-size: 13px; }
    .kb-nav .pill { padding: 5px 10px; font-size: 12px; }
    .kb-nav .lang-toggle { border: 1px solid var(--k-border-2); background: var(--k-surface); }
    .kb-nav .lang-toggle button { color: var(--k-text-3); }
    .kb-nav .lang-toggle button.active { background: var(--k-surface-2); color: var(--k-accent); }
    @media (max-width: 780px) {
      .kb-nav { flex-wrap: wrap; height: auto; padding: 8px 12px; }
      .kb-nav .pill { display: none; }
    }
"""

# /twin 分身画像治理页专属样式：b8af279 终态整块平移（hero 页头、版本卡
# 网格、表格密度/hover、胶囊等宽、作废按钮降权、页内抽屉、归类表单卡化）。
TWIN_EXTRA_CSS = """
    /* twin 页是文档流长页：解除 kb-body 的 100vh+hidden（知识库三栏内滚专用），
       本规则在 STYLE 之后拼接，同 specificity 后者胜。 */
    body.twin-body { height: auto; overflow-y: auto; }
    .twin-hero { display: flex; align-items: center; gap: 14px; padding: 4px 2px 14px;
      border-bottom: 1px solid var(--k-border); margin-bottom: 16px; }
    .twin-hero-mark { width: 42px; height: 42px; border-radius: 12px; flex: none;
      display: flex; align-items: center; justify-content: center;
      font-size: 20px; font-weight: 800; color: #fffdf9;
      background: linear-gradient(135deg, #7a5af8 0%, #5b8def 100%);
      box-shadow: 0 2px 10px rgba(122,90,248,.25); }
    .twin-hero-text h2 { margin: 0 0 3px; font-size: 17px; color: var(--k-text); }
    .twin-hero-text p { margin: 0; color: var(--k-text-3); font-size: 12.5px; max-width: 720px; }
    .twin-hero .spacer { flex: 1; }
    .twin-vc-row { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
      gap: 10px; margin-bottom: 10px; }
    .twin-vc { display: flex; flex-direction: column; align-items: stretch; gap: 5px;
      padding: 11px 13px; text-align: left; font: inherit; color: inherit;
      border: 1px solid var(--k-border); border-radius: 10px; background: var(--k-surface);
      transition: border-color .12s, box-shadow .12s; cursor: pointer; }
    .twin-vc:hover { border-color: rgba(122,90,248,.45); box-shadow: 0 1px 8px rgba(122,90,248,.08); }
    .twin-vc-top { display: flex; align-items: center; gap: 8px; }
    .twin-vc-top strong { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis;
      white-space: nowrap; font-size: 13px; color: var(--k-text); }
    .twin-vc small { color: var(--k-text-3); font-size: 11.5px; }
    .twin-vc-code { font-size: 10.5px; color: var(--k-text-3); opacity: .75;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .twin-vc-more { margin: 2px 0 8px; }
    .twin-vc-open { font-size: 10.5px; color: var(--k-accent); opacity: .75; margin-top: 2px; }
    .twin-vc:hover .twin-vc-open { opacity: 1; }
    /* 类型详情视图（按产出规则展示，owner 2026-11-11 拍板） */
    .twin-persona-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }
    .twin-persona-head .spacer { flex: 1; }
    .twin-persona-head strong { font-size: 16px; color: var(--k-text); }
    .twin-persona-title { margin: 4px 0 10px; font-size: 13.5px; color: var(--k-text-2); }
    .twin-persona-sub { margin: 18px 0 8px; font-size: 12.5px; color: var(--k-text-3); }
    .twin-persona-md { padding: 16px 20px; border: 1px solid var(--k-border); border-radius: 10px;
      background: var(--k-surface); font-size: 13px; line-height: 1.75; color: var(--k-text-2);
      max-height: 640px; overflow-y: auto; }
    .twin-persona-md p { margin: 0 0 10px; }
    .twin-persona-md h1, .twin-persona-md h2, .twin-persona-md h3 { color: var(--k-text); margin: 14px 0 8px; }
    .twin-persona-md pre, .twin-persona-md code { max-width: 100%; overflow-x: auto; overflow-wrap: break-word; }
    .twin-stale { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin: 8px 0; }
    .twin-chip { display: inline-block; padding: 2px 8px; border-radius: 999px;
      border: 1px solid var(--k-border-2); background: var(--k-surface); font-size: 11.5px; color: var(--k-text-2); }
    .twin-chip.warn { border-color: rgba(243,166,52,.55); background: rgba(243,166,52,.12); color: var(--k-text); }
    .twin-hint { margin: 12px 0 0; padding: 9px 14px; border-radius: 8px;
      background: var(--k-surface); border: 1px solid var(--k-border);
      color: var(--k-text-3); font-size: 12.5px; line-height: 1.6; }
    .twin-tabs { display: flex; gap: 4px; margin: 16px 0 0; border-bottom: 1px solid var(--k-border); }
    .twin-tab { border: 1px solid transparent; border-bottom: 0; background: transparent;
      color: var(--k-text-3); font-size: 13px; font-weight: 600; padding: 9px 16px;
      border-radius: 10px 10px 0 0; cursor: pointer; position: relative; top: 1px; }
    .twin-tab:hover { color: var(--k-text-2); }
    .twin-tab.on { color: var(--k-accent); border-color: var(--k-border);
      background: var(--k-surface); }
    .twin-filterbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
      padding: 12px 0 10px; border-bottom: 1px solid var(--k-border); }
    .twin-filterbar label { display: flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--k-text-2); }
    .twin-filterbar select, .twin-filterbar input { padding: 5px 8px; border-radius: 6px;
      border: 1px solid var(--k-border-2); background: var(--k-surface); color: var(--k-text); font-size: 12.5px; }
    .twin-table-wrap { overflow-x: auto; border: 1px solid var(--k-border); border-radius: 10px;
      background: var(--k-surface); margin-top: 12px; }
    .twin-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
    .twin-table th { text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--k-border);
      color: var(--k-text-3); font-size: 11.5px; white-space: nowrap; letter-spacing: .02em; }
    .twin-table td { padding: 9px 12px; border-bottom: 1px solid var(--k-border);
      vertical-align: top; color: var(--k-text-2); }
    .twin-table tbody tr:hover > td { background: rgba(122,90,248,.045); }
    .twin-table tr:last-child td { border-bottom: 0; }
    .twin-subject { max-width: 340px; }
    .twin-sub { display: block; opacity: .6; font-size: 11px; margin-top: 2px; }
    .twin-ts { white-space: nowrap; font-size: 11.5px; opacity: .75; }
    .twin-err { max-width: 220px; overflow-wrap: anywhere; font-size: 11.5px; }
    .twin-void-row > td { opacity: .5; }
    .twin-retired-row > td { opacity: .8; }
    .twin-open-row > td { background: rgba(122,90,248,.06); }
    .twin-memlink { color: var(--k-accent); font-weight: 600; background: none; border: 0;
      padding: 0; cursor: pointer; font-size: inherit; }
    .twin-memlink:hover { text-decoration: underline; }
    .twin-missing { color: var(--k-text-3); }
    .twin-badge { display: inline-block; min-width: 92px; text-align: center; padding: 2px 8px;
      border-radius: 999px; font-size: 11px; font-weight: 600;
      border: 1px solid var(--k-border-2); color: var(--k-text-2); }
    .twin-badge.s-uncompiled { border-color: rgba(243,166,52,.5); color: #a06808; background: rgba(243,166,52,.09); }
    .twin-badge.s-compiled { border-color: rgba(74,222,159,.45); color: #1c7d4d; background: rgba(74,222,159,.08); }
    .twin-badge.s-void, .twin-badge.s-rejected { border-color: rgba(239,102,120,.45); color: #b42334; background: rgba(239,102,120,.07); }
    .twin-badge.s-mapped, .twin-badge.s-canonicalized { border-color: rgba(122,168,255,.45); color: #2a5aa8; background: rgba(122,168,255,.1); }
    .twin-void-btn { color: var(--k-text-3); border-color: transparent; background: transparent; }
    .twin-void-btn:hover:not(:disabled) { color: #b42334; border-color: rgba(239,102,120,.4); background: rgba(239,102,120,.07); }
    .twin-pager { display: flex; align-items: center; gap: 10px; margin-top: 12px; }
    .twin-drawer-row > td { background: rgba(122,90,248,.05); border-bottom: 1px solid var(--k-border); padding: 12px 16px 16px; }
    .twin-mem-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
    .twin-mem-head .spacer { flex: 1; }
    .twin-mem-head strong { font-size: 13.5px; color: var(--k-text); }
    .twin-page-link { color: var(--k-text-3); font-size: 12px; text-decoration: none;
      border: 1px solid var(--k-border-2); padding: 3px 9px; border-radius: 6px; }
    .twin-page-link:hover { color: var(--k-accent); border-color: var(--k-accent); }
    .twin-mem-content { max-height: 360px; overflow-y: auto; padding: 10px 14px;
      border: 1px solid var(--k-border); border-radius: 8px; background: var(--k-surface);
      font-size: 12.5px; line-height: 1.65; color: var(--k-text-2); }
    .twin-mem-content p { margin: 0 0 8px; }
    /* 可用性批三：抽屉内容不参与表格固有宽度（撑宽跳动根因） */
    .twin-drawer-inner { width: 0; min-width: 100%; overflow-wrap: anywhere; }
    .twin-mem-content pre, .twin-mem-content code { max-width: 100%;
      overflow-x: auto; overflow-wrap: break-word; }
    .twin-mem-content table { max-width: 100%; display: block; overflow-x: auto; }
    /* 归类表单：radio 卡片化（主行+后果说明），提交按钮实底 */
    .twin-radio-row { display: flex; gap: 14px; flex-wrap: wrap; align-items: stretch; }
    .twin-radio { display: flex; flex-direction: column; gap: 3px; flex: 1; min-width: 240px;
      padding: 8px 12px; border: 1px solid var(--k-border); border-radius: 8px;
      background: var(--k-surface); cursor: pointer; }
    .twin-radio:has(input:checked) { border-color: var(--k-accent);
      background: rgba(168,85,31,.05); }
    .twin-radio-main { display: flex; align-items: center; gap: 6px;
      font-size: 12.5px; font-weight: 600; color: var(--k-text); }
    .twin-radio-desc { font-size: 11.5px; color: var(--k-text-3); line-height: 1.55; }
    .twin-submit-btn { background: var(--k-accent); color: #fffdf9; border-color: var(--k-accent); }
    .twin-submit-btn:hover:not(:disabled) { background: var(--k-accent-deep); border-color: var(--k-accent-deep); }
    .twin-pend { border: 1px solid var(--k-border); border-radius: 10px; background: var(--k-surface);
      padding: 10px 14px; margin-bottom: 8px; transition: border-color .12s; }
    .twin-pend:hover { border-color: rgba(122,90,248,.35); }
    .twin-pend-head { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; font-size: 12.5px; }
    .twin-pend-head .spacer { flex: 1; }
    .twin-raw { font-size: 12px; color: var(--k-text); background: var(--k-surface-2, rgba(127,127,127,.08));
      padding: 2px 7px; border-radius: 5px; overflow-wrap: anywhere; }
    .twin-resolve { border-top: 1px dashed var(--k-border); margin-top: 8px; padding-top: 9px; }
    .twin-radio-row { margin-bottom: 8px; }
    .twin-resolve-body label { display: flex; align-items: center; gap: 7px; font-size: 12.5px;
      color: var(--k-text-2); margin-bottom: 6px; }
    .twin-resolve-body select, .twin-resolve-body input { padding: 5px 8px; border-radius: 6px;
      border: 1px solid var(--k-border-2); background: var(--k-surface); color: var(--k-text); font-size: 12.5px; }
    .twin-newtype { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 8px; margin-bottom: 6px; }
"""
