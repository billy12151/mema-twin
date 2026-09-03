"""编译素材包模板与写入引导文案。"""

STRONG_MODEL_NOTE = "编译质量取决于当前会话模型；建议在强模型会话中执行 compile/submit"

WRITE_GUIDANCE = (
    "twin.write 只收「可复用的抽象」，不收一次性事实：\n"
    "- 用户在本次工作产物修改中体现的偏好与规则（用语、详略、格式、口吻）；\n"
    "- 该类产物的结构习惯（章节顺序、开头结尾套路、图表使用）；\n"
    "- 开工前应确认的问题与材料（前置清单）。\n"
    "事件性内容（做了什么、何时交付）走普通 memory.remember，不要走 twin.write。"
)


def compile_prompt_material(work_type: str, work_type_zh: str,
                            current: dict | None, evidence: list[dict]) -> str:
    parts: list[str] = []
    parts.append(f"# mema-twin 编译素材包：{work_type_zh}（{work_type}）\n\n")
    parts.append("> 用当前会话模型把下方证据编译成新的 persona prompt，"
                 "再以 twin(action=\"submit\") 提交回库落版本。\n")
    parts.append(f"> {STRONG_MODEL_NOTE}。\n")
    parts.append("\n## 编译规则\n\n")
    for r in (
        "只吸收有证据支撑的偏好；仅出现一次的偏好须标注（单次观察）。",
        "按固定分区组织：整体风格 / 结构与格式 / 话术与用语 / 受众适配（按 audience 分条件段）"
        "/ 用途适配（按 purpose 分条件段）/ 前置确认清单（开工前应核对的材料与问题）。",
        "每条规则后用 `<!-- src: <memory_id> -->` 标注来源记忆 id，保证可溯源。",
        "与当前版本冲突的新证据以新证据为准，并在文末「版本间变更」一节列出差异。",
        "输出纯 Markdown 正文，不要复述本素材包。",
    ):
        parts.append(f"- {r}\n")
    parts.append("\n## 当前版本 prompt\n\n")
    if current:
        tag = "，镜像降级读取" if current.get("from_mirror") else ""
        parts.append(f"（v{current.get('version')}{tag}）\n\n```markdown\n"
                     f"{current.get('prompt_md') or ''}\n```\n")
    else:
        parts.append("（无——这是首个版本 v1）\n")
    parts.append(f"\n## 未编译偏好证据（{len(evidence)} 条）\n\n")
    if evidence:
        for e in evidence:
            mid = e.get("id", "?")
            subject = e.get("subject") or ""
            content = e.get("content") or ""
            parts.append(f"- [{mid}] {subject}：{content}\n")
    else:
        parts.append("（无新证据——如仍要重编，可基于当前版本做结构化重写）\n")
    return "".join(parts)
