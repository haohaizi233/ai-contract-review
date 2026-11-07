# -*- coding: utf-8 -*-
"""安全与防御测试：失效与未审批过期证据拦截测试（Stale & Unapproved Evidence Blocking）。

验证目标：
1. 处于 REVOKED、SUPERSEDED、DRAFT、PENDING_REVIEW 状态的知识条目严禁进入权威检索通道；
2. 历史废止版本的条款不能被装配进 EvidenceBundle 的 authoritative_clauses；
3. 即使作为参考知识召回，也严禁用于驱动强制性违约发现（can_drive_mandatory_finding=False）；
4. `CitationVerifier` 对失效/未审批条目给出正确的未核验判定。
"""

from __future__ import annotations

from pathlib import Path
from contract_kb.modules.retrieval.citation import CitationVerifier
from contract_kb.modules.retrieval.governance import KnowledgeGovernancePolicy
from contract_kb.modules.retrieval.service import GovernedRetrievalService
from contract_kb.storage import Storage


def test_stale_and_unapproved_evidence_blocking(tmp_path: Path):
    db_path = tmp_path / "stale_evidence_sec.sqlite"
    st = Storage(db_path)

    # 1. 录入一份已废止（REVOKED）的旧标准模板
    st.upsert_document(
        {
            "template_id": "doc_revoked_2020",
            "source_file": "旧版2020技术开发标准.docx",
            "sha256": "hash_revoked_2020",
            "category": "TECH_DEVELOPMENT",
            "status": "REVOKED",  # 文档被废止
            "approval_status": "APPROVED",
            "clauses": [
                {
                    "clause_id": "c_revoked_old_rate",
                    "clause_category": "LIABILITY",
                    "original_text": "旧标准：违约金按每日百分之一计算。",
                    "approval_status": "APPROVED",
                }
            ],
        }
    )

    # 2. 录入一份草稿（DRAFT）未审批的标准模板
    st.upsert_document(
        {
            "template_id": "doc_draft_2026",
            "source_file": "草拟2026新规.docx",
            "sha256": "hash_draft_2026",
            "category": "TECH_DEVELOPMENT",
            "document_type": "contract_template",
            "status": "ACTIVE",
            "approval_status": "PENDING_REVIEW",  # 法务未审批
            "clauses": [
                {
                    "clause_id": "c_draft_unapproved",
                    "clause_category": "LIABILITY",
                    "original_text": "法务未审批草拟条款：单方违约赔偿两倍金额。",
                    "approval_status": "PENDING_REVIEW",
                    "knowledge_type": "TEMPLATE",
                }
            ],
        }
    )

    # 3. 录入一份正式生效的现行标准
    did_active, _ = st.upsert_document(
        {
            "template_id": "doc_active_approved",
            "source_file": "现行正式技术标准.docx",
            "sha256": "hash_active_2025",
            "category": "TECH_DEVELOPMENT",
            "document_type": "contract_template",
            "status": "ACTIVE",
            "approval_status": "APPROVED",
            "clauses": [
                {
                    "clause_id": "c_active_approved",
                    "clause_category": "LIABILITY",
                    "original_text": "现行有效标准：违约金按每日千分之三计算。",
                    "approval_status": "APPROVED",
                    "knowledge_type": "TEMPLATE",
                    "is_company_standard": True,
                }
            ],
        }
    )

    st.approve_knowledge_version(
        tenant_id="default",
        document_id=did_active,
        approver_id="legal_admin",
        approval_reason="正式审批现行有效标准",
    )
    svc = GovernedRetrievalService(st)

    # 4. 执行权威检索，要求仅能召回现行已审批标准
    auth_hits = svc.retrieve_authoritative(
        "技术开发合同违约金计算标准",
        top_k=5,
    )

    # 验证：仅有 c_active_approved 能进入权威结果
    clause_ids = [h["clause_id"] for h in auth_hits]
    assert "c_active_approved" in clause_ids
    assert "c_revoked_old_rate" not in clause_ids
    assert "c_draft_unapproved" not in clause_ids

    # 5. 校验 KnowledgeGovernancePolicy 对失效数据的过滤守卫
    revoked_hit = {
        "clause_id": "c_revoked_old_rate",
        "doc_status": "REVOKED",
        "approved_status": "APPROVED",
        "knowledge_type": "TEMPLATE",
    }
    assert not KnowledgeGovernancePolicy.is_chunk_admissible(
        revoked_hit, allow_historical_cases=False
    )

    draft_hit = {
        "clause_id": "c_draft_unapproved",
        "doc_status": "ACTIVE",
        "approved_status": "PENDING_REVIEW",
        "knowledge_type": "TEMPLATE",
    }
    assert not KnowledgeGovernancePolicy.is_chunk_admissible(
        draft_hit, allow_historical_cases=False
    )

    # 6. 引文核验器对未审批与已废止条款拒绝通过
    verifier = CitationVerifier()
    summary = verifier.verify_retrieval_hits(
        st,
        [
            {
                "clause_id": "c_revoked_old_rate",
                "text": "旧标准：违约金按每日百分之一计算。",
                "knowledge_type": "TEMPLATE",
                "tenant_id": "default",
            },
            {
                "clause_id": "c_draft_unapproved",
                "text": "法务未审批草拟条款：单方违约赔偿两倍金额。",
                "knowledge_type": "TEMPLATE",
                "tenant_id": "default",
            },
            {
                "clause_id": "c_active_approved",
                "text": "现行有效标准：违约金按每日千分之三计算。",
                "knowledge_type": "TEMPLATE",
                "tenant_id": "default",
            },
        ],
        expected_tenant_id="default",
    )

    assert summary.total_checked == 3
    assert summary.verified_count == 1
    assert "c_revoked_old_rate" in summary.unverified_ids
    assert "c_draft_unapproved" in summary.unverified_ids
