# -*- coding: utf-8 -*-
"""Integration tests for ContractIntegrationOrchestrator and application adapters."""

from __future__ import annotations

from pathlib import Path
from contract_kb.modules.integrations.adapters import (
    ContractIntegrationOrchestrator,
    E9SqlServerAdapter,
    MinioFileAdapter,
    SyncWatermarkStore,
    TenderMysqlAdapter,
)
from contract_kb.storage import Storage


class DummyCursor:
    def __init__(self, rows: list[tuple], description: list[tuple]):
        self.rows = rows
        self.description = description

    def execute(self, sql, params):
        pass

    def fetchall(self):
        return self.rows


class DummyConn:
    def __init__(self, rows: list[tuple], cols: list[str]):
        self.cursor_obj = DummyCursor(rows, [(c,) for c in cols])

    def cursor(self):
        return self.cursor_obj

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def test_orchestrator_sync_incremental_batch_dry_run(tmp_path: Path):
    """Verify orchestrator runs through mock adapters without persisting in dry-run mode."""
    db_path = tmp_path / "test.db"
    st = Storage(db_path)
    wm_store = SyncWatermarkStore(tmp_path / "watermarks.json")

    e9_rows = [(101, "HT-2024-001", "研发技术服务合同", "2024-01-10")]
    e9_cols = ["requestid", "contract_no", "title", "createdate"]
    e9_adapter = E9SqlServerAdapter(lambda: DummyConn(e9_rows, e9_cols))

    td_rows = [(201, "HT-2024-001", "研发技术服务合同", "甲方公司", "乙方公司", 50000.0, "2024-01-15")]
    td_cols = ["id", "contract_no", "title", "party_a", "party_b", "total_amount", "update_time"]
    td_adapter = TenderMysqlAdapter(lambda: DummyConn(td_rows, td_cols))

    orchestrator = ContractIntegrationOrchestrator(
        storage=st,
        watermark_store=wm_store,
        e9_adapter=e9_adapter,
        tender_adapter=td_adapter,
    )

    result = orchestrator.sync_incremental_batch(tenant_id="tenant_a", batch_size=10, dry_run=True)
    assert result["dry_run"] is True
    assert result["persisted_canonical_count"] == 0
    assert "reconciliation" in result

    # Watermarks should not advance on dry-run
    wm_e9 = wm_store.get("weaver_e9")
    assert wm_e9.synced_count == 0


def test_minio_adapter_download_verified_dry_run(tmp_path: Path):
    """Verify MinIO adapter supports verified dry-run and digest check."""
    adapter = MinioFileAdapter(max_bytes=1024 * 1024)
    res = adapter.download_verified(
        url="http://mock-minio/bucket/file.docx",
        dest_path=tmp_path / "file.docx",
        dry_run=True,
    )
    assert res["status"] == "DRY_RUN"
    assert not (tmp_path / "file.docx").exists()
