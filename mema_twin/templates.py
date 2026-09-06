"""编译素材包模板与写入引导文案。"""

STRONG_MODEL_NOTE = "编译质量取决于当前会话模型；建议在强模型会话中执行 compile/submit"

WRITE_GUIDANCE = (
    "twin.write 只收「可复用的抽象」，不收一次性事实：\n"
    "- 用户在本次工作产物修改中体现的偏好与规则（用语、详略、格式、口吻）；\n"
    "- 该类产物的结构习惯（章节顺序、开头结尾套路、图表使用）；\n"
    "- 开工前应确认的问题与材料（前置清单）。\n"
    "事件性内容（做了什么、何时交付）走普通 memory.remember，不要走 twin.write。"
)


_TYPE_RULES = (
    "只吸收有证据支撑的偏好；仅出现一次的偏好须标注（单次观察）。",
    "按固定分区组织：整体风格 / 结构与格式 / 话术与用语 / 受众适配（按 audience 分条件段）"
    "/ 用途适配（按 purpose 分条件段）/ 前置确认清单（开工前应核对的材料与问题）。",
    "每条规则后用 `<!-- src: <memory_id> -->` 标注来源记忆 id，保证可溯源。",
    "与旧版本冲突的新证据以新证据为准，并在文末「版本间变更」一节列出差异。",
    "规则尽量写成条件化策略：注明适用条件与例外"
    "（例：信息缺失但可逆时自行假设并推进，不可逆才向用户确认）。",
    "用户明确厌恶的行为（如反复确认、给选项不给判断、暴露内部流程）"
    "编为高优先级禁止项，单独分区置顶。",
    "输出纯 Markdown 正文，不要复述本素材包。",
)

_AUDIENCE_RULES = (
    "只保留对该受众稳定成立的口径类偏好（详略/口吻/禁忌/关注点/信息侧重）；"
    "格式与结构类不进画像（那属于各工作类型）。",
    "按固定分区组织：总体口径 / 详略与信息密度 / 话术与禁忌 / 关注点与偏好侧重；"
    "子受众差异写成条件段（例：若为技术评审场合则…）。",
    "每条规则后用 `<!-- src: <memory_id> -->` 标注来源记忆 id，保证可溯源。",
    "与旧版画像冲突的新证据以新证据为准，并在文末「版本间变更」一节列出差异。",
    "用户明确厌恶的行为编为高优先级禁止项，置顶单独分区。",
    "输出纯 Markdown 正文，不要复述本素材包。",
)


def compile_prompt_material(work_type: str, work_type_zh: str,
                            current: dict | None, evidence: list[dict],
                            audience_profiles: list[dict] | None = None,
                            audience_mode: bool = False) -> str:
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
        tag = "，镜像降级读取" if current.get("from_mirror") else ""
        parts.append(f"（v{current.get('version')}{tag}；你的新稿 submit 落版后即取代它）\n\n"
                     f"```markdown\n{current.get('prompt_md') or ''}\n```\n")
    else:
        parts.append("（无——这是首个版本 v1）\n")
    if audience_mode:
        parts.append(f"\n## 该受众全部证据（{len(evidence)} 条，含已编译——画像是全量投影）\n\n")
    else:
        parts.append(f"\n## 未编译偏好证据（{len(evidence)} 条）\n\n")
    if evidence:
        for e in evidence:
            mid = e.get("id", "?")
            subject = e.get("subject") or ""
            content = e.get("content") or ""
            parts.append(f"- [{mid}] {subject}：{content}\n")
    else:
        parts.append("（无新证据——如仍要重编，可基于当前版本做结构化重写）\n")
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
