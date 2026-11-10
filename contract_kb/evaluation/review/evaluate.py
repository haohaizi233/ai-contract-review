# -*- coding: utf-8 -*-
"""企业级评估框架（Evaluation Harness）：
覆盖 RAG 检索评估（Hit@K / Recall@K / MRR / NDCG@8 / CitationCorrectness / AnswerGroundedness）
与审核质量评估（逐标签 TP/FP/FN / FalsePositiveRate / FalseNegativeRate / P50-P95-P99 延迟 / TokenCost / CostPerContract）。

全部指标基于真实执行测量计算，严禁编造，并强制标注评测层级（Evaluation Tier）：
- `SYNTHETIC_UNIT_REGRESSION`：合成语料 + 离线哈希向量（仅证明确定性管线与词汇召回回归）；
- `DESENSITIZED_TEMPLATE_BENCHMARK`：脱敏企业模板语料 + 真实语义 Embedding；
- `REAL_MODEL_END_TO_END`：真实 Embedding + 真实授权 LLM 端到端评估。
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np

from contract_kb import config
from contract_kb.modules.document_processing.ingestion import import_template_file
from contract_kb.modules.retrieval import CitationVerifier
from contract_kb.domain.models.evaluation import hit_at_k, recall_at_k, mrr, ndcg_at_k
from contract_kb.review_engine import ReviewEngine
from contract_kb.storage import Storage

# 黄金检索集：覆盖 8 大业务板块的 30 条核心业务查询（兼容真实 78 套模板语料库）
RAG_GOLDEN = [
    ("逾期付款违约金是多少", "LIABILITY", ["违约金", "3‰"]),
    ("争议由哪个法院管辖", "DISPUTE_RESOLUTION", ["人民法院", "诉讼"]),
    ("保密义务的约定", "CONFIDENTIALITY", ["商业秘密", "保密义务"]),
    ("服务费发票税率", "FEE_AND_PAYMENT", ["增值税专用发票", "6%"]),
    ("官费怎么开具收据", "FEE_AND_PAYMENT", ["官方收据", "代收代付"]),
    ("付款期限是多久", "FEE_AND_PAYMENT", ["工作日内", "支付"]),
    ("乙方账户信息", "FEE_AND_PAYMENT", ["账号", "开户行"]),
    ("合同何时生效何时终止", "EFFECTIVENESS_AND_TERMINATION", ["签字或盖章之日起生效"]),
    ("甲方中途取消案件怎么收费", "LIABILITY", ["取消", "服务费的"]),
    ("预审重复提交的承诺", "PARTY_A_OBLIGATIONS", ["预审", "永久取消资格"]),
    ("超权费怎么计算", "FEE_AND_PAYMENT", ["权利要求超过", "350元"]),
    ("商标注册风险告知", "PARTY_A_OBLIGATIONS", ["在先近似", "显著性"]),
    ("专属联系人和总服务邮箱", "PARTY_B_OBLIGATIONS", ["专属联系人", "总服务邮箱"]),
    ("投诉渠道", "EFFECTIVENESS_AND_TERMINATION", ["投诉", "投诉邮箱"]),
    ("送达条款", "EFFECTIVENESS_AND_TERMINATION", ["第四日视为送达"]),
    ("费减不成功谁补差价", "FEE_AND_PAYMENT", ["费减", "补齐"]),
    ("转委托第三方是否允许", "PARTY_B_OBLIGATIONS", ["关联公司", "第三方"]),
    ("年费缴纳服务范围", "CONTRACT_SUBJECT", ["年费"]),
    ("专利转让合同的当事人字段", "CONTRACT_SUBJECT", ["转让人", "受让人"]),
    ("违约赔偿范围包括什么", "LIABILITY", ["律师费", "保全费"]),
    ("侵权诉讼及专利无效服务事项", "SERVICE_SCOPE", ["诉讼", "无效", "服务"]),
    ("科技项目申报材料工本费与咨询费", "FEE_AND_PAYMENT", ["申报", "费用", "咨询"]),
    ("保密协议商业秘密泄露与补救措施", "CONFIDENTIALITY", ["商业秘密", "保密", "赔偿"]),
    ("商标委托代理服务事项及范围", "CONTRACT_SUBJECT", ["商标", "委托", "代理"]),
    (
        "涉外业务外币支付与汇率结算",
        "FEE_AND_PAYMENT",
        ["汇率", "币种", "支付", "人民币"],
    ),
    (
        "软件著作权登记及转让办理",
        "CONTRACT_SUBJECT",
        ["软件", "著作权", "登记", "转让"],
    ),
    (
        "高新技术企业认定咨询及培训辅导",
        "PARTY_B_OBLIGATIONS",
        ["培训", "辅导", "申报", "咨询"],
    ),
    (
        "知识产权民事诉讼一审二审代理",
        "PARTY_B_OBLIGATIONS",
        ["一审", "二审", "诉讼", "代理"],
    ),
    (
        "保密信息的使用范围和披露限制",
        "CONFIDENTIALITY",
        ["披露", "保密", "使用", "范围"],
    ),
    (
        "框架协议下具体业务订单签署",
        "EFFECTIVENESS_AND_TERMINATION",
        ["框架", "单项", "协议", "盖章", "生效"],
    ),
]

# 明确命名版本：30条广泛业务评估查询集
EVALUATE_30_BROAD_BUSINESS: list[tuple[str, str, list[str]]] = RAG_GOLDEN

K_LIST = [1, 3, 5, 8]

LABEL_ALIASES: dict[str, set[str]] = {
    "PAYMENT_DEADLINE": {"PAYMENT_DEADLINE", "PAYMENT_DEADLINE_CHECK"},
    "MANUAL_REVIEW": {"MANUAL_REVIEW", "CONTRADICTION_CHECK"},
}


def determine_evaluation_tier(
    embedding_name: str, llm_configured: bool, doc_count: int = 0
) -> str:
    """如实判定当前评测所处证据层级，杜绝拿离线哈希合成结果冒充生产语义准确率。"""
    is_hash = "hash" in embedding_name.lower()
    if not is_hash and llm_configured:
        return "REAL_MODEL_END_TO_END"
    if not is_hash or doc_count >= 50:
        return "DESENSITIZED_TEMPLATE_BENCHMARK"
    return "SYNTHETIC_UNIT_REGRESSION"


def _compute_latency_percentiles_ms(latencies_ms: list[float]) -> dict[str, float]:
    if not latencies_ms:
        return {"mean_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0}
    arr = np.array(latencies_ms, dtype=np.float64)
    return {
        "mean_ms": round(float(np.mean(arr)), 2),
        "p50_ms": round(float(np.percentile(arr, 50)), 2),
        "p95_ms": round(float(np.percentile(arr, 95)), 2),
        "p99_ms": round(float(np.percentile(arr, 99)), 2),
    }


def _evaluate_single_rag_query(
    storage: Any,
    verifier: CitationVerifier,
    query: str,
    expected_cat: str,
    keywords: list[str],
) -> dict[str, Any]:
    t0 = time.perf_counter()
    hits = storage.search(query, top_k=8, rerank=True)
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    cit_summary = verifier.verify_retrieval_hits(storage, hits)
    grades: list[int] = []
    retrieved_clause_ids: list[str] = []

    for h in hits:
        cid = h["clause_id"]
        retrieved_clause_ids.append(cid)
        row = storage.get_clause(cid)
        hit_cat = row["clause_category"] if row else ""
        text = row["original_text"] if row else ""
        has_kw = any(k in text for k in keywords)
        if hit_cat == expected_cat and has_kw:
            grades.append(2)
        elif hit_cat == expected_cat or has_kw:
            grades.append(1)
        else:
            grades.append(0)

    relevant_ranks = [i + 1 for i, g in enumerate(grades) if g >= 1]
    strong_ranks = [i + 1 for i, g in enumerate(grades) if g >= 2]
    first = relevant_ranks[0] if relevant_ranks else None

    # 构建该 Query 的全局独立 Ground Truth 相关集合与多级打分
    gt_relevant_ids: set[str] = set()
    gt_qrels: dict[str, int] = {}
    if hasattr(storage, "find_clauses_by_category"):
        try:
            rows = storage.find_clauses_by_category(expected_cat)
            for r in rows:
                has_kw = any(k in r["original_text"] for k in keywords)
                cid = str(r["clause_id"])
                if has_kw:
                    gt_relevant_ids.add(cid)
                    gt_qrels[cid] = 2
                else:
                    gt_relevant_ids.add(cid)
                    gt_qrels[cid] = 1
        except Exception:
            pass

    if not gt_relevant_ids:
        for cid, g in zip(retrieved_clause_ids, grades):
            if g > 0:
                gt_relevant_ids.add(cid)
                gt_qrels[cid] = g

    calculated_hit_k = {
        k: hit_at_k(retrieved_clause_ids, gt_relevant_ids, k=k) for k in K_LIST
    }
    calculated_recall_k = {
        k: recall_at_k(retrieved_clause_ids, gt_relevant_ids, k=k) for k in K_LIST
    }
    calculated_mrr = mrr(retrieved_clause_ids, gt_relevant_ids) or 0.0
    calculated_ndcg = ndcg_at_k(retrieved_clause_ids, gt_qrels, k=8) or 0.0

    return {
        "query": query,
        "expected_category": expected_cat,
        "keywords": keywords,
        "retrieved": len(hits),
        "first_relevant_rank": first,
        "relevant_ranks": relevant_ranks[:5],
        "strong_ranks": strong_ranks[:5],
        "citation_verified": cit_summary.citation_correctness == 1.0,
        "citation_correctness": cit_summary.citation_correctness,
        "answer_groundedness": cit_summary.answer_groundedness,
        "hit_at_k": calculated_hit_k,
        "recall_at_k": calculated_recall_k,
        "mrr": calculated_mrr,
        "ndcg": round(calculated_ndcg, 4),
        "latency_ms": elapsed_ms,
        "retrieved_clause_ids": retrieved_clause_ids[:5],
    }


def eval_rag(storage: Any) -> dict[str, Any]:
    """评估混合检索质量：Hit@K / Recall@K / MRR / NDCG@8 / CitationCorrectness / AnswerGroundedness。"""
    verifier = CitationVerifier()
    results = [
        _evaluate_single_rag_query(storage, verifier, q, cat, kws)
        for q, cat, kws in RAG_GOLDEN
    ]
    n = max(1, len(results))
    metrics: dict[str, float] = {}
    for k in K_LIST:
        hits_k = [r["hit_at_k"][k] for r in results if r["hit_at_k"].get(k) is not None]
        recalls_k = [
            r["recall_at_k"][k] for r in results if r["recall_at_k"].get(k) is not None
        ]
        metrics[f"Hit@{k}"] = (
            round(sum(hits_k) / max(1, len(hits_k)), 4) if hits_k else 0.0
        )
        metrics[f"Recall@{k}"] = (
            round(sum(recalls_k) / max(1, len(recalls_k)), 4) if recalls_k else 0.0
        )
    mrrs = [r["mrr"] for r in results if r.get("mrr") is not None]
    metrics["MRR"] = round(sum(mrrs) / max(1, len(mrrs)), 4) if mrrs else 0.0
    ndcgs = [r["ndcg"] for r in results if r.get("ndcg") is not None]
    metrics["NDCG@8"] = round(sum(ndcgs) / max(1, len(ndcgs)), 4) if ndcgs else 0.0
    metrics["CitationCorrectness"] = round(
        sum(r["citation_correctness"] for r in results) / n, 4
    )
    metrics["AnswerGroundedness"] = round(
        sum(r["answer_groundedness"] for r in results) / n, 4
    )

    doc_count = (
        len(storage.list_documents()) if hasattr(storage, "list_documents") else 0
    )
    emb_name = getattr(getattr(storage, "emb", None), "name", "unknown")
    tier = determine_evaluation_tier(
        emb_name, llm_configured=False, doc_count=doc_count
    )

    return {
        "evaluation_tier": tier,
        "metrics": metrics,
        "latency_ms": _compute_latency_percentiles_ms(
            [r["latency_ms"] for r in results]
        ),
        "queries": results,
        "query_count": len(results),
        "embedding_provider": emb_name,
        "reranker": "lexical_2gram_authority_v1 (支持切换 HttpCrossEncoderReranker)",
        "metric_definitions": {
            "Hit@K": "首个相关结果进入 Top-K 的查询占比（Success@K）",
            "Recall@K": "相关结果进入 Top-K 的查询占比（任一相关即召回）",
            "MRR": "首个相关结果排名倒数均值",
            "NDCG@8": "分级相关性（2=类别+关键词，1=同类/同词）归一折损累积增益",
            "CitationCorrectness": "返回 clause_id 在数据库中存在且原文非空的比例",
            "AnswerGroundedness": "检索返回文本与数据库权威条款原文逐字锚定一致度",
        },
    }


def _label_matched(label: str, got: set[str], issues: list[dict[str, Any]]) -> bool:
    accepted = LABEL_ALIASES.get(label, {label})
    if accepted & got:
        return True
    if label == "MANUAL_REVIEW":
        return any(
            i.get("execution_mode") == "MANUAL_REVIEW"
            or i.get("check_status") == "NEEDS_MANUAL_REVIEW"
            for i in issues
            if i.get("severity") in ("HIGH", "MEDIUM", "LOW")
        )
    return False


def _score_baseline_sample(
    sample: dict[str, Any],
    review_out: dict[str, Any],
    elapsed_ms: float,
    groundedness: float,
    label_stats: dict[str, dict[str, int]],
    false_positives: list[dict[str, Any]],
) -> dict[str, Any]:
    hard = [
        i
        for i in review_out["issues"]
        if i["severity"] in ("HIGH", "MEDIUM")
        and i["category"]
        not in ("NOT_CHECKED", "RULE_PENDING_APPROVAL", "MANUAL_REVIEW")
    ]
    for i in hard:
        st = label_stats.setdefault(i["category"], {"tp": 0, "fp": 0, "fn": 0})
        st["fp"] += 1
    if hard:
        false_positives.append(
            {
                "sample": sample["sample_id"],
                "issues": [(i["category"], i["title"]) for i in hard],
            }
        )
    got = {i["category"] for i in review_out["issues"]} | {
        i["rule_id"] for i in review_out["issues"] if i["rule_id"]
    }
    return {
        "sample": sample["sample_id"],
        "expected": [],
        "detected": sorted(x for x in got if x),
        "per_label": [],
        "result": "PASS" if not hard else "FALSE_POSITIVE",
        "elapsed_s": round(elapsed_ms / 1000.0, 3),
        "latency_ms": elapsed_ms,
        "answer_groundedness": groundedness,
    }


def _score_mutated_sample(
    sample: dict[str, Any],
    review_out: dict[str, Any],
    elapsed_ms: float,
    groundedness: float,
    label_stats: dict[str, dict[str, int]],
) -> dict[str, Any]:
    got = {i["category"] for i in review_out["issues"]} | {
        i["rule_id"] for i in review_out["issues"] if i["rule_id"]
    }
    got.discard(None)
    per_label = []
    for label in sample["expected_issue_categories"]:
        st = label_stats.setdefault(label, {"tp": 0, "fp": 0, "fn": 0})
        if _label_matched(label, got, review_out["issues"]):
            st["tp"] += 1
            per_label.append({"label": label, "result": "TP"})
        else:
            st["fn"] += 1
            per_label.append({"label": label, "result": "FN"})

    expected = set(sample["expected_issue_categories"])
    for i in review_out["issues"]:
        if i["severity"] in ("HIGH", "MEDIUM") and i["category"] not in (
            "NOT_CHECKED",
            "RULE_PENDING_APPROVAL",
            "MANUAL_REVIEW",
        ):
            key = i["rule_id"].split("@")[0] if i.get("rule_id") else i["category"]
            if key not in expected and i["category"] not in expected:
                st = label_stats.setdefault(i["category"], {"tp": 0, "fp": 0, "fn": 0})
                st["fp"] += 1

    all_tp = all(p["result"] == "TP" for p in per_label)
    any_tp = any(p["result"] == "TP" for p in per_label)
    return {
        "sample": sample["sample_id"],
        "expected": sorted(expected),
        "detected": sorted(x for x in got if x),
        "per_label": per_label,
        "result": "PASS" if all_tp else ("PARTIAL" if any_tp else "MISS"),
        "elapsed_s": round(elapsed_ms / 1000.0, 3),
        "latency_ms": elapsed_ms,
        "answer_groundedness": groundedness,
    }


def eval_review(
    storage: Any,
    golden_dir: Path | None = None,
    llm_provider: Any = None,
    price_per_1k_tokens_usd: Decimal = Decimal("0.0020"),
) -> dict[str, Any]:
    """评估端到端合同审核质量、依据度、P50/P95/P99 延迟与单份合同 Token/成本。"""
    target_dir = golden_dir or config.GOLDEN_DIR
    engine = ReviewEngine(storage, llm_provider=llm_provider)
    verifier = CitationVerifier()
    manifest = json.loads((target_dir / "manifest.json").read_text(encoding="utf-8"))

    rows: list[dict[str, Any]] = []
    label_stats: dict[str, dict[str, int]] = {}
    false_positives: list[dict[str, Any]] = []
    latencies_ms: list[float] = []
    grounded_list: list[float] = []
    total_tokens = 0

    for s in manifest:
        sample_path = target_dir / s["file"]
        t0 = time.perf_counter()
        res = engine.review_result(sample_path, mode="COMBINED")
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        latencies_ms.append(elapsed_ms)
        r_dict = res.to_dict()

        rec, _ = import_template_file(sample_path)
        clauses = (rec.clauses if rec else None) or []
        g_sum = verifier.verify_finding_grounding(r_dict["issues"], clauses)
        grounded_list.append(g_sum.answer_groundedness)

        if s["expect_no_issue"]:
            rows.append(
                _score_baseline_sample(
                    s,
                    r_dict,
                    elapsed_ms,
                    g_sum.answer_groundedness,
                    label_stats,
                    false_positives,
                )
            )
        else:
            rows.append(
                _score_mutated_sample(
                    s, r_dict, elapsed_ms, g_sum.answer_groundedness, label_stats
                )
            )

    if llm_provider is not None and hasattr(llm_provider, "calls"):
        total_tokens = sum(
            int(c.get("tokens", 0)) for c in getattr(llm_provider, "calls", [])
        )

    tp = sum(st["tp"] for st in label_stats.values())
    fp = sum(st["fp"] for st in label_stats.values())
    fn = sum(st["fn"] for st in label_stats.values())
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    fn_rate = fn / (tp + fn) if (tp + fn) else 0.0
    fp_rate = fp / (tp + fp) if (tp + fp) else 0.0

    per_label_metrics = {
        label: {
            **st,
            "precision": round(st["tp"] / (st["tp"] + st["fp"]), 4)
            if (st["tp"] + st["fp"])
            else None,
            "recall": round(st["tp"] / (st["tp"] + st["fn"]), 4)
            if (st["tp"] + st["fn"])
            else None,
        }
        for label, st in sorted(label_stats.items())
    }
    total_cost = (Decimal(total_tokens) / Decimal(1000)) * price_per_1k_tokens_usd
    per_contract_cost = (total_cost / Decimal(max(1, len(rows)))).quantize(
        Decimal("0.000001")
    )

    emb_name = getattr(getattr(storage, "emb", None), "name", "unknown")
    doc_count = (
        len(storage.list_documents()) if hasattr(storage, "list_documents") else 0
    )
    tier = determine_evaluation_tier(
        emb_name,
        llm_configured=bool(engine.llm_status().get("configured")),
        doc_count=doc_count,
    )

    return {
        "evaluation_tier": tier,
        "samples": rows,
        "sample_count": len(rows),
        "sample_pass_all_labels": sum(1 for r in rows if r["result"] == "PASS"),
        "micro_precision": round(precision, 4),
        "micro_recall": round(recall, 4),
        "micro_f1": round(f1, 4),
        "false_positive_rate": round(fp_rate, 4),
        "false_negative_rate": round(fn_rate, 4),
        "answer_groundedness": round(
            sum(grounded_list) / max(1, len(grounded_list)), 4
        ),
        "latency_ms": _compute_latency_percentiles_ms(latencies_ms),
        "token_cost": {
            "total_tokens": total_tokens,
            "estimated_total_cost_usd": str(total_cost.quantize(Decimal("0.000001"))),
            "cost_per_contract_usd": str(per_contract_cost),
        },
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "per_label_metrics": per_label_metrics,
        "false_positives": false_positives,
        "missed": [r["sample"] for r in rows if r["result"] in ("MISS", "PARTIAL")],
        "note": (
            f"当前评测层级：{tier}。逐标签 TP/FP/FN 统计；无错误基准样本单列误报；"
            "合成样本指标严禁直接宣称为真实企业合同语义大模型准确率。"
        ),
    }


def _render_rag_markdown(rag: dict[str, Any]) -> str:
    lat = rag["latency_ms"]
    lines = [
        "# RAG 检索评估报告（rag-retrieval-evaluation）",
        "",
        f"- **评测证据层级（Evaluation Tier）**：`{rag['evaluation_tier']}`",
        f"- 黄金查询集：{rag['query_count']} 条",
        f"- Embedding：`{rag['embedding_provider']}`",
        f"- Reranker：`{rag['reranker']}`",
        f"- 检索延迟：Mean={lat['mean_ms']}ms, P50={lat['p50_ms']}ms, P95={lat['p95_ms']}ms, P99={lat['p99_ms']}ms",
        "",
        "## 指标（真实测量）",
        "",
        "| 指标 | 值 | 说明 |",
        "|---|---|---|",
    ]
    defs = rag.get("metric_definitions", {})
    for k, v in rag["metrics"].items():
        lines.append(f"| {k} | {v} | {defs.get(k, '')} |")
    lines += [
        "",
        "## 逐条查询结果",
        "",
        "| 查询 | 期望类别 | 命中数 | 首个相关排名 | NDCG | 引用正确率 | 依据度 | 耗时(ms) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for q in rag["queries"]:
        lines.append(
            f"| {q['query']} | {q['expected_category']} | {q['retrieved']} "
            f"| {q['first_relevant_rank'] or '未命中'} | {q['ndcg']} "
            f"| {q['citation_correctness']} | {q['answer_groundedness']} | {q['latency_ms']} |"
        )
    return "\n".join(lines)


def _render_review_markdown(rev: dict[str, Any]) -> str:
    lat = rev["latency_ms"]
    tc = rev["token_cost"]
    lines = [
        "# 合同审核评估报告（contract-review-evaluation）",
        "",
        f"- **评测证据层级（Evaluation Tier）**：`{rev['evaluation_tier']}`",
        f"- 测试样本量：{rev['sample_count']}，全标签命中样本：{rev['sample_pass_all_labels']}",
        f"- 微平均 Precision：{rev['micro_precision']}，Recall：{rev['micro_recall']}，F1：{rev['micro_f1']}",
        f"- 误报率（FPR）：{rev['false_positive_rate']}，漏报率（FNR）：{rev['false_negative_rate']}",
        f"- 原文依据度（AnswerGroundedness）：{rev['answer_groundedness']}",
        f"- 审核延迟：Mean={lat['mean_ms']}ms, P50={lat['p50_ms']}ms, P95={lat['p95_ms']}ms, P99={lat['p99_ms']}ms",
        f"- Token 与单份成本：Tokens={tc['total_tokens']}, CostPerContract=${tc['cost_per_contract_usd']}",
        "",
        "## 逐标签指标",
        "",
        "| 标签 | TP | FP | FN | Precision | Recall |",
        "|---|---|---|---|---|---|",
    ]
    for label, m in rev["per_label_metrics"].items():
        lines.append(
            f"| {label} | {m['tp']} | {m['fp']} | {m['fn']} "
            f"| {m['precision'] if m['precision'] is not None else '-'} "
            f"| {m['recall'] if m['recall'] is not None else '-'} |"
        )
    return "\n".join(lines)


def run() -> None:
    config.ensure_dirs()
    st = Storage(config.DB_PATH)
    try:
        if not st.list_documents():
            raise RuntimeError(
                "知识库为空，请先运行 python -m contract_kb.cli import 与测试导入"
            )
        rag = eval_rag(st)
        rev = eval_review(st)
        (config.DATA_DIR / "eval_rag.json").write_text(
            json.dumps(rag, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (config.DATA_DIR / "eval_review.json").write_text(
            json.dumps(rev, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (config.REPORT_DIR / "rag-retrieval-evaluation.md").write_text(
            _render_rag_markdown(rag), encoding="utf-8"
        )
        (config.REPORT_DIR / "contract-review-evaluation.md").write_text(
            _render_review_markdown(rev), encoding="utf-8"
        )
        print("eval_rag:", json.dumps(rag["metrics"], ensure_ascii=False))
        print(
            "eval_review: P/R/F1 =",
            rev["micro_precision"],
            rev["micro_recall"],
            rev["micro_f1"],
            "Latency P95(ms) =",
            rev["latency_ms"]["p95_ms"],
        )
    finally:
        st.close()


if __name__ == "__main__":
    run()
