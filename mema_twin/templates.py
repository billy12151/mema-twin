"""编译素材包模板与写入引导文案。"""

STRONG_MODEL_NOTE = "编译质量取决于当前会话模型；建议在强模型会话中执行 compile/submit"

# 硬预算默认值（v0.3.7 拍板可调；v0.7.1 owner 拍板放宽 persona 70 条/10000 字符
# ——开发方案 D1a，产地=装下现状与待吸收；画像维持 4000/40 不放宽 D1b）：
# 超限必须合并或淘汰并写明依据；"条"= 顶层列表行。
# status 的体积可见面（size_chars/over_budget）与编译规则文案同用这套常量。
BUDGET_TYPE_CHARS = 10000
BUDGET_TYPE_RULES = 70
BUDGET_AUD_CHARS = 4000
BUDGET_AUD_RULES = 40

WRITE_GUIDANCE = (
    "twin.write 只收「规则」，不收事实、不收经验。按顺序问：\n"
    "- ① 换一个宿主、换一套工具，这句话还成立吗？出现参数名、接口路径、调用步骤、"
    "报错处理、工具选路，或者会随工具版本变的能力事实（某参数能不能用）→ 不成立 → "
    "不是规则，拆出去（去处见 ③）。例外：工具名只是在说产物的格式（交付 .drawio、"
    "交付 MD 稿）；或用户说过「以后动笔前都要这样做」——例外前提要从用户原话确认，"
    "确认不了就按本步正常拆。\n"
    "- ② 剩下的话不照做，用户会打回吗（产物不合格，含缺前置确认会返工）？不会，只是"
    "自己慢一点/绕一下/翻一次车 → 是执行经验，不要 write。会 → 再问一句：换一个需求、"
    "或换一个项目仍适用，且不是第 ④ 步列出的本需求、本项目内容（含项目特有的写法约定）"
    "吗？都是 → 是规则，写这里；有一项不满足 → 不要 write，去处见 ④。\n"
    "- ③ 拆出去的部分去哪：落在「为达成某目的用哪个工具或通道、失败后怎么降级、"
    "哪一步的做法值得沿用」→ 写进当前任务：step_update(done, reflection=…) 或 "
    "tool_log(task_id=…, note=…)，常规步骤不写（一个任务通常 0–2 条），夜间评估"
    "提炼进 playbook；落在「这个工具自身怎么用」（参数、字段、接口路径的用法与"
    "限制）→ 该工具的技能文档，不要 write 也不要进步骤记录。不在任务里时，"
    "执行经验暂无落点。\n"
    "- ④ 只对本需求、本项目成立的内容 → 不 write：业务口径、需求决议、字段/状态/"
    "权限模型、本需求的话术、项目特有的写法约定，进交付物或 memory.remember"
    "（动笔前按口径召回规则取回）。用户纠正的是「怎么写」（视角、结构、详略、格式、"
    "流程）才沉淀；纠正的是「这条需求写什么」不沉淀。只发生一次的事实（做了什么、"
    "何时交付）同样 → memory.remember。本步清单与 ② 的判断冲突时，以 ② 的可移植性"
    "判断为准。\n"
    "- 一条里混了几种 → 拆开，只有规则那半走 write。\n"
    "- 不限工种、对该受众都成立的口径类要求（详略/口吻/禁忌）→ 传 scope=\"audience\" "
    "写一条。格式与结构类不进受众画像：确实多个工种都需要时只能逐工种写，"
    "写之前确认这条对每个工种都适用。"
)


_TYPE_RULES = (
    "只吸收有证据支撑的偏好；仅出现一次的偏好须标注（单次观察）。",
    "按固定分区组织：整体风格 / 结构与格式 / 话术与用语 / 受众适配（按 audience 分条件段）"
    "/ 用途适配（按 purpose 分条件段）/ 前置确认清单（开工前应核对的材料与问题）。",
    "受众/用途某维度只有单一取值时，该维度写无条件规则，不制造单分支条件段。",
    "每条规则后用 `<!-- src: <memory_id> -->` 标注来源记忆 id，保证可溯源。",
    "与旧版本冲突的新证据以新证据为准。新旧证据表面相反时先判断关系：新证据只是"
    "分寸收窄或补了子场景特例 → 合成一条带适用条件的规则，不丢掉任何一方的约束；"
    "新证据否定旧证据 → 以新为准，并在「版本间变更」注明被取代的旧证据"
    "（如「取代 1247」）；两型之外回落以新为准。",
    "简练优先：规则以最小可执行表述为准——同类偏好合并为一条，示例只留最有代表性的"
    "一个，不重复分区标题已表达的信息，不写空洞要求（如『注意结构清晰』）。"
    "禁止为省字符牺牲可执行性：动作、条件、例外不得抽象掉。"
    "合并后某条证据完全被另一条覆盖的，在晨报「建议作废」列出被覆盖者。",
    "含义稳定，表达自由：证据未变动的规则含义必须不变（不得收紧、放宽、增删限制条件）；"
    "合并重叠条目、条件化改写、重组分区、优化措辞不受限——禁止的是无因语义变化，不是优化。",
    f"硬预算：正文 ≤{BUDGET_TYPE_CHARS} 字符或 ≤{BUDGET_TYPE_RULES} 条（顶层列表行）；"
    "超限必须合并或淘汰，淘汰须写明依据（哪条证据过时/被取代）。",
    "文末「版本间变更」必须分级：语义变更（新增/修改/淘汰/合并）逐条归因到证据 id 或"
    "预算要求（写不出归因即违规）；纯表达/结构优化标记即可。",
    "规则尽量写成条件化策略：注明适用条件与例外"
    "（例：信息缺失但可逆时自行假设并推进，不可逆才向用户确认）。",
    "用户明确厌恶的行为（如反复确认、给选项不给判断、暴露内部流程）"
    "编为高优先级禁止项，单独分区置顶。",
    "只为产物层规则立条。出现参数名、接口路径、调用步骤、报错处理、工具选路，"
    "或会随工具版本变的能力事实的内容，不为其立规则——工具名只是在说产物格式"
    "（如交付 .drawio）、或用户明确要求写进 persona 的「动笔前必做」步骤除外。"
    "旧版中的此类条款在新版剔除，并在「版本间变更」标注「转 playbook / 技能文档"
    "候选」附证据 id——本条是「含义稳定」与夜间保守封套的显式例外；但这些证据的 "
    "id 仍照抄进 source_memory_ids（列入表示已审阅，不是引用；漏列会让它们以增补"
    "形式永久注入）。证据里规则与上述内容混在一条时，只取规则那半。"
    "素材包证据行的 <!-- tool-flag --> 标注指出了工具参数片段与链接片段。参数片段"
    "不进 persona，证据里规则与参数混在一条时只取规则那半；链接片段只是提醒，"
    "不改变本条规则——用户要求的「动笔前必做」步骤与产物格式示例可保留链接。"
    "submit 会对产物正文做同样的检测并提醒。「版本间变更」提及被剔除的参数条款时"
    "只写证据 id 与类别词（如「工具参数」），不复述参数名——复述会被检测器命中。",
    "只为换一个需求、或换一个项目仍然适用、且非项目特有约定的产出物规则立条。"
    "证据里通用写法与本次业务口径（具体字段/状态/权限模型、本需求话术与决议）混在"
    "一条时，只取通用写法那半；通篇只有业务口径的证据不立条，id 仍照抄进 "
    "source_memory_ids，并在晨报「建议作废」列出。出现次数不是判据：只见过一次但"
    "明显通用的写法照常立条（标注单次观察）。按本条剔除已编译的旧条款属于允许的"
    "剔除动作，不受「无证据动机不重组」限制。",
    "输出纯 Markdown 正文，不要复述本素材包。",
)

_AUDIENCE_RULES = (
    "只保留对该受众稳定成立的口径类偏好（详略/口吻/禁忌/关注点/信息侧重）；"
    "格式与结构类不进画像（那属于各工作类型）。",
    "只为口径类条款立条。出现参数名、接口路径、调用步骤、报错处理、工具选路，"
    "或会随工具版本变的能力事实的内容，不进画像——归宿为步骤记录（夜间进 "
    "playbook）或该工具的技能文档；证据里口径与上述内容混在一条时，只取口径那半。"
    "素材包证据行的 <!-- tool-flag --> 标注片段不进画像；submit 会对产物正文做"
    "同样的检测并提醒。",
    "按固定分区组织：总体口径 / 详略与信息密度 / 话术与禁忌 / 关注点与偏好侧重；"
    "子受众差异写成条件段（例：若为技术评审场合则…）。",
    "每条规则后用 `<!-- src: <memory_id> -->` 标注来源记忆 id，保证可溯源。",
    "与旧版画像冲突的新证据以新证据为准。新旧证据表面相反时先判断关系：新证据只是"
    "分寸收窄或补了子场景特例 → 合成一条带适用条件的规则，不丢掉任何一方的约束；"
    "新证据否定旧证据 → 以新为准，并在「版本间变更」注明被取代的旧证据；"
    "两型之外回落以新为准。",
    "简练优先：规则以最小可执行表述为准——同类偏好合并为一条，示例只留最有代表性的"
    "一个，不重复分区标题已表达的信息，不写空洞要求（如『注意结构清晰』）。"
    "禁止为省字符牺牲可执行性：动作、条件、例外不得抽象掉。"
    "合并后某条证据完全被另一条覆盖的，在晨报「建议作废」列出被覆盖者。",
    "含义稳定，表达自由：证据未变动的规则含义必须不变；合并、条件化改写与重组不受限。",
    f"硬预算：正文 ≤{BUDGET_AUD_CHARS} 字符或 ≤{BUDGET_AUD_RULES} 条（顶层列表行）；"
    "超限必须合并或淘汰，淘汰须写明依据。",
    "文末「版本间变更」必须分级：语义变更逐条归因到证据 id 或预算要求；"
    "纯表达/结构优化标记即可。",
    "用户明确厌恶的行为编为高优先级禁止项，置顶单独分区。",
    "只为换一个需求、或换一个项目仍然适用、且非项目特有约定的口径立条；证据里通用"
    "口径与本次业务内容混在一条时，只取口径那半。",
    "输出纯 Markdown 正文，不要复述本素材包。",
)

# 素材包标记串（v0.3.7 验证门 G1 单点真源）：标题类整串子串匹配；节标题类按
# 核心名行首标题匹配（复述者改标题层级也命中）。测试断言每个标记都会出现在
# 生成的素材包里——模板改字而此处不同步会直接红，防止门静默失效。
MATERIAL_TITLE_MARKERS = (
    "mema-twin 编译素材包",
    "mema-twin 受众画像素材包",
)
MATERIAL_SECTION_MARKERS = (
    "编译规则",
    "旧版本 prompt",
    "全部偏好证据",
    "该受众全部证据",
    "同受众跨类型偏好参考",
    "已作废条款",
)
MATERIAL_MARKERS = MATERIAL_TITLE_MARKERS + tuple(
    f"## {m}" for m in MATERIAL_SECTION_MARKERS)

# ---- playbook 素材包（v0.4 P3，独立于 persona 编译链路）----

BUDGET_PB_CHARS = 6000
BUDGET_PB_RULES = 50

PLAYBOOK_TITLE_MARKERS = ("mema-twin playbook 素材包",)
PLAYBOOK_SECTION_MARKERS = (
    "编译规则",
    "现行 playbook",
    "任务执行记录",
    "工具使用记录",
)
PLAYBOOK_MARKERS = PLAYBOOK_TITLE_MARKERS + tuple(
    f"## {m}" for m in PLAYBOOK_SECTION_MARKERS)

# 工具面板行格式（tool_gap 解析依赖）：`- 工具 `name`：…`；允许行首缩进——
# 编译规则示例行自带两格缩进，模型照抄缩进时解析不能失效（对抗评审轮2 P2-4）
TOOL_PANEL_LINE_RE = r"(?m)^[ \t]*- 工具 `([^`]+)`"

# 素材包渲染护栏（对抗评审轮2 P2-5）：单字段渲染截断（库内全文保留）+
# 包级总闸（超出即截掉后位任务并标注，防 2MB reflection 打爆编译上下文）
_PB_RENDER_MAX = 500
_PB_MATERIAL_MAX = 60_000


def _clip(text: str, limit: int = _PB_RENDER_MAX) -> str:
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    return t[:limit] + "…（截断）"


_PLAYBOOK_RULES = (
    "只写本素材包记录过的真实执行经验；每个条目末尾用 `<!-- task: <task_id> -->`"
    " 标注来源任务，写不出来源的条目不得出现（幻觉防御）。",
    "分流判据（v0.6.7 判据补来源与可移植性）：playbook 只收执行路径、工具选路、"
    "踩坑规避——即「为达成某目的，首选哪条路、降级走什么、兜底怎么办」。不收："
    "产出物的结构/口径/措辞要求（判据：这条经验删掉后交付物本身会变差，如"
    "「用户已写内容基本保留」→ 属 twin.write，persona 侧，不要立条）；工具自身的"
    "参数用法（改个参数名就能用对 → 属该工具的技能文档）。白天宿主的执行经验入口"
    "是 step_update(done, reflection) 与 tool_log(task_id, note)。",
    "可复用性判据（v0.6.7）：区分**可复用经验**与**一次性失误自愈**——服务端报错"
    "文案已自解释的自愈（传错参数名/漏传必填字段，照报错改对即可）不要立条："
    "报错本身会教后续宿主，重复记录是噪音。值得立条的是报错无法自解释、需要额外"
    "上下文或环境知识的坑（如授权门语义、镜像降级行为、跨宿主让位机制）。"
    "同型失误高频复现时在收尾汇总标注「接口易用性信号」，那是服务端改进项而非经验。",
    "工具条目三段式，行格式严格如下（tool_gap 依赖此格式解析）：",
    "  - 工具 `tool_name`：<目的>；首选 <做法>；降级 <替代>；兜底 <人工/放弃>。",
    "  绑意图不绑工具名：同一目的一行；多条真实路径并列可拆多行。",
    "「失败→反思→对策」模式：从步骤 reflection 提炼，写成条件化规避规则，"
    "同样标 `<!-- task: N -->`。",
    "与现行 active playbook 冲突的新经验以新经验为准；未复现的旧条目保留。",
    "按固定分区组织：工具面板 / 路径与顺序 / 失败规避 / owner 澄清记录"
    "（open_questions 的高频问答沉淀）。",
    "跨宿主路径要注明验证宿主（client）；宿主能力差异导致的成败不可归因于路径本身。",
    f"硬预算：正文 ≤{BUDGET_PB_CHARS} 字符或 ≤{BUDGET_PB_RULES} 条（顶层列表行）；"
    "超限必须合并或淘汰，淘汰须写明依据。简练优先，示例只留最有代表性的一个。",
    "输出纯 Markdown 正文，不要复述本素材包。",
)


def compile_playbook_material(key: str, active: dict | None,
                              tasks: list[dict], tool_usage: list[dict]) -> str:
    """playbook 评估素材包（task_evaluate 用）。tasks 每项含 brief/outcome/
    steps（含 reflection）/questions 时序；tool_usage 按 purpose 分组、组内按
    client 分层（宿主能力差异是混淆变量，评审 A7）。"""
    key_label = key if key == "global" else f"{key}"
    parts: list[str] = []
    parts.append(f"# mema-twin playbook 素材包：{key_label}\n\n")
    parts.append("> 用当前会话模型把下方执行记录评估成 playbook 更新，"
                 "再以 twin(action=\"playbook_submit\") 提交落版；"
                 "无值得沉淀的新经验则不提交。\n")
    parts.append("\n## 编译规则\n\n")
    for r in _PLAYBOOK_RULES:
        parts.append(f"- {r}\n")
    parts.append("\n## 现行 playbook（编译参考，非执行依据）\n\n")
    if active:
        parts.append(f"（v{active.get('version')}；你的新稿落版后即取代它）\n\n"
                     f"```markdown\n{active.get('content_md') or ''}\n```\n")
    else:
        parts.append("（无——这是首个版本 v1）\n")
    parts.append(f"\n## 任务执行记录（{len(tasks)} 个任务）\n\n")
    included = 0
    if tasks:
        for t in tasks:
            outcome = t.get("outcome") or "未记录"
            section: list[str] = [
                f"### 任务 #{t['id']}：{_clip(t.get('brief') or '')}"
                f"（outcome={outcome}）\n\n"]
            for s in (t.get("steps") or []):
                line = f"- #{s['seq']} {s['title']} [{s['status']}]"
                if (s.get("reflection") or "").strip():
                    line += f"（反思：{_clip(s['reflection'])}）"
                if (s.get("reason") or "").strip():
                    line += f"（{_clip(s['reason'])}）"
                section.append(line + "\n")
            for q in (t.get("questions") or []):
                if q["status"] == "answered":
                    section.append(f"- 疑问（已澄清）：{_clip(q['question'])}"
                                   f" → {_clip(q.get('answer') or '')}\n")
                else:
                    section.append(f"- 疑问（{'blocking，' if q['blocking'] else ''}"
                                   f"未答）：{_clip(q['question'])}\n")
            if sum(len(p) for p in parts) + sum(len(p) for p in section) > _PB_MATERIAL_MAX:
                parts.append(f"\n（素材包超限截断：已含 {included}/{len(tasks)} 个任务的"
                             "记录，其余未带——收窄 reflection 长度或减小 limit 后重取）\n")
                break
            parts.extend(section)
            included += 1
        if included == 0:
            parts.append("（无收口任务）\n")
    else:
        parts.append("（无收口任务）\n")
    parts.append(f"\n## 工具使用记录（{len(tool_usage)} 条，按目的分组、组内按宿主分层）\n\n")
    if tool_usage:
        by_purpose: dict[str, list[dict]] = {}
        for u in tool_usage:
            by_purpose.setdefault(_clip(u.get("purpose") or "(未注明目的)", 200),
                                  []).append(u)
        for purpose, usages in by_purpose.items():
            parts.append(f"- 目的：{purpose}\n")
            by_client: dict[str, list[dict]] = {}
            for u in usages:
                by_client.setdefault(u.get("client") or "(未知宿主)", []).append(u)
            for client, items in by_client.items():
                ok = sum(1 for x in items if x["outcome"] == "success")
                parts.append(f"  - 宿主 {client}：{ok}/{len(items)} 成功"
                             + ("；备注：" + "；".join(
                                 f"`{x['tool']}` {_clip(x['note'], 200)}" for x in items
                                 if (x.get("note") or "").strip())
                                if any((x.get("note") or "").strip() for x in items) else "")
                             + "\n")
    else:
        parts.append("（无工具使用记录）\n")
    return "".join(parts)


def compile_prompt_material(work_type: str, work_type_zh: str,
                            current: dict | None, evidence: list[dict],
                            audience_profiles: list[dict] | None = None,
                            audience_mode: bool = False,
                            voided: list[dict] | None = None) -> str:
    parts: list[str] = []
    if audience_mode:
        parts.append(f"# mema-twin 受众画像素材包：{work_type_zh}（aud-{work_type}）\n\n")
        parts.append("> 用当前会话模型把下方该受众全部证据抽象成受众画像，"
                     "再以 twin(action=\"submit\", work_type=\"aud-{0}\") 提交落版。\n".format(work_type))
    else:
        parts.append(f"# mema-twin 编译素材包：{work_type_zh}（{work_type}）\n\n")
        parts.append("> 用当前会话模型把下方证据编译成新的 persona prompt，"
                     "再以 twin(action=\"submit\") 提交回库落版本。\n")
    parts.append(f"> {STRONG_MODEL_NOTE}。\n")
    parts.append("\n## 编译规则\n\n")
    for r in (_AUDIENCE_RULES if audience_mode else _TYPE_RULES):
        parts.append(f"- {r}\n")
    parts.append("\n## 旧版本 prompt（编译参考，非执行依据）\n\n")
    if current:
        v = current.get("version")
        tag = "，镜像降级读取" if current.get("from_mirror") or v is None else ""
        vt = f"v{v}" if v is not None else ""
        parts.append(f"（{vt}{tag}；你的新稿 submit 落版后即取代它）\n\n"
                     f"```markdown\n{current.get('prompt_md') or ''}\n```\n")
    else:
        parts.append("（无——这是首个版本 v1）\n")
    if audience_mode:
        parts.append(f"\n## 该受众全部证据（{len(evidence)} 条，含已编译——画像是全量投影）\n\n")
    else:
        parts.append(f"\n## 全部偏好证据（{len(evidence)} 条，全量投影——每版从头重编，"
                     "已吸收的证据同样在场）\n\n")
        # 维度分布摘要（v0.3.9 F1）：分条件段的依据前置可见；空值跳过；单取值也显示
        aud_cnt: dict[str, int] = {}
        pur_cnt: dict[str, int] = {}
        for e in evidence:
            a = (e.get("audience") or "").strip()
            p = (e.get("purpose") or "").strip()
            if a:
                aud_cnt[a] = aud_cnt.get(a, 0) + 1
            if p:
                pur_cnt[p] = pur_cnt.get(p, 0) + 1
        if aud_cnt or pur_cnt:
            segs = [f"受众 {'、'.join(f'{k}×{n}' for k, n in aud_cnt.items())}"] if aud_cnt else []
            if pur_cnt:
                segs.append(f"用途 {'、'.join(f'{k}×{n}' for k, n in pur_cnt.items())}")
            parts.append(f"> 维度分布：{'；'.join(segs)}\n\n")
    if evidence:
        for e in evidence:
            mid = e.get("id", "?")
            subject = e.get("subject") or ""
            content = e.get("content") or ""
            # v0.7.1 W3.2：toolcheck 命中的证据条目在最后一行末尾带标注
            # （compile_actions 已写入 e["tool_flag"]；多行证据的标注不在首行）
            flag = e.get("tool_flag")
            suffix = f"<!-- tool-flag: {flag} -->" if flag in ("hard", "soft") else ""
            if not audience_mode:
                # 维度标签（v0.3.9 F1）：code 直渲染（与正文分区标题/submit 对账同
                # 符号系）；空值省略对应段，两值全空省略整个括号
                a = (e.get("audience") or "").strip()
                p = (e.get("purpose") or "").strip()
                tag = "/".join(x for x in (f"受众:{a}" if a else "",
                                          f"用途:{p}" if p else "") if x)
                parts.append((f"- [{mid}] {subject}（{tag}）：{content}" if tag
                              else f"- [{mid}] {subject}：{content}") + suffix + "\n")
            else:
                parts.append(f"- [{mid}] {subject}：{content}" + suffix + "\n")
    else:
        parts.append("（无在世证据——如仍要重编，可基于当前版本做结构化重写）\n")
    # 已作废条款（防御性，常驻）：全量投影下作废行已不在证据集合，此节防旧版
    # 参考把作废条款带回新版；剔除按 <!-- src --> 溯源对位。
    parts.append("\n## 已作废条款（不进新版）\n\n")
    parts.append("> 以下条款已作废：按其 id 从上方旧版参考的 `<!-- src: N -->` 溯源，"
                 "对应条款不得进入新版，其 id 也不得列入 source_memory_ids。\n\n")
    if voided:
        for e in voided:
            cv = e.get("compiled_version")
            origin = f"曾入 v{cv} 编译" if cv is not None else "未入编译"
            parts.append(f"- [{e.get('memory_id')}] {e.get('subject') or ''}（{origin}）\n")
    else:
        parts.append("（无作废条款）\n")
    parts.append("\n## 同受众跨类型偏好参考（受众画像）\n\n")
    if audience_mode:
        parts.append("（本素材包即受众画像本身，无跨类型参考节）\n")
    elif audience_profiles:
        # 守门句（v0.3.6 AR-4）：画像只迁口径类，格式结构仍以本类型为准
        parts.append("> 守门：以下画像只迁移口径/详略/禁忌类偏好到本类型（条件化落位）；"
                     "格式与结构仍以本类型证据为准。\n\n")
        for ap in audience_profiles:
            parts.append(f"### 受众：{ap['zh']}（aud-{ap['audience']} 画像 v{ap['version']}）\n\n"
                         f"```markdown\n{ap['prompt_md'] or ''}\n```\n")
    else:
        parts.append("（暂无——该类型证据涉及的受众尚未生成画像，夜间任务积累后自动出现）\n")
    return "".join(parts)
