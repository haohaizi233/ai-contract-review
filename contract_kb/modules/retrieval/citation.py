# -*- coding: utf-8 -*-
"""原文引文核验与答案依据度评估器（CitationVerifier & Groundedness Scorer）。

提供多维度证据核验：
1. 知识库条目存在性检查；
2. 租户隔离归属检查；
3. 文档生命周期有效性检查 (必须 ACTIVE，严禁默认放行)；
4. 条款业务审批有效性检查 (权威类型必须 APPROVED，撤销立即失效)；
5. 来源哈希 (source_hash) 与版本号一致性核验；
6. 原文精准匹配度检查；
7. 审核发现 (Finding) 原文定界依据度核验。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from pydantic import BaseModel, Field

_WS_RE = re.compile(r"\s+")


def _compact(text: str) -> str:
    return _WS_RE.sub("", text)


class EvidenceVerificationResult(BaseModel):
    """强类型证据真实性与权威状态核验契约。"""

    verified: bool
    status: str  # VERIFIED, MISSING_SOURCE, MISSING_TENANT, TENANT_MISMATCH, DOCUMENT_MISMATCH, HASH_MISMATCH, VERSION_MISMATCH, APPROVAL_MISSING, APPROVAL_REVOKED, SOURCE_INACTIVE, TEXT_MISMATCH
    tenant_id: str
    document_id: str
    clause_id: str
    document_version: str | None = None
    source_hash: str | None = None
    content_hash: str | None = None
    approval_record_id: str | None = None
    failure_reasons: list[str] = Field(default_factory=list)


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
    """双重证据核验器（Strict Fail-Closed）：
    1. 校验检索返回的知识库 `clause_id` 是否真实存在于数据库、属于正确租户、属于 ACTIVE 且 APPROVED 状态且哈希一致；
    2. 校验审核发现（Finding）中的原文引述与知识引用是否真实锚定在源文档与权威知识库。
    """

    def verify_evidence_item(
        self,
        storage: Any,
        item: dict[str, Any] | Any,
        expected_tenant_id: str | None = None,
    ) -> EvidenceVerificationResult:
        """针对单条引文/分块执行深度的权威数据库核验（零默认信任，Strict Fail-Closed）。"""
        item_dict = item.to_dict() if hasattr(item, "to_dict") else dict(item)

        cid = str(item_dict.get("clause_id") or "")
        did = str(item_dict.get("document_id") or "")
        item_tenant = item_dict.get("tenant_id")
        eff_tenant = expected_tenant_id or item_tenant

        if not cid:
            return EvidenceVerificationResult(
                verified=False,
                status="MISSING_SOURCE",
                tenant_id=str(eff_tenant or ""),
                document_id=did,
                clause_id="",
                failure_reasons=["引文缺少 clause_id 标识"],
            )

        if not item_tenant:
            return EvidenceVerificationResult(
                verified=False,
                status="MISSING_TENANT",
                tenant_id="",
                document_id=did,
                clause_id=cid,
                failure_reasons=["引文自身缺少明确的租户标识 (tenant_id)，严禁缺省默认放行"],
            )

        if not eff_tenant:
            return EvidenceVerificationResult(
                verified=False,
                status="MISSING_TENANT",
                tenant_id="",
                document_id=did,
                clause_id=cid,
                failure_reasons=["缺少有效租户上下文，拒绝跨租户或无租户引用"],
            )

        if item_tenant and expected_tenant_id and str(item_tenant) != str(expected_tenant_id):
            return EvidenceVerificationResult(
                verified=False,
                status="TENANT_MISMATCH",
                tenant_id=str(item_tenant),
                document_id=did,
                clause_id=cid,
                failure_reasons=[f"租户隔离不匹配：引文声明租户={item_tenant} vs 审核限定租户={expected_tenant_id}"],
            )

        if not hasattr(storage, "get_clause"):
            return EvidenceVerificationResult(
                verified=False,
                status="STORAGE_UNAVAILABLE",
                tenant_id=str(eff_tenant),
                document_id=did,
                clause_id=cid,
                failure_reasons=["存储接口不可用，无法执行独立数据库权威回查"],
            )

        row = storage.get_clause(cid, tenant_id=eff_tenant)
        row_dict = dict(row) if row is not None else {}
        if not row_dict:
            return EvidenceVerificationResult(
                verified=False,
                status="MISSING_SOURCE",
                tenant_id=str(eff_tenant),
                document_id=did,
                clause_id=cid,
                failure_reasons=[f"条款 {cid} 在租户 {eff_tenant} 的数据库中不存在"],
            )

        # 1. 租户隔离强校验
        db_tenant = str(row_dict.get("tenant_id") or "")
        if db_tenant and str(eff_tenant) != db_tenant:
            return EvidenceVerificationResult(
                verified=False,
                status="TENANT_MISMATCH",
                tenant_id=str(eff_tenant),
                document_id=did,
                clause_id=cid,
                failure_reasons=[f"租户隔离不匹配：请求租户={eff_tenant}，数据库归属={db_tenant}"],
            )

        # 2. 文档 ID 校验
        db_did = str(row_dict.get("document_id") or "")
        if did and db_did and did != db_did:
            return EvidenceVerificationResult(
                verified=False,
                status="DOCUMENT_MISMATCH",
                tenant_id=str(eff_tenant),
                document_id=did,
                clause_id=cid,
                failure_reasons=[f"所属文档ID不匹配：请求={did}，数据库={db_did}"],
            )

        # 3. 文档生命周期校验 (必须明确为 ACTIVE，绝对不使用默认值)
        raw_doc_status = row_dict.get("doc_status")
        if not raw_doc_status or str(raw_doc_status).strip().upper() != "ACTIVE":
            return EvidenceVerificationResult(
                verified=False,
                status="SOURCE_INACTIVE",
                tenant_id=str(eff_tenant),
                document_id=db_did,
                clause_id=cid,
                failure_reasons=[f"依据文档生命周期非 ACTIVE (当前={raw_doc_status})，无法作为有效依据"],
            )

        # 4. 来源哈希 (source_hash) 校验
        db_hash = str(row_dict.get("source_hash") or "").strip()
        req_hash = str(item_dict.get("source_hash") or "").strip()
        if not db_hash:
            return EvidenceVerificationResult(
                verified=False,
                status="HASH_MISMATCH",
                tenant_id=str(eff_tenant),
                document_id=db_did,
                clause_id=cid,
                failure_reasons=["数据库中该文档未记录可信 source_hash"],
            )
        if req_hash and req_hash != db_hash:
            return EvidenceVerificationResult(
                verified=False,
                status="HASH_MISMATCH",
                tenant_id=str(eff_tenant),
                document_id=db_did,
                clause_id=cid,
                source_hash=db_hash,
                failure_reasons=[f"来源哈希不一致：引用携带={req_hash[:16]}...，数据库真实={db_hash[:16]}..."],
            )

        # 5. 文档版本一致性校验
        req_ver = str(item_dict.get("document_version") or item_dict.get("source_version") or "").strip()
        db_ver = str(row_dict.get("source_version") or "").strip()
        if req_ver and db_ver and req_ver != db_ver:
            return EvidenceVerificationResult(
                verified=False,
                status="VERSION_MISMATCH",
                tenant_id=str(eff_tenant),
                document_id=db_did,
                clause_id=cid,
                document_version=db_ver,
                failure_reasons=[f"依据版本不一致：请求={req_ver}，数据库当前={db_ver}"],
            )

        # 6. 审批状态核验（撤销立即失效，未审批不得作为权威标准）
        db_approval = str(row_dict.get("approval_status") or "").strip().upper()
        ktype = str(row_dict.get("knowledge_type") or item_dict.get("knowledge_type") or "TEMPLATE").strip().upper()

        if db_approval == "REVOKED":
            return EvidenceVerificationResult(
                verified=False,
                status="APPROVAL_REVOKED",
                tenant_id=str(eff_tenant),
                document_id=db_did,
                clause_id=cid,
                failure_reasons=["该条款审批已被法务撤销 (REVOKED)，丧失依据效力"],
            )

        if ktype in ("TEMPLATE", "APPROVED_RULE") and db_approval != "APPROVED":
            return EvidenceVerificationResult(
                verified=False,
                status="APPROVAL_MISSING",
                tenant_id=str(eff_tenant),
                document_id=db_did,
                clause_id=cid,
                failure_reasons=[f"权威标准未经正式审批授权 (当前状态={db_approval or 'NONE'})"],
            )

        # 7. 原文一致性核验
        db_text = str(row_dict.get("original_text") or "").strip()
        hit_text = str(item_dict.get("text") or item_dict.get("original_text") or "").strip()
        if not db_text:
            return EvidenceVerificationResult(
                verified=False,
                status="TEXT_MISMATCH",
                tenant_id=str(eff_tenant),
                document_id=db_did,
                clause_id=cid,
                failure_reasons=["数据库条款原文为空"],
            )
        if hit_text:
            norm_hit = _compact(hit_text)
            norm_db = _compact(db_text)
            if not (norm_hit == norm_db or norm_hit in norm_db or norm_db in norm_hit):
                return EvidenceVerificationResult(
                    verified=False,
                    status="TEXT_MISMATCH",
                    tenant_id=str(eff_tenant),
                    document_id=db_did,
                    clause_id=cid,
                    failure_reasons=["引文字符串与数据库原文精准核对失败"],
                )

        # 全部维度核验通过
        return EvidenceVerificationResult(
            verified=True,
            status="VERIFIED",
            tenant_id=str(eff_tenant),
            document_id=db_did,
            clause_id=cid,
            document_version=db_ver,
            source_hash=db_hash,
            failure_reasons=[],
        )

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
            res = self.verify_evidence_item(storage, h, expected_tenant_id=expected_tenant_id)
            if res.verified:
                verified += 1
                grounded_scores.append(1.0)
            else:
                cid = str(h.get("clause_id") or h.get("chunk_id") or "unverified")
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
            quote = _compact(
                str(ref.get("original_text") or ref.get("exact_quote") or "")
            )
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
