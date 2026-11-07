# -*- coding: utf-8 -*-
"""混合检索 RRF 倒数排名融合与可插拔重排序适配器（RerankerPort）。"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse

_CLEAN_RE = re.compile(r"[\s\u3000，。；：、“”‘’（）()《》\-—_/%‰]")


def _norm_text(t: str) -> str:
    return _CLEAN_RE.sub("", t).lower()


def reciprocal_rank_fusion(
    channel_lists: list[list[dict[str, Any]]],
    *,
    id_key: str = "chunk_id",
    rrf_k: int = 60,
) -> list[dict[str, Any]]:
    """标准 Reciprocal Rank Fusion (RRF)：对多路召回结果进行无量纲排名融合。

    公式：score(d) = sum_{c in channels} 1 / (rrf_k + rank_c(d))
    """
    scores: dict[str, float] = {}
    merged: dict[str, dict[str, Any]] = {}
    channels_by_id: dict[str, list[str]] = {}

    for hits in channel_lists:
        for rank, item in enumerate(hits, start=1):
            cid = str(item.get(id_key) or item.get("clause_id") or "")
            if not cid:
                continue
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (rrf_k + rank)
            if cid not in merged:
                merged[cid] = dict(item)
            ch = str(item.get("channel") or "unknown")
            ch_list = channels_by_id.setdefault(cid, [])
            if ch not in ch_list:
                ch_list.append(ch)

    fused: list[dict[str, Any]] = []
    for cid, score in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0])):
        row = dict(merged[cid])
        row["rrf_score"] = round(score, 6)
        row["channels"] = channels_by_id.get(cid, [])
        fused.append(row)
    return fused


@runtime_checkable
class RerankerPort(Protocol):
    """重排序器统一端口协议（支持本地词法特征覆盖与授权 Cross-Encoder 模型切换）。"""

    name: str

    def rerank(
        self, query: str, candidates: list[dict[str, Any]], top_k: int | None = None
    ) -> list[dict[str, Any]]: ...


class LexicalCoverageReranker:
    """确定性双字符覆盖率 + 数值锚点 + 权威等级加权重排序器（离线零外发）。"""

    name = "lexical_2gram_authority_v1"

    def rerank(
        self, query: str, candidates: list[dict[str, Any]], top_k: int | None = None
    ) -> list[dict[str, Any]]:
        if not candidates:
            return []
        qn = _norm_text(query)
        qgrams = {qn[i : i + 2] for i in range(len(qn) - 1)} if len(qn) >= 2 else {qn}
        qnums = set(re.findall(r"\d+(?:\.\d+)?", query))

        scored: list[dict[str, Any]] = []
        for cand in candidates:
            item = dict(cand)
            text = str(item.get("text") or item.get("original_text") or "")
            tn = _norm_text(text)
            tgrams = (
                {tn[i : i + 2] for i in range(len(tn) - 1)} if len(tn) >= 2 else {tn}
            )
            cov = len(qgrams & tgrams) / max(1, len(qgrams))
            tnums = set(re.findall(r"\d+(?:\.\d+)?", text))
            num_bonus = 0.15 * len(qnums & tnums)
            rrf_bonus = float(item.get("rrf_score") or 0.0) * 2.0
            auth_rank = int(item.get("authority_rank") or 2)
            auth_bonus = 0.05 if auth_rank <= 2 else 0.0
            item["rerank_score"] = round(cov + num_bonus + rrf_bonus + auth_bonus, 6)
            scored.append(item)

        scored.sort(
            key=lambda x: (
                -float(x.get("rerank_score", 0.0)),
                int(x.get("authority_rank", 9)),
            )
        )
        return scored[:top_k] if top_k is not None else scored


class HttpCrossEncoderReranker:
    """受控 HTTP Cross-Encoder 重排序适配器（受 ALLOW_EXTERNAL_LLM 出境门禁约束）。"""

    name = "http_cross_encoder"

    def __init__(
        self,
        endpoint_url: str,
        api_key: str = "",
        model: str = "bge-reranker-v2-m3",
        timeout_seconds: float = 10.0,
        fallback: RerankerPort | None = None,
    ):
        parsed = urlparse(endpoint_url)
        host = (parsed.hostname or "").lower()
        is_local = host in ("127.0.0.1", "localhost", "::1")
        allow_ext = os.environ.get("ALLOW_EXTERNAL_LLM", "0").strip() in (
            "1",
            "true",
            "yes",
        )
        if not is_local and not allow_ext:
            raise PermissionError(
                "未开启 ALLOW_EXTERNAL_LLM=1，禁止将合同检索片段发送至非本机 Reranker 端点"
            )
        self.endpoint_url = endpoint_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.fallback = fallback or LexicalCoverageReranker()

    def rerank(
        self, query: str, candidates: list[dict[str, Any]], top_k: int | None = None
    ) -> list[dict[str, Any]]:
        if not candidates:
            return []
        import urllib.request

        docs = [str(c.get("text") or c.get("original_text") or "") for c in candidates]
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            self.endpoint_url,
            data=json.dumps(
                {"model": self.model, "query": query, "documents": docs}
            ).encode("utf-8"),
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            results = payload.get("results") or []
            score_map = {
                int(r["index"]): float(r["relevance_score"])
                for r in results
                if "index" in r and "relevance_score" in r
            }
            ranked = []
            for idx, cand in enumerate(candidates):
                item = dict(cand)
                item["rerank_score"] = round(score_map.get(idx, 0.0), 6)
                ranked.append(item)
            ranked.sort(key=lambda x: -float(x.get("rerank_score", 0.0)))
            return ranked[:top_k] if top_k is not None else ranked
        except Exception:
            return self.fallback.rerank(query, candidates, top_k=top_k)
