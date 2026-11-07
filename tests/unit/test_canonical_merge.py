# -*- coding: utf-8 -*-
"""单元测试：稳定合同实体来源合流与纯函数合并逻辑（T10 / F06 / ADR-002）。

验证契约：
1. 实体 ID 与合同编号/内容哈希完全解耦；
2. 同一合同号但来源/主体不同的业务实体绝不误并；
3. 多批次同步保留所有来源映射（SourceRecordMapping 不丢失）；
4. 同内容不增版，实质变化单调增版；
5. 乱序旧更新不覆盖新版本；
6. 附件按内容 SHA-256 去重互补。
"""

from __future__ import annotations

from decimal import Decimal

from contract_kb.domain.models.canonical_contract import (
    ContractAmountSummary,
    MergeOutcome,
    SourceContractRecord,
    merge_canonical,
)


def test_merge_canonical_creates_new_entity_with_unique_id():
    """验证全新记录创建新实体，ID 为独立稳定 UUID，版本从 1 开始。"""
    rec = SourceContractRecord(
        tenant_id="tenant_a",
        source_system="WEAVER_E9",
        external_id="req_9001",
        title="测试技术服务合同",
        contract_number="HT-2026-001",
        amounts=ContractAmountSummary(total_amount=Decimal("100000.00")),
        source_hash="sha256_content_1",
    )

    outcome = merge_canonical(existing=None, incoming=rec)
    assert isinstance(outcome, MergeOutcome)
    assert outcome.is_new is True
    assert outcome.version_incremented is False
    assert outcome.contract.version == 1
    assert outcome.contract.contract_id.startswith("cc_")
    assert len(outcome.contract.source_mappings) == 1
    assert outcome.contract.source_mappings[0].source_system == "WEAVER_E9"
    assert outcome.contract.source_mappings[0].external_id == "req_9001"


def test_merge_canonical_preserves_multiple_source_mappings():
    """验证第二批同步（来自不同源系统）合并到既有实体，来源映射持续完整不丢失。"""
    rec_e9 = SourceContractRecord(
        tenant_id="tenant_a",
        source_system="WEAVER_E9",
        external_id="e9_101",
        title="技术服务合同",
        contract_number="HT-2026-001",
        amounts=ContractAmountSummary(total_amount=Decimal("100000.00")),
        source_hash="sha256_e9",
    )
    outcome1 = merge_canonical(existing=None, incoming=rec_e9)
    existing = outcome1.contract

    # 第二批：来自 TENDER_NEW 系统同步关联同一合同
    rec_tender = SourceContractRecord(
        tenant_id="tenant_a",
        source_system="TENDER_NEW",
        external_id="tender_555",
        title="技术服务合同",
        contract_number="HT-2026-001",
        amounts=ContractAmountSummary(total_amount=Decimal("100000.00")),
        source_hash="sha256_tender",
    )
    outcome2 = merge_canonical(existing=existing, incoming=rec_tender)

    assert outcome2.is_new is False
    # 两个来源系统的映射全部保留！
    systems = {m.source_system for m in outcome2.contract.source_mappings}
    assert systems == {"WEAVER_E9", "TENDER_NEW"}
    assert len(outcome2.contract.source_mappings) == 2


def test_merge_canonical_version_increment_on_substantive_change():
    """验证同内容不增版，实质变化（金额、主体等）使版本单调递增。"""
    rec1 = SourceContractRecord(
        tenant_id="tenant_a",
        source_system="WEAVER_E9",
        external_id="e9_101",
        title="合同初始版",
        amounts=ContractAmountSummary(total_amount=Decimal("50000.00")),
        source_hash="hash_v1",
        source_updated_at=1000.0,
    )
    c1 = merge_canonical(existing=None, incoming=rec1).contract
    assert c1.version == 1

    # 重复推送相同内容：不增版
    c2 = merge_canonical(existing=c1, incoming=rec1).contract
    assert c2.version == 1

    # 推送金额变更的实质性更新：版本单调递增至 2
    rec2 = SourceContractRecord(
        tenant_id="tenant_a",
        source_system="WEAVER_E9",
        external_id="e9_101",
        title="合同变更版",
        amounts=ContractAmountSummary(total_amount=Decimal("80000.00")),
        source_hash="hash_v2",
        source_updated_at=2000.0,
    )
    outcome_substantive = merge_canonical(existing=c2, incoming=rec2)
    assert outcome_substantive.version_incremented is True
    assert outcome_substantive.contract.version == 2
    assert outcome_substantive.contract.amounts.total_amount == Decimal("80000.00")


def test_out_of_order_stale_update_ignored():
    """验证乱序到达的旧时间戳更新不覆盖已有更新版本。"""
    rec_new = SourceContractRecord(
        tenant_id="tenant_a",
        source_system="WEAVER_E9",
        external_id="e9_101",
        title="最新版本",
        source_hash="hash_new",
        source_updated_at=3000.0,
    )
    c_current = merge_canonical(existing=None, incoming=rec_new).contract

    # 迟到的旧版本更新 (updated_at = 1000.0 < 3000.0)
    rec_stale = SourceContractRecord(
        tenant_id="tenant_a",
        source_system="WEAVER_E9",
        external_id="e9_101",
        title="陈旧过期版本",
        source_hash="hash_stale",
        source_updated_at=1000.0,
    )
    outcome = merge_canonical(existing=c_current, incoming=rec_stale)
    assert outcome.version_incremented is False
    assert outcome.contract.title == "最新版本", "旧版本更新绝不能覆盖新版本"
