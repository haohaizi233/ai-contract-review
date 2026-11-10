# -*- coding: utf-8 -*-
"""检索对比基准（Phase 2）：Hash baseline vs 真实 Embedding，注入困难负样本后实测。

输出 reports/contract-kb/rag-retrieval-evaluation.md（真实测量，含困难负样本指标）。
"""

from __future__ import annotations

import json
import time

from contract_kb import config
from contract_kb.storage import Storage
from contract_kb.infrastructure.ai.embedding_providers import LocalHashEmbedding
from contract_kb.evaluation.datasets.retrieval_golden import (
    GOLDEN_QUERIES,
    HARD_NEGATIVES,
    judge_hits,
)
from contract_kb.infrastructure.ai.reranker_providers import (
    LexicalReranker,
    get_reranker,
)


def build_bench_storage(emb, tmp_db) -> Storage:
    """独立基准库：合成语料 + 困难负样本注入。"""
    st = Storage(tmp_db, emb=emb)
    from contract_kb.modules.document_processing.ingestion import scan_and_import
    from contract_kb.evaluation.datasets.synthetic import generate_corpus
    import tempfile
    from pathlib import Path

    corpus = Path(tempfile.mkdtemp(prefix="bench_corpus"))
    generate_corpus(corpus)
    result = scan_and_import(corpus)
    for d in result.get("_raw", []):
        st.upsert_document(d.to_dict())
    # 注入困难负样本（作为独立文档，带状态标记）
    for hn in HARD_NEGATIVES:
        doc = {
            "template_id": hn.doc_id,
            "source_file": f"{hn.doc_id}.docx",
            "sha256": hn.doc_id.ljust(64, "0")[:64],
            "template_version": "bench",
            "category": hn.category,
            "subtype": hn.subtype,
            "document_type": "hard_negative",
            "clause_count": 1,
            "clauses": [
                {
                    "clause_id": f"{hn.doc_id}-c1",
                    "clause_number": None,
                    "clause_category": hn.clause_category,
                    "clause_subcategory": None,
                    "original_text": hn.text,
                    "normalized_summary": hn.hard_negative_type,
                    "is_company_standard": False,
                    "approval_status": "BENCH",
                    "source_node_ids": [],
                }
            ],
        }
        st.upsert_document(doc)
        if hn.status != "ACTIVE":
            st.set_document_status(hn.doc_id, hn.status)
    return st


def run_benchmark(st: Storage, label: str) -> dict:
    n = len(GOLDEN_QUERIES)
    rows = []
    for q in GOLDEN_QUERIES:
        t0 = time.time()
        hits = st.search(q.query, top_k=8, rerank=True)
        dt = round(time.time() - t0, 3)
        j = judge_hits(q, hits)
        rows.append(
            {
                "qid": q.qid,
                "query": q.query,
                "note": q.note,
                "first_relevant_rank": j["first_relevant"],
                "neg_ranks": j["neg_ranks"],
                "ndcg": round(j["ndcg"], 4),
                "avoided_hard_negatives": j["avoided_hard_negatives"],
                "elapsed_s": dt,
                "top3": [
                    {
                        "text": h["text"][:40],
                        "score": round(h.get("rerank_score", 0), 3),
                    }
                    for h in hits[:3]
                ],
            }
        )
    metrics = {}
    for k in (1, 3, 5, 8):
        metrics[f"Hit@{k}"] = round(
            sum(
                1
                for r in rows
                if r["first_relevant_rank"] and r["first_relevant_rank"] <= k
            )
            / n,
            4,
        )
    metrics["MRR"] = round(
        sum(1.0 / r["first_relevant_rank"] for r in rows if r["first_relevant_rank"])
        / n,
        4,
    )
    metrics["NDCG@8"] = round(sum(r["ndcg"] for r in rows) / n, 4)
    metrics["HardNegAvoidance@8"] = round(
        sum(1 for r in rows if r["avoided_hard_negatives"]) / n, 4
    )
    return {"label": label, "metrics": metrics, "queries": rows, "query_count": n}


def run():
    config.ensure_dirs()
    import tempfile

    tmp = tempfile.mkdtemp(prefix="bench_db")
    results = []
    # 1) Hash baseline
    st1 = build_bench_storage(
        LocalHashEmbedding(config.EMBEDDING_DIM), f"{tmp}/hash.db"
    )
    results.append(run_benchmark(st1, f"hash_baseline({st1.emb.name})"))
    st1.close()
    # 2) 真实 Embedding（如可用）
    try:
        from contract_kb.infrastructure.ai.embedding_providers import (
            SentenceTransformerEmbedding,
        )

        emb2 = SentenceTransformerEmbedding()
        st2 = build_bench_storage(emb2, f"{tmp}/st.db")
        results.append(run_benchmark(st2, f"real_embedding({st2.emb.name})"))
        st2.close()
        real_available = True
    except Exception as e:
        results.append(
            {
                "label": "real_embedding(UNAVAILABLE)",
                "metrics": {},
                "queries": [],
                "query_count": 0,
                "unavailable_reason": str(e)[:200],
            }
        )
        real_available = False
    # 3) Reranker 对比（hash + lexical vs hash + cross-encoder 如可用）
    rer = get_reranker("cross_encoder") if real_available else LexicalReranker()
    results.append(
        {
            "label": f"reranker_active: {rer.name}",
            "metrics": {},
            "queries": [],
            "query_count": 0,
        }
    )

    (config.DATA_DIR / "retrieval_benchmark.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    # 报告
    lines = [
        "# RAG 检索评估报告（rag-retrieval-evaluation）",
        "",
        f"- 黄金查询集：{len(GOLDEN_QUERIES)} 条（按条款语义判定，非大类标签）",
        f"- 困难负样本：{len(HARD_NEGATIVES)} 个（HN1 不同期限 / HN2 不同金额比率 / "
        "HN3 商专近似措辞 / HN4 过期版本 / HN5 相反义务）",
        "",
        "## 指标定义（严格区分）",
        "",
        "- **Hit@K**：首个预期条款进入 Top-K 的查询占比（Success@K）",
        "- **Recall@K**：预期条款进入 Top-K 的查询占比",
        "- **MRR**：首个预期条款排名倒数均值",
        "- **NDCG@8**：分级相关性（2=关键词全中，1=部分，负样本不计增益）归一折损累积增益",
        "- **HardNegAvoidance@8**：困难负样本未混入 Top-8 的查询占比（越高越好）",
        "- **CitationCorrectness**：clause_id 可回查非空原文的比例",
        "",
    ]
    for res in results:
        lines.append(f"## {res['label']}")
        lines.append("")
        if res.get("unavailable_reason"):
            lines.append(f"**未运行**：{res['unavailable_reason']}")
            lines.append("")
            continue
        if not res["metrics"]:
            lines.append("（环境信息，无指标）")
            lines.append("")
            continue
        lines += ["| 指标 | 值 |", "|---|---|"]
        for k, v in res["metrics"].items():
            lines.append(f"| {k} | {v} |")
        lines += [
            "",
            "| QID | 查询 | 首个相关排名 | 负样本排名 | NDCG | 避开负样本 | 备注 |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in res["queries"]:
            lines.append(
                f"| {r['qid']} | {r['query']} | {r['first_relevant_rank'] or '未命中'} "
                f"| {r['neg_ranks'] or '-'} | {r['ndcg']} "
                f"| {'是' if r['avoided_hard_negatives'] else '否'} | {r['note']} |"
            )
        lines.append("")
    lines += [
        "## 说明与局限",
        "",
        "- 所有指标为真实执行测量（retrieval_benchmark.json 保存原始结果）；",
        "- 真实 Embedding（BGE-M3 系列）需安装 sentence-transformers 并配置授权模型；"
        "本环境未安装则该组标记 UNAVAILABLE，不以 Hash 结果冒充；",
        "- 困难负样本注入独立基准库，不影响生产知识库。",
    ]
    (config.REPORT_DIR / "rag-retrieval-evaluation.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    for res in results:
        print(res["label"], json.dumps(res["metrics"], ensure_ascii=False))


if __name__ == "__main__":
    run()
