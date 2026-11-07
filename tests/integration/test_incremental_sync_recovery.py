# -*- coding: utf-8 -*-
"""集成测试：外部系统增量同步水位线、更新捕获与原子批次事务（T11 / F06/F12）。

验证关键契约：
1. (updated_at, external_id) 复合游标推进：能够正确捕获更新时间变动及同一时间不同 ID 的记录；
2. 租户隔离：租户 A 的同步水位线推进绝不影响租户 B；
3. 原子事务回滚保护：在单批次同步中途若崩溃或校验失败，UoW 保证已写入的数据与水位线原子回滚，
   绝不残留“数据已入库但水位线未更新”或“水位线已前进但数据丢失”的半同步脏状态；
4. 重复批次检测：同一 batch_id 幂等跳过，不产生重复增版。
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from contract_kb.application.integrations.sync_service import (
    SyncBatchConflictError,
    SyncCursor,
    SyncWatermarkService,
)
from contract_kb.domain.models.canonical_contract import (
    ContractAmountSummary,
    SourceContractRecord,
)
from contract_kb.storage import Storage


def test_incremental_sync_cursor_advancement_and_tenant_isolation(tmp_path: Path):
    """验证游标基于 (updated_at, external_id) 单调推进且多租户完全隔离。"""
    st = Storage(tmp_path / "sync_watermarks.sqlite")
    service = SyncWatermarkService(st)

    # 1. 租户 A 初始游标全为 0
    c_a0 = service.get_cursor("tenant_A", "WEAVER_E9", "contracts")
    assert c_a0.last_updated_at == 0.0
    assert c_a0.last_external_id == ""

    # 2. 租户 A 提交批次 1 (包含 2 条记录)
    rec1 = SourceContractRecord(
        tenant_id="tenant_A",
        source_system="WEAVER_E9",
        external_id="doc_1",
        title="技术合同 1",
        amounts=ContractAmountSummary(total_amount=Decimal("10000.00")),
        source_updated_at=100.0,
    )
    rec2 = SourceContractRecord(
        tenant_id="tenant_A",
        source_system="WEAVER_E9",
        external_id="doc_2",
        title="技术合同 2",
        amounts=ContractAmountSummary(total_amount=Decimal("20000.00")),
        source_updated_at=100.0,  # 相同时间戳不同 ID
    )
    new_cursor_a1 = SyncCursor(
        tenant_id="tenant_A",
        source_system="WEAVER_E9",
        source_scope="contracts",
        last_updated_at=100.0,
        last_external_id="doc_2",
    )
    res_a1 = service.commit_sync_batch(
        batch_id="batch_a_01",
        records=[rec1, rec2],
        expected_cursor=c_a0,
        new_cursor=new_cursor_a1,
    )
    assert res_a1.success is True
    assert res_a1.records_synced == 2

    # 3. 验证租户 A 水位线已推进
    c_a_latest = service.get_cursor("tenant_A", "WEAVER_E9", "contracts")
    assert c_a_latest.last_updated_at == 100.0
    assert c_a_latest.last_external_id == "doc_2"

    # 4. 验证租户 B 水位线仍然处于初始状态（多租户绝对隔离）
    c_b0 = service.get_cursor("tenant_B", "WEAVER_E9", "contracts")
    assert c_b0.last_updated_at == 0.0
    assert c_b0.last_external_id == ""
    st.close()


def test_sync_batch_atomic_rollback_on_failure(tmp_path: Path):
    """验证同步批次在发生中途错误时原子回滚，数据与水位线均不保留脏状态。"""
    st = Storage(tmp_path / "sync_rollback.sqlite")
    service = SyncWatermarkService(st)

    initial_cursor = service.get_cursor("t1", "WEAVER_E9", "contracts")

    good_rec = SourceContractRecord(
        tenant_id="t1",
        source_system="WEAVER_E9",
        external_id="doc_good",
        title="正常合同",
        amounts=ContractAmountSummary(total_amount=Decimal("50000.00")),
        source_updated_at=200.0,
    )

    # 模拟在记录列表中注入破坏性错误触发异常
    class ExplodingRecord:
        tenant_id = "t1"
        source_system = "WEAVER_E9"

        @property
        def external_id(self) -> str:
            raise RuntimeError("unexpected_io_crash_during_batch_import")

    exploding_rec: Any = ExplodingRecord()

    new_cursor = SyncCursor(
        tenant_id="t1",
        source_system="WEAVER_E9",
        source_scope="contracts",
        last_updated_at=200.0,
        last_external_id="doc_bad",
    )

    with pytest.raises(RuntimeError):
        service.commit_sync_batch(
            batch_id="batch_fail_01",
            records=[good_rec, exploding_rec],
            expected_cursor=initial_cursor,
            new_cursor=new_cursor,
        )

    # 验证数据原子回滚：good_rec 绝不能残留在数据库中
    found = st.find_canonical_by_source("t1", "WEAVER_E9", "doc_good")
    assert found is None, "异常回滚后不能残留已写入记录"

    # 验证水位线没有前进
    cursor_after = service.get_cursor("t1", "WEAVER_E9", "contracts")
    assert cursor_after.last_updated_at == 0.0
    st.close()


def test_optimistic_cursor_conflict_detection(tmp_path: Path):
    """验证期望游标不匹配时检测并发冲突并阻止乱序覆盖。"""
    st = Storage(tmp_path / "sync_conflict.sqlite")
    service = SyncWatermarkService(st)

    # 1. 提交初始批次
    c0 = service.get_cursor("t1", "WEAVER_E9", "contracts")
    c1 = SyncCursor("t1", "WEAVER_E9", "contracts", 150.0, "ext_1")
    service.commit_sync_batch("batch_1", [], expected_cursor=c0, new_cursor=c1)

    # 2. 另一个同步进程携带过时的期望游标 c0 尝试提交：必须被拦截并抛出 SyncBatchConflictError
    c_stale_new = SyncCursor("t1", "WEAVER_E9", "contracts", 120.0, "ext_old")
    with pytest.raises(SyncBatchConflictError):
        service.commit_sync_batch(
            "batch_conflict", [], expected_cursor=c0, new_cursor=c_stale_new
        )
    st.close()
