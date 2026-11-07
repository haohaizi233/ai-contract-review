# -*- coding: utf-8 -*-
"""安全与治理测试：RAG 准入 fail-closed、来源权限及知识类型治理（T12 / F11）。

严格验证 fail-closed 门禁：
1. 草稿条款 (DRAFT) 绝不可进入检索结果与 Prompt；
2. 未审批规则 (PENDING / REJECTED) 绝对被过滤；
3. 缺少知识类型 (knowledge_type is None/empty) 的语料直接拒绝，不默认放行；
4. 跨租户数据隔离：租户 A 的知识在租户 B 的审核中绝对不可见；
5. 历史案例 (HISTORICAL_CASE) 默认被禁止作为强制违规判罚依据。
"""

from __future__ import annotations


from contract_kb.modules.retrieval.governance import (
    KnowledgeGovernancePolicy,
    KnowledgeSourceType,
)


def test_draft_and_archived_documents_blocked():
    """验证处于 DRAFT、ARCHIVED、DELETED 等非 ACTIVE 状态的文档分块全部被拦截。"""
    draft_hit = {
        "clause_id": "c_draft_1",
        "doc_status": "DRAFT",
        "knowledge_type": "TEMPLATE",
        "approved_status": "APPROVED",
        "tenant_id": "t1",
    }
    assert KnowledgeGovernancePolicy.is_chunk_admissible(draft_hit, expected_tenant_id="t1") is False

    archived_hit = {
        "clause_id": "c_archived_1",
        "status": "ARCHIVED",
        "knowledge_type": "TEMPLATE",
        "approved_status": "APPROVED",
        "tenant_id": "t1",
    }
    assert KnowledgeGovernancePolicy.is_chunk_admissible(archived_hit, expected_tenant_id="t1") is False


def test_unapproved_rule_blocked():
    """验证未明确 APPROVED 的规则绝对不可进入准入列表。"""
    pending_rule = {
        "clause_id": "c_rule_01",
        "doc_status": "ACTIVE",
        "knowledge_type": "APPROVED_RULE",
        "approved_status": "PENDING_REVIEW",
        "tenant_id": "t1",
    }
    assert KnowledgeGovernancePolicy.is_chunk_admissible(pending_rule, expected_tenant_id="t1") is False


def test_missing_knowledge_type_rejected_fail_closed():
    """验证缺少 knowledge_type 时采用 fail-closed 拒绝，严禁无声降级为合法模板。"""
    corrupt_hit = {
        "clause_id": "c_no_type_01",
        "doc_status": "ACTIVE",
        "knowledge_type": None,
        "approved_status": "APPROVED",
        "tenant_id": "t1",
    }
    assert KnowledgeGovernancePolicy.is_chunk_admissible(corrupt_hit, expected_tenant_id="t1") is False


def test_cross_tenant_knowledge_leak_blocked():
    """验证租户 A 的知识在租户 B 上下文中检索时被严格拦截。"""
    tenant_a_hit = {
        "clause_id": "c_tenant_a",
        "doc_status": "ACTIVE",
        "knowledge_type": "TEMPLATE",
        "approved_status": "APPROVED",
        "tenant_id": "tenant_A",
    }
    # 租户 B 发起审核检索，必须返回 False
    assert KnowledgeGovernancePolicy.is_chunk_admissible(tenant_a_hit, expected_tenant_id="tenant_B") is False


def test_historical_case_cannot_drive_mandatory_finding():
    """验证历史案例知识类型绝不允许作为强制违规判例驱动项。"""
    desc = KnowledgeGovernancePolicy.descriptor_for("HISTORICAL_CASE")
    assert desc.can_drive_mandatory_finding is False
    assert desc.source_type == KnowledgeSourceType.HISTORICAL_CASE
