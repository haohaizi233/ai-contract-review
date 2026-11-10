# -*- coding: utf-8 -*-
"""Reranker 提供方实现与工厂（Infrastructure AI Adapter）。"""

from __future__ import annotations

import re

from ...application.ports.reranker import Reranker, RerankerPort
from ...modules.retrieval.reranker import (
    HttpCrossEncoderReranker,
    LexicalCoverageReranker,
)

__all__ = [
    "CrossEncoderReranker",
    "HttpCrossEncoderReranker",
    "LexicalCoverageReranker",
    "LexicalReranker",
    "Reranker",
    "RerankerPort",
    "get_reranker",
]

_CLEAN_RE = re.compile(r"[\s\u3000，。；：、“”‘’（）()《》\-—_/%‰]")


class LexicalReranker(Reranker):
    """确定性词汇 reranker（离线基线）：字符 2-gram 覆盖 + 数值匹配加权。"""

    name = "lexical_2gram_baseline"

    def rerank(self, query: str, hits: list[dict]) -> list[dict]:
        qn = _CLEAN_RE.sub("", query).lower()
        qgrams = {qn[i : i + 2] for i in range(len(qn) - 1)} or {qn}
        qnums = set(re.findall(r"\d+(?:\.\d+)?", query))
        scored = []
        for h in hits:
            tn = _CLEAN_RE.sub("", h["text"]).lower()
            tgrams = {tn[i : i + 2] for i in range(len(tn) - 1)} or {tn}
            cov = len(qgrams & tgrams) / max(1, len(qgrams))
            tnums = set(re.findall(r"\d+(?:\.\d+)?", h["text"]))
            item = dict(h)
            item["rerank_score"] = cov + 0.15 * len(qnums & tnums)
            scored.append(item)
        return sorted(scored, key=lambda x: -x["rerank_score"])


class CrossEncoderReranker(Reranker):
    """Cross-Encoder 重排（需授权模型，如 bge-reranker-v2-m3）。

    未安装 sentence-transformers 或未配置模型时构造失败，调用方回退 baseline。
    """

    def __init__(
        self, model_name: str = "BAAI/bge-reranker-v2-m3", max_length: int = 512
    ):
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as e:
            raise RuntimeError(
                "sentence-transformers 未安装；Cross-Encoder reranker 不可用，"
                "请 pip install sentence-transformers 并配置授权模型"
            ) from e
        self.model = CrossEncoder(model_name, max_length=max_length)
        self.name = f"cross_encoder:{model_name}"

    def rerank(self, query: str, hits: list[dict]) -> list[dict]:
        if not hits:
            return hits
        pairs = [(query, h["text"]) for h in hits]
        scores = self.model.predict(pairs)
        scored = [dict(h, rerank_score=float(s)) for h, s in zip(hits, scores)]
        return sorted(scored, key=lambda x: -x["rerank_score"])


def get_reranker(name: str = "lexical") -> Reranker:
    if name.startswith("cross_encoder"):
        model = name.split(":", 1)[1] if ":" in name else "BAAI/bge-reranker-v2-m3"
        try:
            return CrossEncoderReranker(model)
        except RuntimeError:
            return LexicalReranker()  # 降级并由调用方报告
    return LexicalReranker()
