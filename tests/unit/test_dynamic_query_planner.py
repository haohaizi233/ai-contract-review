# -*- coding: utf-8 -*-
"""Unit tests for Contract-Aware Dynamic Query Planner (Phase 12-B)."""

from __future__ import annotations

import time
import pytest

from contract_kb.modules.retrieval.query_planner import (
    ContractAwareQueryPlanner,
    QueryPlanningBudget,
    RetrievalIntent,
)


def test_deterministic_query_planning_sample_clause():
    """验证示例待审条款的意图拆解与槽位提取：
    输入：甲方逾期付款超过30日，乙方有权单方解除合同，但应提前5日书面通知甲方。
    """
    planner = ContractAwareQueryPlanner()
    clause = {
        "clause_id": "c_sample_01",
        "clause_number": "5.2",
        "article_title": "第五条 合同解除与终止",
        "clause_category": "LIABILITY",
        "clause_subcategory": "CANCEL_REFUND",
        "original_text": "甲方逾期付款超过30日，乙方有权单方解除合同，但应提前5日书面通知甲方。",
        "conditions": ["甲方逾期付款超过30日"],
        "exceptions": ["提前5日书面通知甲方"],
        "source_node_ids": ["n101", "n102"],
    }

    intents = planner.plan_clause_intents_deterministic(
        clause,
        contract_category="PATENT",
        contract_subtype="PATENT_APPLICATION",
    )

    assert len(intents) >= 1
    intent = intents[0]
    assert isinstance(intent, RetrievalIntent)
    assert intent.source_clause_id == "c_sample_01"
    assert intent.intent_type == "TERMINATION_RIGHT_CHECK"
    assert intent.legal_subject in ("甲方", "乙方")
    assert intent.action == "单方解除合同"
    assert intent.conditions == ["甲方逾期付款超过30日"]
    assert intent.exceptions == ["提前5日书面通知甲方"]

    # 校验数值约束提取：30日与5日
    numeric_types = {n.type for n in intent.numeric_constraints}
    assert "CALENDAR_DAYS" in numeric_types or "WORKDAYS" in numeric_types
    values = [str(n.value) for n in intent.numeric_constraints]
    assert "30" in values and "5" in values

    # 校验生成的查询语句非空且有针对性
    assert len(intent.lexical_queries) > 0
    assert len(intent.semantic_queries) > 0
    assert any("解除" in q for q in intent.semantic_queries)
    assert intent.target_knowledge_types == ["TEMPLATE", "APPROVED_RULE"]


def test_cross_clause_reference_planning():
    """验证跨条款引用提取：第八条 违约赔偿依照第十二条执行。"""
    planner = ContractAwareQueryPlanner()
    clause = {
        "clause_id": "c_ref_01",
        "clause_number": "8.1",
        "article_title": "第八条 违约责任",
        "clause_category": "LIABILITY",
        "original_text": "乙方如出现迟延交付，其违约赔偿责任依照第十二条执行。",
        "source_node_ids": ["n201"],
    }
    intents = planner.plan_clause_intents_deterministic(clause)
    assert len(intents) == 2  # 包含基础合规意图 + 跨条款引用意图
    ref_intent = [i for i in intents if i.intent_type == "CROSS_CLAUSE_REFERENCE_CHECK"][0]
    assert "第十二条" in ref_intent.referenced_clauses
    assert "第十二条" in ref_intent.lexical_queries


def test_anti_explosion_budget_and_deduplication():
    """防爆炸策略测试：
    1. 意图总数受限于 max_total_queries；
    2. 相同证据意图自动去重；
    3. 优先规划高风险条款（违约责任、争议解决、解除权优先于样板前言）。
    """
    budget = QueryPlanningBudget(
        max_total_queries=6,
        max_intents_per_clause=2,
        priority_cutoff=3,
        enable_deduplication=True,
    )
    planner = ContractAwareQueryPlanner(budget=budget)

    # 构造 10 条混合风险条款
    clauses = [
        # 低风险样板
        {
            "clause_id": "c_front_1",
            "clause_category": "FRONT_MATTER",
            "original_text": "甲乙双方经友好协商达成如下协议：",
        },
        # 高风险条款
        {
            "clause_id": "c_liab_1",
            "clause_category": "LIABILITY",
            "clause_subcategory": "LIQUIDATED_DAMAGES",
            "original_text": "甲方逾期付款的，每日应按逾期金额的0.05%向乙方支付违约金。",
        },
        # 重复意图的高风险条款
        {
            "clause_id": "c_liab_2",
            "clause_category": "LIABILITY",
            "clause_subcategory": "LIQUIDATED_DAMAGES",
            "original_text": "甲方逾期付款的，每日应按逾期金额的0.05%向乙方支付违约金。",
        },
        # 解除权
        {
            "clause_id": "c_term_1",
            "clause_category": "LIABILITY",
            "original_text": "逾期超30日乙方有权单方解除合同。",
        },
        # 争议解决
        {
            "clause_id": "c_disp_1",
            "clause_category": "DISPUTE_RESOLUTION",
            "original_text": "因本合同引起的争议，由深圳国际仲裁院管辖仲裁解决。",
        },
    ]

    intents = planner.plan_intents_for_contract(clauses)

    # 1. 严格遵守预算上限
    assert len(intents) <= budget.max_total_queries
    # 2. 验证高风险条款被包含
    intent_sources = {i.source_clause_id for i in intents}
    assert "c_liab_1" in intent_sources or "c_term_1" in intent_sources
    # 3. 验证去重器生效（c_liab_2 与 c_liab_1 意图相同，不被重复添加）
    assert not ("c_liab_1" in intent_sources and "c_liab_2" in intent_sources)


@pytest.mark.parametrize("clause_count", [10, 50, 100, 300])
def test_query_planner_scalability_performance(clause_count: int):
    """压力测试：验证在 10、50、100、300 条款合同时的查询预算与耗时（防爆炸性能基准）。"""
    budget = QueryPlanningBudget(max_total_queries=16, max_clauses_to_plan=25)
    planner = ContractAwareQueryPlanner(budget=budget)

    # 构造大量合成条款
    synthetic_clauses = []
    categories = ["LIABILITY", "FEE_AND_PAYMENT", "SERVICE_SCOPE", "CONFIDENTIALITY", "CONTRACT_SUBJECT"]
    for i in range(clause_count):
        cat = categories[i % len(categories)]
        synthetic_clauses.append(
            {
                "clause_id": f"clause_{i}",
                "clause_number": f"{i // 5 + 1}.{i % 5 + 1}",
                "article_title": f"第{i // 5 + 1}条",
                "clause_category": cat,
                "original_text": f"第{i}项约定：甲方应当向乙方支付服务费用人民币 {1000 + i * 10} 元整，交付工期为 10 个工作日。",
                "source_node_ids": [f"node_{i}"],
            }
        )

    t0 = time.perf_counter()
    intents = planner.plan_intents_for_contract(synthetic_clauses)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    # 核心硬约束：无论条款数如何增加，最终生成的检索意图数量必须受预算控制，绝不爆炸
    assert len(intents) <= budget.max_total_queries
    # 300 条款合同的规划执行时间必须小于 200ms
    assert elapsed_ms < 200.0, f"Query planning exceeded SLA: {elapsed_ms:.2f} ms for {clause_count} clauses"
