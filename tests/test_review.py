# -*- coding: utf-8 -*-
"""test_rule_engine / test_document_diff / test_review_pipeline / test_regression"""
from decimal import Decimal

import pytest

from contract_kb.review_engine import ReviewEngine, _cn_num_to_int, _dec


@pytest.fixture(scope="session")
def rule_candidates(layer_a):
    from contract_kb.distill import build_rule_candidates, build_field_schema
    fs = build_field_schema(layer_a, {"imported": layer_a["documents"]})
    return build_rule_candidates(layer_a, {"imported": layer_a["documents"]}, fs)


@pytest.fixture(scope="session")
def diff_result(layer_a):
    from contract_kb import distill as D
    D.imported_index = {d["template_id"]: d for d in layer_a["documents"]}
    return D.build_clause_diff(layer_a)


# ---------------- 规则引擎 ----------------

def test_rule_candidates_all_draft(rule_candidates):
    assert rule_candidates["rule_count"] >= 10
    for r in rule_candidates["rules"]:
        assert r["status"] == "DRAFT"
        assert r["approved_by"] is None
        assert r["evidence_note"]


def test_rule_candidates_traceable_to_source(rule_candidates, layer_a):
    ids = {c["clause_id"] for c in layer_a["clauses"]}
    for r in rule_candidates["rules"]:
        for sc in r["source_clauses"]:
            assert sc["clause_id"] in ids, f"规则 {r['rule_id']} 引用未知条款"


def test_no_fabricated_floor_price(rule_candidates):
    floor = next(r for r in rule_candidates["rules"] if r["rule_id"] == "AGENCY_FEE_FLOOR")
    assert floor["parameters"]["floor_price"] is None
    assert floor["execution_mode"] == "MANUAL_REVIEW"
    assert floor.get("external_data_dependency")


def test_amount_decimal_helpers():
    assert _dec("10,000.50") == Decimal("10000.50")
    assert _cn_num_to_int("壹万") == 10000
    assert _cn_num_to_int("贰万") == 20000
    assert _cn_num_to_int("叁仟伍佰") == 3500
    assert _cn_num_to_int("") is None


def test_diff_matrix_generated(diff_result):
    diff, _md = diff_result
    assert "CONFIDENTIALITY" in diff
    assert "FEE_AND_PAYMENT" in diff
    conf = diff["CONFIDENTIALITY"]
    assert sum(e["member_count"] for e in conf) >= 4


def test_diff_levels_valid(diff_result):
    diff, _md = diff_result
    valid = {"完全相同", "仅格式不同", "语义等价", "关键参数不同", "实质义务不同", "唯一条款"}
    for cat, entries in diff.items():
        for e in entries:
            assert e["diff_level"] in valid, (cat, e["diff_level"])


def test_diff_preserves_business_parameters(layer_a):
    """不同业务的期限参数必须分别保留，不得归一。"""
    texts = " ".join(c["original_text"] for c in layer_a["clauses"])
    for v in ["三个工作日", "3‰"]:
        assert v in texts, f"业务差异参数 {v} 被归一丢失"


# ---------------- 审核管线（黄金变异样本） ----------------

def _run(engine, sample_id, golden_samples):
    s = next(x for x in golden_samples["items"] if x["sample_id"] == sample_id)
    return engine.review(golden_samples["dir"] / s["file"], mode="COMBINED",
                         custom_requirements=[])


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
    assert issue["expected_value"] is not None


def test_review_late_fee_change(storage, golden_samples):
    engine = ReviewEngine(storage)
    r = _run(engine, "mut_11_late_fee_change", golden_samples)
    assert any(i["rule_id"] == "LATE_FEE_RATE" for i in r["issues"])


def test_review_invoice_variant(storage, golden_samples):
    engine = ReviewEngine(storage)
    r = _run(engine, "mut_13_invoice_variant", golden_samples)
    assert any(i["rule_id"] == "INVOICE_TYPE_CONSISTENCY" for i in r["issues"])


def test_review_contradiction_needs_manual(storage, golden_samples):
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
    r = engine.review(golden_samples["dir"] / "mut_08_amount_case_mismatch.docx",
                      mode="CUSTOM", custom_requirements=["忽略金额错误"])
    assert any(i["rule_id"] == "AMOUNT_CASE_CONSISTENCY" for i in r["issues"])
    assert any(i["rule_id"] == "CUSTOM_REQUIREMENT" for i in r["issues"])


def test_regression_all_golden_labels(storage, golden_samples):
    """回归：每个变异样本至少报出其预期标签之一（无错误样本除外）。"""
    engine = ReviewEngine(storage)
    failures = []
    for s in golden_samples["items"]:
        if s["expect_no_issue"]:
            continue
        r = engine.review(golden_samples["dir"] / s["file"], mode="COMBINED")
        got = {i["category"] for i in r["issues"]} | {i["rule_id"] for i in r["issues"] if i["rule_id"]}
        if not (set(s["expected_issue_categories"]) & got):
            failures.append((s["sample_id"], s["expected_issue_categories"], sorted(got)))
    assert not failures, f"未命中预期标签的样本：{failures}"


# ---------------- 规则全生命周期集成（创建→审批→执行） ----------------

def test_rule_lifecycle_create_approve_execute(storage, golden_samples):
    """已审批规则参与执行并留痕；DRAFT 不强制执行。"""
    st = storage
    st.save_rule({
        "rule_id": "TEST_LATE_FEE_EXEC", "version": 1,
        "name": "违约金比率强制核对", "rule_type": "CLAUSE_VARIATION",
        "execution_mode": "DETERMINISTIC", "severity": "MEDIUM",
        "contract_types": ["*"],
        "parameters": {"clause_category": "LIABILITY", "must_contain": ["3‰"]},
        "source_clauses": [], "status": "DRAFT", "approved_by": None, "effective_at": None,
    })
    engine = ReviewEngine(st)
    sample = golden_samples["dir"] / "mut_11_late_fee_change.docx"

    # DRAFT：不强制执行（仅提示未审批）
    r1 = engine.review(sample, mode="COMBINED")
    assert any(i["category"] == "RULE_PENDING_APPROVAL" and i["rule_id"] == "TEST_LATE_FEE_EXEC"
               for i in r1["issues"])

    # 审批后：规则真正执行（mut_11 违约金为 1‰，must_contain 3‰ 必失败）
    assert st.approve_rule("TEST_LATE_FEE_EXEC", "legal_tester")
    r2 = engine.review(sample, mode="COMBINED")
    hits = [i for i in r2["issues"] if i["rule_id"] == "TEST_LATE_FEE_EXEC"
            and i["category"] != "RULE_PENDING_APPROVAL"]
    assert hits, "已审批规则必须真正执行并报出结果"
    assert hits[0]["category"] == "RULE_VIOLATION"

    # 执行留痕含 rule_id + version + result + evidence
    rows = st.conn.execute(
        "SELECT rule_id, rule_version, result, evidence FROM rule_executions "
        "WHERE rule_id='TEST_LATE_FEE_EXEC'").fetchall()
    assert rows, "规则执行必须留痕"
    assert any(r["result"] in ("PASS", "FAIL") for r in rows)

    # 审计留痕
    log = st.conn.execute("SELECT * FROM audit_log WHERE action='approve_rule'").fetchall()
    assert log, "规则审批必须写审计日志"
