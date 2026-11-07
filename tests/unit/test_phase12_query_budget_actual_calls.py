# -*- coding: utf-8 -*-
"""Phase 12 检索调用预算与意图复用契约测试。"""

from __future__ import annotations

from pathlib import Path
import pytest

from contract_kb import config
from contract_kb.modules.retrieval.query_planner import (
    ContractAwareQueryPlanner,
    RetrievalIntent,
)
from contract_kb.modules.retrieval.service import (
    GovernedRetrievalService,
    RetrievalExecutionBudget,
)
from contract_kb.storage import Storage


class DummyLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.call_count = 0

    def generate(self, prompt: str) -> str:
        self.call_count += 1
        return self.reply


def test_query_budget_tracks_actual_calls_and_stops_on_exhaustion(
    tmp_path: Path,
) -> None:
    """验证 RetrievalExecutionBudget 精确记录实际底层 search 调用，并在达到上限时熔断阻断。"""
    st = Storage(tmp_path / "budget_test.db")
    svc = GovernedRetrievalService(st)

    # 构造限制仅允许 2 次检索调用的极小预算
    budget = RetrievalExecutionBudget(
        max_total_intents=10,
        max_search_calls=2,
    )

    intent = RetrievalIntent(
        intent_id="test_int_1",
        source_clause_id="clause_1",
        intent_type="STANDARD_COMPLIANCE_CHECK",
        lexical_queries=["违约责任", "赔偿损失"],
        semantic_queries=["违约责任的标准模板约定"],
        referenced_clauses=["第十条"],
        target_knowledge_types=["TEMPLATE", "APPROVED_RULE"],
    )

    # 首次执行意图检索
    svc.retrieve_for_intent(intent, budget=budget)

    # 检索通道触发了 search_lexical_candidates，最多执行 2 次后预算耗尽
    assert budget.actual_search_calls == 2
    assert budget.is_budget_exhausted is True

    # 再次发起检索，应当直接被拦截，调用计数不再递增
    svc.retrieve_for_intent(intent, budget=budget)
    assert budget.actual_search_calls == 2

    budget_dict = budget.to_dict()
    assert budget_dict["max_search_calls"] == 2
    assert budget_dict["actual_search_calls"] == 2
    assert budget_dict["is_budget_exhausted"] is True
    st.close()


def test_llm_query_expansion_explicit_marking(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证 LLM 查询扩展在未配置模型时诚实返回 DISABLED_NO_AUTHORIZED_LLM，而在提供模型时生成增补意图。"""
    planner = ContractAwareQueryPlanner()
    clause = {
        "clause_id": "c_llm",
        "clause_category": "LIABILITY",
        "original_text": "乙方应在逾期后承担违约金。",
    }
    base_intents = planner.plan_clause_intents_deterministic(clause)

    # 1. 未授权外部 LLM 时的行为：显式标记状态，禁止静默假装执行
    monkeypatch.setattr(config, "ALLOW_EXTERNAL_LLM", False)
    intents_no_llm = planner.expand_intents_with_llm(
        clause, base_intents, llm_provider=None
    )
    assert len(intents_no_llm) == len(base_intents)
    assert intents_no_llm[0].llm_expansion_status == "DISABLED_NO_AUTHORIZED_LLM"

    # 2. 授权并注入模拟 LLM 时的行为：生成 LLM 增补意图并标记 SUCCESS
    monkeypatch.setattr(config, "ALLOW_EXTERNAL_LLM", True)
    llm = DummyLLM(reply="违约金逾期计算规则与法律后果分析")
    intents_with_llm = planner.expand_intents_with_llm(
        clause, base_intents, llm_provider=llm
    )
    assert llm.call_count == 1
    assert any(i.generation_method == "llm_assisted_v1" for i in intents_with_llm)
    assert intents_with_llm[0].llm_expansion_status == "SUCCESS"


def test_contract_evidence_bundles_reuse_pre_planned_intents(tmp_path: Path) -> None:
    """验证批量生成证据包时，重用整合同规划意图，并在每个 EvidenceBundle 中注入执行预算指标。"""
    st = Storage(tmp_path / "bundle_test.db")
    svc = GovernedRetrievalService(st)

    clauses = [
        {
            "clause_id": "c1",
            "clause_category": "LIABILITY",
            "original_text": "甲方违约赔偿 10000 元。",
        },
        {
            "clause_id": "c2",
            "clause_category": "CONFIDENTIALITY",
            "original_text": "乙方承担保密义务。",
        },
    ]

    budget = RetrievalExecutionBudget(max_search_calls=50)
    bundles = svc.retrieve_contract_evidence_bundles(clauses, budget=budget)

    assert "c1" in bundles
    assert "c2" in bundles
    b1 = bundles["c1"]
    assert b1.execution_budget is not None
    assert "actual_search_calls" in b1.execution_budget
    assert budget.total_intents_executed > 0
    st.close()
