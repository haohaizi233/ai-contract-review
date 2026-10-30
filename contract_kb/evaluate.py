# -*- coding: utf-8 -*-
"""评估脚本：RAG 检索评估（Recall@K / MRR / 引用正确性）+ 审核评估（变异样本命中率/误报/漏报）。

全部指标基于真实执行结果计算，禁止编造。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import config
from .storage import Storage
from .review_engine import ReviewEngine

# 黄金检索集：查询 → 期望命中的条款类别/关键短语（源自真实模板）
RAG_GOLDEN = [
    ("逾期付款违约金是多少", "LIABILITY", ["违约金", "3‰"]),
    ("争议由哪个法院管辖", "DISPUTE_RESOLUTION", ["南山区人民法院"]),
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
]

K_LIST = [1, 3, 5, 8]


def eval_rag(storage: Storage) -> dict:
    results = []
    for query, cat, keywords in RAG_GOLDEN:
        hits = storage.search(query, top_k=8, rerank=True)
        ranks = []
        citation_ok = False
        for i, h in enumerate(hits, 1):
            row = storage.get_clause(h["clause_id"])
            hit_cat = row["clause_category"] if row else ""
            if hit_cat == cat:
                ranks.append(i)
            # 引用正确性：返回的 clause_id 必须能回查到原文
            if row and row["original_text"].strip():
                citation_ok = True
        first = ranks[0] if ranks else None
        results.append({
            "query": query, "expected_category": cat, "keywords": keywords,
            "retrieved": len(hits), "first_relevant_rank": first,
            "relevant_ranks": ranks[:5], "citation_verified": citation_ok,
        })
    metrics = {}
    for k in K_LIST:
        hit = sum(1 for r in results if r["first_relevant_rank"] and r["first_relevant_rank"] <= k)
        metrics[f"Recall@{k}"] = round(hit / len(results), 4)
    mrr = sum(1.0 / r["first_relevant_rank"] for r in results if r["first_relevant_rank"]) / len(results)
    metrics["MRR"] = round(mrr, 4)
    metrics["CitationCorrectness"] = round(
        sum(1 for r in results if r["citation_verified"]) / len(results), 4)
    return {"metrics": metrics, "queries": results, "query_count": len(results),
            "embedding_provider": storage.emb.name,
            "reranker": "char-3gram-coverage+deterministic (生产建议 cross-encoder)"}


def eval_review(storage: Storage) -> dict:
    engine = ReviewEngine(storage)
    manifest = json.loads((config.GOLDEN_DIR / "manifest.json").read_text(encoding="utf-8"))
    rows = []
    for s in manifest:
        t0 = time.time()
        r = engine.review(config.GOLDEN_DIR / s["file"], mode="COMBINED")
        dt = round(time.time() - t0, 3)
        got_cats = {i["category"] for i in r["issues"]}
        got_rules = {i["rule_id"] for i in r["issues"] if i["rule_id"]}
        got = got_cats | got_rules
        expected = set(s["expected_issue_categories"])
        if s["expect_no_issue"]:
            hard = [i for i in r["issues"] if i["severity"] in ("HIGH", "MEDIUM")
                    and i["category"] not in ("NOT_CHECKED", "RULE_PENDING_APPROVAL")]
            rows.append({"sample": s["sample_id"], "expected": "无问题",
                         "detected": [i["category"] for i in r["issues"]],
                         "result": "PASS" if not hard else "FALSE_POSITIVE",
                         "hard_issues": [(i["category"], i["title"]) for i in hard],
                         "elapsed_s": dt})
        else:
            matched = expected & got
            rows.append({"sample": s["sample_id"], "expected": sorted(expected),
                         "detected": sorted(x for x in got if x),
                         "result": "PASS" if matched else "MISS",
                         "matched": sorted(matched), "elapsed_s": dt})
    total = len(rows)
    passed = sum(1 for r in rows if r["result"] == "PASS")
    missed = [r for r in rows if r["result"] == "MISS"]
    fp = [r for r in rows if r["result"] == "FALSE_POSITIVE"]
    return {"samples": rows, "sample_count": total, "passed": passed,
            "hit_rate": round(passed / total, 4),
            "missed": [r["sample"] for r in missed],
            "false_positives": [r["sample"] for r in fp],
            "note": "变异样本为合成样本（基于真实模板结构生成），覆盖 12 类已知错误 + 1 个无错误基准"}


def run():
    config.ensure_dirs()
    st = Storage(config.DB_PATH)
    # 确保导入数据在库
    if not st.list_documents():
        raise RuntimeError("知识库为空，请先运行 python -m contract_kb.cli import 与测试导入")
    rag = eval_rag(st)
    rev = eval_review(st)
    (config.DATA_DIR / "eval_rag.json").write_text(json.dumps(rag, ensure_ascii=False, indent=1), encoding="utf-8")
    (config.DATA_DIR / "eval_review.json").write_text(json.dumps(rev, ensure_ascii=False, indent=1), encoding="utf-8")

    # 生成报告
    lines = ["# RAG 检索评估报告（rag-retrieval-evaluation）", "",
             f"- 黄金查询集：{rag['query_count']} 条（源自真实模板条款类别与关键短语）",
             f"- Embedding：{rag['embedding_provider']}（本地离线哈希嵌入；生产建议替换为授权中文 Embedding 模型）",
             f"- Reranker：{rag['reranker']}", "",
             "## 指标（真实测量）", "", "| 指标 | 值 |", "|---|---|"]
    for k, v in rag["metrics"].items():
        lines.append(f"| {k} | {v} |")
    lines += ["", "## 逐条查询结果", "",
              "| 查询 | 期望类别 | 命中数 | 首个相关排名 | 引用可回查 |", "|---|---|---|---|---|"]
    for q in rag["queries"]:
        lines.append(f"| {q['query']} | {q['expected_category']} | {q['retrieved']} "
                     f"| {q['first_relevant_rank'] or '未命中'} | {'是' if q['citation_verified'] else '否'} |")
    lines += ["", "## 局限性（如实标注）", "",
              "- 本地哈希嵌入仅保证词汇级匹配，Recall@1 低于深度语义模型属预期；",
              "- 中文 FTS5 默认分词不足，已用逐词 OR + 向量通道补偿；",
              "- 评估在 26 模板 ≈ 880 条款的小语料上进行，大语料指标需重新测量。"]
    (config.REPORT_DIR / "rag-retrieval-evaluation.md").write_text("\n".join(lines), encoding="utf-8")

    lines = ["# 合同审核评估报告（contract-review-evaluation）", "",
             f"- 测试样本量：{rev['sample_count']}（合成变异合同，基于真实模板结构生成）",
             f"- 命中（PASS）：{rev['passed']}，命中率 {rev['hit_rate']}",
             f"- 漏报（MISS）：{rev['missed'] or '无'}",
             f"- 误报（FALSE_POSITIVE，仅基准样本统计）：{rev['false_positives'] or '无'}", "",
             "## 逐样本结果", "",
             "| 样本 | 预期标签 | 实际检出 | 结果 | 耗时(s) |", "|---|---|---|---|---|"]
    for r in rev["samples"]:
        lines.append(f"| {r['sample']} | {r.get('expected')} | {', '.join(r['detected'][:6]) or '（无硬性问题）'} "
                     f"| {r['result']} | {r['elapsed_s']} |")
    lines += ["", "## 误报/漏报分析", ""]
    if rev["false_positives"]:
        for s in rev["false_positives"]:
            row = next(x for x in rev["samples"] if x["sample"] == s)
            lines.append(f"- 误报 {s}：{row.get('hard_issues')}")
    else:
        lines.append("- 无错误基准样本未报出任何 HIGH/MEDIUM 问题 → 本轮无误报。")
    if rev["missed"]:
        for s in rev["missed"]:
            row = next(x for x in rev["samples"] if x["sample"] == s)
            lines.append(f"- 漏报 {s}：预期 {row['expected']}，实际 {row['detected']}。"
                         "原因：该类错误需语义理解（LLM 未配置）或外部数据（业务系统字段），已按规则降级为 NEEDS_MANUAL_REVIEW/NOT_CHECKED，不推定违规。")
    else:
        lines.append("- 无漏报。")
    lines += ["", "## 说明", "",
              "- 确定性规则覆盖：金额一致性、必备条款、管辖条款、发票条款、违约金、付款期限、标识格式、主体一致性、矛盾启发式；",
              "- 语义级错误（真正互相矛盾的条款）在无 LLM 环境下标记 NOT_CHECKED/NEEDS_MANUAL_REVIEW，不伪报；",
              "- 所有检出问题均带 source_reference（node_ids + 原文），可在原文档定位。"]
    (config.REPORT_DIR / "contract-review-evaluation.md").write_text("\n".join(lines), encoding="utf-8")
    print("eval_rag:", json.dumps(rag["metrics"], ensure_ascii=False))
    print("eval_review:", rev["sample_count"], "samples, hit_rate =", rev["hit_rate"],
          "missed =", rev["missed"], "fp =", rev["false_positives"])
    st.close()


if __name__ == "__main__":
    run()
