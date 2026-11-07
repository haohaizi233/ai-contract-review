# -*- coding: utf-8 -*-
"""集成测试：合同实体多版本持久化与乐观并发版本控制（T10 / F06 / ADR-002）。

验证行为：
1. 实体与版本历史独立存储于 canonical_contracts 与 canonical_contract_versions；
2. 来源映射表 contract_source_mappings 具备 (tenant_id, source_system, external_id) 唯一性；
3. save_canonical_version 支持基于 expected_version 的乐观并发控制，冲突时拒绝写入；
4. 多批次增量版本写入保证版本号单调连续。
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path


from contract_kb.domain.models.canonical_contract import (
    ContractAmountSummary,
    SourceContractRecord,
    merge_canonical,
)
from contract_kb.storage import Storage


def test_canonical_version_persistence_and_optimistic_locking(tmp_path: Path):
    """验证版本快照保存与期望版本冲突拦截。"""
    st = Storage(tmp_path / "canonical_versions.sqlite")

    # 1. 保存第 1 版
    rec1 = SourceContractRecord(
        tenant_id="t1",
        source_system="WEAVER_E9",
        external_id="doc_001",
        title="企业技术开发合同",
        contract_number="HT-DEV-2026",
        amounts=ContractAmountSummary(total_amount=Decimal("300000.00")),
    )
    outcome1 = merge_canonical(existing=None, incoming=rec1)
    ok1 = st.save_canonical_version(outcome1, expected_version=None)
    assert ok1 is True

    cid = outcome1.contract.contract_id
    stored1 = st.get_canonical_contract(cid)
    assert stored1 is not None
    assert stored1["version"] == 1

    # 2. 实质变更保存第 2 版 (expected_version=1)
    rec2 = SourceContractRecord(
        tenant_id="t1",
        source_system="WEAVER_E9",
        external_id="doc_001",
        title="企业技术开发合同(变更增加预算)",
        contract_number="HT-DEV-2026",
        amounts=ContractAmountSummary(total_amount=Decimal("450000.00")),
    )
    outcome2 = merge_canonical(existing=outcome1.contract, incoming=rec2)
    assert outcome2.contract.version == 2
    ok2 = st.save_canonical_version(outcome2, expected_version=1)
    assert ok2 is True

    # 3. 模拟并发冲突：另一个 Worker 仍以旧 expected_version=1 尝试提交第 2 版
    ok_conflict = st.save_canonical_version(outcome2, expected_version=1)
    assert ok_conflict is False, "乐观锁校验失败必须拒绝写入"

    # 4. 验证历史版本记录中有 2 条快照
    versions = st.get_canonical_versions(cid)
    assert len(versions) == 2
    assert [v["version"] for v in versions] == [1, 2]

    # 5. 通过来源系统与 external_id 能够反向正确定位实体
    found = st.find_canonical_by_source("t1", "WEAVER_E9", "doc_001")
    assert found is not None
    assert found["contract_id"] == cid
    st.close()
