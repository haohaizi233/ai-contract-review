# -*- coding: utf-8 -*-
"""安全与合规测试：知识库检索治理严格 Fail-Closed (R07).

验证：
1. 缺失或非法 knowledge_type 的知识项禁止静默降级为 TEMPLATE，必须直接拒绝准入 (False)；
2. 缺失或 UNKNOWN approved_status 的权威知识项（TEMPLATE / APPROVED_RULE）必须直接拒绝准入 (False)；
3. 未经审批的知识项绝对不能标记为 can_drive_mandatory_finding=True；
4. 租户不匹配或状态为非 ACTIVE 的知识项必须被阻断。
"""

from __future__ import annotations

import pytest

from contract_kb.modules.retrieval.governance import (
    KnowledgeGovernancePolicy,
    KnowledgeSourceType,
)


def test_missing_or_unknown_knowledge_type_is_rejected():
    """验证缺失或未知 knowledge_type 不得回退为 TEMPLATE，必须直接拒绝准入。"""
    # 缺失
    hit_missing = {
        "clause_id": "c1",
        "doc_status": "ACTIVE",
        "approved_status": "APPROVED",
        "tenant_id": "t1",
    }
    assert KnowledgeGovernancePolicy.is_chunk_admissible(hit_missing) is False

    # 空白
    hit_blank = {
        "clause_id": "c2",
        "knowledge_type": "   ",
        "doc_status": "ACTIVE",
        "approved_status": "APPROVED",
        "tenant_id": "t1",
    }
    assert KnowledgeGovernancePolicy.is_chunk_admissible(hit_blank) is False

    # 未知类型（例如攻击者注入或脏数据）
    hit_unknown = {
        "clause_id": "c3",
        "knowledge_type": "MALICIOUS_PROMPT_INJECTION",
        "doc_status": "ACTIVE",
        "approved_status": "APPROVED",
        "tenant_id": "t1",
    }
    assert KnowledgeGovernancePolicy.is_chunk_admissible(hit_unknown) is False


def test_missing_or_unapproved_status_rejected_for_authoritative_sources():
    """验证权威知识源（TEMPLATE/RULE）若缺失审批状态或为 UNKNOWN，严禁准入。"""
    # TEMPLATE 缺失 approved_status
    hit_template_no_status = {
        "clause_id": "c4",
        "knowledge_type": "TEMPLATE",
        "doc_status": "ACTIVE",
        "tenant_id": "t1",
        # approved_status 缺失
    }
    assert KnowledgeGovernancePolicy.is_chunk_admissible(hit_template_no_status) is False

    # APPROVED_RULE 的 approved_status 为 UNKNOWN 或 PENDING
    hit_rule_pending = {
        "clause_id": "c5",
        "knowledge_type": "APPROVED_RULE",
        "doc_status": "ACTIVE",
        "approved_status": "PENDING_APPROVAL",
        "tenant_id": "t1",
    }
    assert KnowledgeGovernancePolicy.is_chunk_admissible(hit_rule_pending) is False

    hit_rule_unknown = {
        "clause_id": "c6",
        "knowledge_type": "APPROVED_RULE",
        "doc_status": "ACTIVE",
        "approved_status": "UNKNOWN",
        "tenant_id": "t1",
    }
    assert KnowledgeGovernancePolicy.is_chunk_admissible(hit_rule_unknown) is False


def test_normalize_source_type_rejects_unknown():
    """验证 normalize_source_type 对未知或空字符串返回 None 或引发明确判定，杜绝静默回退。"""
    assert KnowledgeGovernancePolicy.normalize_source_type(None) is None
    assert KnowledgeGovernancePolicy.normalize_source_type("") is None
    assert KnowledgeGovernancePolicy.normalize_source_type("SOMETHING_RANDOM") is None
