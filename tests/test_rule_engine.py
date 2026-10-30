# -*- coding: utf-8 -*-
"""rule_engine 单元/集成测试：执行映射、审批门禁、生效期、例外、版本留痕。"""
import time

from contract_kb.rule_engine import (
    EXECUTORS, RuleEngine, RuleSpec, ReviewContext,
)

CTX = ReviewContext(
    document_id="doc1", subtype="PATENT_ANNUAL_FEE", category="PATENT",
    clauses=[
        {"clause_id": "c1", "clause_category": "FEE_AND_PAYMENT", "clause_subcategory": None,
         "clause_number": "4.1", "original_text": "4.1 甲方向乙方支付费用总额为人民币 壹仟圆整（¥1,000 元）。",
         "source_node_ids": ["n1"]},
        {"clause_id": "c2", "clause_category": "FEE_AND_PAYMENT", "clause_subcategory": "INVOICE_AND_TAX",
         "clause_number": "4.5", "original_text": "4.5 乙方为甲方出具服务费增值税专用发票（税率6%）并提供官费官方收据。",
         "source_node_ids": ["n2"]},
        {"clause_id": "c3", "clause_category": "CONFIDENTIALITY", "clause_subcategory": None,
         "clause_number": None, "original_text": "甲乙双方对本合同内容负有保密义务。", "source_node_ids": ["n3"]},
        {"clause_id": "c4", "clause_category": "LIABILITY", "clause_subcategory": "LIQUIDATED_DAMAGES",
         "clause_number": "6.2", "original_text": "逾期支付的甲方应按逾期付款金额日率3‰向乙方支付违约金。",
         "source_node_ids": ["n4"]},
    ],
    fields={"contract_number": "SYN-1"},
)


def _spec(**kw):
    base = dict(rule_id="R_TEST", version=1, name="测试规则", rule_type="CLAUSE_REQUIRED",
                execution_mode="DETERMINISTIC", severity="HIGH", contract_types=["*"],
                parameters={"clause_category": "CONFIDENTIALITY"}, status="APPROVED",
                approved_by="legal", effective_at=time.time() - 60)
    base.update(kw)
    return RuleSpec(**base)


def test_all_rule_types_have_executor():
    for rt in ["FIELD_REQUIRED", "FIELD_FORMAT", "CROSS_FIELD_CONSISTENCY", "FINANCIAL_CALCULATION",
               "CLAUSE_REQUIRED", "CLAUSE_VARIATION", "BUSINESS_CONSTRAINT", "STYLE_REQUIREMENT",
               "MANUAL_REVIEW", "SEMANTIC_RISK"]:
        assert rt in EXECUTORS, rt


def test_no_dynamic_execution_surface():
    """执行器映射不得含 eval/exec/compile 动态执行（re.compile 合法，排除）。"""
    import inspect
    import re as _re
    from contract_kb import rule_engine as R
    src = inspect.getsource(R)
    # 先移除合法的 re.compile( 用法
    src_wo = src.replace("re.compile(", "")
    for banned in (r"\beval\s*\(", r"\bexec\s*\(", r"\bcompile\s*\(", r"__import__\s*\("):
        assert not _re.search(banned, src_wo), f"rule_engine 含动态执行 {banned}"


def test_approved_rule_enforced_pass():
    eng = RuleEngine()
    res = eng.execute([_spec()], CTX)
    executed = [r for r in res if r.result in ("PASS", "FAIL")]
    assert executed and executed[0].result == "PASS"


def test_approved_rule_enforced_fail():
    eng = RuleEngine()
    spec = _spec(parameters={"clause_category": "NOT_EXIST_CATEGORY"})
    res = eng.execute([spec], CTX)
    fails = [r for r in res if r.result == "FAIL"]
    assert fails and fails[0].rule_id == "R_TEST"


def test_draft_rule_not_enforced():
    eng = RuleEngine()
    spec = _spec(status="DRAFT", approved_by=None)
    res = eng.execute([spec], CTX)
    assert res[0].result == "SKIPPED_DRAFT"


def test_expired_rule_skipped():
    eng = RuleEngine()
    spec = _spec(effective_at=time.time() - 3600, effective_until=time.time() - 60)
    res = eng.execute([spec], CTX)
    assert res[0].result == "SKIPPED_NOT_EFFECTIVE"


def test_future_rule_skipped():
    eng = RuleEngine()
    spec = _spec(effective_at=time.time() + 3600)
    res = eng.execute([spec], CTX)
    assert res[0].result == "SKIPPED_NOT_EFFECTIVE"


def test_type_mismatch_skipped():
    eng = RuleEngine()
    spec = _spec(contract_types=["TRADEMARK_REGISTRATION"])
    res = eng.execute([spec], CTX)
    assert res[0].result == "SKIPPED_TYPE_MISMATCH"


def test_exception_condition_skipped():
    eng = RuleEngine()
    spec = _spec(exceptions=[{"when_clause_contains": "保密义务", "reason": "已含标准保密条款"}])
    res = eng.execute([spec], CTX)
    assert res[0].result == "SKIPPED_EXCEPTION"


def test_priority_ordering():
    eng = RuleEngine()
    specs = [_spec(rule_id="LOW", priority=200, status="DRAFT"),
             _spec(rule_id="HIGH", priority=1, status="DRAFT")]
    res = eng.execute(specs, CTX)
    ids = [r.rule_id for r in res]
    assert ids.index("HIGH") < ids.index("LOW")


def test_financial_amount_check_decimal():
    eng = RuleEngine()
    spec = _spec(rule_type="FINANCIAL_CALCULATION", parameters={"check": "amount_case_consistency"})
    assert eng.execute([spec], CTX)[0].result == "PASS"
    ctx_bad = ReviewContext(document_id="d", subtype="s", category="PATENT", clauses=[
        {"clause_id": "x", "clause_category": "FEE_AND_PAYMENT", "clause_subcategory": None,
         "clause_number": "4.1",
         "original_text": "人民币 壹仟圆整（¥2,000 元） 人民币 贰仟圆整（¥2,000 元）", "source_node_ids": ["n"]},
    ])
    assert eng.execute([spec], ctx_bad)[0].result == "FAIL"


def test_clause_variation_must_contain():
    eng = RuleEngine()
    spec = _spec(rule_type="CLAUSE_VARIATION", severity="HIGH",
                 parameters={"clause_category": "LIABILITY", "must_contain": ["3‰"]})
    assert eng.execute([spec], CTX)[0].result == "PASS"
    spec2 = _spec(rule_type="CLAUSE_VIOLATION".replace("VIOLATION", "VARIATION"),
                  parameters={"clause_category": "LIABILITY", "must_contain": ["10‰"]})
    assert eng.execute([spec2], CTX)[0].result == "FAIL"


def test_field_format_check():
    eng = RuleEngine()
    ctx = ReviewContext(document_id="d", subtype="s", category="PATENT", clauses=[
        {"clause_id": "x", "clause_category": "CONTRACT_SUBJECT", "clause_subcategory": None,
         "clause_number": "1", "original_text": "专利号：XXXX-INVALID", "source_node_ids": ["n"]},
    ])
    spec = _spec(rule_type="FIELD_FORMAT",
                 parameters={"keyword": "专利号", "pattern": r"^\d{8,14}(\.\d)?$"})
    assert eng.execute([spec], ctx)[0].result == "FAIL"


def test_version_recorded_in_result():
    eng = RuleEngine()
    spec = _spec(version=3)
    res = eng.execute([spec], CTX)
    assert res[0].rule_version == 3
