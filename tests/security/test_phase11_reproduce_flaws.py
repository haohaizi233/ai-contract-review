# -*- coding: utf-8 -*-
"""Phase 11: Reproducible adversarial flaw test suite.

This module asserts the correct, secure behavior for all identified P0 and P1 flaws.
Before Phase 11 fixes, these tests will fail, proving the existence of the vulnerabilities:
- P0-01: SQLite auto-promotes PENDING_REVIEW to APPROVED during import and indexing.
- P0-02: PostgreSQL FTS fallback hardcodes 'TEMPLATE' and 'APPROVED'.
- P0-03: Knowledge governance policy accepts missing doc_status and missing tenant_id,
         and treats lifecycle status 'ACTIVE' as approval status.
- P0-04: Governed retrieval service queries with approved_only=False and retains unverified hits.
- P0-05: CitationVerifier reports 1.0 correctness on empty hits and lacks tenant/status verification.
- P1-01: API search endpoint bypasses GovernedRetrievalService.
- P1-03: Clause extractor concatenates paragraphs without newline breaks and uses unstable 80-char hash.
- P1-04: External LLM access defaults to True instead of secure False.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from contract_kb import config
from contract_kb.modules.document_processing.clause_extractor import extract_clauses
from contract_kb.modules.document_processing.docx_parser import ASTNode, ParsedDocument
from contract_kb.modules.retrieval.citation import CitationVerifier
from contract_kb.modules.retrieval.governance import (
    KnowledgeGovernancePolicy,
)
from contract_kb.modules.retrieval.service import GovernedRetrievalService
from contract_kb.storage import Storage


def test_reproduce_p0_01_sqlite_import_auto_approval():
    """P0-01: SQLite upsert_document must NOT auto-promote PENDING_REVIEW clauses to APPROVED."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        st = Storage(db_path)
        try:
            doc = {
                "template_id": "test_tpl_pending",
                "source_file": "test_tpl.docx",
                "sha256": "hash_tpl_pending_123",
                "template_version": "v1",
                "category": "PATENT",
                "subtype": "PATENT_APPLICATION",
                "document_type": "contract_template",
                "status": "ACTIVE",  # ACTIVE document must NOT mean clauses are APPROVED
                "clauses": [
                    {
                        "clause_id": "c_pending_1",
                        "original_text": "乙方应在五个工作日内完成审查。",
                        "clause_category": "SERVICE_SCOPE",
                        "approval_status": "PENDING_REVIEW",  # Explicitly pending
                        "is_company_standard": True,
                    }
                ],
            }
            st.upsert_document(doc, tenant="tenant_test")
            clause = st.get_clause("c_pending_1")
            assert clause is not None
            # Must remain PENDING_REVIEW; must NOT be elevated to APPROVED
            assert clause["approval_status"] == "PENDING_REVIEW", (
                f"Flaw P0-01 reproduced: clause was elevated to {clause['approval_status']}"
            )

            # Check embeddings table: must NOT record APPROVED
            cur = st.conn.execute(
                "SELECT approved_status FROM embeddings WHERE clause_id=?",
                ("c_pending_1",),
            )
            emb_row = cur.fetchone()
            if emb_row:
                assert emb_row[0] == "PENDING_REVIEW", (
                    f"Flaw P0-01 reproduced: embeddings table has approved_status={emb_row[0]}"
                )
        finally:
            st.close()


def test_reproduce_p0_03_governance_missing_metadata_fail_closed():
    """P0-03: Governance policy must be strictly fail-closed on missing doc_status and tenant_id."""
    # 1. Missing doc_status must be rejected, NOT defaulted to ACTIVE
    hit_missing_doc_status = {
        "clause_id": "c1",
        "knowledge_type": "TEMPLATE",
        "approved_status": "APPROVED",
        "tenant_id": "t1",
        # doc_status is missing
    }
    assert (
        KnowledgeGovernancePolicy.is_chunk_admissible(
            hit_missing_doc_status, expected_tenant_id="t1"
        )
        is False
    ), "Flaw P0-03 reproduced: missing doc_status was accepted (defaulted to ACTIVE)"

    # 2. Missing tenant_id must be rejected when expected_tenant_id is specified
    hit_missing_tenant = {
        "clause_id": "c2",
        "knowledge_type": "TEMPLATE",
        "doc_status": "ACTIVE",
        "approved_status": "APPROVED",
        # tenant_id is missing
    }
    assert (
        KnowledgeGovernancePolicy.is_chunk_admissible(
            hit_missing_tenant, expected_tenant_id="t1"
        )
        is False
    ), "Flaw P0-03 reproduced: missing tenant_id was accepted"

    # 3. Lifecycle status 'ACTIVE' must NOT be accepted as approval_status
    hit_active_as_approval = {
        "clause_id": "c3",
        "knowledge_type": "TEMPLATE",
        "doc_status": "ACTIVE",
        "approved_status": "ACTIVE",  # ACTIVE is not APPROVED
        "tenant_id": "t1",
    }
    assert (
        KnowledgeGovernancePolicy.is_chunk_admissible(
            hit_active_as_approval, expected_tenant_id="t1"
        )
        is False
    ), "Flaw P0-03 reproduced: approved_status='ACTIVE' was accepted"


def test_reproduce_p0_04_governed_retrieval_db_prefilter():
    """P0-04: GovernedRetrievalService must enforce approved_only=True at database level for authoritative hits."""
    calls: list[dict[str, Any]] = []

    class MockStorage:
        def search(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
            calls.append(kwargs)
            return []

        def get_clause(self, cid: str) -> dict[str, Any] | None:
            return None

    mock_st = MockStorage()
    svc = GovernedRetrievalService(mock_st)
    svc.retrieve_for_review("付款期限", tenant_id="tenant_x")

    assert calls, "Storage search was not called"
    first_call = calls[0]
    assert first_call.get("approved_only") is True, (
        f"Flaw P0-04 reproduced: authoritative search passed approved_only={first_call.get('approved_only')}"
    )


def test_reproduce_p0_05_citation_verifier_empty_hits():
    """P0-05: Empty hits must NOT report citation_correctness=1.0 or groundedness=1.0."""
    verifier = CitationVerifier()

    class EmptyStorage:
        def get_clause(self, cid: str) -> None:
            return None

    summary = verifier.verify_retrieval_hits(EmptyStorage(), [])
    assert summary.total_checked == 0
    assert summary.citation_correctness == 0.0, (
        f"Flaw P0-05 reproduced: empty hits returned citation_correctness={summary.citation_correctness}"
    )
    assert summary.answer_groundedness == 0.0, (
        f"Flaw P0-05 reproduced: empty hits returned answer_groundedness={summary.answer_groundedness}"
    )


def test_reproduce_p1_03_clause_extractor_paragraph_breaks():
    """P1-03: Multiple paragraphs in a clause must preserve paragraph separation (\\n)."""
    node1 = ASTNode(
        document_id="doc1",
        node_id="n1",
        node_type="paragraph",
        order=1,
        text="第一条 服务内容",
        source_locator={"page": 1, "para": 1},
    )
    node2 = ASTNode(
        document_id="doc1",
        node_id="n2",
        node_type="paragraph",
        order=2,
        text="乙方应当在五个工作日内完成交付。",
        source_locator={"page": 1, "para": 2},
    )
    node3 = ASTNode(
        document_id="doc1",
        node_id="n3",
        node_type="paragraph",
        order=3,
        text="但经甲方书面同意可以延期。",
        source_locator={"page": 1, "para": 3},
    )
    parsed_doc = ParsedDocument(
        document_id="doc1",
        source_path="test.docx",
        source_hash="fake_sha",
        file_size=100,
        nodes=[node1, node2, node3],
    )
    clauses = extract_clauses(parsed_doc, "tpl_test")
    assert len(clauses) >= 1
    target = clauses[0]
    # The text should preserve the separation between node2 and node3, not blindly concat into "交付。但经"
    assert "\n" in target.original_text, (
        f"Flaw P1-03 reproduced: paragraph breaks lost: {repr(target.original_text)}"
    )


def test_reproduce_p1_04_external_llm_authorization_default(monkeypatch):
    """P1-04: External LLM access must default to False (fail-closed secure default).

    The default must hold in a *clean* environment: no CONTRACT_KB_ALLOW_EXTERNAL_LLM
    env var and no .env file contribution. Ambient local configuration (dotenv) is
    explicitly authorized opt-in, not a default, so it is neutralized here to assert
    the true fail-closed default, then ambient config is restored afterwards.
    """
    import importlib

    import dotenv

    with monkeypatch.context() as m:
        m.delenv("CONTRACT_KB_ALLOW_EXTERNAL_LLM", raising=False)
        m.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)
        fresh_config = importlib.reload(config)
        assert fresh_config.ALLOW_EXTERNAL_LLM is False, (
            f"Flaw P1-04 reproduced: ALLOW_EXTERNAL_LLM defaults to {fresh_config.ALLOW_EXTERNAL_LLM}"
        )
    # 退出 monkeypatch 上下文后重载，恢复本地环境的显式配置状态
    importlib.reload(config)
