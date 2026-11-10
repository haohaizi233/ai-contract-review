# -*- coding: utf-8 -*-
"""Embedding 抽象端口契约与分词辅助（Application Port）。"""

from __future__ import annotations

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
    """统一的向量化接口契约。"""

    name: str = "abstract"
    dim: int = 256

    @abstractmethod
    def embed(self, texts: list[str]) -> np.ndarray:
        """返回 shape=(n, dim) 的 float32 矩阵，L2 归一化。"""


__all__ = ["EmbeddingProvider", "_tokenize"]
