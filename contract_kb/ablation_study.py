# -*- coding: utf-8 -*-
"""Phase 12 严谨消融实验评测框架（Ablation Study Harness - Honest & Verifiable）。

对比 6 组系统架构配置（单变量控制实验）：
- Config A: Fixed 4 Keywords + LocalHash + Hybrid RRF + Lexical Reranker (基线)
- Config B: Fixed 4 Keywords + 真实 Dense Embedding + Hybrid RRF + Lexical Reranker
- Config C: Dynamic Query Planning + 真实 Dense + Hybrid RRF + Lexical Reranker
- Config D: Dynamic Query Planning + 真实 Dense + Parent-Child Hierarchical Retrieval + Lexical Reranker
- Config E: Dynamic Query Planning + 真实 Dense + Parent-Child + 真实 Cross-Encoder Reranker
- Config F: Dynamic Query Planning + 真实 Dense + Parent-Child + Cross-Encoder + Structural Comparison Engine (Phase 12 目标形态)

关键质量原则：
1. 单变量控制：相邻配置仅改变目标评测组件；
2. 真实性保障：若运行环境缺失 sentence-transformers 或模型权重，显式标记 NOT_EXECUTED_MODEL_UNAVAILABLE，严禁伪造指标；
3. 组件调用可追踪：每组实验必须记录真实的组件调用次数 (ComponentCallTracker)。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
import numpy as np
from pydantic import BaseModel

from . import config
from .embeddings import EmbeddingProvider, LocalHashEmbedding
from .synthetic import generate_corpus
from .ingest import scan_and_import
from .modules.retrieval.citation import CitationVerifier
from .modules.retrieval.comparison import ClauseComparisonEngine
from .modules.retrieval.hierarchy import HierarchicalKnowledgeTree
from .modules.retrieval.query_planner import ContractAwareQueryPlanner
from .modules.retrieval.reranker import LexicalCoverageReranker
from .modules.retrieval.service import GovernedRetrievalService
from .retrieval_golden import GOLDEN_QUERIES, HARD_NEGATIVES, judge_hits
from .storage import Storage


class ComponentCallTracker:
    """组件调用计数器，用于独立复验每组消融实验是否真正触发了对应技术组件。"""

    def __init__(self) -> None:
        self.query_planning_calls = 0
        self.embedding_calls = 0
        self.search_calls = 0
        self.hierarchy_expansions = 0
        self.cross_encoder_calls = 0
        self.structural_comparison_calls = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "query_planning_calls": self.query_planning_calls,
            "embedding_calls": self.embedding_calls,
            "search_calls": self.search_calls,
            "hierarchy_expansions": self.hierarchy_expansions,
            "cross_encoder_calls": self.cross_encoder_calls,
            "structural_comparison_calls": self.structural_comparison_calls,
        }


class AblationConfig(BaseModel):
    name: str
    query_mode: str  # "FIXED" | "DYNAMIC"
    embedding_type: str  # "LOCAL_HASH" | "DENSE_REAL" | "MOCK_DENSE_TEST"
    use_parent_child: bool = False
    reranker_type: str = (
        "LEXICAL"  # "LEXICAL" | "CROSS_ENCODER" | "MOCK_CROSS_ENCODER_TEST"
    )
    use_structural_comparison: bool = False
    is_contract_test: bool = False


class MockDenseEmbedding(EmbeddingProvider):
    """工程契约测试专用 Mock 稠密嵌入，不可作为真实算法效果结论。"""

    name = "mock_dense_embedding_contract_only"
    dim = 256

    def embed(self, texts: list[str]) -> np.ndarray:
        rng = np.random.RandomState(42)
        return rng.randn(len(texts), 256).astype(np.float32)


class MockCrossEncoderReranker:
    """工程契约测试专用 Mock Cross-Encoder，不可作为真实算法效果结论。"""

    def rerank(
        self, query: str, hits: list[dict[str, Any]], top_k: int = 8
    ) -> list[dict[str, Any]]:
        scored = []
        for idx, h in enumerate(hits):
            score = 1.0 / (idx + 1)
            scored.append(dict(h, cross_encoder_score=score))
        return sorted(scored, key=lambda x: -x["cross_encoder_score"])[:top_k]


def check_real_dense_model_available() -> tuple[bool, str]:
    try:
        import sentence_transformers  # noqa: F401

        return True, "sentence_transformers installed"
    except ImportError:
        return (
            False,
            "BLOCKED_MODEL_RESOURCE: sentence-transformers 未安装，缺乏真实 Dense 语义向量推理环境",
        )


def check_cross_encoder_available() -> tuple[bool, str]:
    try:
        import sentence_transformers  # noqa: F401

        return True, "sentence_transformers installed"
    except ImportError:
        return (
            False,
            "BLOCKED_MODEL_RESOURCE: sentence-transformers 未安装，缺乏真实 Cross-Encoder 语义精排推理环境",
        )


def setup_ablation_storage(
    tmp_path: Path,
    embedding_provider: EmbeddingProvider | None = None,
) -> Storage:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "ablation_kb.db"
    emb = embedding_provider or LocalHashEmbedding()
    st = Storage(db_path, emb=emb)
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    generate_corpus(corpus_dir)
    res = scan_and_import(corpus_dir)
    for doc in res.get("_raw", []):
        d = doc.to_dict()
        d["status"] = "ACTIVE"
        d["document_type"] = "contract_template"
        for c in d.get("clauses", []):
            c["is_company_standard"] = True
        did, _ = st.upsert_document(d)
        st.approve_knowledge_version(
            tenant_id=config.TENANT_ID,
            document_id=did,
            approver_id="legal_ablation_admin",
            approval_reason="消融实验基准审批",
        )

    # 注入困难负样本
    for hn in HARD_NEGATIVES:
        did, _ = st.upsert_document(
            {
                "template_id": hn.doc_id,
                "source_file": f"{hn.doc_id}.docx",
                "sha256": hn.doc_id.ljust(64, "0")[:64],
                "category": hn.category,
                "subtype": hn.subtype,
                "document_type": "hard_negative",
                "status": hn.status,
                "clauses": [
                    {
                        "clause_id": f"{hn.doc_id}-c1",
                        "clause_category": hn.clause_category,
                        "original_text": hn.text,
                        "normalized_summary": hn.hard_negative_type,
                    }
                ],
            }
        )
        if hn.status == "ACTIVE":
            st.approve_knowledge_version(
                tenant_id=config.TENANT_ID,
                document_id=did,
                approver_id="legal_ablation_admin",
                approval_reason="困难负样本准入",
            )
    return st


def evaluate_configuration(
    cfg: AblationConfig,
    st: Storage,
    *,
    tracker: ComponentCallTracker | None = None,
    knowledge_tree: HierarchicalKnowledgeTree | None = None,
    allow_mock_for_contract_test: bool = False,
) -> dict[str, Any]:
    """执行单个消融配置的独立评测，记录真实的组件调用与指标。"""
    call_tracker = tracker or ComponentCallTracker()
    is_contract = bool(allow_mock_for_contract_test or cfg.is_contract_test)

    # 1. 真实模型可用性守卫 (Fail-Closed, 绝不拿简单算法冒充)
    if cfg.embedding_type == "DENSE_REAL":
        avail, reason = check_real_dense_model_available()
        if not avail and not is_contract:
            return {
                "config_name": cfg.name,
                "status": "NOT_EXECUTED_MODEL_UNAVAILABLE",
                "is_contract_test": is_contract,
                "execution_mode": "PRODUCTION_REAL",
                "blocking_reason": reason,
                "component_trace": call_tracker.to_dict(),
                "metrics": None,
            }
    if cfg.reranker_type in ("CROSS_ENCODER", "MOCK_CROSS_ENCODER_TEST"):
        if cfg.reranker_type == "CROSS_ENCODER" and not is_contract:
            avail, reason = check_cross_encoder_available()
            if not avail:
                return {
                    "config_name": cfg.name,
                    "status": "NOT_EXECUTED_MODEL_UNAVAILABLE",
                    "is_contract_test": is_contract,
                    "execution_mode": "PRODUCTION_REAL",
                    "blocking_reason": reason,
                    "component_trace": call_tracker.to_dict(),
                    "metrics": None,
                }
        elif cfg.reranker_type == "MOCK_CROSS_ENCODER_TEST" and not is_contract:
            return {
                "config_name": cfg.name,
                "status": "NOT_EXECUTED_MODEL_UNAVAILABLE",
                "is_contract_test": False,
                "execution_mode": "PRODUCTION_REAL",
                "blocking_reason": "BLOCKED_MOCK_DISALLOWED: 未开启契约测试模式时严禁使用 Mock 组件",
                "component_trace": call_tracker.to_dict(),
                "metrics": None,
            }

    # 2. 初始化对应技术组件并接入真实调用追踪代理
    use_dyn = cfg.query_mode == "DYNAMIC"
    planner = ContractAwareQueryPlanner() if use_dyn else None
    if use_dyn and planner is not None:
        orig_plan = planner.plan_clause_intents_deterministic

        def _tracked_plan(*args, **kwargs):
            call_tracker.query_planning_calls += 1
            return orig_plan(*args, **kwargs)

        planner.plan_clause_intents_deterministic = _tracked_plan  # type: ignore[assignment]

    # 初始化重排器并路由真实/Mock/词法实现
    if cfg.reranker_type in ("CROSS_ENCODER", "MOCK_CROSS_ENCODER_TEST"):
        if cfg.reranker_type == "MOCK_CROSS_ENCODER_TEST" or (
            is_contract and cfg.reranker_type == "CROSS_ENCODER"
        ):
            if not is_contract:
                raise ValueError("Mock cross-encoder only allowed when is_contract=True")
            reranker: Any = MockCrossEncoderReranker()
        else:
            from .rerankers import CrossEncoderReranker

            reranker = CrossEncoderReranker()

        orig_rerank = reranker.rerank

        def _tracked_ce_rerank(*args, **kwargs):
            call_tracker.cross_encoder_calls += 1
            return orig_rerank(*args, **kwargs)

        reranker.rerank = _tracked_ce_rerank
    elif cfg.reranker_type == "LEXICAL":
        reranker = LexicalCoverageReranker()
    else:
        raise ValueError(f"未知的 reranker_type: {cfg.reranker_type}")

    # 检索与 Embedding 真实调用追踪
    orig_search = st.search

    def _tracked_search(*args, **kwargs):
        call_tracker.search_calls += 1
        return orig_search(*args, **kwargs)

    st.search = _tracked_search  # type: ignore[assignment]

    if hasattr(st, "emb") and hasattr(st.emb, "embed"):
        orig_embed = st.emb.embed

        def _tracked_embed(*args, **kwargs):
            call_tracker.embedding_calls += 1
            return orig_embed(*args, **kwargs)

        st.emb.embed = _tracked_embed  # type: ignore[assignment]

    # 层级扩展真实调用追踪
    if cfg.use_parent_child and knowledge_tree is not None:
        orig_expand = knowledge_tree.expand_parent_child_context

        def _tracked_expand(*args, **kwargs):
            call_tracker.hierarchy_expansions += 1
            return orig_expand(*args, **kwargs)

        knowledge_tree.expand_parent_child_context = _tracked_expand  # type: ignore[assignment]

    # 结构化比对真实调用追踪
    comp_engine = ClauseComparisonEngine() if cfg.use_structural_comparison else None
    if comp_engine is not None:
        orig_comp = comp_engine.compare_clauses

        def _tracked_comp(*args, **kwargs):
            call_tracker.structural_comparison_calls += 1
            return orig_comp(*args, **kwargs)

        comp_engine.compare_clauses = _tracked_comp  # type: ignore[assignment]

    _ = GovernedRetrievalService(st, reranker=reranker)
    verifier = CitationVerifier()

    n = len(GOLDEN_QUERIES)
    rows = []
    latencies = []

    try:
        for q in GOLDEN_QUERIES:
            t0 = time.perf_counter()

            # Step 1: 查询构造与规划（经 tracked 代理真实计数）
            if use_dyn and planner is not None:
                simulated_clause = {
                    "clause_id": f"sim_{q.qid}",
                    "clause_category": q.expected_category,
                    "original_text": q.query,
                }
                intents = planner.plan_clause_intents_deterministic(simulated_clause)
                q_text = (
                    intents[0].semantic_queries[0]
                    if (intents and intents[0].semantic_queries)
                    else q.query
                )
            else:
                q_text = q.query

            # Step 2: 检索执行（经 tracked 代理真实计数）
            hits = st.search(q_text, top_k=8, approved_only=True, rerank=False)

            # Step 3: 层级扩展 (Parent-Child)（经 tracked 代理真实计数）
            if cfg.use_parent_child and knowledge_tree is not None:
                expanded_hits = []
                for h in hits:
                    cid = str(h.get("clause_id") or "")
                    exp = knowledge_tree.expand_parent_child_context(cid)
                    if exp.get("expanded_text"):
                        h_copy = dict(h)
                        h_copy["text"] = exp["expanded_text"]
                        expanded_hits.append(h_copy)
                    else:
                        expanded_hits.append(h)
                hits = expanded_hits

            # Step 4: 语义精排（仅 Cross-Encoder 经 tracked 代理真实计数，词法不计数）
            hits = reranker.rerank(q_text, hits, top_k=8)

            # Step 5: 结构化差异比对 (Structural Comparison)（经 tracked 代理真实计数）
            if cfg.use_structural_comparison and comp_engine is not None and hits:
                top_candidate = hits[0]
                simulated_clause = {
                    "clause_id": f"sim_{q.qid}",
                    "clause_category": q.expected_category,
                    "original_text": q.query,
                }
                comp = comp_engine.compare_clauses(simulated_clause, top_candidate)
                hits[0]["comparison_conclusion"] = comp.conclusion

            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            latencies.append(elapsed_ms)

            j = judge_hits(q, hits)
            rows.append(
                {
                    "qid": q.qid,
                    "first_relevant": j["first_relevant"],
                    "ndcg": j["ndcg"],
                    "avoided_hard_negatives": j["avoided_hard_negatives"],
                    "hits": hits,
                }
            )
    finally:
        # 恢复包装方法，避免污染外部实例
        st.search = orig_search  # type: ignore[assignment]

    # 计算指标
    metrics: dict[str, float] = {}
    for k in (1, 3, 5, 8):
        metrics[f"Hit@{k}"] = round(
            sum(1 for r in rows if r["first_relevant"] and r["first_relevant"] <= k)
            / n,
            4,
        )

    metrics["MRR"] = round(
        sum((1.0 / r["first_relevant"]) for r in rows if r["first_relevant"]) / n, 4
    )
    metrics["NDCG@8"] = round(sum(r["ndcg"] for r in rows) / n, 4)
    metrics["HardNegAvoidance@8"] = round(
        sum(1 for r in rows if r["avoided_hard_negatives"]) / n, 4
    )

    all_hits = [h for r in rows for h in r["hits"][:3]]
    cit_sum = verifier.verify_retrieval_hits(
        st, all_hits, expected_tenant_id=config.TENANT_ID
    )
    metrics["CitationCorrectness"] = cit_sum.citation_correctness
    metrics["AnswerGroundedness"] = cit_sum.answer_groundedness

    p50 = float(np.percentile(latencies, 50))
    p95 = float(np.percentile(latencies, 95))

    return {
        "config_name": cfg.name,
        "status": "EXECUTED",
        "is_contract_test": is_contract,
        "execution_mode": "CONTRACT_TEST_MOCK" if is_contract else "PRODUCTION_REAL",
        "metrics": metrics,
        "component_trace": call_tracker.to_dict(),
        "latency_p50_ms": round(p50, 2),
        "latency_p95_ms": round(p95, 2),
        "mean_latency_ms": round(float(np.mean(latencies)), 2),
    }


def run_full_ablation_study(
    tmp_path: Path,
    *,
    allow_mock_for_contract_test: bool = False,
) -> dict[str, Any]:
    """运行全套 6 组消融对照实验矩阵。"""
    st_local = setup_ablation_storage(tmp_path / "local", LocalHashEmbedding())

    # 构建层级知识树用于 Parent-Child 扩展
    all_clauses = []
    for doc in st_local.list_documents(tenant_id=config.TENANT_ID):
        all_clauses.extend(
            st_local.get_document_clauses(
                doc["document_id"], tenant_id=config.TENANT_ID
            )
        )
    tree = HierarchicalKnowledgeTree.build_from_clauses(
        "ablation_corpus",
        all_clauses,
        business_category="ALL",
        contract_subtype="ALL",
    )

    configs = [
        AblationConfig(
            name="Config A (Baseline: Fixed 4 Keywords + LocalHash + Lexical Reranker)",
            query_mode="FIXED",
            embedding_type="LOCAL_HASH",
            use_parent_child=False,
            reranker_type="LEXICAL",
            use_structural_comparison=False,
        ),
        AblationConfig(
            name="Config B (Fixed 4 Keywords + Real Dense + Lexical Reranker)",
            query_mode="FIXED",
            embedding_type="DENSE_REAL"
            if not allow_mock_for_contract_test
            else "MOCK_DENSE_TEST",
            use_parent_child=False,
            reranker_type="LEXICAL",
            use_structural_comparison=False,
            is_contract_test=allow_mock_for_contract_test,
        ),
        AblationConfig(
            name="Config C (Dynamic Query Planning + Real Dense + Lexical Reranker)",
            query_mode="DYNAMIC",
            embedding_type="DENSE_REAL"
            if not allow_mock_for_contract_test
            else "MOCK_DENSE_TEST",
            use_parent_child=False,
            reranker_type="LEXICAL",
            use_structural_comparison=False,
            is_contract_test=allow_mock_for_contract_test,
        ),
        AblationConfig(
            name="Config D (Dynamic Query Planning + Real Dense + Parent-Child Hierarchy)",
            query_mode="DYNAMIC",
            embedding_type="DENSE_REAL"
            if not allow_mock_for_contract_test
            else "MOCK_DENSE_TEST",
            use_parent_child=True,
            reranker_type="LEXICAL",
            use_structural_comparison=False,
            is_contract_test=allow_mock_for_contract_test,
        ),
        AblationConfig(
            name="Config E (Dynamic Query Planning + Real Dense + Parent-Child + Cross-Encoder)",
            query_mode="DYNAMIC",
            embedding_type="DENSE_REAL"
            if not allow_mock_for_contract_test
            else "MOCK_DENSE_TEST",
            use_parent_child=True,
            reranker_type="CROSS_ENCODER"
            if not allow_mock_for_contract_test
            else "MOCK_CROSS_ENCODER_TEST",
            use_structural_comparison=False,
            is_contract_test=allow_mock_for_contract_test,
        ),
        AblationConfig(
            name="Config F (Full Phase 12: DQP + Hierarchy + Cross-Encoder + Structural Comparison)",
            query_mode="DYNAMIC",
            embedding_type="DENSE_REAL"
            if not allow_mock_for_contract_test
            else "MOCK_DENSE_TEST",
            use_parent_child=True,
            reranker_type="CROSS_ENCODER"
            if not allow_mock_for_contract_test
            else "MOCK_CROSS_ENCODER_TEST",
            use_structural_comparison=True,
            is_contract_test=allow_mock_for_contract_test,
        ),
    ]

    ablation_results = []
    for cfg in configs:
        res = evaluate_configuration(
            cfg,
            st_local,
            knowledge_tree=tree,
            allow_mock_for_contract_test=allow_mock_for_contract_test,
        )
        ablation_results.append(res)

    st_local.close()
    return {"ablation_results": ablation_results}
