# -*- coding: utf-8 -*-
"""Phase 12 独立多路检索通道与权威知识分类隔离契约测试。"""

from __future__ import annotations

from pathlib import Path
import pytest

from contract_kb.modules.retrieval.governance import KnowledgeSourceType
from contract_kb.modules.retrieval.service import GovernedRetrievalService
from contract_kb.storage import Storage
from contract_kb.storage_pg import PgVectorStorage


def test_storage_implements_separated_recall_channels(tmp_path: Path) -> None:
    """验证 Storage 与 PgVectorStorage 均提供物理分离的多路候选召回接口。"""
    st = Storage(tmp_path / "channels.db")
    assert hasattr(st, "search_lexical_candidates")
    assert hasattr(st, "search_dense_candidates")
    assert hasattr(st, "search_structured_candidates")
    st.close()

    assert hasattr(PgVectorStorage, "search_lexical_candidates")
    assert hasattr(PgVectorStorage, "search_dense_candidates")
    assert hasattr(PgVectorStorage, "search_structured_candidates")


def test_retrieve_authoritative_strictly_excludes_non_binding_references(tmp_path: Path) -> None:
    """验证 retrieve_authoritative 严格排除 LEGAL_REFERENCE 与 HISTORICAL_CASE，只允许 TEMPLATE 与 APPROVED_RULE。"""
    st = Storage(tmp_path / "auth_test.db")

    # 1. 录入一份标准模板条款 (TEMPLATE) - 审批通过
    doc_template = {
        "template_id": "tpl_std",
        "source_file": "template.docx",
        "sha256": "a" * 64,
        "status": "ACTIVE",
        "clauses": [
            {
                "clause_id": "c_tpl_1",
                "clause_category": "LIABILITY",
                "original_text": "标准违约金条款：每日万分之五。",
                "knowledge_type": "TEMPLATE",
            }
        ],
    }
    did_tpl, _ = st.upsert_document(doc_template, tenant="tenant_1")
    st.approve_knowledge_version(tenant_id="tenant_1", document_id=did_tpl, approver_id="admin")

    # 2. 录入一份外部法规参考 (LEGAL_REFERENCE) - 即使审批通过，也不得进入权威标准集合
    doc_legal = {
        "template_id": "law_ref",
        "source_file": "civil_code.docx",
        "sha256": "b" * 64,
        "status": "ACTIVE",
        "clauses": [
            {
                "clause_id": "c_law_1",
                "clause_category": "LIABILITY",
                "original_text": "《民法典》违约金参考规定。",
                "knowledge_type": "LEGAL_REFERENCE",
            }
        ],
    }
    did_law, _ = st.upsert_document(doc_legal, tenant="tenant_1")
    st.approve_knowledge_version(tenant_id="tenant_1", document_id=did_law, approver_id="admin")

    # 3. 录入一份历史合同案卷 (HISTORICAL_CASE)
    doc_hist = {
        "template_id": "hist_case",
        "source_file": "history.docx",
        "sha256": "c" * 64,
        "status": "ACTIVE",
        "clauses": [
            {
                "clause_id": "c_hist_1",
                "clause_category": "LIABILITY",
                "original_text": "某历史合同关于违约金的协商条款。",
                "knowledge_type": "HISTORICAL_CASE",
            }
        ],
    }
    did_hist, _ = st.upsert_document(doc_hist, tenant="tenant_1")

    svc = GovernedRetrievalService(st)

    # 执行权威标准检索
    auth_hits = svc.retrieve_authoritative("违约金", tenant_id="tenant_1")

    # 断言：权威集合中只包含 TEMPLATE 或 APPROVED_RULE，坚决排除 LEGAL_REFERENCE 与 HISTORICAL_CASE
    assert len(auth_hits) > 0
    for h in auth_hits:
        assert h["knowledge_type"] in (
            KnowledgeSourceType.TEMPLATE.value,
            KnowledgeSourceType.APPROVED_RULE.value,
        )
        assert h["knowledge_type"] != KnowledgeSourceType.LEGAL_REFERENCE.value
        assert h["knowledge_type"] != KnowledgeSourceType.HISTORICAL_CASE.value
        assert h["can_drive_mandatory_finding"] is True

    st.close()
