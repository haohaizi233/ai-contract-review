# -*- coding: utf-8 -*-
"""第三轮整改回归（缺陷 5）：合同文本比较接口不得以字符串相似度断言“语义等价”。

覆盖：
- 否定词 / 金额 / 期限 / 责任主体变化必须命中专门检查（clause_diff 结构化变更）；
- 高相似度文本若含上述变化，不得输出“语义等价”；
- 相似度字段保留但明确标注为检索/差异提示。
"""

import json
import re

import pytest

VIEWER = {"X-User": "view_c", "X-Roles": "viewer", "X-Tenant": "t1"}


def _assert_no_semantic_equivalence_claim(r):
    """禁止语义等价断言：响应中任何“等价”字样必须出现在否定语境（不/未/禁）。"""
    assert r["diff_level"] != "语义等价", "禁止以字符串相似度产出语义等价结论"
    text = json.dumps(r, ensure_ascii=False)
    for m in re.finditer("等价", text):
        window = text[max(0, m.start() - 12) : m.start()]
        assert any(w in window for w in ("不", "未", "禁")), (
            f"出现未经否定的等价断言：…{window}等价…"
        )


@pytest.fixture(scope="module")
def client(storage):
    import contract_kb.api as api

    api._storage = storage
    from fastapi.testclient import TestClient

    return TestClient(api.app)


def _compare(client, a, b):
    resp = client.post(
        "/api/contract-kb/compare", json={"clause_texts": [a, b]}, headers=VIEWER
    )
    assert resp.status_code == 200
    return resp.json()


def test_compare_identical(client):
    r = _compare(client, "甲乙双方保密义务条款", "甲乙双方保密义务条款")
    assert r["identical"] is True
    assert r["diff_level"] == "完全相同"


def test_compare_negation_change_not_equivalent(client):
    """否定词变化（不予退还→应予退还）：相似度高也绝不能判“语义等价”。"""
    r = _compare(client, "合同期满后费用不予退还", "合同期满后费用应予退还")
    assert r["similarity"] >= 0.85
    _assert_no_semantic_equivalence_claim(r)
    assert "NEGATION_CHANGE" in r["specialized_checks"]
    assert r["affects_rights"] is True
    types = {d["delta_type"] for d in r["structured_deltas"]}
    assert "NEGATION_CHANGE" in types


def test_compare_amount_change_flagged(client):
    r = _compare(
        client,
        "甲方支付人民币 壹仟圆整（¥1,000 元）",
        "甲方支付人民币 贰仟圆整（¥2,000 元）",
    )
    assert "NUMBER_CHANGE" in r["specialized_checks"]
    assert r["diff_level"] in ("关键参数不同", "实质差异")
    _assert_no_semantic_equivalence_claim(r)


def test_compare_deadline_change_flagged(client):
    r = _compare(
        client, "甲方应在三个工作日内支付服务费", "甲方应在三十个工作日内支付服务费"
    )
    assert "DEADLINE_CHANGE" in r["specialized_checks"]
    _assert_no_semantic_equivalence_claim(r)


def test_compare_party_change_flagged(client):
    r = _compare(client, "甲方对本合同内容保密", "乙方对本合同内容保密")
    assert "PARTY_CHANGE" in r["specialized_checks"]
    _assert_no_semantic_equivalence_claim(r)


def test_compare_similarity_is_hint_only(client):
    r = _compare(client, "本合同一式两份", "本合同一式两份")
    assert "similarity" in r
    assert (
        "提示" in r["similarity_role"] and "不构成语义等价判断" in r["similarity_role"]
    )
    assert "人工" in r["legal_effect_note"] or "法务" in r["legal_effect_note"]


def test_compare_wording_only_has_no_rights_claim(client):
    r = _compare(client, "甲方应妥善保管相关资料", "甲方应妥善的保管相关资料")
    assert r["affects_rights"] is False
    _assert_no_semantic_equivalence_claim(r)
    assert r["diff_level"] == "文本差异（未判定语义等价）"


def test_distill_never_labels_semantic_equivalence(diff_result):
    """差异矩阵同样禁止产出“语义等价”标签（相似度仅作聚类线索）。"""
    diff, _md = diff_result
    for cat, entries in diff.items():
        for e in entries:
            assert e["diff_level"] != "语义等价", cat


@pytest.fixture(scope="module")
def diff_result(layer_a):
    from contract_kb import distill as D

    D.imported_index = {d["template_id"]: d for d in layer_a["documents"]}
    return D.build_clause_diff(layer_a)
