# -*- coding: utf-8 -*-
"""Reranker 抽象端口契约（Application Port）。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ...modules.retrieval.reranker import RerankerPort


class Reranker(ABC):
    name = "abstract"

    @abstractmethod
    def rerank(self, query: str, hits: list[dict]) -> list[dict]:
        """输入命中列表（含 text），返回按相关性降序的新列表（不改原列表顺序以外的字段）。"""


__all__ = ["Reranker", "RerankerPort"]
