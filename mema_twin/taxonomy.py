"""mema-twin 三维度 canonical 枚举（v1，2026-09-02 定稿；v0.3.8 删 other）。

来源：ZCodeProject/docs/mema-avatar-design-2026-09-02.md 第 5 节（D9）。
work_type 33 项（六域，v0.3.9 按产出物目标重划）/ audience 9 项 / purpose 8 项。
别名只收常见精确写法；语义选码由调用方 Agent 对清单完成（归一门：先 taxonomy
查清单再写，未命中整笔打回、必须问用户裁定——v0.3.8）。无 other 杂项桶：
真长尾由用户 canonicalize 立新码，进清单即自动可选。
"""
from __future__ import annotations

from dataclasses import dataclass

KINDS = ("work_type", "audience", "purpose")


@dataclass(frozen=True)
class CanonicalType:
    code: str
    zh: str
    en: str
    domain: str = ""
    aliases: tuple[str, ...] = ()


_WORK_TYPES: tuple[CanonicalType, ...] = (
    # work_type 33 项（六域按产出物目标，v0.3.9 重划；组内顺序沿用 v1 编排）
    # —— 原「通用职场」域成员 ——
    CanonicalType("work_report", "工作汇报", "work report", "记录与同步",
                  ("周报", "月报", "季度总结", "季度汇报", "述职", "述职报告", "工作总结")),
    CanonicalType("presentation", "演讲与汇报材料", "presentation deck", "说服与传播",
                  ("PPT", "路演", "路演材料", "发言稿", "演讲稿", "答辩材料", "汇报PPT", "slides")),
    CanonicalType("meeting_minutes", "会议纪要", "meeting minutes", "记录与同步",
                  ("纪要", "会议记录", "例会纪要", "评审记录", "访谈记录")),
    CanonicalType("comm_copy", "邮件与沟通文案", "email & comm copy", "记录与同步",
                  ("邮件", "email", "沟通文案", "群通告", "IM消息", "站内信")),
    CanonicalType("project_plan", "项目与实施计划", "project plan", "规格与执行",
                  ("项目计划", "实施计划", "排期", "项目排期", "行动计划")),
    CanonicalType("retrospective", "复盘与总结", "retrospective", "分析与复盘",
                  ("复盘", "项目复盘", "经验总结", "总结", "复盘总结")),
    CanonicalType("personal_notes", "个人笔记", "personal notes", "记录与同步",
                  ("笔记", "学习笔记", "灵感记录", "随手记")),
    # —— 原「方案与商务」域成员 ——
    CanonicalType("proposal", "方案书与解决方案", "proposal / solution doc", "说服与传播",
                  ("方案书", "解决方案", "业务方案", "售前方案", "建议方案")),
    CanonicalType("bid_document", "投标与标书", "bid / tender doc", "说服与传播",
                  ("标书", "投标文件", "投标书", "应答文件", "询价回复")),
    CanonicalType("contract_draft", "合同与协议起草", "contract drafting", "法律与契约",
                  ("合同", "协议", "合同起草", "协议起草", "补充协议")),
    CanonicalType("marketing_copy", "营销与对外文案", "marketing copy", "说服与传播",
                  ("营销文案", "推广文案", "活动文案", "文案", "产品介绍文案")),
    CanonicalType("press_release", "公关与新闻稿", "press release", "说服与传播",
                  ("新闻稿", "通稿", "公关稿", "对外声明")),
    # —— 原「产品与研发」域成员 ——
    CanonicalType("product_doc", "产品文档", "product doc (PRD)", "规格与执行",
                  ("PRD", "prd", "需求文档", "需求说明书", "MRD", "功能规格")),
    CanonicalType("software_design", "软件设计", "software design doc", "规格与执行",
                  ("架构设计", "详细设计", "概要设计", "接口文档", "设计文档")),
    CanonicalType("tech_eval", "技术评估与选型", "tech evaluation", "分析与复盘",
                  ("选型报告", "可行性分析", "可行性报告", "PoC")),
    CanonicalType("research_report", "调研报告", "research report", "分析与复盘",
                  ("竞品分析", "竞品报告", "行业研究", "技术调研", "调研")),
    CanonicalType("data_analysis", "数据分析报告", "data analysis report", "分析与复盘",
                  ("数据报告", "分析报告", "经营分析", "报表解读")),
    CanonicalType("test_report", "测试与验收报告", "test / acceptance report", "记录与同步",
                  ("测试报告", "验收报告", "质量报告")),
    CanonicalType("incident_report", "故障与事故报告", "incident report", "分析与复盘",
                  ("事故报告", "故障报告", "故障通报", "事故复盘")),
    CanonicalType("user_manual", "手册与操作指南", "user manual / guide", "教学与传授",
                  ("使用手册", "用户手册", "手册", "指南", "操作指南", "运维指南", "说明书", "FAQ")),
    # —— 原「管理与制度」域成员 ——
    CanonicalType("policy_sop", "制度与流程规范", "policy / SOP", "规格与执行",
                  ("制度", "管理制度", "SOP", "流程规范", "规范", "管理办法")),
    CanonicalType("official_doc", "公文与行政文件", "official admin doc", "记录与同步",
                  ("公文", "行政文件", "通知", "请示", "函件", "红头文件", "公告")),
    CanonicalType("budget_proposal", "预算与立项申请", "budget / project approval", "说服与传播",
                  ("立项报告", "预算申请", "立项申请", "采购申请", "预算报告")),
    CanonicalType("performance_review", "绩效与评价材料", "performance review", "分析与复盘",
                  ("绩效评语", "绩效评价", "晋升材料", "晋升答辩材料", "推荐信")),
    CanonicalType("jd_recruit", "岗位与招聘材料", "JD / recruiting", "规格与执行",
                  ("JD", "岗位说明", "招聘启事", "面试题", "招聘材料")),
    # —— 原「培训与知识」域成员 ——
    CanonicalType("training_material", "培训与教学材料", "training material", "教学与传授",
                  ("课件", "教程", "培训材料", "内训讲义", "讲义")),
    CanonicalType("knowledge_doc", "知识库文档", "knowledge doc", "教学与传授",
                  ("知识库", "wiki", "知识条目", "最佳实践")),
    # —— 原「创意与内容」域成员 ——
    CanonicalType("creative_brief", "创意简报与大纲", "creative brief", "规格与执行",
                  ("创意方案", "创意简报", "活动策划", "策划案", "内容大纲")),
    CanonicalType("video_script", "视频与脚本文案", "video script", "说服与传播",
                  ("脚本", "短视频脚本", "直播话术", "分镜", "分镜脚本")),
    # —— 原「专业服务」域成员 ——
    CanonicalType("due_diligence", "尽调与审计报告", "due diligence / audit", "分析与复盘",
                  ("尽调报告", "尽职调查", "尽调", "审计报告")),
    CanonicalType("legal_opinion", "法律与合规文书", "legal / compliance doc", "法律与契约",
                  ("法律意见", "法律意见书", "合规文书", "合规分析", "律师函")),
    CanonicalType("financial_analysis", "财务与估值分析", "financial analysis", "分析与复盘",
                  ("财务分析", "估值报告", "财务报告", "财报分析")),
    CanonicalType("academic_report", "论文与学术报告", "academic paper / report", "分析与复盘",
                  ("论文", "学术报告", "技术报告", "研究综述", "综述")),
)

_AUDIENCES: tuple[CanonicalType, ...] = (
    CanonicalType("direct_manager", "直属上级", "direct manager", "受众",
                  ("上级", "老板", "直属领导", "主管", "line manager")),
    CanonicalType("leadership", "高层与决策层", "leadership", "受众",
                  ("高层", "决策层", "管理层", "公司领导", "executives")),
    CanonicalType("board_investor", "董事会与投资人", "board & investors", "受众",
                  ("董事会", "投资人", "董事", "股东")),
    CanonicalType("team_peers", "团队与跨部门同事", "team & cross-dept peers", "受众",
                  ("同事", "团队", "团队内部", "跨部门", "组内", "peers")),
    CanonicalType("external_client", "外部客户", "external client", "受众",
                  ("客户", "甲方", "顾客", "买家")),
    CanonicalType("partner_vendor", "合作伙伴与供应商", "partners & vendors", "受众",
                  ("合作伙伴", "供应商", "渠道", "partner")),
    CanonicalType("regulator", "监管与审查机构", "regulator", "受众",
                  ("监管", "监管机构", "审查机构", "审计方", "政府部门")),
    CanonicalType("public", "公开大范围", "public", "受众",
                  ("公开", "公开场合", "大范围", "对外发布", "社交媒体")),
    CanonicalType("self", "个人自用", "self", "受众",
                  ("自己", "自用", "个人", "本人")),
)

_PURPOSES: tuple[CanonicalType, ...] = (
    CanonicalType("request_decision", "申请决策与资源", "request decision / resources", "用途",
                  ("申请决策", "要资源", "申请资源", "审批", "请示批准", "立项")),
    CanonicalType("sync_info", "信息同步与知会", "sync & inform", "用途",
                  ("同步", "知会", "通报", "进展同步", "通知")),
    CanonicalType("drive_action", "推动执行与派工", "drive action / assign", "用途",
                  ("推动", "派工", "安排任务", "布置工作", "推进")),
    CanonicalType("review_defense", "评审与答辩", "review & defense", "用途",
                  ("评审", "答辩", "述职答辩", "汇报答辩")),
    CanonicalType("knowledge_archive", "知识沉淀与传承", "knowledge archive", "用途",
                  ("沉淀", "传承", "归档", "知识整理")),
    CanonicalType("persuade_sell", "说服与售卖", "persuade & sell", "用途",
                  ("说服", "售卖", "销售", "推销")),
    CanonicalType("collaborate_help", "求助与协作", "collaborate / ask help", "用途",
                  ("求助", "协作", "求支持", "帮忙", "协助")),
    CanonicalType("record_evidence", "记录与存证", "record & evidence", "用途",
                  ("存证", "留痕", "备案", "记录")),
)

_BY_KIND: dict[str, tuple[CanonicalType, ...]] = {
    "work_type": _WORK_TYPES,
    "audience": _AUDIENCES,
    "purpose": _PURPOSES,
}


def all_types(kind: str) -> tuple[CanonicalType, ...]:
    _require_kind(kind)
    return _BY_KIND[kind]


def by_code(kind: str, code: str) -> CanonicalType | None:
    for t in all_types(kind):
        if t.code == code:
            return t
    return None


def match_exact(kind: str, value: str) -> CanonicalType | None:
    """精确/别名匹配（大小写与首尾空白不敏感），只在本 kind 内匹配。"""
    v = (value or "").strip().casefold()
    if not v:
        return None
    for t in all_types(kind):
        for cand in (t.code, t.zh, t.en, *t.aliases):
            if cand and v == cand.strip().casefold():
                return t
    return None


def _require_kind(kind: str) -> None:
    if kind not in KINDS:
        raise ValueError(f"unknown type_kind: {kind!r}; expected one of {KINDS}")


# v0.6.2 D1：维度选码说明（适用边界+易混淆项），≤120 字——独立 dict 注入，
# 不改 CanonicalType 构造块（50 条含括号别名的正则注入有语法风险，R1 评审期实证）
_DESCRIPTIONS: dict[str, str] = {
    # ---- work_type 33 ----
    "academic_report": "论文/学术报告/研究综述等学术文体，强调引用规范与论证结构；行业与市场研究归 research_report",
    "bid_document": "投标/标书/询价应答等竞标文书，强调合规响应与评分点覆盖；售前方案书归 proposal",
    "budget_proposal": "立项/预算/采购申请，面向资源审批方；执行排期归 project_plan",
    "comm_copy": "邮件/群通告/IM 消息等日常沟通文案；对外宣传归 marketing_copy",
    "contract_draft": "合同/协议的条款文本起草与修订；法律风险意见归 legal_opinion",
    "creative_brief": "创意方案/策划案/内容大纲等构思阶段产物；成品投放文案归 marketing_copy",
    "data_analysis": "基于数据的分析报告与报表解读，结论须数据支撑；市场调研归 research_report，财务口径归 financial_analysis",
    "due_diligence": "尽调/审计类系统性核查长报告；单项法律合规意见归 legal_opinion",
    "financial_analysis": "财务报表/估值/预算执行分析，财务口径；一般经营数据归 data_analysis",
    "incident_report": "故障/事故的通报与专项复盘，含影响面与根因；泛经验总结归 retrospective",
    "jd_recruit": "岗位 JD/招聘启事/面试题等招聘材料；人员评价归 performance_review",
    "knowledge_doc": "知识库条目/wiki/最佳实践等沉淀文档；教学课件归 training_material，个人随手记归 personal_notes",
    "legal_opinion": "法律意见/合规分析/律师函等法律专业文书；合同条款文本归 contract_draft",
    "marketing_copy": "推广/活动/产品介绍等营销文案；新闻通稿归 press_release，口播脚本归 video_script",
    "meeting_minutes": "会议纪要/评审记录/访谈记录等过程记录；周期性工作汇报归 work_report",
    "official_doc": "公文/红头文件/请示/函件等正式行政文体；日常团队沟通归 comm_copy",
    "performance_review": "绩效评语/晋升材料/推荐信等对人的评价材料；述职汇报归 work_report",
    "personal_notes": "个人笔记/灵感/随手记，无固定结构要求；可共享的结构化知识归 knowledge_doc",
    "policy_sop": "管理制度/SOP/流程规范等约束性文档；操作层手册归 user_manual",
    "presentation": "PPT/发言稿/路演材料等演示文体；纯文字周期汇报归 work_report",
    "press_release": "新闻稿/通稿/对外声明等媒体口径内容；商业推广文案归 marketing_copy",
    "product_doc": "PRD/需求文档/MRD 等产品侧文档；技术实现侧归 software_design，测试验收归 test_report",
    "project_plan": "项目计划/实施排期/行动计划；资源与立项申请归 budget_proposal",
    "proposal": "方案书/解决方案/售前建议方案；竞标文书归 bid_document，需求文档归 product_doc",
    "research_report": "调研/行业研究/竞品分析/技术调研等洞察类报告；数据分析归 data_analysis，选型结论归 tech_eval",
    "retrospective": "复盘/经验总结类回顾文档；故障专项归 incident_report，周期汇报归 work_report",
    "software_design": "架构设计/详细设计/接口文档等技术实现侧文档；需求侧归 product_doc",
    "tech_eval": "技术选型/可行性分析/PoC 结论；调研过程归 research_report",
    "test_report": "测试报告/验收报告/质量报告；故障根因归 incident_report",
    "training_material": "课件/教程/内训讲义等教学材料；知识条目归 knowledge_doc",
    "user_manual": "使用手册/操作指南等操作层文档；管理制度归 policy_sop",
    "video_script": "短视频脚本/直播话术/分镜等视频文案；图文推广归 marketing_copy",
    "work_report": "周报/月报/季度总结/述职等周期性工作汇报；经营数据分析归 data_analysis，述职演示归 presentation",
    # ---- audience 9 ----
    "board_investor": "董事会/投资人/股东层——决策与资本视角；内部直属上级归 direct_manager，公司高层归 leadership",
    "direct_manager": "直属上级——进展汇报与请示的第一对象；公司高层决策层归 leadership",
    "external_client": "外部客户/甲方——交付物与商务沟通的对象；供应商与渠道归 partner_vendor",
    "leadership": "高层与决策层——资源/战略/取舍视角的汇报；具体执行请示归 direct_manager",
    "partner_vendor": "合作伙伴/供应商/渠道——商务协作对象；交付客户归 external_client",
    "public": "公开发布/大范围传播的内容；面向媒体口径的稿子可同时选 press_release 工种",
    "regulator": "监管/审查/审计机构等外部监督方；内部合规意见归 legal_opinion",
    "self": "自己看的记录与提醒；团队共享归 team_peers",
    "team_peers": "团队与跨部门同事——协作与知会对象；直属上级归 direct_manager，高层归 leadership",
    # ---- purpose 8 ----
    "request_decision": "需要 owner/决策方拍板或给资源——结论等待方；单纯告知进展归 sync_info",
    "sync_info": "同步进展/知会信息，无需对方行动；要资源要拍板归 request_decision",
    "drive_action": "推动对方执行/派工/布置任务——结论期待行动；单纯告知归 sync_info",
    "review_defense": "内容将被评审/答辩/质询——需经得起追问；存档留痕归 record_evidence",
    "knowledge_archive": "为长期沉淀与传承而写——后人可检索复用；单纯留痕归 record_evidence",
    "persuade_sell": "目标是说服/售卖——论点与证据服务转化；客观同步归 sync_info",
    "collaborate_help": "向他人求助/请求协作——明确求助点；布置任务归 drive_action",
    "record_evidence": "为留痕/备案/合规存证而写——可追溯优先；知识复用归 knowledge_archive",
}


def description_for(code: str) -> str:
    """维度选码说明（v0.6.2 D1）：code 精确查，自建码走 DB description 列
    （调用方合并），内置码查本表；无则空串。"""
    return _DESCRIPTIONS.get(code, "")

