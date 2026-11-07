# -*- coding: utf-8 -*-
"""原文引文核验与答案依据度评估器（CitationVerifier & Groundedness Scorer）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_WS_RE = re.compile(r"\s+")


def _compact(text: str) -> str:
    return _WS_RE.sub("", text)


@dataclass(frozen=True)
class CitationVerificationSummary:
    total_checked: int
    verified_count: int
    citation_correctness: float
    answer_groundedness: float
    unverified_ids: list[str]


class CitationVerifier:
    """双重证据核验器：
    1. 校验检索返回的知识库 `clause_id` 是否真实存在于数据库且原文非空；
    2. 校验审核发现（Finding）中的原文引述与知识引用是否真实锚定在源文档与知识库。
    """

    def verify_retrieval_hits(
        self, storage: Any, hits: list[dict[str, Any]]
    ) -> CitationVerificationSummary:
        if not hits:
            return CitationVerificationSummary(
                total_checked=0,
                verified_count=0,
                citation_correctness=1.0,
                answer_groundedness=1.0,
                unverified_ids=[],
            )
        verified = 0
        grounded_scores: list[float] = []
        unverified_ids: list[str] = []

        for h in hits:
            cid = str(h.get("clause_id") or "")
            row = storage.get_clause(cid) if cid else None
            row_dict = dict(row) if row is not None else {}
            db_text = str(row_dict.get("original_text") or "").strip()
            hit_text = str(h.get("text") or h.get("original_text") or "").strip()
            if row is not None and db_text:
                verified += 1
                if (
                    not hit_text
                    or _compact(hit_text) in _compact(db_text)
                    or _compact(db_text) in _compact(hit_text)
                ):
                    grounded_scores.append(1.0)
                else:
                    grounded_scores.append(0.5)
            else:
                unverified_ids.append(cid or str(h.get("chunk_id") or "unknown"))
                grounded_scores.append(0.0)

        total = len(hits)
        return CitationVerificationSummary(
            total_checked=total,
            verified_count=verified,
            citation_correctness=round(verified / total, 4),
            answer_groundedness=round(sum(grounded_scores) / total, 4),
            unverified_ids=unverified_ids,
        )

    def verify_finding_grounding(
        self,
        issues: list[dict[str, Any]],
        contract_clauses: list[dict[str, Any]],
    ) -> CitationVerificationSummary:
        """评估审核问题列表的原文定界依据度（AnswerGroundedness）。"""
        substantive = [
            i
            for i in issues
            if i.get("category") not in ("NOT_CHECKED", "RULE_PENDING_APPROVAL")
            and i.get("source_reference") is not None
        ]
        if not substantive:
            return CitationVerificationSummary(
                total_checked=0,
                verified_count=0,
                citation_correctness=1.0,
                answer_groundedness=1.0,
                unverified_ids=[],
            )

        corpus_compact = _compact(
            "\n".join(str(c.get("original_text") or "") for c in contract_clauses)
        )
        valid_nodes: set[str] = set()
        for c in contract_clauses:
            valid_nodes.update(c.get("source_node_ids") or [])

        verified = 0
        grounded_scores: list[float] = []
        unverified: list[str] = []

        for item in substantive:
            ref = item.get("source_reference") or {}
            quote = _compact(str(ref.get("original_text") or ""))
            node_ids = set(ref.get("node_ids") or [])
            node_ok = bool(node_ids and (node_ids & valid_nodes))
            quote_ok = bool(quote and quote in corpus_compact)

            if node_ok and quote_ok:
                verified += 1
                grounded_scores.append(1.0)
            elif node_ok or quote_ok:
                verified += 1
                grounded_scores.append(0.75)
            else:
                unverified.append(str(item.get("issue_id") or ""))
                grounded_scores.append(0.0)

        total = len(substantive)
        return CitationVerificationSummary(
            total_checked=total,
            verified_count=verified,
            citation_correctness=round(verified / total, 4),
            answer_groundedness=round(sum(grounded_scores) / total, 4),
            unverified_ids=unverified,
        )
