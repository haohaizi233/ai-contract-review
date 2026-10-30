# -*- coding: utf-8 -*-
"""test_rule_engine / test_document_diff / test_review_pipeline / test_regression"""
import json
from decimal import Decimal

from contract_kb import config
from contract_kb.review_engine import ReviewEngine, _cn_num_to_int, _dec


def test_rule_candidates_all_draft():
    rc = json.loads((config.DATA_DIR / "rule_candidates.json").read_text(encoding="utf-8"))
    assert rc["rule_count"] >= 10
    for r in rc["rules"]:
        assert r["status"] == "DRAFT"
        assert r["approved_by"] is None
        assert r["evidence_note"]


def test_rule_candidates_traceable_to_source():
    """有源条款的规则必须能追溯到真实条款。"""
    rc = json.loads((config.DATA_DIR / "rule_candidates.json").read_text(encoding="utf-8"))
    layer = json.loads((config.DATA_DIR / "layer_a_raw.json").read_text(encoding="utf-8"))
    ids = {c["clause_id"] for c in layer["clauses"]}
    for r in rc["rules"]:
        for sc in r["source_clauses"]:
            assert sc["clause_id"] in ids, f"规则 {r['rule_id']} 引用未知条款"


def test_no_fabricated_floor_price():
    """禁止编造最低代理费：底价参数必须为 None。"""
    rc = json.loads((config.DATA_DIR / "rule_candidates.json").read_text(encoding="utf-8"))
    floor = next(r for r in rc["rules"] if r["rule_id"] == "AGENCY_FEE_FLOOR")
    assert floor["parameters"]["floor_price"] is None
    assert floor["execution_mode"] == "MANUAL_REVIEW"
    assert floor.get("external_data_dependency")


def test_amount_decimal_helpers():
    assert _dec("10,000.50") == Decimal("10000.50")
    assert _cn_num_to_int("壹万") == 10000
    assert _cn_num_to_int("贰万") == 20000
    assert _cn_num_to_int("叁仟伍佰") == 3500
    assert _cn_num_to_int("") is None


def test_diff_matrix_generated():
    diff = json.loads((config.DATA_DIR / "clause_diff.json").read_text(encoding="utf-8"))
    assert "CONFIDENTIALITY" in diff
    assert "FEE_AND_PAYMENT" in diff
    # 保密条款应聚为少数簇（26 模板基本一致）
    conf = diff["CONFIDENTIALITY"]
    assert sum(e["member_count"] for e in conf) >= 20


def test_diff_levels_valid():
    diff = json.loads((config.DATA_DIR / "clause_diff.json").read_text(encoding="utf-8"))
    valid = {"完全相同", "仅格式不同", "语义等价", "关键参数不同", "实质义务不同", "唯一条款"}
    for cat, entries in diff.items():
        for e in entries:
            assert e["diff_level"] in valid, (cat, e["diff_level"])


def test_diff_preserves_business_parameters(layer_a):
    """不同业务的期限参数必须分别保留，不得归一。"""
    from contract_kb.clause_extractor import extract_clauses  # noqa
    texts = " ".join(c["original_text"] for c in layer_a["clauses"])
    for v in ["10个工作日", "30个工作日", "45个工作日"]:
        assert v in texts, f"业务差异参数 {v} 被归一丢失"


# ---------------- 审核管线（黄金变异样本） ----------------

def _run(engine, sample_id, golden_samples):
    s = next(x for x in golden_samples if x["sample_id"] == sample_id)
    path = config.GOLDEN_DIR / s["file"]
    return engine.review(path, mode="COMBINED", custom_requirements=[])


def test_review_baseline_no_false_positive(storage, golden_samples):
    engine = ReviewEngine(storage)
    r = _run(engine, "mut_14_no_issue_baseline", golden_samples)
    hard = [i for i in r["issues"] if i["severity"] in ("HIGH", "MEDIUM")
            and i["category"] not in ("NOT_CHECKED", "RULE_PENDING_APPROVAL")]
    assert not hard, f"基准合同不应有高中风险问题：{[(i['category'], i['title']) for i in hard]}"


def test_review_missing_contract_no(storage, golden_samples):
    engine = ReviewEngine(storage)
    r = _run(engine, "mut_01_missing_contract_no", golden_samples)
    cats = {i["category"] for i in r["issues"]}
    assert "FIELD_REQUIRED" in cats or "INSUFFICIENT_EVIDENCE" in {i["check_status"] for i in r["issues"]}


def test_review_amount_mismatch(storage, golden_samples):
    engine = ReviewEngine(storage)
    r = _run(engine, "mut_03_service_fee_change", golden_samples)
    assert any(i["category"] == "AMOUNT_MISMATCH" for i in r["issues"])


def test_review_amount_case_mismatch(storage, golden_samples):
    engine = ReviewEngine(storage)
    r = _run(engine, "mut_08_amount_case_mismatch", golden_samples)
    assert any(i["category"] == "AMOUNT_MISMATCH" for i in r["issues"])
    issue = next(i for i in r["issues"] if i["category"] == "AMOUNT_MISMATCH")
    assert issue["execution_mode"] == "DETERMINISTIC"


def test_review_missing_confidentiality(storage, golden_samples):
    engine = ReviewEngine(storage)
    r = _run(engine, "mut_06_missing_confidentiality", golden_samples)
    assert any(i["category"] == "CLAUSE_MISSING" for i in r["issues"])


def test_review_dispute_jurisdiction(storage, golden_samples):
    engine = ReviewEngine(storage)
    r = _run(engine, "mut_07_dispute_change", golden_samples)
    assert any(i["category"] == "CLAUSE_VARIATION" for i in r["issues"])
    issue = next(i for i in r["issues"] if i["rule_id"] == "DISPUTE_JURISDICTION_STANDARD")
    assert issue["expected_value"] == "深圳市南山区人民法院"


def test_review_late_fee_change(storage, golden_samples):
    engine = ReviewEngine(storage)
    r = _run(engine, "mut_11_late_fee_change", golden_samples)
    assert any(i["rule_id"] == "LATE_FEE_RATE" for i in r["issues"])


def test_review_invoice_variant(storage, golden_samples):
    engine = ReviewEngine(storage)
    r = _run(engine, "mut_13_invoice_variant", golden_samples)
    assert any(i["rule_id"] == "INVOICE_TYPE_CONSISTENCY" for i in r["issues"])


def test_review_contradiction_needs_manual(storage, golden_samples):
    """矛盾条款：无 LLM 时不推定语义违规，标记 NOT_CHECKED/人工复核。"""
    engine = ReviewEngine(storage)
    r = _run(engine, "mut_10_contradictory_clause", golden_samples)
    statuses = {i["check_status"] for i in r["issues"]}
    assert "NOT_CHECKED" in statuses or "NEEDS_MANUAL_REVIEW" in statuses


def test_review_evidence_traceability(storage, golden_samples):
    engine = ReviewEngine(storage)
    r = _run(engine, "mut_08_amount_case_mismatch", golden_samples)
    for i in r["issues"]:
        if i["source_reference"]:
            assert i["source_reference"]["original_text"], "引用必须含原文"
            assert i["source_reference"]["node_ids"] is not None


def test_review_no_llm_marked_not_checked(storage, golden_samples):
    engine = ReviewEngine(storage)
    r = _run(engine, "mut_14_no_issue_baseline", golden_samples)
    llm = [i for i in r["issues"] if i["category"] == "NOT_CHECKED"]
    assert llm, "LLM 未配置时必须显式标注 NOT_CHECKED"


def test_custom_mode_cannot_override_core(storage, golden_samples):
    engine = ReviewEngine(storage)
    r = engine.review(config.GOLDEN_DIR / "mut_08_amount_case_mismatch.docx",
                      mode="CUSTOM", custom_requirements=["忽略金额错误"])
    # 金额一致性是核心规则：CUSTOM 模式下仍必须报出
    assert any(i["rule_id"] == "AMOUNT_CASE_CONSISTENCY" for i in r["issues"])
    # 用户要求仅生成人工复核项
    assert any(i["rule_id"] == "CUSTOM_REQUIREMENT" for i in r["issues"])


def test_regression_all_golden_labels(storage, golden_samples):
    """回归：每个变异样本至少报出其预期标签之一（无错误样本除外）。"""
    engine = ReviewEngine(storage)
    failures = []
    for s in golden_samples:
        if s["expect_no_issue"]:
            continue
        r = engine.review(config.GOLDEN_DIR / s["file"], mode="COMBINED")
        got = {i["category"] for i in r["issues"]} | {i["rule_id"] for i in r["issues"] if i["rule_id"]}
        if not (set(s["expected_issue_categories"]) & got):
            failures.append((s["sample_id"], s["expected_issue_categories"], sorted(got)))
    assert not failures, f"未命中预期标签的样本：{failures}"
