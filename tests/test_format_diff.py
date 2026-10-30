# -*- coding: utf-8 -*-
"""test_format_auditor / test_clause_diff（Phase 4）。"""

from contract_kb.format_auditor import audit_format
from contract_kb.clause_diff import diff_clauses, diff_clause_sets


class _FakeNode:
    def __init__(self, **kw):
        self.node_id = kw.get("node_id", "n")
        self.node_type = "paragraph"
        self.order = kw.get("order", 0)
        self.text = kw.get("text", "")
        self.section = kw.get("section")
        self.clause_number = None
        self.table_index = None
        self.row_index = None
        self.column_index = None
        self.style = kw.get("style", {})
        self.source_locator = {}


class _FakeDoc:
    def __init__(self, nodes, tables=None):
        self.document_id = "d1"
        self.nodes = nodes
        self.table_dims = tables or []
        self.headers = []
        self.footers = []
        self.page_margins_cm = None


def test_format_heading_bold_compliance():
    nodes = [
        _FakeNode(text="合同编号:X", order=0),
        _FakeNode(
            text="第一条 合同事项",
            order=1,
            style={"runs": [{"text": "第一条", "bold": True}]},
        ),
    ]
    doc = _FakeDoc(nodes)
    issues = audit_format(doc)["issues"]
    assert not any(
        i["check"] == "heading_level" and i["issue_type"] == "NON_COMPLIANT"
        for i in issues
    )


def test_format_heading_unbold_flagged():
    nodes = [
        _FakeNode(text="合同编号:X", order=0),
        _FakeNode(
            text="第一条 合同事项", order=1, style={"runs": [{"text": "第一条"}]}
        ),
    ]
    issues = audit_format(_FakeDoc(nodes))["issues"]
    hits = [
        i
        for i in issues
        if i["check"] == "heading_level" and i["issue_type"] == "NON_COMPLIANT"
    ]
    assert hits and hits[0]["expected"] == "bold"


def test_format_unreadable_distinct():
    """样式缺失 → FORMAT_UNREADABLE，不得判为 NON_COMPLIANT。"""
    nodes = [
        _FakeNode(text="合同编号:X", order=0, style={}),
        _FakeNode(text="第一条 合同事项", order=1, style={}),
    ]
    issues = audit_format(_FakeDoc(nodes))["issues"]
    kinds = {i["issue_type"] for i in issues if i["check"] == "heading_level"}
    assert kinds == {"FORMAT_UNREADABLE"}


def test_format_contract_number_position():
    nodes = [_FakeNode(text=f"段落{i}", order=i) for i in range(5)]
    nodes.append(_FakeNode(text="合同编号:X", order=5))
    issues = audit_format(_FakeDoc(nodes))["issues"]
    hits = [i for i in issues if i["check"] == "contract_number_position"]
    assert hits and hits[0]["issue_type"] == "TEMPLATE_DIFF"


def test_format_blank_lines():
    nodes = [
        _FakeNode(text="合同编号:X", order=0),
        _FakeNode(text="", order=1),
        _FakeNode(text="", order=2),
        _FakeNode(text="正文", order=3),
    ]
    issues = audit_format(_FakeDoc(nodes))["issues"]
    assert any(i["check"] == "blank_lines" for i in issues)


def test_format_numbering_continuity():
    nodes = [
        _FakeNode(text="合同编号:X", order=0),
        _FakeNode(text="4.1 甲方向乙方支付费用。", order=1),
        _FakeNode(text="4.3 账户信息。", order=2),
    ]
    issues = audit_format(_FakeDoc(nodes))["issues"]
    hits = [i for i in issues if i["check"] == "numbering"]
    assert hits and hits[0]["issue_type"] == "NON_COMPLIANT"


def test_format_table_structure():
    doc = _FakeDoc(
        [_FakeNode(text="合同编号:X")],
        tables=[{"table_index": 0, "rows": 0, "cols": 0}],
    )
    issues = audit_format(doc)["issues"]
    assert any(
        i["check"] == "table_structure" and i["issue_type"] == "NON_COMPLIANT"
        for i in issues
    )


def test_clause_diff_number_change():
    d = diff_clauses(
        "甲方支付人民币 壹仟圆整（¥1,000 元）", "甲方支付人民币 贰仟圆整（¥2,000 元）"
    )
    types = {x.delta_type for x in d}
    assert "NUMBER_CHANGE" in types


def test_clause_diff_deadline_change():
    d = diff_clauses("甲方三个工作日内支付", "甲方三十个工作日内支付")
    assert any(x.delta_type == "DEADLINE_CHANGE" for x in d)


def test_clause_diff_party_change():
    d = diff_clauses("甲方保守秘密", "乙方保守秘密")
    assert any(x.delta_type == "PARTY_CHANGE" for x in d)


def test_clause_diff_negation_change():
    d = diff_clauses("费用不予退还", "费用应予退还")
    assert any(x.delta_type == "NEGATION_CHANGE" for x in d)


def test_clause_diff_obligation_change():
    d = diff_clauses("甲方应当支付", "甲方可以支付")
    assert any(x.delta_type == "OBLIGATION_CHANGE" for x in d)


def test_clause_diff_no_false_legal_equivalence():
    """相似度不得作为法律等价断言（note 字段必须声明人工确认）。"""
    d = diff_clauses("甲方应当保密", "乙方可以披露")
    for x in d:
        assert "人工" in x.legal_effect_note or "法务" in x.legal_effect_note


def test_clause_diff_sets_missing_and_new():
    std = [
        {
            "clause_category": "CONFIDENTIALITY",
            "original_text": "保密义务条款",
            "source_node_ids": ["s1"],
        }
    ]
    rev = [
        {
            "clause_category": "LIABILITY",
            "original_text": "违约条款",
            "source_node_ids": ["r1"],
        }
    ]
    rep = diff_clause_sets(std, rev)
    statuses = {r["status"] for r in rep}
    assert "MISSING_IN_REVIEWED" in statuses
    assert "NEW_IN_REVIEWED" in statuses


def test_clause_diff_sets_pairing():
    std = [
        {
            "clause_category": "LIABILITY",
            "original_text": "逾期违约金日率3‰",
            "source_node_ids": ["s1"],
        }
    ]
    rev = [
        {
            "clause_category": "LIABILITY",
            "original_text": "逾期违约金日率1‰",
            "source_node_ids": ["r1"],
        }
    ]
    rep = diff_clause_sets(std, rev)
    diff = next(r for r in rep if r["clause_category"] == "LIABILITY")
    assert diff["status"] == "DIFF"
    assert any(d["delta_type"] == "NUMBER_CHANGE" for d in diff["deltas"])


# ---------------- 检索基准（困难负样本） ----------------


def test_retrieval_golden_judge():
    from contract_kb.retrieval_golden import GOLDEN_QUERIES, judge_hits

    q = GOLDEN_QUERIES[0]
    hits = [
        {
            "text": "逾期支付违约金日率3‰",
            "business_category": "LIABILITY",
            "clause_category": "LIABILITY",
        },
        {
            "text": "逾期支付违约金日率1‰",
            "business_category": "PATENT",
            "clause_category": "LIABILITY",
        },
    ]
    j = judge_hits(q, hits)
    assert j["grades"][0] == 2
    assert j["grades"][1] == -1
    assert not j["avoided_hard_negatives"]


def test_retrieval_benchmark_runs():
    from contract_kb.benchmark_retrieval import build_bench_storage, run_benchmark
    from contract_kb.embeddings import LocalHashEmbedding
    import tempfile

    tmp = tempfile.mkdtemp()
    st = build_bench_storage(LocalHashEmbedding(256), f"{tmp}/b.db")
    res = run_benchmark(st, "test")
    assert res["query_count"] >= 10
    assert res["metrics"]["MRR"] > 0
    assert "HardNegAvoidance@8" in res["metrics"]
    st.close()
