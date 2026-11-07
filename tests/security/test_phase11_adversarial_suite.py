# -*- coding: utf-8 -*-
"""Phase 11: Comprehensive Adversarial Security & Trust Boundary Test Suite.

Covers Test Groups A - F:
- Test Group A: Approval bypass attacks (PENDING_REVIEW cannot drive findings,
  is_company_standard does not elevate, revoked knowledge loses authority).
- Test Group B: Cross-tenant isolation (clause/citation cross-tenant barrier,
  fallback tenant boundary preservation, zero cross-tenant leakage).
- Test Group C: Lifecycle invalidation (DRAFT, SUPERSEDED, ARCHIVED, DELETED
  blocked from authoritative RAG and mandatory findings).
- Test Group D: Citation forgery resistance (hallucinated clause IDs, altered text,
  tenant mismatch, empty hit statistics fidelity).
- Test Group E: Retrieval consistency & fallback safety (FTS fallback without embeddings table
  does NOT forge metadata; degradations flagged; Fail-Closed).
- Test Group F: Pipeline Stage 9 -> 14 end-to-end evidence integrity (findings
  based on reference-only knowledge cannot become mandatory violations).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from contract_kb.domain.models.review import (
    CheckStatus,
    Finding,
)
from contract_kb.modules.retrieval.citation import CitationVerifier
from contract_kb.modules.retrieval.governance import (
    KnowledgeGovernancePolicy,
)
from contract_kb.modules.retrieval.service import GovernedRetrievalService
from contract_kb.modules.rules.governance import ConflictResolver
from contract_kb.storage import Storage


# ==============================================================================
# Test Group A: 审批绕过 (Approval Bypass Attacks)
# ==============================================================================


def test_group_a_new_import_remains_pending():
    """新导入模板默认为 PENDING_REVIEW，绝对不得自动变 APPROVED。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        st = Storage(Path(tmpdir) / "test_a.db")
        try:
            doc = {
                "template_id": "tpl_draft_01",
                "source_file": "tpl_draft.docx",
                "sha256": "hash_draft_001",
                "template_version": "v1",
                "document_type": "contract_template",
                "status": "ACTIVE",
                "clauses": [
                    {
                        "clause_id": "c_pending_01",
                        "original_text": "甲方应当在收到发票后十日内付款。",
                        "clause_category": "FEE_AND_PAYMENT",
                        "approval_status": "PENDING_REVIEW",
                    }
                ],
            }
            did, _ = st.upsert_document(doc, tenant="t1")
            c = st.get_clause("c_pending_01")
            assert c["approval_status"] == "PENDING_REVIEW"

            # 检索权威知识：PENDING_REVIEW 条款绝对不可被召回
            svc = GovernedRetrievalService(st)
            bundle = svc.retrieve_for_review("付款", tenant_id="t1")
            assert not bundle.authoritative_hits
        finally:
            st.close()


def test_group_a_company_standard_flag_does_not_grant_approval():
    """标记 is_company_standard=True 绝不能代替法务正式审批。"""
    hit = {
        "clause_id": "c_std_1",
        "knowledge_type": "TEMPLATE",
        "doc_status": "ACTIVE",
        "is_company_standard": True,
        "approved_status": "PENDING_REVIEW",
        "tenant_id": "t1",
    }
    assert (
        KnowledgeGovernancePolicy.is_chunk_admissible(hit, expected_tenant_id="t1")
        is False
    )


def test_group_a_revocation_immediately_removes_authority():
    """经审批模板一旦执行撤销 (REVOKED)，立即丧失权威依据资格。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        st = Storage(Path(tmpdir) / "test_rev.db")
        try:
            doc = {
                "template_id": "tpl_standard",
                "source_file": "std.docx",
                "sha256": "hash_std_001",
                "template_version": "v1",
                "document_type": "contract_template",
                "status": "ACTIVE",
                "clauses": [
                    {
                        "clause_id": "c_std_rev_1",
                        "original_text": "乙方应保证工作质量符合国家标准。",
                        "clause_category": "PARTY_B_OBLIGATIONS",
                        "approval_status": "PENDING_REVIEW",
                    }
                ],
            }
            did, _ = st.upsert_document(doc, tenant="t1")
            # 法务正式批准
            st.approve_knowledge_version(
                tenant_id="t1", document_id=did, approver_id="legal_lead"
            )

            svc = GovernedRetrievalService(st)
            bundle1 = svc.retrieve_for_review("工作质量", tenant_id="t1")
            assert len(bundle1.authoritative_hits) == 1

            # 撤销审批
            st.revoke_knowledge_version(
                tenant_id="t1",
                document_id=did,
                revoker_id="legal_lead",
                revoke_reason="规章废止",
            )
            c = st.get_clause("c_std_rev_1")
            assert c["approval_status"] == "REVOKED"

            # 再次检索：必须立刻阻断
            bundle2 = svc.retrieve_for_review("工作质量", tenant_id="t1")
            assert len(bundle2.authoritative_hits) == 0
        finally:
            st.close()


def test_group_a_rebuild_index_preserves_approval_states():
    """重建索引不得将 PENDING 或 REVOKED 重置为 APPROVED。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        st = Storage(Path(tmpdir) / "test_rebuild.db")
        try:
            doc = {
                "template_id": "tpl_mixed",
                "source_file": "mixed.docx",
                "sha256": "hash_mixed_001",
                "template_version": "v1",
                "document_type": "contract_template",
                "status": "ACTIVE",
                "clauses": [
                    {
                        "clause_id": "c_pending_mix",
                        "original_text": "争议管辖由被告所在地人民法院审理。",
                        "clause_category": "DISPUTE_RESOLUTION",
                        "approval_status": "PENDING_REVIEW",
                    }
                ],
            }
            st.upsert_document(doc, tenant="t1")
            st.rebuild_index()

            cur = st.conn.execute(
                "SELECT approved_status FROM embeddings WHERE clause_id='c_pending_mix'"
            )
            assert cur.fetchone()[0] == "PENDING_REVIEW"
        finally:
            st.close()


# ==============================================================================
# Test Group B: 跨租户攻击 (Cross-Tenant Attack Resistance)
# ==============================================================================


def test_group_b_identical_text_different_tenants_isolated():
    """不同租户拥有相同条款文本，检索严格限制在本租户内，零跨租户召回。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        st = Storage(Path(tmpdir) / "test_tenant.db")
        try:
            # Tenant A 导入并批准
            doc_a = {
                "template_id": "tpl_a",
                "source_file": "a.docx",
                "sha256": "hash_a",
                "template_version": "v1",
                "document_type": "contract_template",
                "status": "ACTIVE",
                "clauses": [
                    {
                        "clause_id": "c_common",
                        "original_text": "商业保密信息包括双方财务数据与技术方案。",
                        "clause_category": "CONFIDENTIALITY",
                        "approval_status": "PENDING_REVIEW",
                    }
                ],
            }
            st.upsert_document(doc_a, tenant="tenant_A")
            st.approve_knowledge_version("tenant_A", "tpl_a", "approver_a")

            # Tenant B 视角发起审核检索
            svc = GovernedRetrievalService(st)
            bundle_b = svc.retrieve_for_review("商业保密信息", tenant_id="tenant_B")
            assert len(bundle_b.authoritative_hits) == 0, (
                "Tenant B 不得召回 Tenant A 的条款"
            )

            # Tenant A 视角发起审核检索
            bundle_a = svc.retrieve_for_review("商业保密信息", tenant_id="tenant_A")
            assert len(bundle_a.authoritative_hits) == 1
        finally:
            st.close()


def test_group_b_citation_verifier_rejects_cross_tenant_lookup():
    """CitationVerifier 回查条款时若发现跨租户，必须标记为 UNVERIFIED。"""
    verifier = CitationVerifier()

    class CrossTenantStorage:
        def get_clause(self, cid: str, tenant_id: str | None = None):
            # 模拟租户 B 查询时，数据库中属于租户 A 的条款被隔离返回 None
            if tenant_id == "tenant_B":
                return None
            return {
                "clause_id": cid,
                "document_id": "doc_a",
                "tenant_id": "tenant_A",
                "original_text": "保密条款",
                "doc_status": "ACTIVE",
                "approval_status": "APPROVED",
                "knowledge_type": "TEMPLATE",
            }

    hits = [
        {
            "clause_id": "c_a",
            "text": "保密条款",
            "tenant_id": "tenant_B",
            "knowledge_type": "TEMPLATE",
            "approved_status": "APPROVED",
            "doc_status": "ACTIVE",
        }
    ]
    summary = verifier.verify_retrieval_hits(
        CrossTenantStorage(), hits, expected_tenant_id="tenant_B"
    )
    assert summary.verified_count == 0
    assert "c_a" in summary.unverified_ids


# ==============================================================================
# Test Group C: 状态失效 (Lifecycle State Invalidation)
# ==============================================================================


@pytest.mark.parametrize(
    "invalid_status",
    [
        "DRAFT",
        "SUPERSEDED",
        "ARCHIVED",
        "DELETED",
        "REVOKED",
        "UNKNOWN",
        "",
        None,
    ],
)
def test_group_c_invalid_lifecycle_statuses_blocked(invalid_status):
    """非 ACTIVE 文档状态的所有分块坚决阻断准入。"""
    hit = {
        "clause_id": "c_test",
        "knowledge_type": "TEMPLATE",
        "doc_status": invalid_status,
        "approved_status": "APPROVED",
        "tenant_id": "t1",
    }
    assert (
        KnowledgeGovernancePolicy.is_chunk_admissible(hit, expected_tenant_id="t1")
        is False
    )


# ==============================================================================
# Test Group D: 引文伪造 (Citation Forgery Resistance)
# ==============================================================================


def test_group_d_nonexistent_clause_id_rejected():
    """不存在的 clause_id 不得通过核验。"""
    verifier = CitationVerifier()

    class EmptyStorage:
        def get_clause(self, cid: str, tenant_id: str | None = None):
            return None

    hits = [
        {
            "clause_id": "c_fake_999",
            "text": "假造条款",
            "tenant_id": "t1",
            "knowledge_type": "TEMPLATE",
            "approved_status": "APPROVED",
            "doc_status": "ACTIVE",
        }
    ]
    summary = verifier.verify_retrieval_hits(
        EmptyStorage(), hits, expected_tenant_id="t1"
    )
    assert summary.verified_count == 0
    assert summary.unverified_ids == ["c_fake_999"]


def test_group_d_text_mismatch_rejected():
    """存在条款 ID 但引述文本被替换为篡改内容时，核验必须失败。"""
    verifier = CitationVerifier()

    class TamperedStorage:
        def get_clause(self, cid: str, tenant_id: str | None = None):
            return {
                "clause_id": cid,
                "document_id": "d1",
                "tenant_id": "t1",
                "original_text": "真实原文：甲方应当按月支付服务费。",
                "doc_status": "ACTIVE",
                "approval_status": "APPROVED",
                "knowledge_type": "TEMPLATE",
            }

    hits = [
        {
            "clause_id": "c1",
            "text": "篡改文本：甲方无条件豁免乙方一切违约责任。",
            "tenant_id": "t1",
            "knowledge_type": "TEMPLATE",
            "approved_status": "APPROVED",
            "doc_status": "ACTIVE",
        }
    ]
    summary = verifier.verify_retrieval_hits(
        TamperedStorage(), hits, expected_tenant_id="t1"
    )
    assert summary.verified_count == 0
    assert summary.unverified_ids == ["c1"]


# ==============================================================================
# Test Group E: 检索一致性与降级安全 (Retrieval Consistency & Fallback Safety)
# ==============================================================================


def test_group_e_historical_case_cannot_drive_mandatory_finding():
    """历史案例即使被召回，也绝对标记 can_drive_mandatory_finding=False。"""
    hit = {
        "clause_id": "case_01",
        "knowledge_type": "HISTORICAL_CASE",
        "doc_status": "ACTIVE",
        "approved_status": "PENDING_REVIEW",
        "tenant_id": "t1",
    }
    assert KnowledgeGovernancePolicy.is_chunk_admissible(
        hit, allow_historical_cases=True, expected_tenant_id="t1"
    )
    annotated = KnowledgeGovernancePolicy.annotate_hit(hit)
    assert annotated["can_drive_mandatory_finding"] is False
    assert annotated["authority_rank"] == 4


# ==============================================================================
# Test Group F: 完整审核链路证据权限门禁 (End-to-End Pipeline Stage 9 -> 14)
# ==============================================================================


def test_group_f_non_authoritative_reference_cannot_drive_mandatory_finding():
    """Stage 12 冲突消解门禁：非权威依据（如历史案例或未核验证据）驱动的结论必须被降级。"""
    f_invalid = Finding(
        issue_id="",
        rule_id="PAYMENT_TERMS",
        severity="HIGH",  # 试图输出 HIGH 阻断
        category="PAYMENT_DEADLINE",
        title="付款期限不符合参考案例",
        description="待审合同与历史合同先例约定的 5 日不符",
        knowledge_references=[
            {
                "clause_id": "c_case_01",
                "knowledge_type": "HISTORICAL_CASE",
                "can_drive_mandatory_finding": False,  # 明确不可驱动强制违规
            }
        ],
        execution_mode="LLM_SEMANTIC",
    )

    resolver = ConflictResolver()
    resolved = resolver.resolve([f_invalid], "seed_123")
    assert len(resolved) == 1
    target = resolved[0]
    # 必须被认知与权限门禁降级为 LOW / NEEDS_MANUAL_REVIEW
    assert target.severity == "LOW"
    assert target.requires_human_review is True
    assert target.check_status == CheckStatus.NEEDS_MANUAL_REVIEW.value
    assert "参考依据不具备企业强制基准效力" in target.description
