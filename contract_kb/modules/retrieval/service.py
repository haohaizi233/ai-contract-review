# -*- coding: utf-8 -*-
"""企业受控知识检索应用服务（GovernedRetrievalService）：
实现元数据强制前置过滤、权威源与历史案卷隔离检索、RRF 融合、可插拔 Reranker 与严格引文核验。
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
    """受控混合检索编排服务。"""

    def __init__(
        self,
        storage: Any,
        reranker: RerankerPort | None = None,
        citation_verifier: CitationVerifier | None = None,
    ):
        self.storage = storage
        self.reranker = reranker or LexicalCoverageReranker()
        self.citation_verifier = citation_verifier or CitationVerifier()

    def retrieve_authoritative(
        self,
        query: str,
        *,
        top_k: int = 6,
        business_category: str | None = None,
        contract_subtype: str | None = None,
        clause_number: str | None = None,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """权威标准通道：数据库 SQL 层强制 approved_only=True 与 tenant_id 预过滤。"""
        raw_auth = self.storage.search(
            query,
            top_k=top_k * 2,
            business_category=business_category,
            contract_subtype=contract_subtype,
            clause_number=clause_number,
            approved_only=True,
            rerank=False,
            tenant_id=tenant_id,
        )
        if not raw_auth and (business_category or contract_subtype):
            raw_auth = self.storage.search(
                query,
                top_k=top_k * 2,
                approved_only=True,
                rerank=False,
                tenant_id=tenant_id,
            )

        admissible = [
            KnowledgeGovernancePolicy.annotate_hit(h)
            for h in raw_auth
            if KnowledgeGovernancePolicy.is_chunk_admissible(
                h,
                allow_historical_cases=False,
                expected_tenant_id=tenant_id,
            )
        ]
        return self.reranker.rerank(query, admissible, top_k=top_k)

    def retrieve_historical_reference(
        self,
        query: str,
        *,
        top_k: int = 2,
        business_category: str | None = None,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """历史案卷先例通道：仅作为参考，严禁作为强制违规标准。"""
        if top_k <= 0:
            return []
        raw_hist = self.storage.search(
            query,
            top_k=top_k * 2,
            business_category=business_category,
            knowledge_type=KnowledgeSourceType.HISTORICAL_CASE.value,
            approved_only=False,
            rerank=False,
            tenant_id=tenant_id,
        )
        admissible = [
            KnowledgeGovernancePolicy.annotate_hit(h)
            for h in raw_hist
            if KnowledgeGovernancePolicy.is_chunk_admissible(
                h,
                allow_historical_cases=True,
                expected_tenant_id=tenant_id,
            )
        ]
        return self.reranker.rerank(query, admissible, top_k=top_k)

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
        """为合同审核流水线执行分层受控检索，自动剔除引文核验失败候选。"""
        ranked_auth = self.retrieve_authoritative(
            query,
            top_k=top_k,
            business_category=business_category,
            contract_subtype=contract_subtype,
            clause_number=clause_number,
            tenant_id=tenant_id,
        )

        ranked_hist: list[dict[str, Any]] = []
        if include_historical_cases:
            ranked_hist = self.retrieve_historical_reference(
                query,
                top_k=historical_top_k,
                business_category=business_category,
                tenant_id=tenant_id,
            )

        summary = self.citation_verifier.verify_retrieval_hits(
            self.storage,
            ranked_auth + ranked_hist,
            expected_tenant_id=tenant_id,
        )

        # 严格证据链门禁：未通过引文与来源核验的候选，坚决剔除出权威提示词上下文！
        unverified_set = set(summary.unverified_ids)
        verified_auth = [
            h
            for h in ranked_auth
            if str(h.get("clause_id") or h.get("chunk_id") or "") not in unverified_set
        ]
        verified_hist = [
            h
            for h in ranked_hist
            if str(h.get("clause_id") or h.get("chunk_id") or "") not in unverified_set
        ]

        return GovernedRetrievalBundle(
            authoritative_hits=verified_auth,
            historical_reference_hits=verified_hist,
            citation_summary=summary,
        )

    def search_user_visible_knowledge(
        self,
        query: str,
        *,
        top_k: int = 8,
        business_category: str | None = None,
        contract_subtype: str | None = None,
        clause_number: str | None = None,
        keyword: str | None = None,
        rerank: bool = True,
        tenant_id: str | None = None,
        include_unapproved: bool = False,
    ) -> list[dict[str, Any]]:
        """用户端知识库检索入口：默认仅返回已获审批的标准知识，附带权威治理元数据。"""
        approved_only = not include_unapproved
        hits = self.storage.search(
            query,
            top_k=top_k * 2,
            business_category=business_category,
            contract_subtype=contract_subtype,
            clause_number=clause_number,
            keyword=keyword,
            approved_only=approved_only,
            rerank=False,
            tenant_id=tenant_id,
        )
        admissible = [
            KnowledgeGovernancePolicy.annotate_hit(h)
            for h in hits
            if KnowledgeGovernancePolicy.is_chunk_admissible(
                h,
                allow_historical_cases=True,
                expected_tenant_id=tenant_id,
            )
        ]
        if rerank:
            return self.reranker.rerank(query, admissible, top_k=top_k)
        return admissible[:top_k]
