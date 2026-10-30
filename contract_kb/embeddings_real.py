# -*- coding: utf-8 -*-
"""真实 Embedding 提供方（Phase 2）：sentence-transformers 中文模型（BGE-M3 系列）可插拔。

- LocalHashEmbedding：离线基线（既有）；
- SentenceTransformerEmbedding：真实语义向量（需安装 sentence-transformers + 授权模型）；
- get_embedding_provider(spec)：按配置选择，未安装时明确报错/降级并由调用方标注。
"""
from __future__ import annotations

import numpy as np

from .embeddings import EmbeddingProvider, LocalHashEmbedding, CloudEmbedding


class SentenceTransformerEmbedding(EmbeddingProvider):
    """本地/私有部署中文 Embedding（默认 BAAI/bge-m3，1024 维）。"""

    def __init__(self, model_name: str = "BAAI/bge-m3", batch_size: int = 16,
                 normalize: bool = True):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise RuntimeError(
                "sentence-transformers 未安装：pip install sentence-transformers "
                "（模型权重需预先下载或配置本地路径，未经授权不外发文本）") from e
        self.model = SentenceTransformer(model_name)
        self.name = f"st:{model_name}"
        self.dim = int(self.model.get_sentence_embedding_dimension())
        self.batch_size = batch_size
        self.normalize = normalize

    def embed(self, texts: list[str]) -> np.ndarray:
        vecs = self.model.encode(texts, batch_size=self.batch_size,
                                 normalize_embeddings=self.normalize,
                                 show_progress_bar=False)
        return np.asarray(vecs, dtype=np.float32)


def get_embedding_provider_by_spec(spec: str | None = None) -> EmbeddingProvider:
    """spec：local_hash | st:<model> | cloud:<provider>；None 用配置默认。"""
    from . import config
    spec = spec or config.EMBEDDING_PROVIDER
    if spec.startswith("st:"):
        return SentenceTransformerEmbedding(spec.split(":", 1)[1])
    if spec.startswith("cloud:"):
        return CloudEmbedding(spec.split(":", 1)[1])
    return LocalHashEmbedding(config.EMBEDDING_DIM)
