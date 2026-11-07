# -*- coding: utf-8 -*-
"""P0-03 失败复现与安全测试：CitationVerifier 严格证据真实性核验与 Fail-Closed。

验证：
1. 缺少 document_id、source_hash、tenant_id 或 doc_status 时必须严格判为不通过（Fail-Closed）；
2. 来源哈希不匹配（HASH_MISMATCH）时必须拒绝；
3. 知识版本不一致（VERSION_MISMATCH）时必须拒绝；
4. 审批状态被撤销（APPROVAL_REVOKED）或未审批时必须拒绝；
5. 输出包含强类型 EvidenceVerificationResult，涵盖失败原因。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
import pytest

from contract_kb.storage import Storage
from contract_kb.modules.retrieval.citation import (
    CitationVerifier,
    EvidenceVerificationResult,
)


def _setup_test_db(tmpdir: Path) -> tuple[Storage, str]:
    st = Storage(tmpdir / "citation_test.db")
    doc = {
        "template_id": "std_template_001",
        "source_file": "std_template.docx",
        "sha256": "correct_hash_aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899",
        "template_version": "v2.0",
        "category": "PATENT",
        "subtype": "PATENT_APPLICATION",
        "document_type": "contract_template",
        "status": "ACTIVE",
        "clauses": [
            {
                "clause_id": "std_clause_101",
                "clause_number": "第十条",
                "clause_category": "FEE_AND_PAYMENT",
                "original_text": "甲方应在收到发票后十五个工作日内完成付款。",
            }
        ],
    }
    did, _ = st.upsert_document(doc, tenant="tenant_citation")
    st.approve_knowledge_version(
        tenant_id="tenant_citation",
        document_id=did,
        approver_id="legal_lead",
        approval_reason="已通过合规审查",
    )
    return st, did


def test_citation_verifier_returns_typed_evidence_result():
    """验证 CitationVerifier 输出包含强类型 EvidenceVerificationResult。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        st, did = _setup_test_db(Path(tmpdir))
        try:
            verifier = CitationVerifier()
            # 构造合法匹配的 hit
            hit_valid = {
                "clause_id": "std_clause_101",
                "document_id": did,
                "tenant_id": "tenant_citation",
                "source_hash": "correct_hash_aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899",
                "source_version": "v2.0",
                "knowledge_type": "TEMPLATE",
                "text": "甲方应在收到发票后十五个工作日内完成付款。",
            }
            res = verifier.verify_evidence_item(st, hit_valid, expected_tenant_id="tenant_citation")
            assert isinstance(res, EvidenceVerificationResult)
            assert res.verified is True
            assert res.status == "VERIFIED"
            assert res.clause_id == "std_clause_101"
            assert not res.failure_reasons
        finally:
            st.close()


def test_citation_hash_mismatch_fails_closed():
    """验证当外部引用的 source_hash 与数据库真实哈希不一致时，严格判定为 HASH_MISMATCH。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        st, did = _setup_test_db(Path(tmpdir))
        try:
            verifier = CitationVerifier()
            hit_tampered = {
                "clause_id": "std_clause_101",
                "document_id": did,
                "tenant_id": "tenant_citation",
                "source_hash": "tampered_fake_hash_9999999999999999999999999999999999999999999999999999",
                "source_version": "v2.0",
                "knowledge_type": "TEMPLATE",
                "text": "甲方应在收到发票后十五个工作日内完成付款。",
            }
            res = verifier.verify_evidence_item(st, hit_tampered, expected_tenant_id="tenant_citation")
            assert res.verified is False
            assert res.status == "HASH_MISMATCH"
            assert any("哈希不一致" in r or "hash" in r.lower() for r in res.failure_reasons)
        finally:
            st.close()


def test_citation_revoked_source_fails_closed():
    """验证来源条款被撤销后，证据核验必须失败（APPROVAL_REVOKED），不能作为有效依据。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        st, did = _setup_test_db(Path(tmpdir))
        try:
            verifier = CitationVerifier()
            # 撤销该文档的审批状态
            st.revoke_knowledge_version(
                tenant_id="tenant_citation",
                document_id=did,
                revoker_id="legal_lead",
                revoke_reason="紧急撤回瑕疵条款",
            )

            hit_revoked = {
                "clause_id": "std_clause_101",
                "document_id": did,
                "tenant_id": "tenant_citation",
                "source_hash": "correct_hash_aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899",
                "source_version": "v2.0",
                "knowledge_type": "TEMPLATE",
                "text": "甲方应在收到发票后十五个工作日内完成付款。",
            }
            res = verifier.verify_evidence_item(st, hit_revoked, expected_tenant_id="tenant_citation")
            assert res.verified is False
            assert res.status == "APPROVAL_REVOKED"
        finally:
            st.close()


def test_citation_missing_fields_fails_closed():
    """验证缺少 tenant_id 或 document_id 时，严禁默认补齐放行，必须 Fail-Closed。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        st, did = _setup_test_db(Path(tmpdir))
        try:
            verifier = CitationVerifier()
            # 1. 缺失 tenant_id
            hit_no_tenant = {
                "clause_id": "std_clause_101",
                "document_id": did,
                "text": "甲方应在收到发票后十五个工作日内完成付款。",
            }
            res = verifier.verify_evidence_item(st, hit_no_tenant)
            assert res.verified is False
            assert res.status == "MISSING_TENANT"

            # 2. 跨租户不匹配
            hit_wrong_tenant = {
                "clause_id": "std_clause_101",
                "document_id": did,
                "tenant_id": "tenant_other",
                "text": "甲方应在收到发票后十五个工作日内完成付款。",
            }
            res_wrong = verifier.verify_evidence_item(st, hit_wrong_tenant, expected_tenant_id="tenant_citation")
            assert res_wrong.verified is False
            assert res_wrong.status == "TENANT_MISMATCH"

            # 3. 文档 ID 不匹配
            hit_wrong_doc = {
                "clause_id": "std_clause_101",
                "document_id": "some_other_doc",
                "tenant_id": "tenant_citation",
                "text": "甲方应在收到发票后十五个工作日内完成付款。",
            }
            res_doc = verifier.verify_evidence_item(st, hit_wrong_doc, expected_tenant_id="tenant_citation")
            assert res_doc.verified is False
            assert res_doc.status == "DOCUMENT_MISMATCH"
        finally:
            st.close()
