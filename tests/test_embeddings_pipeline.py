# -*- coding: utf-8 -*-
"""第三轮整改回归（缺陷 3）：Embedding 接入 / 维度校验 / 索引重建与版本管理。

覆盖：
- SentenceTransformerEmbedding 真正接入默认工厂（st:<model>）与 Storage；
- 未安装 sentence-transformers 时明确报错（不静默降级）；
- 写入/查询向量维度校验（不一致 fail-closed）；
- Embedding 提供方/维度变更后必须 rebuild_index（版本递增留痕），
  不重建直接混用 → EmbeddingConfigMismatch。
"""

import sys
import types

import numpy as np
import pytest

from contract_kb.embeddings import (
    EmbeddingProvider,
    LocalHashEmbedding,
    get_embedding_provider,
)
from contract_kb.storage import EmbeddingConfigMismatch, Storage


class _TinyProvider(EmbeddingProvider):
    """测试用确定性小维度 Embedding（验证维度校验与版本管理）。"""

    def __init__(self, dim: int = 8, name: str = "tiny"):
        self.dim = dim
        self.name = f"{name}_{dim}"

    def embed(self, texts):
        mat = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for j, ch in enumerate(t[: self.dim]):
                mat[i, j % self.dim] += (ord(ch) % 7) + 1.0
            n = np.linalg.norm(mat[i])
            if n:
                mat[i] /= n
        return mat


class _BrokenDimProvider(_TinyProvider):
    """声称 dim=8 但实际输出 4 维（用于维度校验测试）。"""

    def embed(self, texts):
        return np.ones((len(texts), 4), dtype=np.float32)


DOC = {
    "template_id": "emb_test_doc",
    "source_file": "emb_synth.docx",
    "sha256": "c" * 64,
    "template_version": "v1",
    "category": "PATENT",
    "subtype": "PATENT_ANNUAL_FEE",
    "document_type": "contract_template",
    "clause_count": 2,
    "clauses": [
        {
            "clause_id": "ec1",
            "clause_number": "4.1",
            "clause_category": "FEE_AND_PAYMENT",
            "clause_subcategory": None,
            "original_text": "甲方应在三个工作日内支付服务费。",
            "normalized_summary": "[FEE]",
            "is_company_standard": True,
            "approval_status": "PENDING_REVIEW",
            "source_node_ids": ["n1"],
        },
        {
            "clause_id": "ec2",
            "clause_number": "6.2",
            "clause_category": "LIABILITY",
            "clause_subcategory": "LIQUIDATED_DAMAGES",
            "original_text": "逾期支付按日率3‰支付违约金。",
            "normalized_summary": "[LIAB]",
            "is_company_standard": True,
            "approval_status": "PENDING_REVIEW",
            "source_node_ids": ["n2"],
        },
    ],
}


# ---------------- 工厂接入 ----------------


def test_default_factory_local_hash():
    p = get_embedding_provider("local_hash")
    assert isinstance(p, LocalHashEmbedding)
    assert p.dim == 256


def test_factory_st_spec_wired(monkeypatch):
    """st:<model> 路由到 SentenceTransformerEmbedding（真实接入工厂）。"""
    fake_mod = types.ModuleType("sentence_transformers")

    class FakeST:
        def __init__(self, model_name):
            self.model_name = model_name

        def get_sentence_embedding_dimension(self):
            return 384

        def encode(self, texts, **kw):
            return np.stack([np.full(384, 0.5, dtype=np.float32) for _ in texts])

    fake_mod.SentenceTransformer = FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_mod)
    p = get_embedding_provider("st:fake-bge")
    from contract_kb.embeddings_real import SentenceTransformerEmbedding

    assert isinstance(p, SentenceTransformerEmbedding)
    assert p.dim == 384 and p.name == "st:fake-bge"
    out = p.embed(["测试"])
    assert out.shape == (1, 384)


def test_factory_st_spec_missing_package_fails_loud(monkeypatch):
    """未安装 sentence-transformers：明确报错，不静默降级为哈希嵌入。"""
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    with pytest.raises(RuntimeError, match="sentence-transformers"):
        get_embedding_provider("st:BAAI/bge-m3")


def test_storage_uses_factory_provider(tmp_path):
    st = Storage(tmp_path / "s.db", emb=get_embedding_provider("local_hash"))
    assert st.emb.name == "local_hash_256"
    st.close()


# ---------------- 维度校验 / 索引重建 / 版本管理 ----------------


def test_storage_dim_mismatch_requires_rebuild(tmp_path):
    db = tmp_path / "d.db"
    st = Storage(db, emb=_TinyProvider(8))
    st.upsert_document(dict(DOC), tenant="t1")
    st.close()
    # 换维度提供方直接打开 → fail-closed
    with pytest.raises(EmbeddingConfigMismatch):
        Storage(db, emb=_TinyProvider(4))
    # 显式重建（版本递增）后可用
    st2 = Storage(db, emb=_TinyProvider(4), rebuild_on_mismatch=True)
    v = st2.get_index_version()
    assert v["dim"] == 4 and v["version"] >= 2
    assert st2.search("三个工作日 支付", top_k=2)
    st2.close()


def test_index_version_bumped_on_rebuild(tmp_path):
    st = Storage(tmp_path / "v.db", emb=_TinyProvider(8))
    st.upsert_document(dict(DOC), tenant="t1")
    v1 = st.get_index_version()
    v2 = st.rebuild_index()
    assert v2 == v1["version"] + 1
    meta = st.get_index_version()
    assert meta["provider"] == st.emb.name and meta["dim"] == 8
    st.close()


def test_write_dim_validated(tmp_path):
    st = Storage(tmp_path / "w.db", emb=_BrokenDimProvider(8))
    with pytest.raises(EmbeddingConfigMismatch):
        st.upsert_document(dict(DOC), tenant="t1")
    st.close()


def test_query_dim_validated(tmp_path):
    st = Storage(tmp_path / "q.db", emb=_TinyProvider(8))
    st.upsert_document(dict(DOC), tenant="t1")

    class _DimDrift(_TinyProvider):
        def embed(self, texts):
            return np.ones((len(texts), 3), dtype=np.float32)

    st.emb = _DimDrift(8)  # 运行期提供方维度漂移
    with pytest.raises(EmbeddingConfigMismatch):
        st.search("违约金", top_k=2)
    st.close()
