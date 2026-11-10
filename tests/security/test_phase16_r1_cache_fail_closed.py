# -*- coding: utf-8 -*-
"""Phase 16-S-R1: 缓存与 TOCTOU 审批撤销复核 Fail-Closed 严密安全测试集。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from contract_kb.application.agents.legal_review_agent import LegalReviewAgent
from contract_kb.domain.models.review_identity import ReviewStatus


def test_cache_pre_authorization_gate():
    """核验外部模型外发未授权时，严禁通过历史缓存直接绕过门禁返回模型结论。"""
    agent = LegalReviewAgent(base_url="https://api.openai.com", api_key="sk-test")
    cache_key = agent._cache_key(
        "LIABILITY", "TEXT_DELTA", "std_auth", "rev_auth", "title_auth"
    )
    agent._cache[cache_key] = {
        "id": "ann-cache-auth",
        "risk_level": "HIGH",
        "risk_label": "违约金风险",
        "ai_analysis": "历史大模型分析结果",
        "action_advice": "历史建议",
        "from_cache": True,
        "source": "LLM_CACHE",
    }

    # 模拟外部模型外发未授权
    with patch.object(agent, "is_external_allowed", return_value=False):
        res = agent.review_clause(
            annotation={
                "id": "ann-cache-auth",
                "clause_category": "LIABILITY",
                "delta_type": "TEXT_DELTA",
                "standard_span": "std_auth",
                "reviewed_span": "rev_auth",
                "clause_title": "title_auth",
            },
            force_refresh=False,
        )

    # 必须回退至规则引擎速断/高精度回退，不得直接命中 LLM_CACHE
    assert res.get("from_cache") is not True
    assert "外部大模型外发调用未授权" in res.get("ai_analysis", "")


def test_cache_lookup_db_exception_fails_closed():
    """缓存规则有效性检查遇数据库异常，必须 Fail-Closed 判定缓存失效。"""
    agent = LegalReviewAgent()
    cache_key = agent._cache_key(
        "PAYMENT", "TEXT_DELTA", "std_db", "rev_db", "title_db"
    )
    agent._cache[cache_key] = {
        "id": "ann-db-fail",
        "risk_level": "MEDIUM",
        "risk_label": "付款条款",
        "ai_analysis": "已缓存合规分析",
        "action_advice": "建议保留",
        "evidence_references": [
            {
                "rule_id": "RULE_PAYMENT_01",
                "approval_status": "APPROVED",
                "revision": "r1",
            }
        ],
    }

    failing_storage = MagicMock()
    failing_storage.get_rules.side_effect = RuntimeError("DB connection pool exhausted")

    res = agent.review_clause(
        annotation={
            "id": "ann-db-fail",
            "clause_category": "PAYMENT",
            "delta_type": "TEXT_DELTA",
            "standard_span": "std_db",
            "reviewed_span": "rev_db",
            "clause_title": "title_db",
        },
        storage=failing_storage,
        force_refresh=False,
    )
    # 确保绝不复用旧缓存
    assert res.get("from_cache") is not True


def test_cache_invalidated_on_rule_revision_or_description_change():
    """底层规则 revision 或内容发生修订后，缓存自动判定失效并淘汰。"""
    agent = LegalReviewAgent()
    cache_key = agent._cache_key(
        "CONFIDENTIALITY", "TEXT_DELTA", "std_rev", "rev_rev", "title_rev"
    )
    agent._cache[cache_key] = {
        "id": "ann-rev-change",
        "risk_level": "HIGH",
        "risk_label": "保密条款",
        "ai_analysis": "基于旧规则修订版分析",
        "action_advice": "旧处置建议",
        "evidence_references": [
            {
                "rule_id": "RULE_CONF_01",
                "approval_status": "APPROVED",
                "revision": "r1",
                "description": "旧保密规则描述",
            }
        ],
    }

    active_storage = MagicMock()
    # 数据库中已更新为 revision r2 且描述变更
    active_storage.get_rules.return_value = [
        {
            "rule_id": "RULE_CONF_01",
            "status": "APPROVED",
            "revision": "r2",
            "description": "新修订的严格保密规则",
        }
    ]

    res = agent.review_clause(
        annotation={
            "id": "ann-rev-change",
            "clause_category": "CONFIDENTIALITY",
            "delta_type": "TEXT_DELTA",
            "standard_span": "std_rev",
            "reviewed_span": "rev_rev",
            "clause_title": "title_rev",
        },
        storage=active_storage,
        force_refresh=False,
    )
    assert res.get("from_cache") is not True
    assert res.get("ai_analysis") != "基于旧规则修订版分析"


def test_toctou_rule_revocation_triggers_human_review_and_aborts_cache():
    """推理期间风控规则被法务管理员撤销，TOCTOU 复核必须阻断入缓存并转人工复核。"""
    agent = LegalReviewAgent(base_url="https://api.openai.com", api_key="sk-test")
    cache_key = agent._cache_key(
        "TERMINATION", "TEXT_DELTA", "std_term", "rev_term", "title_term"
    )
    with agent._cache_lock:
        if cache_key in agent._cache:
            del agent._cache[cache_key]

    revoking_storage = MagicMock()
    # 第一次查询 (检索阶段): APPROVED
    # 第二次查询 (TOCTOU 复核阶段): 规则被撤销 (status=REVOKED) 或不存在
    revoking_storage.get_rules.side_effect = [
        [
            {
                "rule_id": "RULE_TERM_01",
                "clause_category": "TERMINATION",
                "status": "APPROVED",
                "name": "解除权风控",
                "parameters": {},
            }
        ],
        [
            {
                "rule_id": "RULE_TERM_01",
                "clause_category": "TERMINATION",
                "status": "APPROVED",
                "name": "解除权风控",
                "parameters": {},
            }
        ],
        [
            {
                "rule_id": "RULE_TERM_01",
                "clause_category": "TERMINATION",
                "status": "APPROVED",
                "name": "解除权风控",
                "parameters": {},
            }
        ],
        [],  # TOCTOU 时没有 APPROVED 规则
    ]

    with (
        patch.object(agent, "is_external_allowed", return_value=True),
        patch(
            "contract_kb.application.agents.legal_review_agent.OpenAICompatProvider"
        ) as mock_prov_cls,
    ):
        mock_prov = MagicMock()
        mock_prov.complete.return_value = {
            "text": json.dumps(
                {
                    "risk_level": "HIGH",
                    "risk_label": "解除权缺失",
                    "ai_analysis": "依据解除权规则研判得出结论",
                    "action_advice": "增加约定解除情形",
                }
            ),
            "usage": {"prompt_tokens": 10, "completion_tokens": 10},
        }
        mock_prov_cls.return_value = mock_prov

        res = agent.review_clause(
            annotation={
                "id": "ann-toctou-revoke",
                "clause_category": "TERMINATION",
                "delta_type": "TEXT_DELTA",
                "standard_span": "std_term",
                "reviewed_span": "rev_term",
                "clause_title": "title_term",
            },
            storage=revoking_storage,
            force_refresh=True,
        )

    # 1. review_status 必须变为 NEEDS_HUMAN_REVIEW
    assert res.get("review_status") == ReviewStatus.NEEDS_HUMAN_REVIEW.value
    assert res.get("toctou_revocation_detected") is True
    # 2. 证据状态标记为 REVOKED
    refs = res.get("evidence_references", [])
    if refs:
        assert refs[0].get("approval_status") == "REVOKED"
    # 3. 严禁写入持久缓存
    assert cache_key not in agent._cache
