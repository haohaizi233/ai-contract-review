# -*- coding: utf-8 -*-
"""企业标准合同领域模型（Canonical Contract Model）。

核心原则：
- 隔离外部异构源系统表结构（泛微 e9 `formtable_main_*`、新系统 `crm_contract`、企微微盘 `fileid`）；
- 外部系统主键与字段映射统一封装在 `SourceRecordMapping` 中，不污染核心合同实体；
- 金额字段强制采用 `Decimal` 精确十进制，严禁浮点数；
- 合同编号值对象支持签约主体编码前缀提取与回填一致性校验。
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any

from ..value_objects.amounts import parse_decimal


class ContractLifecycleStatus(str, Enum):
    DRAFT = "DRAFT"
    UNDER_REVIEW = "UNDER_REVIEW"
    PENDING_MODIFICATION = "PENDING_MODIFICATION"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"


class SourceSystemType(str, Enum):
    UPLOAD = "UPLOAD"
    TEMPLATE_KB = "TEMPLATE_KB"
    WEAVER_E9 = "WEAVER_E9"
    TENDER_NEW = "TENDER_NEW"
    WECOM_WEDRIVE = "WECOM_WEDRIVE"


_CONTRACT_NO_PATTERN = re.compile(r"^([A-Za-z]{2,8})[-_]?(\d{4,8})[-_]?([A-Za-z0-9]+)$")


@dataclass(frozen=True)
class ContractNumber:
    """合同编号值对象：解析签约主体前缀、日期段与流水号。"""

    raw: str
    entity_prefix: str = ""
    date_segment: str = ""
    sequence_segment: str = ""

    @classmethod
    def parse(cls, value: str | None) -> "ContractNumber | None":
        if not value:
            return None
        cleaned = value.strip()
        if not cleaned or set(cleaned) <= {"_", "＿", "-", " "}:
            return None
        m = _CONTRACT_NO_PATTERN.match(cleaned)
        if m:
            return cls(
                raw=cleaned,
                entity_prefix=m.group(1).upper(),
                date_segment=m.group(2),
                sequence_segment=m.group(3),
            )
        prefix_m = re.match(r"^([A-Za-z]{2,8})", cleaned)
        return cls(
            raw=cleaned,
            entity_prefix=prefix_m.group(1).upper() if prefix_m else "",
        )


@dataclass(frozen=True)
class SourceRecordMapping:
    """外部业务系统来源映射（防腐层追踪标识，不泄漏外部表结构至核心规则）。"""

    source_system: str
    external_id: str
    external_code: str = ""
    synced_at: float = field(default_factory=time.time)
    checksum: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_system": self.source_system,
            "external_id": self.external_id,
            "external_code": self.external_code,
            "synced_at": self.synced_at,
            "checksum": self.checksum,
            "extra": dict(self.extra),
        }


@dataclass
class ContractParty:
    role: str  # PARTY_A | PARTY_B | PARTY_C
    name: str
    credit_code: str = ""
    contact_name: str = ""
    contact_phone: str = ""
    bank_account: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "name": self.name,
            "credit_code": self.credit_code,
            "contact_name": self.contact_name,
            "contact_phone": self.contact_phone,
            "bank_account": self.bank_account,
        }


@dataclass
class ContractAmountSummary:
    """合同金额结构化摘要（强制 Decimal）。"""

    total_amount: Decimal | None = None
    service_fee: Decimal | None = None
    official_fee: Decimal | None = None
    currency: str = "CNY"
    tax_rate_pct: Decimal | None = None

    def __post_init__(self) -> None:
        for attr in ("total_amount", "service_fee", "official_fee", "tax_rate_pct"):
            val = getattr(self, attr)
            if isinstance(val, float):
                raise TypeError(
                    f"ContractAmountSummary.{attr} 严禁使用 float，请使用 Decimal 或字符串"
                )

    @classmethod
    def from_values(
        cls,
        total: Decimal | str | int | None = None,
        service_fee: Decimal | str | int | None = None,
        official_fee: Decimal | str | int | None = None,
        currency: str = "CNY",
        tax_rate_pct: Decimal | str | int | None = None,
    ) -> "ContractAmountSummary":
        def _to_dec(v: Any) -> Decimal | None:
            if v is None:
                return None
            if isinstance(v, Decimal):
                return v
            return parse_decimal(str(v))

        return cls(
            total_amount=_to_dec(total),
            service_fee=_to_dec(service_fee),
            official_fee=_to_dec(official_fee),
            currency=currency,
            tax_rate_pct=_to_dec(tax_rate_pct),
        )

    def verify_split_sum(self) -> bool:
        """若同时存在总额、服务费与官费，校验总额 == 服务费 + 官费。"""
        if (
            self.total_amount is not None
            and self.service_fee is not None
            and self.official_fee is not None
        ):
            return self.total_amount == (self.service_fee + self.official_fee)
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_amount": (
                str(self.total_amount) if self.total_amount is not None else None
            ),
            "service_fee": (
                str(self.service_fee) if self.service_fee is not None else None
            ),
            "official_fee": (
                str(self.official_fee) if self.official_fee is not None else None
            ),
            "currency": self.currency,
            "tax_rate_pct": (
                str(self.tax_rate_pct) if self.tax_rate_pct is not None else None
            ),
        }


@dataclass
class CanonicalContract:
    """统一企业合同聚合根。"""

    contract_id: str
    tenant_id: str = "default"
    contract_number: ContractNumber | None = None
    title: str = ""
    business_category: str = ""
    contract_subtype: str = ""
    lifecycle_status: str = ContractLifecycleStatus.DRAFT.value
    source_system: str = SourceSystemType.UPLOAD.value
    source_document_id: str = ""
    source_hash: str = ""
    parties: list[ContractParty] = field(default_factory=list)
    amounts: ContractAmountSummary = field(default_factory=ContractAmountSummary)
    source_mappings: list[SourceRecordMapping] = field(default_factory=list)
    version: int = 1
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "tenant_id": self.tenant_id,
            "contract_number": (
                self.contract_number.raw if self.contract_number else None
            ),
            "entity_prefix": (
                self.contract_number.entity_prefix if self.contract_number else ""
            ),
            "title": self.title,
            "business_category": self.business_category,
            "contract_subtype": self.contract_subtype,
            "lifecycle_status": self.lifecycle_status,
            "source_system": self.source_system,
            "source_document_id": self.source_document_id,
            "source_hash": self.source_hash,
            "parties": [p.to_dict() for p in self.parties],
            "amounts": self.amounts.to_dict(),
            "source_mappings": [m.to_dict() for m in self.source_mappings],
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class SourceContractRecord:
    """外部业务系统输入的合同源记录 DTO。"""

    tenant_id: str
    source_system: str
    external_id: str
    external_code: str = ""
    title: str = ""
    contract_number: str = ""
    business_category: str = ""
    contract_subtype: str = ""
    lifecycle_status: str = ContractLifecycleStatus.DRAFT.value
    parties: list[ContractParty] = field(default_factory=list)
    amounts: ContractAmountSummary = field(default_factory=ContractAmountSummary)
    source_updated_at: float = field(default_factory=time.time)
    source_hash: str = ""
    attachments: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class MergeOutcome:
    """合同合并纯函数计算结果。"""

    contract: CanonicalContract
    is_new: bool
    version_incremented: bool
    conflicts: list[dict[str, Any]] = field(default_factory=list)


def merge_canonical(
    existing: CanonicalContract | None, incoming: SourceContractRecord
) -> MergeOutcome:
    """纯函数：合并外部来源记录到统一合同实体（T10 / F06）。"""
    mapping = SourceRecordMapping(
        source_system=incoming.source_system,
        external_id=incoming.external_id,
        external_code=incoming.external_code,
        synced_at=incoming.source_updated_at,
        checksum=incoming.source_hash,
        extra=incoming.extra,
    )

    if existing is None:
        c_id = f"cc_{uuid.uuid4().hex[:12]}"
        contract = CanonicalContract(
            contract_id=c_id,
            tenant_id=incoming.tenant_id,
            contract_number=ContractNumber.parse(incoming.contract_number),
            title=incoming.title,
            business_category=incoming.business_category,
            contract_subtype=incoming.contract_subtype,
            lifecycle_status=incoming.lifecycle_status,
            source_system=incoming.source_system,
            source_document_id=incoming.external_id,
            source_hash=incoming.source_hash,
            parties=list(incoming.parties),
            amounts=incoming.amounts,
            source_mappings=[mapping],
            version=1,
            created_at=time.time(),
            updated_at=time.time(),
        )
        return MergeOutcome(contract=contract, is_new=True, version_incremented=False)

    # 检查是否为同源乱序更新：如果传入的 source_updated_at 小于已有映射的更新时间，忽略本次更新
    for m in existing.source_mappings:
        if (
            m.source_system == incoming.source_system
            and m.external_id == incoming.external_id
        ):
            if incoming.source_updated_at < m.synced_at:
                return MergeOutcome(
                    contract=existing, is_new=False, version_incremented=False
                )

    # 保留并更新来源映射（不丢失其他来源的映射）
    updated_mappings: list[SourceRecordMapping] = []
    replaced = False
    for m in existing.source_mappings:
        if (
            m.source_system == incoming.source_system
            and m.external_id == incoming.external_id
        ):
            updated_mappings.append(mapping)
            replaced = True
        else:
            updated_mappings.append(m)
    if not replaced:
        updated_mappings.append(mapping)

    # 检查实质性变动
    substantive_change = False
    if incoming.title and incoming.title != existing.title:
        substantive_change = True
    if incoming.source_hash and incoming.source_hash != existing.source_hash:
        substantive_change = True
    if (
        incoming.amounts.total_amount is not None
        and incoming.amounts.total_amount != existing.amounts.total_amount
    ):
        substantive_change = True
    if incoming.parties and len(incoming.parties) != len(existing.parties):
        substantive_change = True

    new_version = existing.version + 1 if substantive_change else existing.version

    merged_title = incoming.title if incoming.title else existing.title
    merged_amounts = (
        incoming.amounts
        if incoming.amounts.total_amount is not None
        else existing.amounts
    )
    merged_parties = incoming.parties if incoming.parties else existing.parties
    merged_status = (
        incoming.lifecycle_status
        if incoming.lifecycle_status != ContractLifecycleStatus.DRAFT.value
        else existing.lifecycle_status
    )
    merged_hash = (
        incoming.source_hash if incoming.source_hash else existing.source_hash
    )

    merged_contract = CanonicalContract(
        contract_id=existing.contract_id,
        tenant_id=existing.tenant_id,
        contract_number=(
            ContractNumber.parse(incoming.contract_number) or existing.contract_number
        ),
        title=merged_title,
        business_category=incoming.business_category or existing.business_category,
        contract_subtype=incoming.contract_subtype or existing.contract_subtype,
        lifecycle_status=merged_status,
        source_system=existing.source_system,
        source_document_id=existing.source_document_id,
        source_hash=merged_hash,
        parties=merged_parties,
        amounts=merged_amounts,
        source_mappings=updated_mappings,
        version=new_version,
        created_at=existing.created_at,
        updated_at=time.time() if substantive_change else existing.updated_at,
    )
    return MergeOutcome(
        contract=merged_contract,
        is_new=False,
        version_incremented=substantive_change,
        conflicts=[],
    )
