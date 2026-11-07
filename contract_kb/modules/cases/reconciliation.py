# -*- coding: utf-8 -*-
"""历史合同案卷治理、跨源对账与去重合流纯领域逻辑（Case Reconciliation & Safe Cache）。

核心职责：
- 将原 `tools/live_db_merge.py`、`tools/cross_verify_and_merge.py`、`tools/contract_merge_repair.py`
  中的合同编号归一、年份范围判定、附件摘要去重互补、e9×tender 双向交叉对账抽离为无数据库直连副作用的纯函数；
- 提供带 SHA-256 校验摘要的安全 JSON 缓存（`SafeJsonCache`），替代存在反序列化代码执行风险（CWE-502）的 `pickle`；
- 原生支持 `dry_run=True` 预演模式，在不写入目标库或覆盖物理文件的前提下输出完整对账差异报告。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ...domain.models.canonical_contract import (
    CanonicalContract,
    ContractAmountSummary,
    ContractLifecycleStatus,
    ContractNumber,
    ContractParty,
    SourceRecordMapping,
    SourceSystemType,
)

_SEP_RE = re.compile(r"[,，;；/、\s]+")
_YEAR_RE = re.compile(r"(202[0-9]|201[0-9])")


def normalize_contract_no(raw: str | None) -> str:
    """合同编号规范化：去除全半角空白并统一大写。"""
    return re.sub(r"\s+", "", (raw or "").strip()).upper()


def split_composite_contract_keys(raw: str | None) -> list[str]:
    """拆分复合合同编号（如 `HT001、HT002`）为去重有序键列表。"""
    normed = normalize_contract_no(raw)
    if not normed:
        return []
    parts = [p for p in _SEP_RE.split(normed) if len(p) >= 4]
    return list(dict.fromkeys(parts or [normed]))


def is_contract_in_year_scope(
    contract_no: str | None,
    *,
    date_str: str | None = None,
    min_year: int = 2024,
    max_year: int = 2099,
) -> bool:
    """纯函数判定合同记录是否处于目标治理年份区间（默认 2024+）。"""
    for candidate in (date_str or "", contract_no or ""):
        m = _YEAR_RE.search(str(candidate))
        if m:
            yr = int(m.group(1))
            return min_year <= yr <= max_year
    return False


@dataclass(frozen=True)
class AttachmentEntity:
    """标准化附件元数据（按内容 SHA-256 去重互补）。"""

    file_name: str
    sha256: str
    size_bytes: int
    source_system: str
    source_locator: str


def deduplicate_attachments_by_digest(
    e9_attachments: list[AttachmentEntity],
    tender_attachments: list[AttachmentEntity],
) -> tuple[list[AttachmentEntity], int]:
    """按文件内容 SHA-256 合并两库附件：相同内容只留一份（优先保留新系统命名），不同内容互补保留。

    返回：(去重互补后的附件列表, 消除的重复文件数)
    """
    by_digest: dict[str, AttachmentEntity] = {}
    duplicates_removed = 0
    # 先装入 tender 新系统附件，再用 e9 附件补漏
    for item in list(tender_attachments) + list(e9_attachments):
        if not item.sha256:
            continue
        if item.sha256 in by_digest:
            duplicates_removed += 1
            continue
        by_digest[item.sha256] = item
    return list(by_digest.values()), duplicates_removed


@dataclass
class ReconciliationDiffReport:
    """泛微 e9 与新业务系统 tender 全量/增量交叉对账报告。"""

    dry_run: bool
    total_unique_contracts: int
    matched_both_count: int
    only_in_e9: list[str] = field(default_factory=list)
    only_in_tender: list[str] = field(default_factory=list)
    amount_conflicts: list[dict[str, Any]] = field(default_factory=list)
    party_conflicts: list[dict[str, Any]] = field(default_factory=list)
    deduplicated_attachment_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _index_by_contract_no(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for r in records:
        for k in split_composite_contract_keys(r.get("contract_no")):
            indexed.setdefault(k, r)
    return indexed


def _detect_pair_conflicts(
    cno: str,
    r_e9: dict[str, Any],
    r_td: dict[str, Any],
    amt_conflicts: list[dict[str, Any]],
    party_conflicts: list[dict[str, Any]],
) -> None:
    a_e9 = ContractAmountSummary.from_values(total=r_e9.get("total_amount"))
    a_td = ContractAmountSummary.from_values(total=r_td.get("total_amount"))
    if (
        a_e9.total_amount is not None
        and a_td.total_amount is not None
        and a_e9.total_amount != a_td.total_amount
    ):
        amt_conflicts.append(
            {
                "contract_no": cno,
                "e9_amount": str(a_e9.total_amount),
                "tender_amount": str(a_td.total_amount),
            }
        )
    p_e9 = str(r_e9.get("party_a") or "").strip()
    p_td = str(r_td.get("party_a") or "").strip()
    if p_e9 and p_td and p_e9 != p_td:
        party_conflicts.append(
            {"contract_no": cno, "e9_party_a": p_e9, "tender_party_a": p_td}
        )


def _first_non_empty(*vals: Any) -> str:
    for v in vals:
        if v:
            return str(v)
    return ""


def _build_canonical_from_sources(
    cno: str,
    tenant_id: str,
    r_e9: dict[str, Any] | None,
    r_td: dict[str, Any] | None,
) -> CanonicalContract:
    mappings: list[SourceRecordMapping] = []
    if r_td is not None:
        ext_td = _first_non_empty(r_td.get("id"), r_td.get("apply_id"), cno)
        mappings.append(
            SourceRecordMapping(
                source_system=SourceSystemType.TENDER_NEW.value,
                external_id=ext_td,
                external_code=cno,
            )
        )
    if r_e9 is not None:
        ext_e9 = _first_non_empty(r_e9.get("requestid"), cno)
        mappings.append(
            SourceRecordMapping(
                source_system=SourceSystemType.WEAVER_E9.value,
                external_id=ext_e9,
                external_code=cno,
            )
        )

    primary = r_td if r_td is not None else (r_e9 or {})
    secondary = (r_e9 or {}) if r_td is not None else {}
    title = _first_non_empty(primary.get("title"), secondary.get("title"), cno)
    party_a = _first_non_empty(primary.get("party_a"), secondary.get("party_a"))
    party_b = _first_non_empty(primary.get("party_b"), secondary.get("party_b"))
    total_raw = primary.get("total_amount")
    if total_raw is None:
        total_raw = secondary.get("total_amount")

    parties = [
        ContractParty(role=role, name=name)
        for role, name in (("PARTY_A", party_a), ("PARTY_B", party_b))
        if name
    ]
    src_sys = (
        SourceSystemType.TENDER_NEW.value
        if r_td is not None
        else SourceSystemType.WEAVER_E9.value
    )
    cid_digest = hashlib.sha256(f"{tenant_id}:{cno}".encode("utf-8")).hexdigest()[:14]
    return CanonicalContract(
        contract_id=f"cc_{cid_digest}",
        tenant_id=tenant_id,
        contract_number=ContractNumber.parse(cno),
        title=title,
        business_category=str(primary.get("business_category") or ""),
        contract_subtype=str(primary.get("contract_subtype") or ""),
        lifecycle_status=ContractLifecycleStatus.APPROVED.value,
        source_system=src_sys,
        parties=parties,
        amounts=ContractAmountSummary.from_values(total=total_raw),
        source_mappings=mappings,
    )


def reconcile_source_records(
    e9_records: list[dict[str, Any]],
    tender_records: list[dict[str, Any]],
    *,
    tenant_id: str = "default",
    dry_run: bool = True,
) -> tuple[list[CanonicalContract], ReconciliationDiffReport]:
    """纯函数对账合流：将 e9 与 tender 源记录归一化为 CanonicalContract 列表并输出差异报告。"""
    e9_by_no = _index_by_contract_no(e9_records)
    tender_by_no = _index_by_contract_no(tender_records)
    all_keys = sorted(set(e9_by_no) | set(tender_by_no))

    only_e9: list[str] = []
    only_tender: list[str] = []
    matched = 0
    amt_conflicts: list[dict[str, Any]] = []
    party_conflicts: list[dict[str, Any]] = []
    canonical_list: list[CanonicalContract] = []

    for cno in all_keys:
        r_e9 = e9_by_no.get(cno)
        r_td = tender_by_no.get(cno)
        if r_e9 is not None and r_td is not None:
            matched += 1
            _detect_pair_conflicts(cno, r_e9, r_td, amt_conflicts, party_conflicts)
        elif r_e9 is not None:
            only_e9.append(cno)
        else:
            only_tender.append(cno)

        canonical_list.append(_build_canonical_from_sources(cno, tenant_id, r_e9, r_td))

    report = ReconciliationDiffReport(
        dry_run=dry_run,
        total_unique_contracts=len(all_keys),
        matched_both_count=matched,
        only_in_e9=only_e9,
        only_in_tender=only_tender,
        amount_conflicts=amt_conflicts,
        party_conflicts=party_conflicts,
    )
    return canonical_list, report


class SafeJsonCache:
    """带完整性 SHA-256 校验的 JSON 缓存存取器（替代高危 pickle.load）。"""

    @staticmethod
    def save(path: str | Path, data: dict[str, Any]) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(data, ensure_ascii=False, sort_keys=True, default=list)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        envelope = {"version": 1, "sha256": digest, "payload": json.loads(body)}
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)

    @staticmethod
    def load(path: str | Path) -> dict[str, Any] | None:
        target = Path(path)
        if not target.exists():
            return None
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or "payload" not in raw or "sha256" not in raw:
            raise ValueError(f"缓存文件格式非法：{target}")
        canonical = json.dumps(
            raw["payload"], ensure_ascii=False, sort_keys=True, default=list
        )
        actual_sha = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if actual_sha != raw["sha256"]:
            raise ValueError(f"缓存文件完整性校验失败（疑似被篡改）：{target}")
        return dict(raw["payload"])
