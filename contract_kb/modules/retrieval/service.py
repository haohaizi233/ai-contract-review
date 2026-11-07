# -*- coding: utf-8 -*-
"""企业受控知识检索应用服务（GovernedRetrievalService）：
实现元数据预过滤、权威源与历史案卷隔离检索、RRF 融合、可插拔 Reranker 与引文核验。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .citation import CitationVerificationSummary, CitationVerifier
from .governance import KnowledgeGovernancePolicy, KnowledgeSourceType
from .reranker import LexicalCoverageReranker, RerankerPort


@dataclass
class GovernedRetrievalBundle:
    """分层受控检索输出包：明确隔离权威标准依据与历史合同参考先例。"""

    authoritative_hits: list[dict[str, Any]] = field(default_factory=list)
    historical_reference_hits: list[dict[str, Any]] = field(default_factory=list)
    citation_summary: CitationVerificationSummary | None = None

    def all_prompt_context(self) -> list[dict[str, Any]]:
        """合并为带权威标识的 Prompt 参考列表（权威条款在前，历史参考在后）。"""
        return list(self.authoritative_hits) + list(self.historical_reference_hits)


class GovernedRetrievalService:
    """受控混合检索编排器。"""

    def __init__(
        self,
        storage: Any,
        reranker: RerankerPort | None = None,
        citation_verifier: CitationVerifier | None = None,
    ):
        self.storage = storage
        self.reranker = reranker or LexicalCoverageReranker()
        self.citation_verifier = citation_verifier or CitationVerifier()

    def retrieve_for_review(
        self,
        query: str,
        *,
        top_k: int = 6,
        business_category: str | None = None,
        contract_subtype: str | None = None,
        clause_number: str | None = None,
        tenant_id: str | None = None,
        include_historical_cases: bool = False,
        historical_top_k: int = 2,
    ) -> GovernedRetrievalBundle:
        # 1. 权威标准通道（仅限 ACTIVE 的 TEMPLATE / APPROVED_RULE，严禁混入 HISTORICAL_CASE 与 DRAFT）
        raw_auth = self.storage.search(
            query,
            top_k=top_k * 2,
            business_category=business_category,
            contract_subtype=contract_subtype,
            clause_number=clause_number,
            approved_only=False,
            rerank=False,
            tenant_id=tenant_id,
        )
        # 若指定子类型无匹配，放宽至同大类或全局现行模板
        if not raw_auth and (business_category or contract_subtype):
            raw_auth = self.storage.search(
                query,
                top_k=top_k * 2,
                approved_only=False,
                rerank=False,
                tenant_id=tenant_id,
            )

        admissible_auth = [
            KnowledgeGovernancePolicy.annotate_hit(h)
            for h in raw_auth
            if KnowledgeGovernancePolicy.is_chunk_admissible(
                h,
                allow_historical_cases=False,
                expected_tenant_id=tenant_id,
            )
        ]
        ranked_auth = self.reranker.rerank(query, admissible_auth, top_k=top_k)

        # 2. 历史案卷先例通道（仅在显式开启时单独召回，强制标记 can_drive_mandatory_finding=False）
        ranked_hist: list[dict[str, Any]] = []
        if include_historical_cases and historical_top_k > 0:
            raw_hist = self.storage.search(
                query,
                top_k=historical_top_k * 2,
                business_category=business_category,
                knowledge_type=KnowledgeSourceType.HISTORICAL_CASE.value,
                approved_only=False,
                rerank=False,
                tenant_id=tenant_id,
            )
            admissible_hist = [
                KnowledgeGovernancePolicy.annotate_hit(h)
                for h in raw_hist
                if KnowledgeGovernancePolicy.is_chunk_admissible(
                    h,
                    allow_historical_cases=True,
                    expected_tenant_id=tenant_id,
                )
            ]
            ranked_hist = self.reranker.rerank(
                query, admissible_hist, top_k=historical_top_k
            )

        summary = self.citation_verifier.verify_retrieval_hits(
            self.storage, ranked_auth + ranked_hist
        )
        return GovernedRetrievalBundle(
            authoritative_hits=ranked_auth,
            historical_reference_hits=ranked_hist,
            citation_summary=summary,
        )
