# -*- coding: utf-8 -*-
"""测试多数据源增量合流中已有映射保留与版本号严格单调递增（R06 整改验证）。"""

from __future__ import annotations

from pathlib import Path

from contract_kb.storage import Storage
from contract_kb.modules.integrations.adapters import (
    ContractIntegrationOrchestrator,
    E9SqlServerAdapter,
    TenderMysqlAdapter,
    SyncWatermarkStore,
)


def test_multi_source_incremental_sync_preserves_mappings_and_increments_version(tmp_path: Path):
    """
    验证三批增量数据合流：
    - Batch 1: 写入双来源数据（WEAVER_E9 与 TENDER_MYSQL 关联同一合同编号）；
    - Batch 2: 仅更新 TENDER_MYSQL，验证原有 WEAVER_E9 映射不丢失，版本号由 1 递增为 2；
    - Batch 3: 仅更新 WEAVER_E9，验证 TENDER_MYSQL 映射不丢失，版本号由 2 递增为 3。
    """
    db_file = tmp_path / "multi_source_sync.sqlite"
    st = Storage(db_file)
    wm_file = tmp_path / "watermark.json"
    wm_store = SyncWatermarkStore(wm_file)

    # Mock 适配器返回受控合成数据
    class MockE9Adapter:
        def __init__(self, batches):
            self.batches = batches
            self.call_count = 0
        def fetch_contracts_since(self, cursor: int, limit: int = 100):
            if self.call_count < len(self.batches):
                res = self.batches[self.call_count]
                self.call_count += 1
                return res
            return []

    class MockTenderAdapter:
        def __init__(self, batches):
            self.batches = batches
            self.call_count = 0
        def fetch_contracts_since(self, cursor: int, limit: int = 100):
            if self.call_count < len(self.batches):
                res = self.batches[self.call_count]
                self.call_count += 1
                return res
            return []

    e9_b1 = [{"requestid": 101, "contract_no": "HT-2026-MULTI-01", "title": "E9 Service Contract", "party_a": "Alpha", "party_b": "Beta", "total_amt": "100000"}]
    td_b1 = [{"id": 501, "contract_no": "HT-2026-MULTI-01", "contract_name": "Tender Service Contract", "customer_name": "Alpha Corp", "company_name": "Beta LLC", "total_amount": "100000"}]

    e9_b2 = []  # Batch 2: E9 无新数据
    td_b2 = [{"id": 502, "contract_no": "HT-2026-MULTI-01", "contract_name": "Tender Service Contract Updated", "customer_name": "Alpha Corp", "company_name": "Beta LLC", "total_amount": "120000"}]

    e9_b3 = [{"requestid": 102, "contract_no": "HT-2026-MULTI-01", "title": "E9 Service Contract Final", "party_a": "Alpha Group", "party_b": "Beta LLC", "total_amt": "120000"}]
    td_b3 = []  # Batch 3: Tender 无新数据

    e9_adapter = MockE9Adapter([e9_b1, e9_b2, e9_b3])
    td_adapter = MockTenderAdapter([td_b1, td_b2, td_b3])

    orchestrator = ContractIntegrationOrchestrator(
        storage=st,
        watermark_store=wm_store,
        e9_adapter=e9_adapter,
        tender_adapter=td_adapter,
    )

    # 1. 执行 Batch 1
    res1 = orchestrator.sync_incremental_batch(tenant_id="audit_tenant", dry_run=False)
    assert res1["persisted_canonical_count"] >= 1

    c1 = st.find_canonical_by_source("audit_tenant", "WEAVER_E9", "101")
    assert c1 is not None, "Batch 1 后应能通过 WEAVER_E9 查到标准合同"
    sources1 = {m["source_system"] for m in c1.get("source_mappings", [])}
    assert "WEAVER_E9" in sources1
    assert "TENDER_NEW" in sources1
    assert c1["version"] == 1

    # 2. 执行 Batch 2（仅 Tender 变动）
    res2 = orchestrator.sync_incremental_batch(tenant_id="audit_tenant", dry_run=False)
    assert res2["persisted_canonical_count"] >= 1

    c2 = st.find_canonical_by_source("audit_tenant", "TENDER_NEW", "502")
    assert c2 is not None
    # 关键断言：WEAVER_E9 来源绝对不能丢失！
    sources2 = {m["source_system"] for m in c2.get("source_mappings", [])}
    assert "WEAVER_E9" in sources2, f"Batch 2 增量合流导致旧 WEAVER_E9 来源映射丢失！当前 sources: {sources2}"
    assert "TENDER_NEW" in sources2
    assert c2["version"] == 2, f"Batch 2 更新后版本号应为 2，实际为: {c2['version']}"

    # 3. 执行 Batch 3（仅 E9 变动）
    res3 = orchestrator.sync_incremental_batch(tenant_id="audit_tenant", dry_run=False)
    assert res3["persisted_canonical_count"] >= 1

    c3 = st.find_canonical_by_source("audit_tenant", "WEAVER_E9", "102")
    assert c3 is not None
    sources3 = {m["source_system"] for m in c3.get("source_mappings", [])}
    assert "WEAVER_E9" in sources3
    assert "TENDER_NEW" in sources3, f"Batch 3 增量合流导致 TENDER_NEW 来源映射丢失！当前 sources: {sources3}"
    assert c3["version"] == 3, f"Batch 3 更新后版本号应为 3，实际为: {c3['version']}"

    st.close()
