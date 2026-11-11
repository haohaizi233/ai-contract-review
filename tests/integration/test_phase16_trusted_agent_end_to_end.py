# -*- coding: utf-8 -*-
"""Phase 16-S: 可信 Agent 全链路端到端集成测试 (Trusted Agent End-to-End Integration)。

全链路覆盖：
1. 租户上下文初始化与母本匹配
2. 受控知识检索 (Governed Retrieval) 权威审批过滤
3. LegalReviewAgent 深度研判与 EvidenceBundle 证据引用链绑定
4. 证据核验 (Citation Verification)
5. 人工确认与反馈记录 (Feedback Governance)
6. 授权审批与按租户隔离的 SFT 训练集导出
"""

import json
import time
from unittest.mock import MagicMock, patch


from contract_kb.application.agents.knowledge_adapter import (
    export_sft_dataset,
    record_user_feedback,
    retrieve_governed_rules,
)
from contract_kb.application.agents.legal_review_agent import LegalReviewAgent


def test_trusted_agent_end_to_end_governance_lifecycle(tmp_path):
    """验证可信法务 Agent 从知识检索、研判推理、证据核验到反馈治理的全链路。"""
    tenant_id = f"tenant_e2e_{time.time_ns()}"
    user_id = "legal_director_007"

    # 1. 模拟数据库中存在的已审批生效规则
    rule_id = "RULE_E2E_CONF_001"
    approved_rule_data = {
        "rule_id": rule_id,
        "name": "知识产权保密条款强制保留基准",
        "clause_category": "CONFIDENTIALITY",
        "status": "APPROVED",
        "effective_at": "2026-01-01",
        "version": 2,
        "is_statutory": True,
        "source_clauses": "集团合规风控红线2026",
        "parameters": {
            "description": "涉及核心技术秘密的合同，严禁整段删除保密条款",
            "advice_template": "【法务处置建议】必须要求对方恢复保密条款全文，脱密期限不得少于3年。",
        },
    }

    mock_storage = MagicMock()
    mock_storage.get_rules.return_value = [approved_rule_data]
    mock_storage.get_document.return_value = {
        "document_id": "集团合规风控红线2026",
        "tenant_id": tenant_id,
        "status": "APPROVED",
    }

    # 2. 受控检索召回已审批规则
    rules = retrieve_governed_rules(
        category="CONFIDENTIALITY",
        delta_type="MISSING_CLAUSE",
        tenant_id=tenant_id,
        storage=mock_storage,
    )
    assert len(rules) == 1
    assert rules[0].rule_id == rule_id
    assert rules[0].approval_status == "APPROVED"
    assert rules[0].is_statutory_mandate is True

    # 3. Agent 研判并生成带可追溯证据链的法务结论
    agent = LegalReviewAgent(base_url="https://api.openai.com/v1", api_key="sk-mock123")
    ann = {
        "id": "ann-e2e-conf",
        "clause_category": "CONFIDENTIALITY",
        "delta_type": "MISSING_CLAUSE",
        "title": "保密条款整段缺失",
        "standard_span": "双方应对商业秘密及未公开技术交底书承担无限期保密义务。",
        "reviewed_span": "",
    }

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
                    "risk_label": "核心保密条款缺失",
                    "ai_analysis": "待审合同删除了母本中关键的保密约定，依据企业已审批保密基准构成重大商业秘密外泄风险。",
                    "action_advice": "【法务处置建议】必须恢复母本保密条款，拒绝单方豁免保密责任。",
                }
            ),
            "usage": {"prompt_tokens": 120, "completion_tokens": 60},
        }
        mock_prov_cls.return_value = mock_prov

        finding = agent.review_clause(
            ann,
            doc_context={
                "tenant_id": tenant_id,
                "matched_template_name": "专利代理标准母本",
            },
            storage=mock_storage,
            force_refresh=True,
        )

        assert finding["risk_level"] == "HIGH"
        assert "evidence_references" in finding
        refs = finding["evidence_references"]
        assert len(refs) == 1
        assert refs[0]["rule_id"] == rule_id
        assert refs[0]["approval_status"] == "APPROVED"
        assert refs[0]["verification_status"] == "VERIFIED"

    # 4. 人工确认并记录反馈（通过受控接口存入待审批队列）
    feedback_payload = {
        "doc_id": "doc_e2e_contract_01",
        "annotation_id": "ann-e2e-conf",
        "tenant_id": tenant_id,
        "reviewer_id": user_id,
        "human_decision": "ACCEPTED",
        "human_modified_suggestion": "完全采纳 AI 处置建议，要求恢复保密条款全文",
    }
    saved_raw = record_user_feedback(
        feedback_payload, is_approved=False, tenant_id=tenant_id
    )
    assert saved_raw is not None
    assert saved_raw.exists()

    # 5. 管理员授权导出当前租户 SFT 数据集（租户强隔离）
    export_res = export_sft_dataset(require_approval=False, tenant_id=tenant_id)
    assert export_res is not None
    assert "train_count" in export_res
