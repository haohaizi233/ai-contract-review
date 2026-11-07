# -*- coding: utf-8 -*-
"""安全与防御测试：跨租户检索与上下文扩展越权防御（Cross-Tenant Retrieval & Expansion Guard）。

验证目标：
1. 条款检索（Lexical / Dense / Cross-Ref）绝对无法越权召回其他租户的私有合同与内部规则；
2. 即使待审合同伪造跨条款引用指向另一租户的敏感条款，多粒度扩展与引文核验层也必须坚决阻断；
3. `GovernedRetrievalService` 在组装 `EvidenceBundle` 时严格执行租户前置过滤与后验清洗。
"""

from __future__ import annotations

from pathlib import Path
from contract_kb.modules.retrieval.governance import KnowledgeGovernancePolicy
from contract_kb.modules.retrieval.service import GovernedRetrievalService
from contract_kb.storage import Storage


def test_cross_tenant_evidence_expansion_blocked(tmp_path: Path):
    db_path = tmp_path / "cross_tenant_sec.sqlite"
    st = Storage(db_path)

    # 1. 租户 tenant_victim（受害租户）录入包含机密商业机密的条款
    st.upsert_document(
        {
            "template_id": "victim_confidential_doc",
            "source_file": "Victim_Secret_Strategy.docx",
            "sha256": "hash_victim_sec",
            "category": "TECH_SERVICE",
            "status": "ACTIVE",
            "approval_status": "APPROVED",
            "clauses": [
                {
                    "clause_id": "victim_secret_c1",
                    "clause_number": "8.8",
                    "clause_category": "CONFIDENTIALITY",
                    "original_text": "Victim内部绝密：本公司核心算法源码保密期为永久，泄露违约金为五千万元。",
                    "approval_status": "APPROVED",
                    "is_company_standard": True,
                }
            ],
        },
        tenant="tenant_victim",
    )

    # 2. 租户 tenant_attacker（攻击者租户）录入普通条款
    st.upsert_document(
        {
            "template_id": "attacker_doc",
            "source_file": "Attacker_Standard.docx",
            "sha256": "hash_attacker_sec",
            "category": "TECH_SERVICE",
            "status": "ACTIVE",
            "approval_status": "APPROVED",
            "clauses": [
                {
                    "clause_id": "attacker_c1",
                    "clause_number": "8.8",
                    "clause_category": "CONFIDENTIALITY",
                    "original_text": "本合同保密条款按双方约定履行。",
                    "approval_status": "APPROVED",
                    "is_company_standard": True,
                }
            ],
        },
        tenant="tenant_attacker",
    )

    svc = GovernedRetrievalService(st)

    # 3. 攻击者以自身租户身份搜索，查询包含 victim 绝密关键字
    attacker_hits = svc.retrieve_authoritative(
        "核心算法源码保密期为永久 违约金五千万元",
        top_k=5,
        tenant_id="tenant_attacker",
    )

    # 验证：绝不召回 tenant_victim 的任何记录
    for h in attacker_hits:
        assert h.get("tenant_id") == "tenant_attacker"
        assert h.get("document_id") != "victim_confidential_doc"
        assert "五千万元" not in (h.get("text") or h.get("original_text") or "")

    # 4. 待审条款伪造援引指向 victim 的 clause_number "8.8"
    clause_with_forged_ref = {
        "clause_id": "target_clause_1",
        "clause_number": "1.1",
        "clause_category": "CONFIDENTIALITY",
        "original_text": "保密事项详见第8.8款之特别约定。",
        "conditions": [],
        "exceptions": [],
    }

    bundle = svc.retrieve_for_clause(
        clause_with_forged_ref,
        top_k=3,
        tenant_id="tenant_attacker",
    )

    # 验证：EvidenceBundle 中 cross_referenced_clauses 与 authoritative_clauses 绝无 victim 数据
    for ac in bundle.authoritative_clauses:
        assert ac.get("tenant_id", "tenant_attacker") == "tenant_attacker"
        assert ac.get("document_id") != "victim_confidential_doc"

    for crc in bundle.cross_referenced_clauses:
        assert crc.get("tenant_id", "tenant_attacker") == "tenant_attacker"
        assert crc.get("document_id") != "victim_confidential_doc"

    # 5. 底层准入治理拦截策略强校验
    leaked_chunk = {
        "clause_id": "victim_secret_c1",
        "tenant_id": "tenant_victim",
        "doc_status": "ACTIVE",
        "approved_status": "APPROVED",
        "knowledge_type": "TEMPLATE",
    }
    assert not KnowledgeGovernancePolicy.is_chunk_admissible(
        leaked_chunk, expected_tenant_id="tenant_attacker"
    )
