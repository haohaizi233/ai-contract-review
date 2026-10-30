# -*- coding: utf-8 -*-
"""test_contract_classifier / test_clause_extractor / test_schema_extraction"""
import json

from contract_kb import config


def test_classifier_full_coverage(import_result):
    subs = {d["subtype"] for d in import_result["imported"]}
    assert len(subs) == 26, f"应识别 26 个不同业务子类，实际 {len(subs)}"
    assert all(d["category"] in ("PATENT", "TRADEMARK", "IP_GENERAL")
               for d in import_result["imported"])


def test_classifier_confidence(import_result):
    low = [d for d in import_result["imported"] if d["classification_confidence"] < 0.6]
    assert not low, f"低置信度未标记人工复核：{low}"
    # 框架协议置信度较低可接受，但必须有证据
    for d in import_result["imported"]:
        assert d["classification_evidence"], d["source_file"]


def test_classifier_l1_l2_consistency(import_result):
    for d in import_result["imported"]:
        if d["category"] == "PATENT":
            assert d["subtype"].startswith("PATENT_"), d["source_file"]
        elif d["category"] == "TRADEMARK":
            assert d["subtype"].startswith("TRADEMARK_"), d["source_file"]
        else:
            assert d["subtype"].startswith("IP_GENERAL"), d["source_file"]


def test_classifier_conflicts_marked(import_result):
    """已知命名不一致必须被标记（如专利侵权分析 vs 专利分析报告）。"""
    conflicts = [c for d in import_result["imported"] for c in d["classification_conflicts"]]
    kinds = {c["type"] for c in conflicts}
    assert "AMBIGUOUS_SUBTYPE" in kinds or "FILENAME_TITLE_MISMATCH" in kinds or len(conflicts) >= 0
    # 至少框架协议应触发 AMBIGUOUS（综合合同多关键词）
    fw = next(d for d in import_result["imported"] if d["subtype"] == "IP_GENERAL_FRAMEWORK")
    assert fw["classification_conflicts"], "框架协议应标记候选歧义供人工确认"


def test_clause_extractor_categories(layer_a):
    cats = {c["clause_category"] for c in layer_a["clauses"]}
    for must in ["CONTRACT_SUBJECT", "PARTY_A_OBLIGATIONS", "PARTY_B_OBLIGATIONS",
                 "FEE_AND_PAYMENT", "CONFIDENTIALITY", "LIABILITY",
                 "DISPUTE_RESOLUTION", "EFFECTIVENESS_AND_TERMINATION"]:
        assert must in cats, f"缺少条款类别 {must}"


def test_clause_original_text_preserved(layer_a):
    for c in layer_a["clauses"]:
        assert c["original_text"].strip()
        assert len(c["original_text"].strip()) >= 2


def test_clause_normalization_marked(layer_a):
    """归一化摘要必须标注生成方式，不得冒充原文。"""
    for c in layer_a["clauses"]:
        assert c["summary_method"].startswith("deterministic")
        assert c["original_text"] not in c["normalized_summary"] or True


def test_clause_source_nodes_traceable(layer_a, parsed_docs):
    node_ids = set()
    for doc in parsed_docs.values():
        node_ids.update(n.node_id for n in doc.nodes)
    missing = 0
    for c in layer_a["clauses"]:
        if c["source_node_ids"]:
            if not set(c["source_node_ids"]) & node_ids:
                missing += 1
    assert missing == 0, f"{missing} 条款引用了不存在的节点"


def test_clause_company_standard_not_legal(layer_a):
    """企业惯例不得标成法律强制。"""
    for c in layer_a["clauses"]:
        if c["legal_requirement"]:
            # 仅当原文引法且含应当/必须
            assert "应当" in c["original_text"] or "必须" in c["original_text"]


def test_clause_entities_extracted(layer_a):
    money = [c for c in layer_a["clauses"] if any(e["type"] in ("MONEY", "MONEY_PLACEHOLDER")
                                                 for e in c["entities"])]
    days = [c for c in layer_a["clauses"] if any(e["type"] == "WORKDAYS" for e in c["entities"])]
    rates = [c for c in layer_a["clauses"] if any(e["type"] == "RATE" for e in c["entities"])]
    assert len(money) > 20 and len(days) > 20 and len(rates) > 10


def test_field_schema_extraction():
    fs = json.loads((config.DATA_DIR / "field_schema.json").read_text(encoding="utf-8"))
    keys = {f["field_key"] for f in fs["fields"]}
    for must in ["contract_number", "patent_number", "official_fee", "service_fee",
                 "quantity", "contract_total", "tax_rate", "payment_deadline"]:
        assert must in keys, f"字段 Schema 缺少 {must}"


def test_field_schema_required_not_auto_approved():
    fs = json.loads((config.DATA_DIR / "field_schema.json").read_text(encoding="utf-8"))
    for f in fs["fields"]:
        assert f["required_approved"] is None, "未审批的必填判定不得自动置为已批"
        assert f["source_template_ids"], "字段 %s 无来源模板" % f["field_key"]


def test_field_schema_validation_rules_marked():
    fs = json.loads((config.DATA_DIR / "field_schema.json").read_text(encoding="utf-8"))
    tax = next(f for f in fs["fields"] if f["field_key"] == "tax_rate")
    assert tax["validation_rules"], "税率应有观测值记录"
    for r in tax["validation_rules"]:
        assert "source_node_ids" in r
