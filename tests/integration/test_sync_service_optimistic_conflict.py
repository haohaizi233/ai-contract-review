# -*- coding: utf-8 -*-
"""测试同步服务乐观锁失败回滚与游标保护（R03 整改验证）。"""

from __future__ import annotations

from pathlib import Path
import pytest

from contract_kb.storage import Storage
from contract_kb.application.integrations.sync_service import (
    SyncWatermarkService,
    SyncCursor,
    SyncBatchConflictError,
)
from contract_kb.domain.models.canonical_contract import SourceContractRecord


def test_sync_batch_conflict_rolls_back_and_preserves_cursor(tmp_path: Path):
    """
    验证当 save_canonical_version 返回 False（并发版本冲突）时：
    1. commit_sync_batch 明确抛出 SyncBatchConflictError；
    2. 整个批次事务回滚；
    3. 水位线游标严格不推进，保持原有初始游标；
    4. 数据库中未被成功落库的变化不被记录为成功。
    """
    db_file = tmp_path / "sync_conflict.db"
    st = Storage(db_file)
    svc = SyncWatermarkService(st)

    # 初始游标与当前空库一致（0.0, ""）
    initial_cursor = svc.get_cursor("tenant_audit", "WEAVER_E9", "contract_scope")
    assert initial_cursor.last_updated_at == 0.0
    assert initial_cursor.last_external_id == ""

    new_cursor = SyncCursor(
        tenant_id="tenant_audit",
        source_system="WEAVER_E9",
        source_scope="contract_scope",
        last_updated_at=100.0,
        last_external_id="id1",
    )

    records = [
        SourceContractRecord(
            tenant_id="tenant_audit",
            source_system="WEAVER_E9",
            external_id="id1",
            title="Synthetic Contract",
            contract_number="HT-2026-0001",
        )
    ]

    # 注入：模拟 save_canonical_version 发生乐观锁并发冲突返回 False
    st.save_canonical_version = lambda *args, **kwargs: False

    # 关键断言 1：必须抛出 SyncBatchConflictError 阻断，绝不允许返回 success=True
    with pytest.raises(SyncBatchConflictError, match="冲突|conflict|save_canonical_version"):
        svc.commit_sync_batch(
            batch_id="test_rejected_batch_01",
            records=records,
            expected_cursor=initial_cursor,
            new_cursor=new_cursor,
        )

    # 关键断言 2：游标必须保持不变，绝不推进到 100.0 / id1
    current_cursor = svc.get_cursor("tenant_audit", "WEAVER_E9", "contract_scope")
    assert current_cursor.last_updated_at == 0.0, (
        f"乐观锁冲突后游标被错误推进！实际游标: {current_cursor.last_updated_at}"
    )
    assert current_cursor.last_external_id != "id1"

    # 关键断言 3：批次历史不应存在 COMMITTED 记录
    with st._lock:
        row = st.conn.execute(
            "SELECT * FROM sync_batch_history WHERE batch_id = 'test_rejected_batch_01'"
        ).fetchone()
        assert row is None or row["status"] != "COMMITTED"

    st.close()
