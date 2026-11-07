# -*- coding: utf-8 -*-
"""企业受控知识检索应用服务（GovernedRetrievalService）：
实现元数据强制前置过滤、权威源与历史案卷隔离检索、多路混合候选召回、RRF 融合、可插拔 Reranker 与条款级证据包绑定。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .citation import CitationVerificationSummary, CitationVerifier
from .governance import KnowledgeGovernancePolicy, KnowledgeSourceType
from .query_planner import ContractAwareQueryPlanner, RetrievalIntent
from .reranker import LexicalCoverageReranker, RerankerPort, reciprocal_rank_fusion


@dataclass
class GovernedRetrievalBundle:
    """分层受控检索输出包：明确隔离权威标准依据与历史合同参考先例。"""

    authoritative_hits: list[dict[str, Any]] = field(default_factory=list)
    historical_reference_hits: list[dict[str, Any]] = field(default_factory=list)
    citation_summary: CitationVerificationSummary | None = None

    def all_prompt_context(self) -> list[dict[str, Any]]:
        """合并为带权威标识的 Prompt 参考列表（权威条款在前，历史参考在后）。"""
        return list(self.authoritative_hits) + list(self.historical_reference_hits)


@dataclass
class EvidenceBundle:
    """强类型条款级证据包：与具体待审条款形成一一绑定的可溯源证据链。"""

    target_clause_id: str
    intents: list[RetrievalIntent] = field(default_factory=list)
    authoritative_clauses: list[dict[str, Any]] = field(default_factory=list)
    parent_clause: dict[str, Any] | None = None
    related_conditions: list[str] = field(default_factory=list)
    related_exceptions: list[str] = field(default_factory=list)
    cross_referenced_clauses: list[dict[str, Any]] = field(default_factory=list)
    historical_cases: list[dict[str, Any]] = field(default_factory=list)
    citation_summary: CitationVerificationSummary | None = None

    def prompt_context_text(self) -> str:
        """为下游大模型与差异对比引擎生成可验证的提示词证据片段。"""
        parts: list[str] = []
        if self.parent_clause:
            p_text = self.parent_clause.get("text") or self.parent_clause.get("original_text") or ""
            parts.append(f"【所属章条背景】{self.parent_clause.get('title', '')}：{p_text[:120]}")
        if self.related_conditions:
            parts.append(f"【通用前置条件】{'；'.join(self.related_conditions)}")
        for ac in self.authoritative_clauses:
            badge = ac.get("prompt_badge") or "[企业现行标准模板条款-权威基准]"
            cnum = ac.get("clause_number") or ""
            cid = ac.get("clause_id") or ""
            text = ac.get("text") or ac.get("original_text") or ""
            parts.append(f"{badge} {cnum} (ID:{cid})：{text}")
        for rc in self.cross_referenced_clauses:
            parts.append(f"[援引外部标准条款] {rc.get('clause_number', '')}：{rc.get('text', '')[:100]}")
        for hc in self.historical_cases:
            badge = hc.get("prompt_badge") or "[历史合同先例-仅供参考]"
            parts.append(f"{badge}：{hc.get('text', '')[:100]}")
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_clause_id": self.target_clause_id,
            "intents": [
                (i.model_dump() if hasattr(i, "model_dump") else i) for i in self.intents
            ],
            "authoritative_clauses": list(self.authoritative_clauses),
            "parent_clause": dict(self.parent_clause) if self.parent_clause else None,
            "related_conditions": list(self.related_conditions),
            "related_exceptions": list(self.related_exceptions),
            "cross_referenced_clauses": list(self.cross_referenced_clauses),
            "historical_cases": list(self.historical_cases),
            "citation_summary": (
                asdict(self.citation_summary) if self.citation_summary else None
            ),
        }


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

    def retrieve_for_intent(
        self,
        intent: RetrievalIntent,
        *,
        top_k: int = 3,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """依据强类型检索意图执行多路检索（词法 Channel A + 语义 Channel B + 跨引用 Channel D）。"""
        candidate_lists: list[list[dict[str, Any]]] = []

        # Channel A: Lexical queries
        for lq in intent.lexical_queries[:2]:
            hits = self.storage.search(
                lq,
                top_k=top_k * 2,
                business_category=intent.contract_category,
                contract_subtype=intent.contract_subtype,
                approved_only=True,
                rerank=False,
                tenant_id=tenant_id,
            )
            if hits:
                candidate_lists.append(hits)

        # Channel B: Semantic queries
        for sq in intent.semantic_queries[:2]:
            hits = self.storage.search(
                sq,
                top_k=top_k * 2,
                business_category=intent.contract_category,
                contract_subtype=intent.contract_subtype,
                approved_only=True,
                rerank=False,
                tenant_id=tenant_id,
            )
            if hits:
                candidate_lists.append(hits)

        # Channel D: Referenced clauses (e.g. 第十二条)
        for ref in intent.referenced_clauses[:2]:
            hits = self.storage.search(
                ref,
                top_k=top_k,
                clause_number=ref,
                approved_only=True,
                rerank=False,
                tenant_id=tenant_id,
            )
            if hits:
                candidate_lists.append(hits)

        if not candidate_lists:
            # 兜底查询
            primary_q = (
                intent.semantic_queries[0]
                if intent.semantic_queries
                else (intent.lexical_queries[0] if intent.lexical_queries else intent.intent_type)
            )
            hits = self.storage.search(
                primary_q,
                top_k=top_k * 2,
                approved_only=True,
                rerank=False,
                tenant_id=tenant_id,
            )
            candidate_lists.append(hits)

        # RRF 融合多路结果
        fused = reciprocal_rank_fusion(candidate_lists, rrf_k=60)

        # 严格执行治理准入过滤
        admissible = [
            KnowledgeGovernancePolicy.annotate_hit(h)
            for h in fused
            if KnowledgeGovernancePolicy.is_chunk_admissible(
                h,
                allow_historical_cases=False,
                expected_tenant_id=tenant_id,
            )
        ]

        rerank_query = (
            intent.semantic_queries[0]
            if intent.semantic_queries
            else " ".join(intent.lexical_queries[:2])
        )
        return self.reranker.rerank(rerank_query, admissible, top_k=top_k)

    def retrieve_for_clause(
        self,
        clause: dict[str, Any],
        *,
        top_k: int = 3,
        tenant_id: str | None = None,
        contract_category: str | None = None,
        contract_subtype: str | None = None,
        planner: ContractAwareQueryPlanner | None = None,
        knowledge_tree: Any | None = None,
    ) -> EvidenceBundle:
        """为单条待审条款执行完整的证据检索、上下文扩展与引文核验，输出结构化 EvidenceBundle。"""
        cid = str(clause.get("clause_id") or "unknown")
        p = planner or ContractAwareQueryPlanner()
        intents = p.plan_clause_intents_deterministic(
            clause,
            contract_category=contract_category,
            contract_subtype=contract_subtype,
        )

        all_auth_hits: list[dict[str, Any]] = []
        cross_ref_hits: list[dict[str, Any]] = []
        seen_cids: set[str] = set()

        for intent in intents:
            hits = self.retrieve_for_intent(intent, top_k=top_k, tenant_id=tenant_id)
            for h in hits:
                c_id = h.get("clause_id") or h.get("chunk_id")
                if c_id not in seen_cids:
                    seen_cids.add(c_id)
                    if intent.intent_type == "CROSS_CLAUSE_REFERENCE_CHECK":
                        cross_ref_hits.append(h)
                    else:
                        all_auth_hits.append(h)

        # 上下文扩展（若提供知识树）
        parent_node_dict: dict[str, Any] | None = None
        inherited_conds: list[str] = list(clause.get("conditions") or [])
        inherited_excs: list[str] = list(clause.get("exceptions") or [])
        if knowledge_tree is not None and hasattr(knowledge_tree, "expand_parent_child_context"):
            expanded = knowledge_tree.expand_parent_child_context(cid)
            if expanded.get("parent_article"):
                parent_node_dict = expanded["parent_article"].to_dict()
            for c_item in (expanded.get("inherited_conditions") or []):
                if c_item not in inherited_conds:
                    inherited_conds.append(c_item)
            for e_item in (expanded.get("inherited_exceptions") or []):
                if e_item not in inherited_excs:
                    inherited_excs.append(e_item)

        # 引文核验
        summary = self.citation_verifier.verify_retrieval_hits(
            self.storage,
            all_auth_hits + cross_ref_hits,
            expected_tenant_id=tenant_id,
        )
        unverified_set = set(summary.unverified_ids)
        verified_auth = [
            h
            for h in all_auth_hits
            if str(h.get("clause_id") or h.get("chunk_id") or "") not in unverified_set
        ]
        verified_cross = [
            h
            for h in cross_ref_hits
            if str(h.get("clause_id") or h.get("chunk_id") or "") not in unverified_set
        ]

        return EvidenceBundle(
            target_clause_id=cid,
            intents=intents,
            authoritative_clauses=verified_auth[:top_k],
            parent_clause=parent_node_dict,
            related_conditions=inherited_conds,
            related_exceptions=inherited_excs,
            cross_referenced_clauses=verified_cross[:2],
            historical_cases=[],
            citation_summary=summary,
        )

    def retrieve_contract_evidence_bundles(
        self,
        clauses: list[dict[str, Any]],
        *,
        top_k_per_clause: int = 2,
        tenant_id: str | None = None,
        contract_category: str | None = None,
        contract_subtype: str | None = None,
        planner: ContractAwareQueryPlanner | None = None,
        knowledge_tree: Any | None = None,
    ) -> dict[str, EvidenceBundle]:
        """批量为整份待审合同的所有条款生成结构化证据包字典（clause_id -> EvidenceBundle）。"""
        bundles: dict[str, EvidenceBundle] = {}
        p = planner or ContractAwareQueryPlanner()
        # 仅针对高风险或关键业务条款规划检索，其余留存轻量空包
        prioritized_intents = p.plan_intents_for_contract(
            clauses,
            contract_category=contract_category,
            contract_subtype=contract_subtype,
        )
        planned_clause_ids = {i.source_clause_id for i in prioritized_intents}

        for c in clauses:
            cid = str(c.get("clause_id") or "unknown")
            if cid in planned_clause_ids:
                bundle = self.retrieve_for_clause(
                    c,
                    top_k=top_k_per_clause,
                    tenant_id=tenant_id,
                    contract_category=contract_category,
                    contract_subtype=contract_subtype,
                    planner=p,
                    knowledge_tree=knowledge_tree,
                )
            else:
                bundle = EvidenceBundle(target_clause_id=cid)
            bundles[cid] = bundle
        return bundles

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
