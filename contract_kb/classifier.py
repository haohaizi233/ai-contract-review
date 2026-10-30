# -*- coding: utf-8 -*-
"""合同分类器：分层分类（PATENT / TRADEMARK / IP_GENERAL + 二级业务子类）。

分类证据来源（按权重）：
1. 第一条合同事项正文（最强）
2. 文档标题/副标题
3. 业务特征字段（表格列头）
4. 特征条款（预审承诺、年费期限、超权费等）
5. 文件名（最弱，仅作辅助）

标题与正文矛盾时输出 classification_conflicts，不直接信任文件名。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

# 二级分类关键词字典：key=子类代码, value=(中文名, [正文关键词], [文件名关键词], [特征列头/条款关键词])
SUBTYPES: dict[str, dict] = {
    "PATENT_APPLICATION": {
        "name": "专利申请", "matter_kw": ["专利申请", "申请文件初稿", "技术交底书"],
        "fname_kw": ["（申请）", "(申请)"], "feature_kw": ["超权费", "实审阶段", "费减"],
    },
    "PATENT_BIBLIO_CHANGE": {
        "name": "著录项目变更", "matter_kw": ["著录项目变更"], "fname_kw": ["著录项目变更"], "feature_kw": ["变更事项"],
    },
    "PATENT_TRANSFER": {
        "name": "专利转让", "matter_kw": ["专利转让事宜", "转让人名称"], "fname_kw": ["（转让）"], "feature_kw": ["受让人名称"],
    },
    "PATENT_LICENSE_RECORDAL": {
        "name": "专利实施许可备案", "matter_kw": ["专利实施许可备案事宜", "许可人名称"], "fname_kw": ["许可备案）"], "feature_kw": ["被许可人名称"],
    },
    "PATENT_LICENSE_CHANGE_CANCEL": {
        "name": "专利许可备案变更注销", "matter_kw": ["许可备案变更", "许可备案注销"], "fname_kw": ["许可备案注销"], "feature_kw": ["备案号"],
    },
    "PATENT_ANNUAL_FEE": {
        "name": "专利年费缴纳", "matter_kw": ["年费代缴", "年费期限"], "fname_kw": ["年费缴纳"], "feature_kw": ["第    年专利年费", "年费期限"],
    },
    "PATENT_REEXAMINATION": {
        "name": "专利复审", "matter_kw": ["专利驳回复审", "专利复审"], "fname_kw": ["（专利复审）"], "feature_kw": [],
    },
    "PATENT_PRIORITY_REVIEW": {
        "name": "专利优先审查", "matter_kw": ["优先审查服务事项"], "fname_kw": ["优先审查"], "feature_kw": ["专利状态"],
    },
    "PATENT_FAST_PRESCREEN": {
        "name": "专利快速预审", "matter_kw": ["快速预审服务事项"], "fname_kw": ["快速预审"], "feature_kw": ["预审历史记录"],
    },
    "PATENT_DOCUMENT_COPY": {
        "name": "专利文件副本", "matter_kw": ["文件副本", "在先申请文件副本", "专利登记簿副本"], "fname_kw": ["优先权等副本"], "feature_kw": [],
    },
    "PATENT_SEARCH_REPORT": {
        "name": "专利检索报告", "matter_kw": ["专利检索报告"], "fname_kw": ["（专利检索报告）"], "feature_kw": [],
    },
    "PATENT_EVALUATION_REPORT": {
        "name": "专利评价报告", "matter_kw": ["专利评价报告"], "fname_kw": ["（专利评价报告）"], "feature_kw": [],
    },
    "PATENT_STABILITY_REPORT": {
        "name": "专利稳定性分析报告", "matter_kw": ["评估专利稳定性", "稳定性分析报告"], "fname_kw": ["稳定性分析报告"], "feature_kw": ["公开号"],
    },
    "PATENT_INFRINGEMENT_REPORT": {
        "name": "专利侵权分析报告", "matter_kw": ["侵权比对分析报告", "侵权分析"], "fname_kw": ["专利侵权分析"], "feature_kw": ["比对"],
    },
    "PATENT_NAVIGATION_REPORT": {
        "name": "专利导航分析报告", "matter_kw": ["专利导航分析报告"], "fname_kw": ["专利导航分析报告"], "feature_kw": [],
    },
    "PATENT_DUE_DILIGENCE_REPORT": {
        "name": "专利尽职调查报告", "matter_kw": ["专利尽职调查报告", "尽职调查"], "fname_kw": ["尽职调查报告"], "feature_kw": ["目标市场"],
    },
    "TRADEMARK_REGISTRATION": {
        "name": "商标注册", "matter_kw": ["商标注册服务事宜", "商标注册申请单"], "fname_kw": ["（注册）"], "feature_kw": ["商标图样/文字", "商标字体"],
    },
    "TRADEMARK_CHANGE": {
        "name": "商标变更", "matter_kw": ["商标变更名称地址", "商标变更"], "fname_kw": ["商标服务合同（变更）"], "feature_kw": ["变更前申请人", "变更后申请人"],
    },
    "TRADEMARK_TRANSFER": {
        "name": "商标转让", "matter_kw": ["商标转让服务", "商标转让"], "fname_kw": ["商标服务合同（转让）"], "feature_kw": ["转让人名称"],
    },
    "TRADEMARK_RENEWAL": {
        "name": "商标续展", "matter_kw": ["商标续展"], "fname_kw": ["（续展）"], "feature_kw": ["宽展期", "专有权期限"],
    },
    "TRADEMARK_LICENSE_RECORDAL": {
        "name": "商标许可备案", "matter_kw": ["商标实施许可备案"], "fname_kw": ["商标服务合同（许可备案）"], "feature_kw": ["商标注册号"],
    },
    "TRADEMARK_OPPOSITION": {
        "name": "商标异议申请", "matter_kw": ["商标提出异议申请", "被异议商标"], "fname_kw": ["商标服务合同（异议）"], "feature_kw": ["被异议人"],
    },
    "TRADEMARK_OPPOSITION_DEFENSE": {
        "name": "商标异议答辩", "matter_kw": ["商标的异议答辩"], "fname_kw": ["异议答辩"], "feature_kw": ["答辩人"],
    },
    "TRADEMARK_REFUSAL_REVIEW": {
        "name": "商标驳回复审", "matter_kw": ["商标的驳回复审"], "fname_kw": ["驳回复审"], "feature_kw": ["申请商标"],
    },
    "TRADEMARK_CANCELLATION": {
        "name": "商标撤三", "matter_kw": ["商标撤三申请", "撤三答复"], "fname_kw": ["（撤三）"], "feature_kw": ["商标所有权人"],
    },
    "IP_GENERAL_FRAMEWORK": {
        "name": "综合知识产权服务框架协议", "matter_kw": ["知识产权服务指示函", "服务指示", "知识产权服务价格表"],
        "fname_kw": ["知识产权服务合同"], "feature_kw": ["指示函", "服务编号"],
    },
}

# 一级分类归属
L1_OF = {k: ("IP_GENERAL" if k.startswith("IP_GENERAL") else
             "PATENT" if k.startswith("PATENT") else "TRADEMARK") for k in SUBTYPES}

TITLE_CONFLICT_RULES = [
    # (文件名片段, 正文标题片段, 说明)
    ("专利侵权分析", "专利分析报告", "文件名为‘专利侵权分析’，正文标题为‘(专利分析报告)’，正文事项为侵权比对分析"),
    ("仅优先审查", "（优先审查）", "文件名‘仅优先审查’，正文标题‘(优先审查)’，措辞差异不影响业务归属"),
    ("优先权等副本", "文件副本", "文件名‘优先权等副本’，正文标题‘(文件副本)’"),
    ("撤三", "撤三/撤三答复", "文件名‘撤三’，正文标题含撤三答复两种业务"),
]


@dataclass
class Classification:
    template_id: str
    category: str = ""
    subtype: str = ""
    subtype_name: str = ""
    classification_evidence: list = field(default_factory=list)
    classification_confidence: float = 0.0
    classification_conflicts: list = field(default_factory=list)
    review_status: str = "PENDING_REVIEW"

    def to_dict(self):
        return asdict(self)


def classify(document_id: str, filename: str, title: str, subtitle: str,
             matter_text: str, table_headers: list[str], full_text: str) -> Classification:
    """对单份模板分类。matter_text = 第一条合同事项正文（含表格列头）。"""
    scores: dict[str, float] = {}
    evidence: dict[str, list] = {}

    def add(sub: str, w: float, ev: str):
        scores[sub] = scores.get(sub, 0.0) + w
        evidence.setdefault(sub, []).append(ev)

    for sub, spec in SUBTYPES.items():
        for kw in spec["matter_kw"]:
            if kw in matter_text:
                add(sub, 5.0, f"第一条合同事项含关键词『{kw}』")
        for kw in spec["fname_kw"]:
            if kw in filename:
                add(sub, 1.5, f"文件名含『{kw}』(低权重)")
        for kw in spec["feature_kw"]:
            if kw in full_text:
                add(sub, 2.0, f"特征条款/字段含『{kw}』")
        for kw in spec["matter_kw"]:
            if kw in title or kw in subtitle:
                add(sub, 3.0, f"文档标题含『{kw}』")
        # 表头特征
        joined = " ".join(table_headers)
        for kw in spec["feature_kw"]:
            if kw in joined:
                add(sub, 2.5, f"表格列头含『{kw}』")

    if not scores:
        return Classification(document_id, "UNKNOWN", "UNCLASSIFIED", "未分类",
                              ["所有特征匹配均未命中"], 0.0, [], "NEEDS_MANUAL_REVIEW")

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    best, best_score = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    total = sum(scores.values()) or 1.0
    conf = round(min(0.99, best_score / total + (best_score - second) / max(best_score, 1) * 0.3), 3)

    cls = Classification(document_id, L1_OF[best], best, SUBTYPES[best]["name"],
                         evidence.get(best, []), conf)
    if len(ranked) > 1 and second > 0 and best_score < second * 1.5:
        cls.classification_conflicts.append(
            {"type": "AMBIGUOUS_SUBTYPE", "candidates": [best, ranked[1][0]],
             "scores": [best_score, second], "note": "次优候选得分接近，需人工确认"})
    if conf < 0.85:
        cls.classification_conflicts.append(
            {"type": "LOW_CONFIDENCE", "confidence": conf,
             "note": "分类置信度低于 0.85（多业务关键词并存的综合合同常见），需人工确认"})

    # 文件名与正文标题一致性检测：文件名关键词未出现在文档标题/副标题 → 标记
    title_all = (title or "") + " " + (subtitle or "")
    for fn_kw, title_kw, note in TITLE_CONFLICT_RULES:
        if fn_kw in filename and fn_kw not in title_all and title_kw not in filename:
            cls.classification_conflicts.append(
                {"type": "FILENAME_TITLE_MISMATCH", "file_name_token": fn_kw,
                 "expected_title_token": title_kw,
                 "actual_title": (subtitle or title or "")[:40], "note": note})

    return cls
