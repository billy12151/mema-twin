"""mema-twin 三维度 canonical 枚举（v1，2026-09-02 定稿）。

来源：ZCodeProject/docs/mema-avatar-design-2026-09-02.md 第 5 节（D9）。
work_type 34 项（七域）/ audience 10 项 / purpose 9 项。
别名只收常见精确写法；语义近似由 embed 档归一（已接入，见 embed.py /
normalize.py），未命中进 pending 由用户治理裁定，绝不自动新建 canonical。
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
    # 通用职场（7）
    CanonicalType("work_report", "工作汇报", "work report", "通用职场",
                  ("周报", "月报", "季度总结", "季度汇报", "述职", "述职报告", "工作总结")),
    CanonicalType("presentation", "演讲与汇报材料", "presentation deck", "通用职场",
                  ("PPT", "路演", "路演材料", "发言稿", "演讲稿", "答辩材料", "汇报PPT", "slides")),
    CanonicalType("meeting_minutes", "会议纪要", "meeting minutes", "通用职场",
                  ("纪要", "会议记录", "例会纪要", "评审记录", "访谈记录")),
    CanonicalType("comm_copy", "邮件与沟通文案", "email & comm copy", "通用职场",
                  ("邮件", "email", "沟通文案", "群通告", "IM消息", "站内信")),
    CanonicalType("project_plan", "项目与实施计划", "project plan", "通用职场",
                  ("项目计划", "实施计划", "排期", "项目排期", "行动计划")),
    CanonicalType("retrospective", "复盘与总结", "retrospective", "通用职场",
                  ("复盘", "项目复盘", "经验总结", "总结", "复盘总结")),
    CanonicalType("personal_notes", "个人笔记", "personal notes", "通用职场",
                  ("笔记", "学习笔记", "灵感记录", "随手记")),
    # 方案与商务（5）
    CanonicalType("proposal", "方案书与解决方案", "proposal / solution doc", "方案与商务",
                  ("方案书", "解决方案", "业务方案", "售前方案", "建议方案")),
    CanonicalType("bid_document", "投标与标书", "bid / tender doc", "方案与商务",
                  ("标书", "投标文件", "投标书", "应答文件", "询价回复")),
    CanonicalType("contract_draft", "合同与协议起草", "contract drafting", "方案与商务",
                  ("合同", "协议", "合同起草", "协议起草", "补充协议")),
    CanonicalType("marketing_copy", "营销与对外文案", "marketing copy", "方案与商务",
                  ("营销文案", "推广文案", "活动文案", "文案", "产品介绍文案")),
    CanonicalType("press_release", "公关与新闻稿", "press release", "方案与商务",
                  ("新闻稿", "通稿", "公关稿", "对外声明")),
    # 产品与研发（8）
    CanonicalType("product_doc", "产品文档", "product doc (PRD)", "产品与研发",
                  ("PRD", "prd", "需求文档", "需求说明书", "MRD", "功能规格")),
    CanonicalType("software_design", "软件设计", "software design doc", "产品与研发",
                  ("架构设计", "详细设计", "概要设计", "接口文档", "设计文档")),
    CanonicalType("tech_eval", "技术评估与选型", "tech evaluation", "产品与研发",
                  ("选型报告", "可行性分析", "可行性报告", "PoC")),
    CanonicalType("research_report", "调研报告", "research report", "产品与研发",
                  ("竞品分析", "竞品报告", "行业研究", "技术调研", "调研")),
    CanonicalType("data_analysis", "数据分析报告", "data analysis report", "产品与研发",
                  ("数据报告", "分析报告", "经营分析", "报表解读")),
    CanonicalType("test_report", "测试与验收报告", "test / acceptance report", "产品与研发",
                  ("测试报告", "验收报告", "质量报告")),
    CanonicalType("incident_report", "故障与事故报告", "incident report", "产品与研发",
                  ("事故报告", "故障报告", "故障通报", "事故复盘")),
    CanonicalType("user_manual", "手册与操作指南", "user manual / guide", "产品与研发",
                  ("使用手册", "用户手册", "手册", "指南", "操作指南", "运维指南", "说明书", "FAQ")),
    # 管理与制度（5）
    CanonicalType("policy_sop", "制度与流程规范", "policy / SOP", "管理与制度",
                  ("制度", "管理制度", "SOP", "流程规范", "规范", "管理办法")),
    CanonicalType("official_doc", "公文与行政文件", "official admin doc", "管理与制度",
                  ("公文", "行政文件", "通知", "请示", "函件", "红头文件", "公告")),
    CanonicalType("budget_proposal", "预算与立项申请", "budget / project approval", "管理与制度",
                  ("立项报告", "预算申请", "立项申请", "采购申请", "预算报告")),
    CanonicalType("performance_review", "绩效与评价材料", "performance review", "管理与制度",
                  ("绩效评语", "绩效评价", "晋升材料", "晋升答辩材料", "推荐信")),
    CanonicalType("jd_recruit", "岗位与招聘材料", "JD / recruiting", "管理与制度",
                  ("JD", "岗位说明", "招聘启事", "面试题", "招聘材料")),
    # 培训与知识（2）
    CanonicalType("training_material", "培训与教学材料", "training material", "培训与知识",
                  ("课件", "教程", "培训材料", "内训讲义", "讲义")),
    CanonicalType("knowledge_doc", "知识库文档", "knowledge doc", "培训与知识",
                  ("知识库", "wiki", "知识条目", "最佳实践")),
    # 创意与内容（2）
    CanonicalType("creative_brief", "创意简报与大纲", "creative brief", "创意与内容",
                  ("创意方案", "创意简报", "活动策划", "策划案", "内容大纲")),
    CanonicalType("video_script", "视频与脚本文案", "video script", "创意与内容",
                  ("脚本", "短视频脚本", "直播话术", "分镜", "分镜脚本")),
    # 专业服务（4）
    CanonicalType("due_diligence", "尽调与审计报告", "due diligence / audit", "专业服务",
                  ("尽调报告", "尽职调查", "尽调", "审计报告")),
    CanonicalType("legal_opinion", "法律与合规文书", "legal / compliance doc", "专业服务",
                  ("法律意见", "法律意见书", "合规文书", "合规分析", "律师函")),
    CanonicalType("financial_analysis", "财务与估值分析", "financial analysis", "专业服务",
                  ("财务分析", "估值报告", "财务报告", "财报分析")),
    CanonicalType("academic_report", "论文与学术报告", "academic paper / report", "专业服务",
                  ("论文", "学术报告", "技术报告", "研究综述", "综述")),
    # 其他（1）
    CanonicalType("other", "其他", "other", "其他", ("其它",)),
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
                  ("自己", "自用", "个人", "私人")),
    CanonicalType("other", "其他", "other", "受众", ("其它",)),
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
    CanonicalType("other", "其他", "other", "用途", ("其它",)),
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
