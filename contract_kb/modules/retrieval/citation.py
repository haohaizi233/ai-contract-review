# -*- coding: utf-8 -*-
"""原文引文核验与答案依据度评估器（CitationVerifier & Groundedness Scorer）。

提供多维度证据核验：
1. 知识库条目存在性检查；
2. 租户隔离归属检查；
3. 文档生命周期有效性检查 (必须 ACTIVE)；
4. 条款业务审批有效性检查 (权威类型必须 APPROVED)；
5. 原文精准匹配度检查；
6. 审核发现 (Finding) 原文定界依据度核验。
"""

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

    @property
    def checked_count(self) -> int:
        return self.total_checked


class CitationVerifier:
    """双重证据核验器：
    1. 校验检索返回的知识库 `clause_id` 是否真实存在于数据库、属于正确租户、属于 ACTIVE 且 APPROVED 状态且原文非空；
    2. 校验审核发现（Finding）中的原文引述与知识引用是否真实锚定在源文档与知识库。
    """

    def verify_retrieval_hits(
        self,
        storage: Any,
        hits: list[dict[str, Any]],
        expected_tenant_id: str | None = None,
    ) -> CitationVerificationSummary:
        if not hits:
            return CitationVerificationSummary(
                total_checked=0,
                verified_count=0,
                citation_correctness=0.0,
                answer_groundedness=0.0,
                unverified_ids=[],
            )
        verified = 0
        grounded_scores: list[float] = []
        unverified_ids: list[str] = []

        for h in hits:
            cid = str(h.get("clause_id") or "")
            if not cid:
                unverified_ids.append(str(h.get("chunk_id") or "missing_clause_id"))
                grounded_scores.append(0.0)
                continue

            eff_tenant = expected_tenant_id or h.get("tenant_id")
            row = (
                storage.get_clause(cid, tenant_id=eff_tenant)
                if hasattr(storage, "get_clause")
                else None
            )
            row_dict = dict(row) if row is not None else {}
            if not row_dict:
                unverified_ids.append(cid)
                grounded_scores.append(0.0)
                continue

            # 1. 租户边界检查
            if eff_tenant is not None and row_dict.get("tenant_id") is not None:
                if str(row_dict.get("tenant_id")) != str(eff_tenant):
                    unverified_ids.append(cid)
                    grounded_scores.append(0.0)
                    continue

            # 2. 文档状态检查 (必须为 ACTIVE)
            doc_status = str(row_dict.get("doc_status") or "ACTIVE").upper()
            if doc_status != "ACTIVE":
                unverified_ids.append(cid)
                grounded_scores.append(0.0)
                continue

            # 3. 审批状态核验：若为 TEMPLATE 或 APPROVED_RULE，数据库真实状态必须为 APPROVED
            ktype = str(
                row_dict.get("knowledge_type") or h.get("knowledge_type") or "TEMPLATE"
            ).upper()
            db_approval = str(
                row_dict.get("approval_status") or row_dict.get("approved_status") or ""
            ).upper()
            if ktype in ("TEMPLATE", "APPROVED_RULE") and db_approval != "APPROVED":
                unverified_ids.append(cid)
                grounded_scores.append(0.0)
                continue

            # 4. 原文一致性核验：原文必须非空且存在真实匹配
            db_text = str(row_dict.get("original_text") or "").strip()
            hit_text = str(h.get("text") or h.get("original_text") or "").strip()
            if not db_text or not hit_text:
                unverified_ids.append(cid)
                grounded_scores.append(0.0)
                continue

            norm_hit = _compact(hit_text)
            norm_db = _compact(db_text)
            if norm_hit == norm_db or norm_hit in norm_db or norm_db in norm_hit:
                verified += 1
                grounded_scores.append(1.0)
            else:
                unverified_ids.append(cid)
                grounded_scores.append(0.0)

        total = len(hits)
        return CitationVerificationSummary(
            total_checked=total,
            verified_count=verified,
            citation_correctness=round(verified / total, 4) if total > 0 else 0.0,
            answer_groundedness=(
                round(sum(grounded_scores) / total, 4) if total > 0 else 0.0
            ),
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
            quote = _compact(str(ref.get("original_text") or ref.get("exact_quote") or ""))
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
            citation_correctness=round(verified / total, 4) if total > 0 else 0.0,
            answer_groundedness=(
                round(sum(grounded_scores) / total, 4) if total > 0 else 0.0
            ),
            unverified_ids=unverified,
        )
