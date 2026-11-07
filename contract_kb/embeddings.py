# -*- coding: utf-8 -*-
"""Embedding 抽象层门面：默认本地离线实现（不外发数据），云服务需显式配置与授权。"""

from __future__ import annotations

from .embeddings_base import (
    CloudEmbedding,
    EmbeddingProvider,
    LocalHashEmbedding,
    _tokenize,
)

__all__ = [
    "CloudEmbedding",
    "EmbeddingProvider",
    "LocalHashEmbedding",
    "_tokenize",
    "get_embedding_provider",
]


def get_embedding_provider(spec: str | None = None) -> EmbeddingProvider:
    """按配置选择 Embedding 提供方（默认工厂）。"""
    from . import config
    from .embeddings_real import get_embedding_provider_by_spec

    resolved = (spec or config.EMBEDDING_PROVIDER).strip()
    return get_embedding_provider_by_spec(resolved)
