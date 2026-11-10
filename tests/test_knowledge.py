# -*- coding: utf-8 -*-
"""test_contract_classifier / test_clause_extractor / test_schema_extraction（合成语料）"""


def test_classifier_full_coverage(import_result):
    subs = {d["subtype"] for d in import_result["imported"]}
    assert len(subs) == 6, f"合成语料应识别 6 个业务子类，实际 {len(subs)}"
    assert all(
        d["category"] in ("PATENT", "TRADEMARK", "IP_GENERAL")
        for d in import_result["imported"]
    )


def test_classifier_confidence(import_result):
    for d in import_result["imported"]:
        assert d["classification_confidence"] >= 0.6, d["source_file"]
        assert d["classification_evidence"], d["source_file"]


def test_classifier_l1_l2_consistency(import_result):
    for d in import_result["imported"]:
        if d["category"] == "PATENT":
            assert d["subtype"].startswith("PATENT_"), d["source_file"]
        elif d["category"] == "TRADEMARK":
            assert d["subtype"].startswith("TRADEMARK_"), d["source_file"]
        else:
            assert d["subtype"].startswith("IP_GENERAL"), d["source_file"]


def test_classifier_subtype_matches_oracle(import_result):
    """合成语料子类已知（生成器标签），分类必须与真值一致。"""
    from contract_kb.evaluation.datasets.synthetic import SYNTH_TEMPLATES

    oracle = {t[0]: t[4] for t in SYNTH_TEMPLATES}
    for d in import_result["imported"]:
        assert d["subtype"] == oracle[d["source_file"]], (
            f"{d['source_file']}: 期望 {oracle[d['source_file']]}, 实际 {d['subtype']}"
        )


def test_clause_extractor_categories(layer_a):
    cats = {c["clause_category"] for c in layer_a["clauses"]}
    for must in [
        "CONTRACT_SUBJECT",
        "PARTY_A_OBLIGATIONS",
        "PARTY_B_OBLIGATIONS",
        "FEE_AND_PAYMENT",
        "CONFIDENTIALITY",
        "LIABILITY",
        "DISPUTE_RESOLUTION",
        "EFFECTIVENESS_AND_TERMINATION",
    ]:
        assert must in cats, f"缺少条款类别 {must}"


def test_clause_original_text_preserved(layer_a):
    for c in layer_a["clauses"]:
        assert c["original_text"].strip()
        assert len(c["original_text"].strip()) >= 2


def test_clause_normalization_marked(layer_a):
    """归一化摘要必须标注生成方式，不得冒充原文。"""
    for c in layer_a["clauses"]:
        assert c["summary_method"].startswith("deterministic")


def test_clause_source_nodes_traceable(layer_a, parsed_docs):
    node_ids = set()
    for doc in parsed_docs.values():
        node_ids.update(n.node_id for n in doc.nodes)
    for c in layer_a["clauses"]:
        if c["source_node_ids"]:
            assert set(c["source_node_ids"]) & node_ids, c["clause_id"]


def test_clause_company_standard_not_legal(layer_a):
    """企业惯例不得标成法律强制。"""
    for c in layer_a["clauses"]:
        if c["legal_requirement"]:
            assert "应当" in c["original_text"] or "必须" in c["original_text"]


def test_clause_entities_extracted(layer_a):
    money = [
        c
        for c in layer_a["clauses"]
        if any(e["type"] in ("MONEY", "MONEY_PLACEHOLDER") for e in c["entities"])
    ]
    days = [
        c
        for c in layer_a["clauses"]
        if any(e["type"] == "WORKDAYS" for e in c["entities"])
    ]
    rates = [
        c for c in layer_a["clauses"] if any(e["type"] == "RATE" for e in c["entities"])
    ]
    assert len(money) >= 5 and len(days) >= 5 and len(rates) >= 3


def test_synthetic_corpus_has_no_real_enterprise_info(parsed_docs):
    """Phase 0 安全：合成语料只允许合成标记（正向白名单校验，不引入真实值）。"""
    from contract_kb.evaluation.datasets.synthetic import SYNTH_COMPANY_B, SYNTH_ACCOUNT
    import re as _re

    for name, doc in parsed_docs.items():
        text = "\n".join(n.text for n in doc.nodes)
        # 1) 必含合成主体标记
        assert SYNTH_COMPANY_B in text, name
        # 2) 邮箱必须全部为 example.com 合成域
        for m in _re.findall(r"[\w.\-]+@[\w.\-]+", text):
            assert m.endswith("@example.com"), f"{name} 出现非合成邮箱 {m}"
        # 3) 银行账号/手机号只允许合成值
        from contract_kb.evaluation.datasets.synthetic import SYNTH_PHONE

        for m in _re.findall(r"\d{10,}", text):
            assert m in (SYNTH_ACCOUNT, SYNTH_PHONE), f"{name} 出现非合成数字串 {m}"
        # 4) 受控特征词扫描（清单由本地环境变量提供，不硬编码真实标记）
        import os as _os

        banned = [
            m for m in _os.environ.get("CONTRACT_KB_BANNED_MARKERS", "").split("|") if m
        ]
        for marker in banned:
            assert marker not in text.lower(), f"{name} 含受控特征 {marker}"


def test_field_schema_extraction(layer_a):
    from contract_kb.distill import build_field_schema

    fs = build_field_schema(layer_a, {"imported": layer_a["documents"]})
    keys = {f["field_key"] for f in fs["fields"]}
    for must in [
        "contract_number",
        "official_fee",
        "service_fee",
        "quantity",
        "contract_total",
        "tax_rate",
        "payment_deadline",
    ]:
        assert must in keys, f"字段 Schema 缺少 {must}"


def test_field_schema_required_not_auto_approved(layer_a):
    from contract_kb.distill import build_field_schema

    fs = build_field_schema(layer_a, {"imported": layer_a["documents"]})
    for f in fs["fields"]:
        assert f["required_approved"] is None, "未审批的必填判定不得自动置为已批"
        assert f["source_template_ids"], f"字段 {f['field_key']} 无来源模板"
