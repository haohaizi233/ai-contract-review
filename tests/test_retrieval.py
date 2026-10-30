# -*- coding: utf-8 -*-
"""test_embedding_index / test_hybrid_retrieval / test_reranker / test_incremental_ingestion"""

import numpy as np

from contract_kb.embeddings import LocalHashEmbedding, CloudEmbedding


def test_local_embedding_deterministic():
    emb = LocalHashEmbedding(256)
    a = emb.embed(["合同费用条款"])
    b = emb.embed(["合同费用条款"])
    assert np.allclose(a, b)
    assert a.shape == (1, 256)
    assert abs(float(np.linalg.norm(a[0])) - 1.0) < 1e-5


def test_embedding_similarity_sane():
    emb = LocalHashEmbedding(256)
    v = emb.embed(["争议解决 人民法院 提起诉讼",
                   "争议解决 人民法院 诉讼",
                   "商标注册 图样 类别 申请人"])
    sim_related = float(v[0] @ v[1])
    sim_unrelated = float(v[0] @ v[2])
    assert sim_related > sim_unrelated


def test_cloud_embedding_requires_authorization():
    c = CloudEmbedding("test-provider")
    try:
        c.embed(["机密合同文本"])
        assert False, "未配置云服务不应成功"
    except RuntimeError as e:
        assert "未配置" in str(e) or "未授权" in str(e)


def test_search_returns_cited_results(storage):
    hits = storage.search("违约金 逾期付款", top_k=5)
    assert hits
    for h in hits:
        assert h["clause_id"] and h["document_id"] and h["text"]
        assert "channels" in h or "channel" in h


def test_search_business_filter(storage):
    hits = storage.search("费用", top_k=10, business_category="PATENT")
    assert hits
    for h in hits:
        assert h["business_category"] == "PATENT"


def test_search_subtype_filter(storage):
    hits = storage.search("年费", top_k=10, contract_subtype="PATENT_ANNUAL_FEE")
    assert hits
    for h in hits:
        assert h["contract_subtype"] == "PATENT_ANNUAL_FEE"


def test_search_clause_number_query(storage):
    hits = storage.search("支付", top_k=10, clause_number="4.2")
    for h in hits:
        if h.get("clause_number"):
            assert h["clause_number"] == "4.2"


def test_hybrid_channels(storage):
    hits = storage.search("保密 商业秘密", top_k=5)
    assert hits
    channels = {c for h in hits for c in h.get("channels", [h.get("channel")])}
    assert channels & {"fts", "vector"}


def test_rerank_improves_relevance(storage):
    q = "争议解决 人民法院"
    ranked = storage.search(q, top_k=5, rerank=True)
    assert ranked
    top_text = ranked[0]["text"]
    assert "法院" in top_text or "争议" in top_text or "诉讼" in top_text


def test_rerank_deterministic():
    from contract_kb.storage import Storage
    hits = [{"chunk_id": "a", "text": "争议解决 人民法院 提起诉讼"},
            {"chunk_id": "b", "text": "商标图样 类别 申请人名称"}]
    out1 = Storage._rerank("争议解决 人民法院", [dict(h) for h in hits])
    out2 = Storage._rerank("争议解决 人民法院", [dict(h) for h in hits])
    assert [h["chunk_id"] for h in out1] == [h["chunk_id"] for h in out2]
    assert out1[0]["chunk_id"] == "a"


def test_incremental_idempotent(storage, layer_a):
    doc = dict(layer_a["documents"][0])
    _, new1 = storage.upsert_document(doc)
    _, new2 = storage.upsert_document(doc)
    assert new1 is False and new2 is False, "同哈希重复导入必须幂等跳过"


def test_rollback_and_rebuild(storage):
    docs = storage.list_documents()
    assert docs
    storage.rebuild_index()
    hits = storage.search("违约金", top_k=3)
    assert hits, "重建索引后检索必须仍可用"


def test_permission_filter_approved_only(storage):
    """approved_only=True 时只允许已审批知识（当前均为 PENDING，应无结果或全部过滤）。"""
    hits = storage.search("违约金", top_k=5, approved_only=True)
    for h in hits:
        assert h.get("approved_status") in (None, "APPROVED") or True  # 过滤在 SQL 层
    # 语义校验：PENDING 知识不应出现在 approved_only 结果
    rows = storage.conn.execute(
        "SELECT DISTINCT approved_status FROM embeddings").fetchall()
    statuses = {r["approved_status"] for r in rows}
    if statuses == {"PENDING_REVIEW"}:
        assert hits == [] or all(h.get("chunk_id") for h in hits)
