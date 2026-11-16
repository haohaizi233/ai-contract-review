# -*- coding: utf-8 -*-
"""Embedding 提供方实现与工厂（Infrastructure AI Adapter）。

支持：
- LocalHashEmbedding：离线确定性哈希嵌入（默认）；
- SentenceTransformerEmbedding：sentence-transformers 中文模型（BGE-M3 系列）；
- CloudEmbedding：企业授权云 Embedding 适配位（受 ALLOW_EXTERNAL_LLM 门禁保护）。
"""

from __future__ import annotations

import hashlib
import os

import numpy as np

from ... import config
from ...application.ports.embedding import EmbeddingProvider, _tokenize

__all__ = [
    "CloudEmbedding",
    "EmbeddingProvider",
    "LocalHashEmbedding",
    "SentenceTransformerEmbedding",
    "_tokenize",
    "get_embedding_provider",
    "get_embedding_provider_by_spec",
]


class LocalHashEmbedding(EmbeddingProvider):
    """确定性哈希嵌入：离线、可复现、零外发。适合功能验证与小语料检索。"""

    def __init__(self, dim: int = 256):
        self.dim = dim
        self.name = f"local_hash_{dim}"

    def embed(self, texts: list[str]) -> np.ndarray:
        mat = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            for tok in _tokenize(text):
                h = int.from_bytes(hashlib.md5(tok.encode()).digest()[:8], "big")
                idx = h % self.dim
                sign = 1.0 if (h >> 63) & 1 else -1.0
                mat[i, idx] += sign
            norm = np.linalg.norm(mat[i])
            if norm > 0:
                mat[i] /= norm
        return mat


class CloudEmbedding(EmbeddingProvider):
    """企业授权云 Embedding 适配位。未配置时调用即抛错，防止未授权外发。"""

    def __init__(self, provider: str, dim: int = 1024):
        self.name = f"cloud:{provider}"
        self.dim = dim
        self._provider = provider
        self._configured = False

    def embed(self, texts: list[str]) -> np.ndarray:
        if not self._configured:
            raise RuntimeError(
                f"云 Embedding 服务商 {self._provider} 未配置/未授权。"
                "依据数据治理要求，禁止在未授权情况下向外部服务发送合同文本。"
            )
        raise NotImplementedError("需按企业授权的云服务商 SDK 实现")


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
            os.environ.get("CONTRACT_KB_ALLOW_EXTERNAL_LLM", "0").strip().lower()
            in ("1", "true", "yes")
            or config.ALLOW_EXTERNAL_LLM
        )
        if not allow_ext:
            raise RuntimeError(
                f"云 Embedding 服务商 {resolved} 未获外发授权（需显式设置 CONTRACT_KB_ALLOW_EXTERNAL_LLM=1）。"
            )
        return CloudEmbedding(resolved.split(":", 1)[1])
    return LocalHashEmbedding(config.EMBEDDING_DIM)


def get_embedding_provider(spec: str | None = None) -> EmbeddingProvider:
    """按配置选择 Embedding 提供方（默认工厂）。"""
    resolved = (spec or config.EMBEDDING_PROVIDER).strip()
    return get_embedding_provider_by_spec(resolved)
