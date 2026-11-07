# -*- coding: utf-8 -*-
"""真实 Embedding 提供方（Phase 2）：sentence-transformers 中文模型（BGE-M3 系列）可插拔。"""

from __future__ import annotations

import os

import numpy as np

from . import config
from .embeddings_base import CloudEmbedding, EmbeddingProvider, LocalHashEmbedding


class SentenceTransformerEmbedding(EmbeddingProvider):
    """本地/私有部署中文 Embedding（默认 BAAI/bge-m3，1024 维）。"""

    def __init__(
        self,
        model_name: str = "BAAI/bge-m3",
        batch_size: int = 16,
        normalize: bool = True,
    ):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise RuntimeError(
                "sentence-transformers 未安装：pip install sentence-transformers "
                "（模型权重需预先下载或配置本地路径，未经授权不外发文本）"
            ) from e
        self.model = SentenceTransformer(model_name)
        self.name = f"st:{model_name}"
        self.dim = int(self.model.get_sentence_embedding_dimension())
        self.batch_size = batch_size
        self.normalize = normalize

    def embed(self, texts: list[str]) -> np.ndarray:
        vecs = self.model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=self.normalize,
            show_progress_bar=False,
        )
        return np.asarray(vecs, dtype=np.float32)


def get_embedding_provider_by_spec(spec: str | None = None) -> EmbeddingProvider:
    """spec：local_hash | st:<model> | cloud:<provider>；None 用配置默认。"""
    resolved = (
        spec
        if spec is not None
        else os.environ.get("CONTRACT_KB_EMBEDDING", config.EMBEDDING_PROVIDER)
    ).strip()
    if resolved.startswith("st:"):
        return SentenceTransformerEmbedding(resolved.split(":", 1)[1])
    if resolved.startswith("cloud:"):
        allow_ext = (
            os.environ.get("CONTRACT_KB_ALLOW_EXTERNAL_LLM", "0") == "1"
            or config.ALLOW_EXTERNAL_LLM
        )
        if not allow_ext:
            raise RuntimeError(
                f"云 Embedding 服务商 {resolved} 未获外发授权（需显式设置 CONTRACT_KB_ALLOW_EXTERNAL_LLM=1）。"
            )
        return CloudEmbedding(resolved.split(":", 1)[1])
    return LocalHashEmbedding(config.EMBEDDING_DIM)
