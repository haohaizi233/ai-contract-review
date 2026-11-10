# -*- coding: utf-8 -*-
"""Phase 13R3 & Phase 13R4: 法务 Agent 可信 RAG 检索、规则审批治理与模型出境安全测试。

覆盖：
1. Case C: 废止规则（REVOKED）坚决阻断，已审批规则（APPROVED）权威采纳与引用核验；
2. SSRF 防护测试：拦截云元数据内部地址与非法协议；
3. ALLOW_EXTERNAL_LLM 出境门禁：未显式授权时静默降级为确定性规则审查，严禁合同数据泄露。
"""

import os
from unittest.mock import patch

from contract_kb.legal_agent import LegalReviewAgent, _is_safe_endpoint_url
from contract_kb.legal_agent_kb import (
    GOVERNED_RULES_STORE,
    GovernedLegalRule,
    retrieve_governed_rules,
    retrieve_legal_rules,
)


def test_case_c_revoked_rule_rejected_and_approved_rule_cited() -> None:
    """Case C: 废止规则（REVOKED）必须被过滤，仅已审批规则（APPROVED）可作为依据被引用。"""
    # 构造一条已废止的旧规则与一条现行生效的已审批规则
    revoked_id = "TEST_RULE_REVOKED_001"
    approved_id = "TEST_RULE_APPROVED_002"

    try:
        GOVERNED_RULES_STORE[revoked_id] = GovernedLegalRule(
            rule_id=revoked_id,
            name="旧版已作废免除乙方保密责任规则",
            business_scope="ALL",
            contract_subtype="ALL",
            clause_category="CONFIDENTIALITY",
            effective_date="2020-01-01",
            approval_status="REVOKED",  # 已废止
            source_document="已废止旧规章",
            revision=1,
            rule_description="【已废止旧规定】允许送审稿豁免保密责任。",
            advice_template="【已作废建议】可接受免除保密。",
        )

        GOVERNED_RULES_STORE[approved_id] = GovernedLegalRule(
            rule_id=approved_id,
            name="现行标准保密条款双向约束强制指引",
            business_scope="ALL",
            contract_subtype="ALL",
            clause_category="CONFIDENTIALITY",
            effective_date="2026-01-01",
            approval_status="APPROVED",  # 已审批生效
            source_document="企业核心风控指引2026",
            revision=3,
            is_statutory_mandate=True,
            rule_description="【现行强制标准】严禁送审稿删除或免除保密责任。",
            advice_template="【法务处置建议】要求恢复保密条款全文。",
        )

        # 执行治理检索
        matched_rules = retrieve_governed_rules("CONFIDENTIALITY", "MISSING_CLAUSE")
        matched_ids = [r.rule_id for r in matched_rules]

        # 验证 REVOKED 绝不在检索结果中
        assert revoked_id not in matched_ids, "已废止规则 (REVOKED) 绝不得进入匹配结果"

        # 验证 APPROVED 正常召回
        assert approved_id in matched_ids, "已审批生效规则 (APPROVED) 必须可信召回"

        # 验证提示词构造中不含废止内容
        prompt_rules = retrieve_legal_rules("CONFIDENTIALITY", "MISSING_CLAUSE")
        assert "旧版已作废免除乙方保密责任规则" not in prompt_rules
        assert approved_id in prompt_rules
        assert "现行强制标准" in prompt_rules
    finally:
        GOVERNED_RULES_STORE.pop(revoked_id, None)
        GOVERNED_RULES_STORE.pop(approved_id, None)


def test_ssrf_protection_blocks_metadata_and_invalid_protocols() -> None:
    """验证 SSRF 防护逻辑拦截内网元数据及非法协议端点。"""
    # 拦截 AWS / GCP / Azure 链路本地元数据 IP
    ok_aws, msg_aws = _is_safe_endpoint_url("http://169.254.169.254/latest/meta-data/")
    assert ok_aws is False
    assert "云服务内部元数据" in msg_aws

    # 拦截 GCP metadata
    ok_gcp, msg_gcp = _is_safe_endpoint_url(
        "http://metadata.google.internal/computeMetadata/v1/"
    )
    assert ok_gcp is False
    assert "云服务内部元数据" in msg_gcp

    # 拦截非法协议 (如 file://, ftp://, gopher://)
    ok_file, msg_file = _is_safe_endpoint_url("file:///etc/passwd")
    assert ok_file is False
    assert "不支持的协议类型" in msg_file

    ok_ftp, msg_ftp = _is_safe_endpoint_url("ftp://example.com/test")
    assert ok_ftp is False

    # 允许合规 HTTP/HTTPS
    ok_valid, msg_valid = _is_safe_endpoint_url("https://api.openai.com/v1")
    assert ok_valid is True
    assert msg_valid == "OK"


def test_egress_gate_blocks_external_llm_when_not_authorized() -> None:
    """验证未显式配置 CONTRACT_KB_ALLOW_EXTERNAL_LLM=1 时，外部大模型调用被拦截并安全降级。"""
    with patch.dict(os.environ, {"CONTRACT_KB_ALLOW_EXTERNAL_LLM": "0"}, clear=False):
        agent = LegalReviewAgent()
        assert agent.is_external_allowed() is False

        sample_ann = {
            "id": "ann-test-gate",
            "clause_category": "LIABILITY",
            "delta_type": "NUMBER_CHANGE",
            "title": "违约金下调",
            "standard_span": "每日 3‰",
            "reviewed_span": "每日 1‰",
            "quote_standard": "甲方逾期付款的，每日应按逾期未付金额的 3‰ 向乙方支付逾期违约金。",
            "quote_reviewed": "甲方逾期付款的，每日应按逾期未付金额的 1‰ 向乙方支付逾期违约金。",
        }

        # 应当安全降级为规则库审查，绝不发起外部 HTTP 请求
        res = agent.review_clause(sample_ann)

        assert res is not None
        assert res["review_status"] == "RULE_REVIEW_COMPLETED"
        assert res["source"] in ("AUTHORIZATION_GATE", "FALLBACK_EXTERNAL_LLM_DISABLED")
        assert "违约金" in res["action_advice"] or "违约金" in res["ai_analysis"]
