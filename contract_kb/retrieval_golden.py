# -*- coding: utf-8 -*-
"""黄金检索集（Phase 2）：按条款 ID 判定 + 困难负样本。

设计：
- 每条查询标注 expected_clause_keys（业务语义键：类别+关键词组合），判定改为"检索到的条款
  是否命中预期条款"而非仅大类标签；
- 困难负样本注入索引：
    HN1 同合同类型不同期限（年费第3年 vs 第6年）
    HN2 同条款类别不同金额（1000元 vs 2000元）
    HN3 商标合同与专利合同相似措辞条款
    HN4 已过期模板版本（status=SUPERSEDED，应被生效版本过滤排除）
    HN5 相似措辞但法律义务不同（"不予退还" vs "应退还"）
- 指标：Hit@K / Recall@K / MRR / NDCG@K / HardNegativeAvoidance（负样本未进 Top-K 比例）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class GoldenQuery:
    qid: str
    query: str
    expected_category: str
    expected_keywords: list[str]  # 命中预期条款需含关键词
    avoid_keywords: list[str] = field(default_factory=list)  # 困难负样本特征词
    note: str = ""


# 黄金查询集（含困难负样本场景）
GOLDEN_QUERIES: list[GoldenQuery] = [
    GoldenQuery(
        "Q01",
        "逾期付款违约金的计算标准",
        "LIABILITY",
        ["违约金", "3‰"],
        avoid_keywords=["1‰"],
        note="HN2/HN5：不同比率、相似措辞",
    ),
    GoldenQuery(
        "Q02",
        "甲方单方解除合同费用退不退",
        "LIABILITY",
        ["不予返还", "退还甲方已支付"],
        avoid_keywords=["应退还甲方全部"],
        note="HN5：相似措辞不同义务",
    ),
    GoldenQuery(
        "Q03",
        "争议由哪个法院管辖",
        "DISPUTE_RESOLUTION",
        ["人民法院", "诉讼"],
        avoid_keywords=["仲裁委员会"],
        note="HN5：诉讼 vs 仲裁",
    ),
    GoldenQuery(
        "Q04",
        "服务费发票开什么税率",
        "FEE_AND_PAYMENT",
        ["增值税专用发票", "6%"],
        avoid_keywords=["13%"],
        note="HN2：不同税率",
    ),
    GoldenQuery(
        "Q05",
        "合同费用总额是多少",
        "FEE_AND_PAYMENT",
        ["圆整", "¥"],
        avoid_keywords=["2,000"],
        note="HN2：不同金额",
    ),
    GoldenQuery(
        "Q06",
        "付款期限是多久",
        "FEE_AND_PAYMENT",
        ["工作日内", "支付"],
        avoid_keywords=["三十个工作日"],
        note="HN1：不同期限",
    ),
    GoldenQuery(
        "Q07",
        "保密义务怎么约定",
        "CONFIDENTIALITY",
        ["保密义务", "商业秘密"],
        avoid_keywords=[],
        note="基础查询",
    ),
    GoldenQuery(
        "Q08",
        "合同何时生效",
        "EFFECTIVENESS_AND_TERMINATION",
        ["签字或盖章之日起生效"],
        avoid_keywords=[],
        note="基础查询",
    ),
    GoldenQuery(
        "Q09",
        "商标注册风险由谁承担",
        "PARTY_A_OBLIGATIONS",
        ["风险", "显著性"],
        avoid_keywords=["专利"],
        note="HN3：商标 vs 专利相似措辞",
    ),
    GoldenQuery(
        "Q10",
        "专利年费代缴服务范围",
        "CONTRACT_SUBJECT",
        ["年费"],
        avoid_keywords=["转让人", "受让人"],
        note="HN3：专利业务间区分",
    ),
    GoldenQuery(
        "Q11",
        "乙方账户信息",
        "FEE_AND_PAYMENT",
        ["账号", "开户行"],
        avoid_keywords=[],
        note="基础查询",
    ),
    GoldenQuery(
        "Q12",
        "甲方中途取消怎么收费",
        "LIABILITY",
        ["取消", "服务费"],
        avoid_keywords=[],
        note="取消阶梯条款",
    ),
    GoldenQuery(
        "Q13",
        "合同编号写在哪里",
        "FRONT_MATTER",
        ["合同编号"],
        avoid_keywords=[],
        note="首部字段定位",
    ),
    GoldenQuery(
        "Q14",
        "违约赔偿包括哪些费用",
        "LIABILITY",
        ["律师费", "保全费"],
        avoid_keywords=[],
        note="追偿范围",
    ),
    GoldenQuery(
        "Q15",
        "投诉渠道是什么",
        "EFFECTIVENESS_AND_TERMINATION",
        ["投诉"],
        avoid_keywords=[],
        note="投诉条款",
    ),
]

# 明确命名版本：15条困难负样本基准查询集
BENCHMARK_15_HARD_NEGATIVE: list[GoldenQuery] = GOLDEN_QUERIES


@dataclass
class GoldenDoc:
    """注入索引的干扰/负样本文档。"""

    doc_id: str
    subtype: str
    category: str
    clause_category: str
    text: str
    hard_negative_type: str
    status: str = "ACTIVE"


# 困难负样本条款（与真实条款高度相似但关键参数/义务不同）
HARD_NEGATIVES: list[GoldenDoc] = [
    GoldenDoc(
        "hn1_deadline",
        "PATENT_ANNUAL_FEE",
        "PATENT",
        "FEE_AND_PAYMENT",
        "4.2 甲方应自合同生效之日起三十个工作日内向乙方支付上述全部费用。",
        "HN1_不同期限",
    ),
    GoldenDoc(
        "hn2_amount",
        "PATENT_ANNUAL_FEE",
        "PATENT",
        "FEE_AND_PAYMENT",
        "4.1 甲方向乙方支付费用总额为人民币 贰仟圆整（¥2,000 元），含代收代付官费。",
        "HN2_不同金额",
    ),
    GoldenDoc(
        "hn2_rate",
        "PATENT_ANNUAL_FEE",
        "PATENT",
        "LIABILITY",
        "6.2 逾期支付的甲方应按逾期付款金额日率1‰向乙方支付违约金。",
        "HN2_不同比率",
    ),
    GoldenDoc(
        "hn3_tm_patent",
        "TRADEMARK_REGISTRATION",
        "TRADEMARK",
        "PARTY_A_OBLIGATIONS",
        "3.4 乙方已事先告知甲方相关专利注册的风险（包括但不限于该专利可能存在在先近似、"
        "缺乏显著性等），后果由甲方自行承担。",
        "HN3_商专近似措辞",
    ),
    GoldenDoc(
        "hn4_expired",
        "PATENT_ANNUAL_FEE",
        "PATENT",
        "LIABILITY",
        "6.1 非因乙方过错，甲方单方解除与乙方的服务关系，甲方支付给乙方的费用应退还甲方全部。",
        "HN5_义务相反",
        status="SUPERSEDED",
    ),
    GoldenDoc(
        "hn5_obligation",
        "PATENT_APPLICATION",
        "PATENT",
        "LIABILITY",
        "6.1 甲方单方解除合同的，乙方应退还甲方已支付的全部费用。",
        "HN5_义务相反",
    ),
    GoldenDoc(
        "hn_arbitration",
        "PATENT_APPLICATION",
        "PATENT",
        "DISPUTE_RESOLUTION",
        "第七条 争议解决 本合同争议提交仲裁委员会仲裁解决。",
        "HN5_诉讼vs仲裁",
    ),
    GoldenDoc(
        "hn3_patent_tm",
        "PATENT_SEARCH_REPORT",
        "PATENT",
        "PARTY_A_OBLIGATIONS",
        "3.4 乙方已事先告知甲方本商标注册的相关风险（包括但不限于该商标可能存在在先近似商标等），"
        "后果由甲方自行承担。",
        "HN3_专商近似措辞",
    ),
]


def ndcg_at_k(grades: list[int], k: int) -> float:
    dcg = sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(grades[:k]))
    ideal = sorted(grades, reverse=True)
    idcg = sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(ideal[:k])) or 1.0
    return dcg / idcg


def judge_hits(query: GoldenQuery, hits: list[dict]) -> dict:
    """逐命中分级：2=类别+关键词全中；1=部分；0=无关；-1=困难负样本。"""
    grades, neg_ranks, rel_ranks = [], [], []
    for i, h in enumerate(hits, 1):
        text = h.get("text", "")
        cat_ok = (
            h.get("business_category") == query.expected_category
            or h.get("clause_category") == query.expected_category
        )
        kw_hit = [k for k in query.expected_keywords if k in text]
        is_neg = any(k in text for k in query.avoid_keywords)
        strong = len(kw_hit) == len(query.expected_keywords)
        if is_neg and not strong:
            # 困难负样本：命中避让特征且未完全命中预期关键词 → 判负样本
            grades.append(-1)
            neg_ranks.append(i)
        elif strong:
            grades.append(2)
            rel_ranks.append(i)
        elif kw_hit or cat_ok:
            grades.append(1)
            rel_ranks.append(i)
        else:
            grades.append(0)
    nonneg = [max(g, 0) for g in grades]
    return {
        "grades": grades,
        "first_relevant": rel_ranks[0] if rel_ranks else None,
        "neg_ranks": neg_ranks,
        "ndcg": ndcg_at_k(nonneg, len(grades) or 1),
        "avoided_hard_negatives": len(neg_ranks) == 0,
    }
