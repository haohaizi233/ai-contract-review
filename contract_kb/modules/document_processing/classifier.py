# -*- coding: utf-8 -*-
"""合同分类器：支持全量 78 套标准模板、标准业务板块与五级分类体系。

分类证据来源（按权重）：
1. 模板编码（MB...）权威映射（最高置信度，链接五级路由与排他规则）
2. 第一条合同事项正文（最强文本特征）
3. 文档标题/副标题
4. 业务特征字段（表格列头）
5. 特征条款（预审承诺、包干费、风险提成、诉讼管辖等）
6. 文件名（辅助线索）
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ..templates.catalog_data import (
    TEMPLATE_5TH_LAYER_RULES,
    get_template_leaf_map,
)

_TPL_LEAF_MAP = get_template_leaf_map()

# 业务板块基础代码与中文映射（兼容基线 IP_GENERAL 与 78 模板细分板块）
CATEGORY_NAMES = {
    "PATENT": "国内专利代理",
    "TRADEMARK": "国内商标代理",
    "COPYRIGHT": "国内著作权代理",
    "IP_GENERAL": "综合知识产权框架与保密",
    "MIXED_IP": "跨板块综合业务",
    "INT_IP": "国际专利与商标",
    "PROJECT_AUDIT": "科创项目与财税认证",
    "LEGAL_LITIGATION": "法律服务与维权诉讼",
    "FRAMEWORK_NDA": "总委框架与保密协议",
    "HIGH_END_CONSULTING": "高端咨询与数据服务",
    "IP_OPERATION": "知产交易与运营",
    "GOVERNANCE_RULE": "管理制度与规范",
}

# 二级分类关键词字典
SUBTYPES: dict[str, dict[str, Any]] = {
    # 1. 国内专利
    "PATENT_APPLICATION": {
        "name": "专利常规申请",
        "category": "PATENT",
        "matter_kw": ["专利申请", "申请文件初稿", "技术交底书"],
        "fname_kw": ["（申请）", "(申请)"],
        "feature_kw": ["超权费", "实审阶段", "费减", "申请类型", "发明人名称"],
    },
    "PATENT_APPLICATION_RISK": {
        "name": "专利风险代理申请",
        "category": "PATENT",
        "matter_kw": ["风险专利", "授权奖励", "风险排查"],
        "fname_kw": ["风险代理"],
        "feature_kw": ["风险专利申请", "未授权退还", "IPO上市前"],
    },
    "PATENT_FAST_PRESCREEN": {
        "name": "专利快速预审",
        "category": "PATENT",
        "matter_kw": ["快速预审服务事项", "预审服务事项"],
        "fname_kw": ["快速预审"],
        "feature_kw": ["保护中心预审"],
    },
    "PATENT_PRIORITY_REVIEW": {
        "name": "专利优先审查",
        "category": "PATENT",
        "matter_kw": ["优先审查服务事项"],
        "fname_kw": ["优先审查"],
        "feature_kw": ["专利状态", "优先审查请求"],
    },
    "PATENT_REEXAMINATION": {
        "name": "专利复审",
        "category": "PATENT",
        "matter_kw": ["专利驳回复审", "专利复审"],
        "fname_kw": ["（专利复审）", "专利复审"],
        "feature_kw": ["驳回复审"],
    },
    "PATENT_INVALIDATION": {
        "name": "专利无效宣告",
        "category": "HIGH_END_CONSULTING",
        "matter_kw": ["专利无效", "无效宣告请求", "口头审理"],
        "fname_kw": ["专利无效", "无效宣告"],
        "feature_kw": ["无效宣告请求书", "口审"],
    },
    "PATENT_ANNUAL_FEE": {
        "name": "专利年费代缴",
        "category": "PATENT",
        "matter_kw": ["年费代缴", "年费期限"],
        "fname_kw": ["年费缴纳", "(年费缴纳)"],
        "feature_kw": ["专利年费", "年费期限"],
    },
    "PATENT_DOCUMENT_COPY": {
        "name": "专利文件副本",
        "category": "PATENT",
        "matter_kw": ["文件副本", "在先申请文件副本", "专利登记簿副本"],
        "fname_kw": ["优先权等副本"],
        "feature_kw": ["副本费用"],
    },
    "PATENT_BIBLIO_CHANGE": {
        "name": "著录项目变更",
        "category": "PATENT",
        "matter_kw": ["著录项目变更"],
        "fname_kw": ["著录项目变更"],
        "feature_kw": ["变更事项", "变更前申请人"],
    },
    "PATENT_LICENSE_RECORDAL": {
        "name": "专利实施许可备案",
        "category": "PATENT",
        "matter_kw": ["专利实施许可备案事宜", "许可人名称"],
        "fname_kw": ["许可备案）"],
        "feature_kw": ["被许可人名称", "许可期限"],
    },
    "PATENT_LICENSE_CHANGE_CANCEL": {
        "name": "专利许可备案变更注销",
        "category": "PATENT",
        "matter_kw": ["许可备案变更", "许可备案注销"],
        "fname_kw": ["许可备案注销"],
        "feature_kw": ["备案号"],
    },
    "PATENT_TRANSFER": {
        "name": "专利转让",
        "category": "PATENT",
        "matter_kw": ["专利转让事宜", "专利转让服务", "转让人名称"],
        "fname_kw": ["（转让）", "(转让)", "patent_transfer"],
        "feature_kw": ["受让人名称", "转让人名称"],
    },
    "PATENT_EVALUATION_REPORT": {
        "name": "专利评价报告",
        "category": "PATENT",
        "matter_kw": ["专利评价报告"],
        "fname_kw": ["（专利评价报告）"],
        "feature_kw": [],
    },
    "PATENT_LAYOUT_DESIGN": {
        "name": "集成电路布图设计",
        "category": "PATENT",
        "matter_kw": ["集成电路布图设计", "布图设计登记"],
        "fname_kw": ["集成电路布图"],
        "feature_kw": ["布图名称"],
    },
    "PATENT_PACKAGE_COMBO": {
        "name": "国内专利组合服务",
        "category": "PATENT",
        "matter_kw": ["国内专利组合", "专利综合服务"],
        "fname_kw": ["国内专利综合", "专利组合"],
        "feature_kw": ["打包服务"],
    },
    "PATENT_SEARCH_REPORT": {
        "name": "专利检索报告",
        "category": "HIGH_END_CONSULTING",
        "matter_kw": ["专利检索报告", "专利查新检索"],
        "fname_kw": ["（专利检索报告）"],
        "feature_kw": ["检索范围"],
    },
    "PATENT_DUE_DILIGENCE_REPORT": {
        "name": "专利尽职调查报告",
        "category": "HIGH_END_CONSULTING",
        "matter_kw": ["专利尽职调查", "专利盘点"],
        "fname_kw": ["尽职调查报告"],
        "feature_kw": ["分级管理"],
    },
    "PATENT_STABILITY_REPORT": {
        "name": "专利稳定性分析",
        "category": "HIGH_END_CONSULTING",
        "matter_kw": ["评估专利稳定性", "稳定性分析报告"],
        "fname_kw": ["稳定性分析报告"],
        "feature_kw": ["公开号", "稳定性结论"],
    },
    "PATENT_INFRINGEMENT_REPORT": {
        "name": "专利侵权比对分析",
        "category": "HIGH_END_CONSULTING",
        "matter_kw": ["侵权比对分析报告", "专利侵权分析"],
        "fname_kw": ["专利侵权分析"],
        "feature_kw": ["全面覆盖原则"],
    },
    "PATENT_NAVIGATION_REPORT": {
        "name": "专利导航分析报告",
        "category": "HIGH_END_CONSULTING",
        "matter_kw": ["专利导航分析报告", "专利导航"],
        "fname_kw": ["专利导航分析报告"],
        "feature_kw": ["产业规划", "技术发展路线"],
    },
    "PATENT_FTO_REPORT": {
        "name": "专利FTO防侵权检索与尽调",
        "category": "HIGH_END_CONSULTING",
        "matter_kw": ["FTO", "自由实施", "防侵权调查"],
        "fname_kw": ["FTO"],
        "feature_kw": ["自由实施", "风险专利清单"],
    },
    "PATENT_CONSULTANT": {
        "name": "常年知识产权顾问",
        "category": "HIGH_END_CONSULTING",
        "matter_kw": ["常年知识产权顾问", "年度知识产权顾问"],
        "fname_kw": ["顾问合同"],
        "feature_kw": ["顾问期限", "定期辅导"],
    },
    # 2. 国内商标
    "TRADEMARK_REGISTRATION": {
        "name": "商标新注册",
        "category": "TRADEMARK",
        "matter_kw": ["商标注册服务事宜", "商标注册申请单"],
        "fname_kw": ["（注册）", "(注册)"],
        "feature_kw": ["商标图样/文字", "商标字体"],
    },
    "TRADEMARK_CONSULTATION": {
        "name": "商标咨询与包过服务",
        "category": "TRADEMARK",
        "matter_kw": ["商标咨询服务", "担保注册"],
        "fname_kw": ["商标咨询服务"],
        "feature_kw": ["未获准注册退", "不过退费"],
    },
    "TRADEMARK_CHANGE": {
        "name": "商标变更",
        "category": "TRADEMARK",
        "matter_kw": ["商标变更名称地址", "商标变更"],
        "fname_kw": ["商标服务合同（变更）"],
        "feature_kw": ["变更前申请人", "变更后申请人"],
    },
    "TRADEMARK_CHANGE_WORD_ONLY": {
        "name": "商标变更(纯文字版)",
        "category": "TRADEMARK",
        "matter_kw": ["纯文字商标变更", "文字商标名址变更"],
        "fname_kw": ["变更-文字商标", "纯文字"],
        "feature_kw": ["变更后名址"],
    },
    "TRADEMARK_RENEWAL": {
        "name": "商标续展",
        "category": "TRADEMARK",
        "matter_kw": ["商标续展"],
        "fname_kw": ["（续展）", "(续展)"],
        "feature_kw": ["宽展期", "专有权期限"],
    },
    "TRADEMARK_CERTIFICATE_REISSUE": {
        "name": "商标补发注册证",
        "category": "TRADEMARK",
        "matter_kw": ["补发商标注册证", "补发注册证"],
        "fname_kw": ["补证"],
        "feature_kw": ["补发注册证"],
    },
    "TRADEMARK_LICENSE_RECORDAL": {
        "name": "商标许可备案",
        "category": "TRADEMARK",
        "matter_kw": ["商标实施许可备案", "商标使用许可备案"],
        "fname_kw": ["商标服务合同（许可备案）"],
        "feature_kw": ["商标注册号", "许可人", "被许可人"],
    },
    "TRADEMARK_TRANSFER_AGENCY": {
        "name": "商标转让代理过户",
        "category": "TRADEMARK",
        "matter_kw": ["商标转让服务", "商标转让代理"],
        "fname_kw": ["商标服务合同（转让）"],
        "feature_kw": ["转让注册商标"],
    },
    "TRADEMARK_REFUSAL_REVIEW": {
        "name": "商标驳回复审",
        "category": "TRADEMARK",
        "matter_kw": ["商标的驳回复审", "驳回复审申请"],
        "fname_kw": ["驳回复审"],
        "feature_kw": ["驳回通知书号"],
    },
    "TRADEMARK_OPPOSITION": {
        "name": "商标异议申请",
        "category": "TRADEMARK",
        "matter_kw": ["商标提出异议申请", "初审公告商标异议"],
        "fname_kw": ["商标服务合同（异议）"],
        "feature_kw": ["被异议商标", "被异议人"],
    },
    "TRADEMARK_OPPOSITION_DEFENSE": {
        "name": "商标异议答辩",
        "category": "TRADEMARK",
        "matter_kw": ["商标的异议答辩", "商标异议答辩"],
        "fname_kw": ["异议答辩"],
        "feature_kw": ["答辩人", "被异议人"],
    },
    "TRADEMARK_NON_REGISTRATION_REVIEW": {
        "name": "商标不予注册复审",
        "category": "TRADEMARK",
        "matter_kw": ["不予注册复审", "不予注册决定"],
        "fname_kw": ["不予注册"],
        "feature_kw": ["不予核准通知书"],
    },
    "TRADEMARK_CANCELLATION": {
        "name": "商标撤销连续三年不使用(撤三)",
        "category": "TRADEMARK",
        "matter_kw": ["商标撤三申请", "撤三答复", "连续三年不使用"],
        "fname_kw": ["（撤三）"],
        "feature_kw": ["商标所有权人", "使用证据"],
    },
    "TRADEMARK_INVALIDATION": {
        "name": "商标无效宣告",
        "category": "TRADEMARK",
        "matter_kw": ["商标无效宣告", "注册商标无效宣告"],
        "fname_kw": ["商标无效宣告"],
        "feature_kw": ["争议商标", "裁定书"],
    },
    "TRADEMARK_PACKAGE_COMBO": {
        "name": "国内商标组合服务",
        "category": "TRADEMARK",
        "matter_kw": ["国内商标组合", "商标综合服务"],
        "fname_kw": ["商标综合", "商标组合"],
        "feature_kw": ["多项商标业务"],
    },
    # 3. 国内著作权
    "COPYRIGHT_REGISTRATION": {
        "name": "软著与作品版权登记",
        "category": "COPYRIGHT",
        "matter_kw": [
            "著作权服务",
            "著作权登记",
            "计算机软件著作权登记",
            "作品著作权登记",
            "版权登记",
            "版权服务",
        ],
        "fname_kw": ["版权服务合同", "著作权服务合同"],
        "feature_kw": ["软件全称", "著作权人", "著作权服务内容"],
    },
    "COPYRIGHT_URGENT": {
        "name": "计算机软著加急登记",
        "category": "COPYRIGHT",
        "matter_kw": ["加急软著", "软著快速出证"],
        "fname_kw": ["软著加急"],
        "feature_kw": ["加急工作日", "出证时限"],
    },
    # 4. 跨板块组合合同
    "MIXED_PATENT_TRADEMARK": {
        "name": "专利+商标综合服务",
        "category": "MIXED_IP",
        "matter_kw": ["专利+商标", "专利及商标", "专利、商标"],
        "fname_kw": ["专利+商标", "国内专利+国内商标", "（专利+商标）"],
        "feature_kw": ["专利类：", "商标类："],
    },
    "MIXED_PATENT_TRADEMARK_CHANGE": {
        "name": "专利+商标同步变更",
        "category": "MIXED_IP",
        "matter_kw": ["专利及商标变更", "专利著录变更及商标变更"],
        "fname_kw": ["变更）知识产权"],
        "feature_kw": ["变更前名址", "变更后名址"],
    },
    "MIXED_PATENT_TRADEMARK_TRANSFER": {
        "name": "专利+商标同步转让过户",
        "category": "MIXED_IP",
        "matter_kw": ["专利转让及商标转让", "商标转让+专利转让"],
        "fname_kw": ["商标转让+专利转让", "转让）知识产权"],
        "feature_kw": [],
    },
    "MIXED_PATENT_COPYRIGHT": {
        "name": "专利+著作权综合服务",
        "category": "MIXED_IP",
        "matter_kw": ["专利+著作权", "专利及著作权", "专利+版权"],
        "fname_kw": ["专利+著作权", "专利+版权"],
        "feature_kw": [],
    },
    "MIXED_TRADEMARK_COPYRIGHT": {
        "name": "商标+著作权综合服务",
        "category": "MIXED_IP",
        "matter_kw": ["商标+著作权", "商标及著作权", "商标+版权"],
        "fname_kw": ["商标+著作权", "商标+版权"],
        "feature_kw": [],
    },
    "MIXED_PATENT_TM_COPYRIGHT": {
        "name": "专利+商标+著作权三栖综合服务",
        "category": "MIXED_IP",
        "matter_kw": ["专利+商标+著作权", "专利、商标及著作权"],
        "fname_kw": ["专利+商标+著作权", "三合一"],
        "feature_kw": ["三栖代理"],
    },
    # 5. 国际专利与商标
    "INT_PATENT_PCT_STAGE": {
        "name": "PCT国际申请阶段",
        "category": "INT_IP",
        "matter_kw": ["PCT国际申请", "RO/CN", "国际检索局"],
        "fname_kw": ["PCT国际申请"],
        "feature_kw": ["国际申请费", "PCT19条"],
    },
    "INT_PATENT_NATIONAL_LUMPSUM": {
        "name": "涉外专利国家阶段(包干费版)",
        "category": "INT_IP",
        "matter_kw": ["包干制方式结算", "包干费版"],
        "fname_kw": ["(包干费版)PCT", "包干费版"],
        "feature_kw": ["包干费用", "巴黎公约"],
    },
    "INT_PATENT_NATIONAL_STAGES": {
        "name": "涉外专利国家阶段(分阶段费用版)",
        "category": "INT_IP",
        "matter_kw": ["涉外专利申请"],
        "fname_kw": ["(分阶段费用版)涉外专利申请", "分阶段费用版"],
        "feature_kw": ["申请途径", "外所律师费"],
    },
    "INT_PATENT_OA_LUMPSUM": {
        "name": "涉外专利答复复审(包干费版)",
        "category": "INT_IP",
        "matter_kw": ["涉外答复包干"],
        "fname_kw": ["(包干费版)涉外专利专利答复"],
        "feature_kw": ["OA答复", "包干收费"],
    },
    "INT_PATENT_OA_STAGES": {
        "name": "涉外专利答复复审(分阶段费用版)",
        "category": "INT_IP",
        "matter_kw": ["涉外答复分阶段"],
        "fname_kw": ["(分阶段费用版)涉外专利专利答复"],
        "feature_kw": ["IDS", "RCE"],
    },
    "INT_TRADEMARK_SINGLE_COUNTRY": {
        "name": "单一国家商标注册",
        "category": "INT_IP",
        "matter_kw": ["单一国家", "海外商标注册", "逐一国家"],
        "fname_kw": ["单一国家商标注册"],
        "feature_kw": ["目标国家", "外所官费"],
    },
    "INT_TRADEMARK_MADRID": {
        "name": "马德里商标国际注册",
        "category": "INT_IP",
        "matter_kw": ["马德里商标", "WIPO", "马德里国际注册"],
        "fname_kw": ["马德里商标注册"],
        "feature_kw": ["基础商标", "指定缔约方"],
    },
    "INT_TRADEMARK_POST_CASE": {
        "name": "国际商标后续案件服务",
        "category": "INT_IP",
        "matter_kw": ["变更等国际商标案件", "使用宣誓", "国际商标续展"],
        "fname_kw": ["变更等国际商标案件"],
        "feature_kw": ["宣誓", "后续程序"],
    },
    # 6. 科创项目与财税审计
    "PROJECT_TECH_CONSULTATION": {
        "name": "高企/贯标/专精特新咨询",
        "category": "PROJECT_AUDIT",
        "matter_kw": ["高新技术企业", "专精特新", "知识产权贯标", "技术咨询服务"],
        "fname_kw": ["技术咨询服务合同"],
        "feature_kw": ["申报辅导", "认定通过"],
    },
    "PROJECT_BUD_FUND": {
        "name": "BUD专项基金咨询",
        "category": "PROJECT_AUDIT",
        "matter_kw": ["BUD专项基金", "发展品牌", "拓展销路"],
        "fname_kw": ["BUD专项基金"],
        "feature_kw": ["香港BUD", "资助申请"],
    },
    "PROJECT_DATA_IP": {
        "name": "数据知识产权咨询",
        "category": "PROJECT_AUDIT",
        "matter_kw": ["数据知识产权", "数据登记", "数据要素"],
        "fname_kw": ["数据知识产权"],
        "feature_kw": ["数据存证", "登记证书"],
    },
    "PROJECT_SOFTWARE_ASSESSMENT": {
        "name": "双软评估认证",
        "category": "PROJECT_AUDIT",
        "matter_kw": ["双软评估", "软件企业评估", "软件产品评估"],
        "fname_kw": ["双软评估"],
        "feature_kw": ["评估证书", "软件产品"],
    },
    "PROJECT_TECH_SEARCH_TEST": {
        "name": "科技查新与软件测试",
        "category": "PROJECT_AUDIT",
        "matter_kw": ["科技查新", "软件测试", "软件登记测试"],
        "fname_kw": ["科技查新测试"],
        "feature_kw": ["查新报告", "测试报告"],
    },
    "AUDIT_REPORT_GUANGDONG": {
        "name": "专项审计报告(广东版)",
        "category": "PROJECT_AUDIT",
        "matter_kw": ["广东专项审计"],
        "fname_kw": ["广东专项审计"],
        "feature_kw": ["广东地区", "高企专项审计"],
    },
    "AUDIT_REPORT_NON_GUANGDONG": {
        "name": "专项审计报告(非广东版)",
        "category": "PROJECT_AUDIT",
        "matter_kw": ["非广东专项审计"],
        "fname_kw": ["非广东专项审计"],
        "feature_kw": ["非广东地区", "全国审计"],
    },
    # 7. 法律服务与维权诉讼
    "LEGAL_PATENT_LITIGATION": {
        "name": "专利维权诉讼专项",
        "category": "LEGAL_LITIGATION",
        "matter_kw": ["专利侵权诉讼", "侵权维权"],
        "fname_kw": ["专利侵权维权诉讼"],
        "feature_kw": ["赔偿请求"],
    },
    "LEGAL_PATENT_LIT_AND_INVALIDATION": {
        "name": "专利诉讼及无效联动",
        "category": "LEGAL_LITIGATION",
        "matter_kw": ["侵权诉讼及专利无效宣告", "诉无联动"],
        "fname_kw": ["诉讼及专利无效宣告"],
        "feature_kw": ["诉无联动"],
    },
    "LEGAL_TRADEMARK_ADMIN_LITIGATION": {
        "name": "商标行政诉讼",
        "category": "LEGAL_LITIGATION",
        "matter_kw": ["商标行政诉讼", "北京知识产权法院"],
        "fname_kw": ["商标行政诉讼"],
        "feature_kw": ["起诉状"],
    },
    "LEGAL_GENERAL_LITIGATION": {
        "name": "专项法律服务(诉讼类)",
        "category": "LEGAL_LITIGATION",
        "matter_kw": ["专项法律服务", "诉讼委托"],
        "fname_kw": ["（诉讼类）"],
        "feature_kw": ["诉讼代理费", "出庭"],
    },
    "LEGAL_NON_LITIGATION": {
        "name": "专项法律服务(非诉类/法顾)",
        "category": "LEGAL_LITIGATION",
        "matter_kw": ["非诉法律服务", "律师函", "电商投诉", "法律顾问"],
        "fname_kw": ["（非诉类）"],
        "feature_kw": ["电商投诉", "常年法顾"],
    },
    "LEGAL_MASS_ENFORCEMENT_FULL_RISK": {
        "name": "维权打假专项(全风险代理)",
        "category": "LEGAL_LITIGATION",
        "matter_kw": ["全风险代理维权打假"],
        "fname_kw": ["（全风险代理）"],
        "feature_kw": ["0前期费", "赔偿款分成"],
    },
    "LEGAL_MASS_ENFORCEMENT_BASE_SPLIT": {
        "name": "维权打假专项(基础费+分成)",
        "category": "LEGAL_LITIGATION",
        "matter_kw": ["维权打假"],
        "fname_kw": ["维权打假专项委托合同"],
        "feature_kw": ["基础服务费", "赔偿款分成"],
    },
    "LEGAL_CRIMINAL_ENFORCEMENT": {
        "name": "刑事打击维权打假专项",
        "category": "LEGAL_LITIGATION",
        "matter_kw": ["刑事打击", "公安立案", "假冒注册商标罪"],
        "fname_kw": ["刑事打击维权打假"],
        "feature_kw": ["公安机关", "刑事立案"],
    },
    "LEGAL_ADMIN_ENFORCEMENT": {
        "name": "行政查处维权打假专项",
        "category": "LEGAL_LITIGATION",
        "matter_kw": ["行政查处", "市场监督管理局", "查扣侵权产品"],
        "fname_kw": ["行政查处维权打假"],
        "feature_kw": ["市场监管", "行政投诉"],
    },
    "LEGAL_COMPREHENSIVE_ENFORCEMENT": {
        "name": "民事+行政+刑事综合打击维权",
        "category": "LEGAL_LITIGATION",
        "matter_kw": ["民事+行政+刑事", "三合一维权", "全方位维权"],
        "fname_kw": ["综合打击维权"],
        "feature_kw": ["多维打击"],
    },
    "LEGAL_STRATEGIC_PROTECTION": {
        "name": "知识产权战略保护合作",
        "category": "LEGAL_LITIGATION",
        "matter_kw": ["战略保护合作", "全方位知识产权战略"],
        "fname_kw": ["战略保护合作协议"],
        "feature_kw": ["框架保护"],
    },
    # 8. 知产交易与运营（买卖双向，不含真实企业字号）
    "TRADE_PATENT_BUY": {
        "name": "专利权转让(运营买受方)",
        "category": "IP_OPERATION",
        "matter_kw": ["甲方向乙方购买如下专利", "专利收购合同"],
        "fname_kw": ["甲方买方"],
        "feature_kw": ["资产收购"],
    },
    "TRADE_PATENT_SELL": {
        "name": "专利权转让(运营出让方)",
        "category": "IP_OPERATION",
        "matter_kw": ["购买现成专利", "专利出售合同"],
        "fname_kw": ["乙方卖方"],
        "feature_kw": ["现成专利转让"],
    },
    "TRADE_TRADEMARK_BUY": {
        "name": "商标权转让(运营买受方)",
        "category": "IP_OPERATION",
        "matter_kw": ["甲方向乙方购买如下商标", "商标收购"],
        "fname_kw": ["商标权转让合同模板（甲方买方"],
        "feature_kw": ["商标收购"],
    },
    "TRADE_TRADEMARK_SELL": {
        "name": "商标权转让(运营出让方)",
        "category": "IP_OPERATION",
        "matter_kw": ["购买现成商标", "出售商标"],
        "fname_kw": ["商标权转让合同模板（乙方卖方"],
        "feature_kw": ["商标出售", "转让总价款"],
    },
    # 9. 总委框架与保密协议（保持与历史基线 IP_GENERAL 契约一致）
    "IP_GENERAL_FRAMEWORK": {
        "name": "知识产权总委框架协议",
        "category": "IP_GENERAL",
        "matter_kw": ["总委托", "知识产权服务指示函", "年度框架", "单个事项服务关系"],
        "fname_kw": [
            "知识产权服务合同（总委框架）",
            "(框架协议)",
            "（框架协议）",
            "ip_framework",
        ],
        "feature_kw": ["指示函", "框架协议", "知识产权服务价格表"],
    },
    "IP_GENERAL_FRAMEWORK_INTL": {
        "name": "知识产权总委框架(含国际版)",
        "category": "IP_GENERAL",
        "matter_kw": ["总委托", "含国际专利"],
        "fname_kw": ["（含涉外专利版）", "含国际业务报价版"],
        "feature_kw": ["涉外价格表"],
    },
    "IP_GENERAL_NDA": {
        "name": "商业秘密保密协议(NDA)",
        "category": "IP_GENERAL",
        "matter_kw": ["保密协议", "商业秘密保密"],
        "fname_kw": ["保密协议"],
        "feature_kw": ["保密期限", "泄密责任"],
    },
    "GOVERNANCE_NUMBERING": {
        "name": "合同编号与归档制度",
        "category": "GOVERNANCE_RULE",
        "matter_kw": ["合同编号规则", "编号制度", "归档流程"],
        "fname_kw": ["合同编号制度"],
        "feature_kw": ["流水号", "编码规范"],
    },
}

# 一级分类归属字典
L1_OF = {k: v["category"] for k, v in SUBTYPES.items()}

# 78 套标准模板编号 → (category, subtype) 声明式权威映射表（O(1) 查表，消除高复杂度分支）
TEMPLATE_CODE_SUBTYPE_MAP: dict[str, tuple[str, str]] = {
    # 01_国内专利 (24)
    "MB20251205014": ("PATENT", "PATENT_APPLICATION"),
    "MB20251209001": ("PATENT", "PATENT_APPLICATION_RISK"),
    "MB20251205012": ("PATENT", "PATENT_FAST_PRESCREEN"),
    "MB20251205018": ("PATENT", "PATENT_PRIORITY_REVIEW"),
    "MB20251205021": ("PATENT", "PATENT_REEXAMINATION"),
    "MB20251205013": ("PATENT", "PATENT_ANNUAL_FEE"),
    "MB20251205019": ("PATENT", "PATENT_BIBLIO_CHANGE"),
    "MB20251205027": ("PATENT", "PATENT_TRANSFER"),
    "MB20251205045": ("IP_OPERATION", "TRADE_PATENT_BUY"),
    "MB20251205046": ("IP_OPERATION", "TRADE_PATENT_SELL"),
    "MB20251205015": ("PATENT", "PATENT_LICENSE_RECORDAL"),
    "MB20251205016": ("PATENT", "PATENT_LICENSE_CHANGE_CANCEL"),
    "MB20251205017": ("PATENT", "PATENT_DOCUMENT_COPY"),
    "MB20251205024": ("PATENT", "PATENT_EVALUATION_REPORT"),
    "MB20251205020": ("HIGH_END_CONSULTING", "PATENT_NAVIGATION_REPORT"),
    "MB20251205022": ("HIGH_END_CONSULTING", "PATENT_SEARCH_REPORT"),
    "MB20251205023": ("HIGH_END_CONSULTING", "PATENT_DUE_DILIGENCE_REPORT"),
    "MB20251205025": ("HIGH_END_CONSULTING", "PATENT_INFRINGEMENT_REPORT"),
    "MB20251229002": ("HIGH_END_CONSULTING", "PATENT_FTO_REPORT"),
    "MB20251205026": ("HIGH_END_CONSULTING", "PATENT_STABILITY_REPORT"),
    "MB20251205028": ("HIGH_END_CONSULTING", "PATENT_CONSULTANT"),
    "MB20251205029": ("HIGH_END_CONSULTING", "PATENT_INVALIDATION"),
    "MB20251205042": ("PATENT", "PATENT_LAYOUT_DESIGN"),
    "MB20251205086": ("PATENT", "PATENT_PACKAGE_COMBO"),
    # 02_国内商标 (17)
    "MB20251205057": ("TRADEMARK", "TRADEMARK_REGISTRATION"),
    "MB20251205059": ("TRADEMARK", "TRADEMARK_CONSULTATION"),
    "MB20251205047": ("TRADEMARK", "TRADEMARK_CHANGE"),
    "MB20260729001": ("TRADEMARK", "TRADEMARK_CHANGE_WORD_ONLY"),
    "MB20251205058": ("TRADEMARK", "TRADEMARK_TRANSFER_AGENCY"),
    "MB20251205043": ("IP_OPERATION", "TRADE_TRADEMARK_BUY"),
    "MB20251205044": ("IP_OPERATION", "TRADE_TRADEMARK_SELL"),
    "MB20251205054": ("TRADEMARK", "TRADEMARK_RENEWAL"),
    "MB20251205049": ("TRADEMARK", "TRADEMARK_CERTIFICATE_REISSUE"),
    "MB20251205053": ("TRADEMARK", "TRADEMARK_LICENSE_RECORDAL"),
    "MB20251205048": ("TRADEMARK", "TRADEMARK_REFUSAL_REVIEW"),
    "MB20251205055": ("TRADEMARK", "TRADEMARK_OPPOSITION"),
    "MB20251205056": ("TRADEMARK", "TRADEMARK_OPPOSITION_DEFENSE"),
    "MB20251205050": ("TRADEMARK", "TRADEMARK_NON_REGISTRATION_REVIEW"),
    "MB20251205051": ("TRADEMARK", "TRADEMARK_CANCELLATION"),
    "MB20251205052": ("TRADEMARK", "TRADEMARK_INVALIDATION"),
    "MB20251205085": ("TRADEMARK", "TRADEMARK_PACKAGE_COMBO"),
    # 03_国内著作权 (2)
    "MB20251205087": ("COPYRIGHT", "COPYRIGHT_REGISTRATION"),
    "MB20251205088": ("COPYRIGHT", "COPYRIGHT_URGENT"),
    # 04_跨板块组合 (6)
    "MB20251205069": ("MIXED_IP", "MIXED_PATENT_TRADEMARK"),
    "MB20251205072": ("MIXED_IP", "MIXED_PATENT_TRADEMARK_CHANGE"),
    "MB20251205068": ("MIXED_IP", "MIXED_PATENT_TRADEMARK_TRANSFER"),
    "MB20251205078": ("MIXED_IP", "MIXED_PATENT_COPYRIGHT"),
    "MB20251205065": ("MIXED_IP", "MIXED_TRADEMARK_COPYRIGHT"),
    "MB20251205074": ("MIXED_IP", "MIXED_PATENT_TM_COPYRIGHT"),
    # 05_国际专利与商标 (8)
    "MB20251205041": ("INT_IP", "INT_PATENT_PCT_STAGE"),
    "MB20251205037": ("INT_IP", "INT_PATENT_NATIONAL_LUMPSUM"),
    "MB20251205039": ("INT_IP", "INT_PATENT_NATIONAL_STAGES"),
    "MB20251205038": ("INT_IP", "INT_PATENT_OA_LUMPSUM"),
    "MB20251205040": ("INT_IP", "INT_PATENT_OA_STAGES"),
    "MB20251205035": ("INT_IP", "INT_TRADEMARK_SINGLE_COUNTRY"),
    "MB20251205036": ("INT_IP", "INT_TRADEMARK_MADRID"),
    "MB20251205030": ("INT_IP", "INT_TRADEMARK_POST_CASE"),
    # 06_科创项目与财税审计 (7)
    "MB20251205061": ("PROJECT_AUDIT", "AUDIT_REPORT_GUANGDONG"),
    "MB20251205062": ("PROJECT_AUDIT", "AUDIT_REPORT_NON_GUANGDONG"),
    "MB20251205063": ("PROJECT_AUDIT", "PROJECT_TECH_SEARCH_TEST"),
    "MB20251205064": ("PROJECT_AUDIT", "PROJECT_SOFTWARE_ASSESSMENT"),
    "MB20251205080": ("PROJECT_AUDIT", "PROJECT_BUD_FUND"),
    "MB20251205082": ("PROJECT_AUDIT", "PROJECT_TECH_CONSULTATION"),
    "MB20251205084": ("PROJECT_AUDIT", "PROJECT_DATA_IP"),
    # 07_法律服务与维权诉讼 (11)
    "MB20251205071": ("LEGAL_LITIGATION", "LEGAL_PATENT_LITIGATION"),
    "MB20251205066": ("LEGAL_LITIGATION", "LEGAL_PATENT_LIT_AND_INVALIDATION"),
    "MB20251205067": ("LEGAL_LITIGATION", "LEGAL_TRADEMARK_ADMIN_LITIGATION"),
    "MB20251205075": ("LEGAL_LITIGATION", "LEGAL_GENERAL_LITIGATION"),
    "MB20251205073": ("LEGAL_LITIGATION", "LEGAL_NON_LITIGATION"),
    "MB20251205076": ("LEGAL_LITIGATION", "LEGAL_MASS_ENFORCEMENT_FULL_RISK"),
    "MB20251205077": ("LEGAL_LITIGATION", "LEGAL_MASS_ENFORCEMENT_BASE_SPLIT"),
    "MB20251205079": ("LEGAL_LITIGATION", "LEGAL_CRIMINAL_ENFORCEMENT"),
    "MB20251205081": ("LEGAL_LITIGATION", "LEGAL_ADMIN_ENFORCEMENT"),
    "MB20251205083": ("LEGAL_LITIGATION", "LEGAL_COMPREHENSIVE_ENFORCEMENT"),
    "MB20251205070": ("LEGAL_LITIGATION", "LEGAL_STRATEGIC_PROTECTION"),
    # 08_总委框架与保密协议 (3)
    "MB20251205089": ("IP_GENERAL", "IP_GENERAL_FRAMEWORK"),
    "MB20251225003": ("IP_GENERAL", "IP_GENERAL_FRAMEWORK_INTL"),
    "MB20251205011": ("IP_GENERAL", "IP_GENERAL_NDA"),
}


@dataclass
class Classification:
    template_id: str
    category: str = ""
    subtype: str = ""
    subtype_name: str = ""
    classification_evidence: list = field(default_factory=list)
    classification_confidence: float = 0.0
    classification_conflicts: list = field(default_factory=list)
    review_status: str = "APPROVED"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _score_subtype_features(
    filename: str,
    title: str,
    subtitle: str,
    matter_text: str,
    table_headers: list[str],
    full_text: str,
) -> tuple[dict[str, float], dict[str, list[str]]]:
    """对所有候选子类按结构化文本特征打分，纯函数无副作用。"""
    scores: dict[str, float] = {}
    evidence: dict[str, list[str]] = {}
    joined_headers = " ".join(table_headers)

    def add(s_name: str, weight: float, ev: str) -> None:
        scores[s_name] = scores.get(s_name, 0.0) + weight
        evidence.setdefault(s_name, []).append(ev)

    for sub_code, spec in SUBTYPES.items():
        for kw in spec.get("matter_kw", []):
            if kw in matter_text:
                add(sub_code, 5.0, f"第一条合同事项含关键词『{kw}』")
            if kw in title or kw in subtitle:
                add(sub_code, 3.5, f"文档标题含『{kw}』")
        for kw in spec.get("fname_kw", []):
            if kw in filename:
                add(sub_code, 2.5, f"文件名含『{kw}』")
            clean_kw = re.sub(r"[（）()【】\[\]\s]", "", kw)
            if clean_kw and (clean_kw in subtitle or clean_kw in title):
                add(sub_code, 6.0, f"文档标题/副标题显式包含业务标识『{clean_kw}』")
        sname = spec.get("name", "")
        if sname and (sname in title or sname in subtitle):
            add(sub_code, 5.0, f"文档标题直接命中子类名称『{sname}』")
        for kw in spec.get("feature_kw", []):
            if kw in full_text:
                add(sub_code, 2.0, f"特征条款含『{kw}』")
            if kw in joined_headers:
                add(sub_code, 2.5, f"表格列头含『{kw}』")
    return scores, evidence


def _classify_by_template_code(
    document_id: str, tcode: str, base_evidence: list[str]
) -> Classification:
    """命中权威 5 级模板编号时的确定性分类。"""
    rule = TEMPLATE_5TH_LAYER_RULES[tcode]
    leaf = _TPL_LEAF_MAP.get(tcode, {})
    cat, sub = TEMPLATE_CODE_SUBTYPE_MAP.get(tcode, ("PATENT", "PATENT_PACKAGE_COMBO"))
    l1_name = str(leaf.get("l1", CATEGORY_NAMES.get(cat, cat)))
    l2_name = str(leaf.get("l2", SUBTYPES.get(sub, {}).get("name", sub)))
    l3_name = str(leaf.get("l3", rule.get("sub_scene", sub)))
    evs = list(base_evidence) + [
        f"【五级知识图谱权威路由】{l1_name} → {l2_name} → {l3_name}",
        f"【核心排他决策红线】{rule['disambiguation_rule']}",
        f"【绑定新系统产品】共 {len(rule['new_products'])} 项产品编码",
    ]
    return Classification(
        template_id=document_id,
        category=cat,
        subtype=sub,
        subtype_name=str(SUBTYPES.get(sub, {}).get("name", l2_name)),
        classification_evidence=evs,
        classification_confidence=0.99,
        classification_conflicts=[],
        review_status="APPROVED",
    )


def classify(
    document_id: str,
    filename: str,
    title: str,
    subtitle: str,
    matter_text: str,
    table_headers: list[str],
    full_text: str,
    storage: Any = None,
    tenant_id: str | None = None,
) -> Classification:
    """对单份合同/模板分类。不隐式创建全局数据库连接，保证纯函数可复现性。"""
    if "合同编号制度" in filename or "合同编号制度" in title:
        return Classification(
            template_id=document_id,
            category="GOVERNANCE_RULE",
            subtype="GOVERNANCE_NUMBERING",
            subtype_name="合同编号与归档管理制度",
            classification_evidence=["匹配到合同编号与归档管理规范文件"],
            classification_confidence=1.0,
            classification_conflicts=[],
            review_status="APPROVED",
        )

    matched_evs: list[str] = []
    if matter_text:
        first_line = matter_text.strip().split("\n")[0][:40]
        matched_evs.append(f"第一条事项正文摘要：{first_line}")
    if table_headers:
        top_cols = "、".join(c.replace("\n", " ") for c in table_headers[:4])
        matched_evs.append(f"表格特征列头：{top_cols}")

    tcode_match = re.search(r"MB\d{11}", filename) or re.search(
        r"MB\d{11}", document_id
    )
    tcode = tcode_match.group(0) if tcode_match else None
    if tcode and tcode in TEMPLATE_5TH_LAYER_RULES:
        return _classify_by_template_code(document_id, tcode, matched_evs)

    scores, evidence = _score_subtype_features(
        filename, title, subtitle, matter_text, table_headers, full_text
    )

    # 可选注入存储端口的 RAG 增强（严格经统一存储接口，绝不直接访问 .conn 或隐式打开全局 DB）
    if storage is not None:
        clean_seed = re.sub(
            r"\{\{.*?\}\}|\[.*?\]", " ", f"{title} {subtitle} {matter_text[:200]}"
        )
        query_seed = " ".join(clean_seed.split())[:120]
        if query_seed and hasattr(storage, "search"):
            hits = storage.search(query_seed, top_k=3, rerank=True, tenant_id=tenant_id)
            for h in hits:
                sub = h.get("contract_subtype")
                if sub in SUBTYPES:
                    scores[sub] = scores.get(sub, 0.0) + 4.5
                    evidence.setdefault(sub, []).append(
                        f"RAG语义检索命中母本 {h['document_id']} ({SUBTYPES[sub]['name']})"
                    )

    if not scores:
        return Classification(
            document_id,
            "UNKNOWN",
            "UNCLASSIFIED",
            "未分类",
            ["所有特征匹配均未命中"],
            0.0,
            [],
            "NEEDS_MANUAL_REVIEW",
        )

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    best, best_score = ranked[0]
    second_sub, second_score = ranked[1] if len(ranked) > 1 else ("", 0.0)
    top3_total = sum(v for _, v in ranked[:3]) or 1.0
    margin = (best_score - second_score) / max(best_score, 1.0)
    raw_conf = (best_score / top3_total) * 0.72 + margin * 0.28
    if best_score < 4.0:
        raw_conf = min(raw_conf, 0.55)
    conf = round(min(0.99, raw_conf), 3)

    conflicts = []
    if second_score > 0 and (best_score - second_score) / max(best_score, 1.0) < 0.12:
        conflicts.append(
            {
                "candidate": second_sub,
                "candidate_score": second_score,
                "best_score": best_score,
            }
        )

    best_cat = str(SUBTYPES[best].get("category", "PATENT"))
    return Classification(
        template_id=document_id,
        category=best_cat,
        subtype=best,
        subtype_name=str(SUBTYPES[best]["name"]),
        classification_evidence=evidence.get(best, []) or matched_evs,
        classification_confidence=conf,
        classification_conflicts=conflicts,
        review_status="APPROVED"
        if conf >= 0.7 and not conflicts
        else "NEEDS_MANUAL_REVIEW",
    )
