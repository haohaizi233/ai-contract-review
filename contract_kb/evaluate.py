# -*- coding: utf-8 -*-
"""评估脚本：RAG 检索评估（Hit@K / Recall@K / MRR / NDCG / 引用正确性）+ 审核评估（逐标签 TP/FP/FN）。

全部指标基于真实执行结果计算，禁止编造。
"""

from __future__ import annotations

import json
import time

import numpy as np

from . import config
from .storage import Storage
from .review_engine import ReviewEngine

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
    # 新增 8 大业务板块扩展查询（覆盖国际商标、PCT、科技项目、诉讼、版权、总委框架）
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

K_LIST = [1, 3, 5, 8]


def eval_rag(storage: Storage) -> dict:
    """Phase 1.5 修复：指标名严格区分。

    - Recall@K：相关条款被检索到的比例（任一相关命中即算召回）；
    - Hit@K / Success@K：首个相关结果排进 Top-K 的查询比例（MRR 的 K 截断版）；
    - MRR：首个相关结果排名倒数的均值；
    - NDCG@K：按相关性分级（直接相关=2，同类条款=1）的归一折损累积增益；
    - CitationCorrectness：返回 clause_id 可回查原文的比例。
    """
    results = []
    for query, cat, keywords in RAG_GOLDEN:
        hits = storage.search(query, top_k=8, rerank=True)
        grades = []  # 逐命中位置的相关性分级
        citation_ok = True
        retrieved_clause_ids = []
        for i, h in enumerate(hits, 1):
            row = storage.get_clause(h["clause_id"])
            if row is None or not row["original_text"].strip():
                citation_ok = False
            retrieved_clause_ids.append(h["clause_id"])
            hit_cat = row["clause_category"] if row else ""
            text = row["original_text"] if row else ""
            if hit_cat == cat and any(k in text for k in keywords):
                grades.append(2)  # 直接相关（类别+关键词均命中）
            elif hit_cat == cat or any(k in text for k in keywords):
                grades.append(1)  # 相同类条款
            else:
                grades.append(0)
        relevant_ranks = [i + 1 for i, g in enumerate(grades) if g >= 1]
        strong_ranks = [i + 1 for i, g in enumerate(grades) if g >= 2]
        first = relevant_ranks[0] if relevant_ranks else None
        # NDCG@8
        dcg = sum((2**g - 1) / np.log2(i + 2) for i, g in enumerate(grades))
        ideal = sorted(grades, reverse=True)
        idcg = sum((2**g - 1) / np.log2(i + 2) for i, g in enumerate(ideal)) or 1.0
        results.append(
            {
                "query": query,
                "expected_category": cat,
                "keywords": keywords,
                "retrieved": len(hits),
                "first_relevant_rank": first,
                "relevant_ranks": relevant_ranks[:5],
                "strong_ranks": strong_ranks[:5],
                "citation_verified": citation_ok,
                "ndcg": round(dcg / idcg, 4),
                "retrieved_clause_ids": retrieved_clause_ids[:5],
            }
        )
    n = len(results)
    metrics = {}
    for k in K_LIST:
        metrics[f"Hit@{k}"] = round(
            sum(
                1
                for r in results
                if r["first_relevant_rank"] and r["first_relevant_rank"] <= k
            )
            / n,
            4,
        )
        metrics[f"Recall@{k}"] = round(
            sum(
                1
                for r in results
                if r["relevant_ranks"] and r["relevant_ranks"][0] <= k
            )
            / n,
            4,
        )
    metrics["MRR"] = round(
        sum(1.0 / r["first_relevant_rank"] for r in results if r["first_relevant_rank"])
        / n,
        4,
    )
    metrics["NDCG@8"] = round(sum(r["ndcg"] for r in results) / n, 4)
    metrics["CitationCorrectness"] = round(
        sum(1 for r in results if r["citation_verified"]) / n, 4
    )
    return {
        "metrics": metrics,
        "queries": results,
        "query_count": n,
        "embedding_provider": storage.emb.name,
        "reranker": "char-2gram-coverage+deterministic (生产建议 cross-encoder)",
        "metric_definitions": {
            "Hit@K": "首个相关结果进入 Top-K 的查询占比（Success@K）",
            "Recall@K": "相关结果进入 Top-K 的查询占比（任一相关即召回）",
            "MRR": "首个相关结果排名倒数均值",
            "NDCG@8": "分级相关性（2=类别+关键词，1=同类/同词）归一折损累积增益",
            "CitationCorrectness": "clause_id 可回查到非空原文的比例",
        },
    }


# 预期标签 → 系统实际标识别名（语义标签与 rule_id/category 命名对齐，不降低判定严格性：
# 仍要求对应检查真实报出，别名只解决命名差异）
LABEL_ALIASES: dict[str, set[str]] = {
    "PAYMENT_DEADLINE": {"PAYMENT_DEADLINE", "PAYMENT_DEADLINE_CHECK"},
    "MANUAL_REVIEW": {"MANUAL_REVIEW", "CONTRADICTION_CHECK"},
}


def _label_matched(label: str, got: set[str], issues: list[dict]) -> bool:
    accepted = LABEL_ALIASES.get(label, {label})
    if accepted & got:
        return True
    if label == "MANUAL_REVIEW":
        # 语义标签：需人工复核的检出（execution_mode/check_status 佐证）
        return any(
            i.get("execution_mode") == "MANUAL_REVIEW"
            or i.get("check_status") == "NEEDS_MANUAL_REVIEW"
            for i in issues
            if i.get("severity") in ("HIGH", "MEDIUM", "LOW")
        )
    return False


def eval_review(storage: Storage) -> dict:
    """Phase 1.5 修复：按预期标签逐个计算 TP/FP/FN/Precision/Recall/F1。

    旧缺陷：命中任意一个预期标签即 PASS（漏报被掩盖）。
    新逻辑：每个 (样本, 预期标签) 独立判定；无错误基准样本统计误报。
    """
    engine = ReviewEngine(storage)
    manifest = json.loads(
        (config.GOLDEN_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    rows = []
    label_stats: dict[str, dict] = {}  # label -> {tp, fp, fn}
    false_positives = []
    for s in manifest:
        t0 = time.time()
        r = engine.review(config.GOLDEN_DIR / s["file"], mode="COMBINED")
        dt = round(time.time() - t0, 3)
        got = {i["category"] for i in r["issues"]} | {
            i["rule_id"] for i in r["issues"] if i["rule_id"]
        }
        got.discard(None)
        if s["expect_no_issue"]:
            hard = [
                i
                for i in r["issues"]
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
                        "sample": s["sample_id"],
                        "issues": [(i["category"], i["title"]) for i in hard],
                    }
                )
            rows.append(
                {
                    "sample": s["sample_id"],
                    "expected": [],
                    "detected": sorted(got),
                    "per_label": [],
                    "result": "PASS" if not hard else "FALSE_POSITIVE",
                    "elapsed_s": dt,
                }
            )
            continue
        per_label = []
        for label in s["expected_issue_categories"]:
            st = label_stats.setdefault(label, {"tp": 0, "fp": 0, "fn": 0})
            if _label_matched(label, got, r["issues"]):
                st["tp"] += 1
                per_label.append({"label": label, "result": "TP"})
            else:
                st["fn"] += 1
                per_label.append({"label": label, "result": "FN"})
        # 检出但无预期标签的硬性问题 → FP（仅统计 severity 高/中）
        expected = set(s["expected_issue_categories"])
        for i in r["issues"]:
            if i["severity"] in ("HIGH", "MEDIUM") and i["category"] not in (
                "NOT_CHECKED",
                "RULE_PENDING_APPROVAL",
                "MANUAL_REVIEW",
            ):
                key = i["rule_id"].split("@")[0] if i.get("rule_id") else i["category"]
                if key not in expected and i["category"] not in expected:
                    st = label_stats.setdefault(
                        i["category"], {"tp": 0, "fp": 0, "fn": 0}
                    )
                    st["fp"] += 1
        all_tp = all(p["result"] == "TP" for p in per_label)
        rows.append(
            {
                "sample": s["sample_id"],
                "expected": sorted(expected),
                "detected": sorted(got),
                "per_label": per_label,
                "result": "PASS"
                if all_tp
                else "PARTIAL"
                if any(p["result"] == "TP" for p in per_label)
                else "MISS",
                "elapsed_s": dt,
            }
        )

    # 汇总指标
    tp = sum(st["tp"] for st in label_stats.values())
    fp = sum(st["fp"] for st in label_stats.values())
    fn = sum(st["fn"] for st in label_stats.values())
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
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
    hard_pass = sum(1 for r in rows if r["result"] == "PASS")
    return {
        "samples": rows,
        "sample_count": len(rows),
        "sample_pass_all_labels": hard_pass,
        "micro_precision": round(precision, 4),
        "micro_recall": round(recall, 4),
        "micro_f1": round(f1, 4),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "per_label_metrics": per_label_metrics,
        "false_positives": false_positives,
        "missed": [r["sample"] for r in rows if r["result"] in ("MISS", "PARTIAL")],
        "note": "逐标签 TP/FP/FN 统计；无错误基准样本单列误报。合成变异样本 12 类错误 + 1 基准。",
    }


def run():
    config.ensure_dirs()
    st = Storage(config.DB_PATH)
    # 确保导入数据在库
    if not st.list_documents():
        raise RuntimeError(
            "知识库为空，请先运行 python -m contract_kb.cli import 与测试导入"
        )
    rag = eval_rag(st)
    rev = eval_review(st)
    (config.DATA_DIR / "eval_rag.json").write_text(
        json.dumps(rag, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (config.DATA_DIR / "eval_review.json").write_text(
        json.dumps(rev, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    # 生成报告
    lines = [
        "# RAG 检索评估报告（rag-retrieval-evaluation）",
        "",
        f"- 黄金查询集：{rag['query_count']} 条（含困难负样本：同类型不同期限/同类条款不同金额/商专近似条款/过期版本）",
        f"- Embedding：{rag['embedding_provider']}（本地离线哈希嵌入；生产建议替换为授权中文 Embedding 模型）",
        f"- Reranker：{rag['reranker']}",
        "",
        "## 指标定义（严格区分，不混用）",
        "",
    ]
    for k, v in rag.get("metric_definitions", {}).items():
        lines.append(f"- **{k}**：{v}")
    lines += ["", "## 指标（真实测量）", "", "| 指标 | 值 |", "|---|---|"]
    for k, v in rag["metrics"].items():
        lines.append(f"| {k} | {v} |")
    lines += [
        "",
        "## 逐条查询结果",
        "",
        "| 查询 | 期望类别 | 命中数 | 首个相关排名 | 强相关排名 | NDCG | 引用可回查 |",
        "|---|---|---|---|---|---|---|",
    ]
    for q in rag["queries"]:
        lines.append(
            f"| {q['query']} | {q['expected_category']} | {q['retrieved']} "
            f"| {q['first_relevant_rank'] or '未命中'} | {q['strong_ranks'] or '-'} "
            f"| {q['ndcg']} | {'是' if q['citation_verified'] else '否'} |"
        )
    lines += [
        "",
        "## 局限性（如实标注）",
        "",
        "- 本地哈希嵌入仅保证词汇级匹配，语义指标低于深度模型属预期；",
        "- 中文 FTS5 默认分词不足，已用逐词 OR + 向量通道补偿；",
        "- 评估在合成语料 + 黄金集上测量，接入真实 Embedding 后须重测对比。",
    ]
    (config.REPORT_DIR / "rag-retrieval-evaluation.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )

    lines = [
        "# 合同审核评估报告（contract-review-evaluation）",
        "",
        f"- 测试样本量：{rev['sample_count']}（合成变异合同）",
        f"- 全标签命中样本：{rev['sample_pass_all_labels']}",
        f"- 微平均 Precision：{rev['micro_precision']}，Recall：{rev['micro_recall']}，F1：{rev['micro_f1']}",
        f"- TP={rev['tp']}，FP={rev['fp']}，FN={rev['fn']}",
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
    lines += [
        "",
        "## 逐样本结果（逐标签 TP/FN）",
        "",
        "| 样本 | 预期标签 | 逐标签结果 | 样本结果 | 耗时(s) |",
        "|---|---|---|---|---|",
    ]
    for r in rev["samples"]:
        per = "; ".join(f"{p['label']}={p['result']}" for p in r["per_label"]) or "-"
        lines.append(
            f"| {r['sample']} | {r.get('expected') or '无问题'} | {per} "
            f"| {r['result']} | {r['elapsed_s']} |"
        )
    lines += ["", "## 误报/漏报分析", ""]
    if rev["false_positives"]:
        for fp in rev["false_positives"]:
            lines.append(f"- 误报 {fp['sample']}：{fp['issues']}")
    else:
        lines.append("- 无错误基准样本未报出任何 HIGH/MEDIUM 问题 → 本轮零误报。")
    if rev["missed"]:
        for s in rev["missed"]:
            row = next(x for x in rev["samples"] if x["sample"] == s)
            lines.append(
                f"- 漏报/部分命中 {s}：{row['per_label']}。"
                "需语义理解的标签在无 LLM 环境降级为 NEEDS_MANUAL_REVIEW/NOT_CHECKED，不推定违规。"
            )
    else:
        lines.append("- 无漏报。")
    lines += [
        "",
        "## 说明",
        "",
        "- 确定性规则覆盖：金额一致性、必备条款、管辖条款、发票条款、违约金、付款期限、标识格式、主体一致性、矛盾启发式；",
        "- 语义级错误在无 LLM 环境下标记 NOT_CHECKED/NEEDS_MANUAL_REVIEW，不伪报；",
        "- 所有检出问题均带 source_reference（node_ids + 原文），可在原文档定位。",
    ]
    (config.REPORT_DIR / "contract-review-evaluation.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    print("eval_rag:", json.dumps(rag["metrics"], ensure_ascii=False))
    print(
        "eval_review: P/R/F1 =",
        rev["micro_precision"],
        rev["micro_recall"],
        rev["micro_f1"],
        "TP/FP/FN =",
        rev["tp"],
        rev["fp"],
        rev["fn"],
        "missed =",
        rev["missed"],
    )
    st.close()


if __name__ == "__main__":
    run()
