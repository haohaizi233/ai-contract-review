# -*- coding: utf-8 -*-
"""Embedding 抽象层：默认本地离线实现（不外发数据），云服务需显式配置与授权。"""

from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod

import numpy as np

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9]+")


def _tokenize(text: str) -> list[str]:
    """中文按字、英文数字按词；再生成双字组合增强短语信号。"""
    toks = _TOKEN_RE.findall(text.lower())
    bigrams = [toks[i] + toks[i + 1] for i in range(len(toks) - 1)]
    return toks + bigrams


class EmbeddingProvider(ABC):
    name: str = "abstract"
    dim: int = 256

    @abstractmethod
    def embed(self, texts: list[str]) -> np.ndarray:
        """返回 shape=(n, dim) 的 float32 矩阵，L2 归一化。"""


class LocalHashEmbedding(EmbeddingProvider):
    """确定性哈希嵌入：离线、可复现、零外发。适合功能验证与小语料检索。

    已知局限（如实标注）：语义质量低于深度模型，仅保证词汇级近似匹配；
    企业级语义检索应接入授权的中文 Embedding 模型（实现同接口即可替换）。
    """

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
        self._configured = False  # 需显式配置密钥与授权后置 True

    def embed(self, texts: list[str]) -> np.ndarray:
        if not self._configured:
            raise RuntimeError(
                f"云 Embedding 服务商 {self._provider} 未配置/未授权。"
                "依据数据治理要求，禁止在未授权情况下向外部服务发送合同文本。"
            )
        raise NotImplementedError("需按企业授权的云服务商 SDK 实现")


def get_embedding_provider(spec: str | None = None) -> EmbeddingProvider:
    """按配置选择 Embedding 提供方（默认工厂）。

    spec（CONTRACT_KB_EMBEDDING）：
    - local_hash / 空 → LocalHashEmbedding（离线基线）；
    - st:<model> → SentenceTransformerEmbedding（真实语义向量，如 st:BAAI/bge-m3）；
    - cloud:<provider> → CloudEmbedding（需显式授权，未授权拒绝外发）。
    """
    from . import config

    spec = (spec or config.EMBEDDING_PROVIDER).strip()
    if spec.startswith("st:"):
        from .embeddings_real import SentenceTransformerEmbedding

        return SentenceTransformerEmbedding(spec.split(":", 1)[1])
    if spec.startswith("cloud:"):
        return CloudEmbedding(spec.split(":", 1)[1])
    return LocalHashEmbedding(config.EMBEDDING_DIM)
