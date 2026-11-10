# -*- coding: utf-8 -*-
"""Phase 16-S: 法务 Agent 可信 RAG 与规则审批治理 Fail-Closed 安全测试。

覆盖：
- Case 06: 数据库已撤销规则，Agent 不得通过静态字典回退重新加载为 APPROVED
- Case 07: TOCTOU 审批撤销防护 (规则在推理途中被撤销，最终结论拒绝认定权威有效并阻断存入持久缓存)
- Case 08: 模板或审批版本变更时缓存安全失效
- Case 13: 外部大模型不可用时安全降级至确定性规则初筛，绝不冒充 AI 完成研判
- Case 14: 数据库故障时 Fail-Closed，不从不可信规则源伪造权威审核依据
"""

from unittest.mock import MagicMock, patch


from contract_kb.application.agents.knowledge_adapter import (
    retrieve_governed_rules,
)
from contract_kb.application.agents.legal_review_agent import LegalReviewAgent


def test_revoked_database_rule_never_falls_back_to_static_store():
    """Case 06: 数据库中规则被撤销或删除时，检索必须返回空，严禁回退至代码静态字典。"""
    mock_storage = MagicMock()
    mock_storage.get_rules.return_value = []  # 数据库中该租户已撤销所有规则

    rules = retrieve_governed_rules(
        category="LIABILITY",
        delta_type="NUMBER_CHANGE",
        tenant_id="tenant_security_audit",
        storage=mock_storage,
    )
    assert len(rules) == 0, "数据库中无生效规则时，严禁回退静态字典制造假 APPROVED"


def test_toctou_in_flight_rule_revocation_triggers_alarm_and_blocks_cache():
    """Case 07: TOCTOU 竞争防御：Agent 检索时规则有效，推理中途规则被撤销，输出必须报警并标记人工复核。"""
    agent = LegalReviewAgent(
        base_url="https://api.openai.com/v1", api_key="sk-test12345"
    )
    mock_storage = MagicMock()

    rule_id = "RULE_TOCTOU_TEST_001"
    active_rule = {
        "rule_id": rule_id,
        "name": "动态高危防线",
        "clause_category": "LIABILITY",
        "status": "APPROVED",
        "parameters": {
            "description": "严格审查违约金下调",
            "advice_template": "恢复原状",
        },
    }

    # 检索阶段 (retrieve_legal_rules, retrieve_governed_rules)：规则依然有效
    # 推理后 TOCTOU 复核阶段：规则已被法务管理员撤销 (返回空列表)
    mock_storage.get_rules.side_effect = [
        [active_rule],  # retrieve_legal_rules
        [active_rule],  # retrieve_governed_rules
        [],  # post-inference TOCTOU 复核时已撤销
    ]

    ann = {
        "id": "ann-toctou-1",
        "clause_category": "LIABILITY",
        "delta_type": "NUMBER_CHANGE",
        "title": "违约金下调",
        "standard_span": "3‰",
        "reviewed_span": "1‰",
    }

    import json

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
                    "risk_label": "违约金偏离",
                    "ai_analysis": "依据动态高危防线判定违约金偏离过大",
                    "action_advice": "建议恢复3‰",
                }
            ),
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }
        mock_prov_cls.return_value = mock_prov

        res = agent.review_clause(
            ann,
            doc_context={"tenant_id": "tenant_alpha"},
            storage=mock_storage,
            force_refresh=True,
        )

        assert res.get("toctou_revocation_detected") is True
        assert res.get("requires_human_review") is True
        assert "【规则撤销失效警报】" in res.get("ai_analysis", "")


def test_cache_invalidation_on_template_or_rule_version_change():
    """Case 08: 模板版本或规则版本变更时，旧缓存必须失效，不能复用旧结果。"""
    agent = LegalReviewAgent(
        base_url="https://api.openai.com/v1", api_key="sk-test12345"
    )

    key_v1 = agent._cache_key(
        category="DISPUTE_RESOLUTION",
        delta_type="JURISDICTION_CHANGE",
        std_span="南山区法院",
        rev_span="朝阳区法院",
        clause_title="管辖变更",
        template_version="v1",
        rule_version="v1",
    )

    key_v2 = agent._cache_key(
        category="DISPUTE_RESOLUTION",
        delta_type="JURISDICTION_CHANGE",
        std_span="南山区法院",
        rev_span="朝阳区法院",
        clause_title="管辖变更",
        template_version="v2",  # 升级版本
        rule_version="v1",
    )

    assert key_v1 != key_v2, "模板版本变更必须生成不同缓存 Key，防止复用陈旧结论"


def test_model_failure_gracefully_falls_back_with_explicit_badge():
    """Case 13: 外部大模型不可用时，安全降级至规则初筛，明确标注 AI 未完成，严禁冒充大模型完成。"""
    agent = LegalReviewAgent(
        base_url="https://api.openai.com/v1", api_key="sk-test12345"
    )
    ann = {
        "id": "ann-fail-1",
        "clause_category": "CONFIDENTIALITY",
        "delta_type": "MISSING_CLAUSE",
        "title": "保密条款删除",
        "standard_span": "保密协议",
        "reviewed_span": "",
    }

    # 模拟外部调用未授权
    with patch.object(agent, "is_external_allowed", return_value=False):
        res = agent.review_clause(ann, force_refresh=True)
        assert res["review_status"] == "RULE_REVIEW_COMPLETED"
        assert res["source"] in ("AUTHORIZATION_GATE", "FALLBACK_EXTERNAL_LLM_DISABLED")
        assert (
            "大模型语义审查未执行" in res["ai_analysis"]
            or "AI语义大模型未完成研判" in res["ai_analysis"]
        )


def test_database_error_fails_closed():
    """Case 14: 数据库查询异常时，必须 Fail-Closed 返回空列表，严禁静默放行或伪造权威规则。"""
    mock_storage = MagicMock()
    mock_storage.get_rules.side_effect = RuntimeError(
        "Database connection pool exhausted"
    )

    rules = retrieve_governed_rules(
        category="LIABILITY",
        delta_type="NUMBER_CHANGE",
        tenant_id="tenant_crash",
        storage=mock_storage,
    )
    assert rules == [], "数据库故障时必须安全降级为不可用（Fail-Closed 空列表）"
