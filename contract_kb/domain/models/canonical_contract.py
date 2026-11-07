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
