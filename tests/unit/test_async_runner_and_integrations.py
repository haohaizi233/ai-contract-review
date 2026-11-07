# -*- coding: utf-8 -*-
"""Phase E 单元测试：持久化异步任务执行器、跨源对账纯函数、防腐适配器与脱敏可观测性。"""

from __future__ import annotations

import hashlib
import io
import json
import logging
from pathlib import Path
import time

import pytest

from contract_kb.infrastructure.observability import (
    MetricsCollector,
    StructuredJsonFormatter,
    bind_trace_context,
    clear_trace_context,
    redact_dict,
    redact_sensitive_text,
)
from contract_kb.infrastructure.task_queue import (
    JobExecutionContext,
    PersistentTaskRunner,
)
from contract_kb.modules.cases import (
    AttachmentEntity,
    SafeJsonCache,
    deduplicate_attachments_by_digest,
    is_contract_in_year_scope,
    normalize_contract_no,
    reconcile_source_records,
    split_composite_contract_keys,
)
from contract_kb.modules.integrations import (
    ContractIntegrationOrchestrator,
    E9SqlServerAdapter,
    MinioFileAdapter,
    SyncWatermarkStore,
    TenderMysqlAdapter,
    WeDriveReadOnlyAdapter,
)
from contract_kb.storage import Storage


def test_task_runner_idempotency_and_temp_cleanup(tmp_path: Path):
    st = Storage(tmp_path / "jobs.sqlite")
    runner = PersistentTaskRunner(st, base_backoff_seconds=0.0)
    runner.register_handler(
        "ECHO",
        lambda payload, ctx: (ctx.update_progress(60), {"echo": payload["msg"]})[1],
    )

    f1 = tmp_path / "up1.docx"
    f2 = tmp_path / "up2.docx"
    f1.write_bytes(b"abc")
    f2.write_bytes(b"abc")

    j1, created1 = runner.submit(
        "ECHO",
        {"msg": "hello"},
        tenant_id="t_alpha",
        created_by="u1",
        idempotency_key="idem-001",
        temp_file_path=f1,
    )
    j2, created2 = runner.submit(
        "ECHO",
        {"msg": "hello"},
        tenant_id="t_alpha",
        created_by="u1",
        idempotency_key="idem-001",
        temp_file_path=f2,
    )
    assert created1 is True
    assert created2 is False
    assert j1["job_id"] == j2["job_id"]
    # 幂等命中的冗余临时文件立即被清理，主任务临时文件在执行成功后清理
    assert not f2.exists()
    assert f1.exists()

    done = runner.process_next()
    assert done is not None
    assert done["status"] == "SUCCEEDED"
    assert done["progress"] == 100
    assert done["result"] == {"echo": "hello"}
    assert not f1.exists()
    st.close()


def test_task_runner_retry_backoff_to_dead_letter_and_redaction(tmp_path: Path):
    st = Storage(tmp_path / "dlq.sqlite")
    runner = PersistentTaskRunner(st, base_backoff_seconds=0.0)

    def _failing_handler(_payload: dict, _ctx: JobExecutionContext) -> dict:
        raise RuntimeError("connect failed: postgresql://svc:TopSecret999@10.0.0.1/db")

    runner.register_handler("ALWAYS_FAIL", _failing_handler)
    job, _ = runner.submit("ALWAYS_FAIL", {}, max_attempts=2)

    step1 = runner.process_next()
    assert step1 is not None
    assert step1["status"] == "RETRYING"
    assert step1["attempts"] == 1
    assert "TopSecret999" not in (step1["error_message"] or "")
    assert "***REDACTED***" in (step1["error_message"] or "")

    step2 = runner.process_next()
    assert step2 is not None
    assert step2["status"] == "DEAD_LETTER"
    assert step2["attempts"] == 2
    assert "exhausted_max_attempts(2)" in (step2["dead_letter_reason"] or "")
    assert "TopSecret999" not in (step2["dead_letter_reason"] or "")
    st.close()


def test_task_runner_cancel_and_background_workers(tmp_path: Path):
    st = Storage(tmp_path / "bg.sqlite")
    runner = PersistentTaskRunner(
        st, max_workers=2, base_backoff_seconds=0.0, poll_interval_seconds=0.05
    )
    runner.register_handler("FAST_OK", lambda p, _c: {"v": p["v"] * 2})

    to_cancel, _ = runner.submit("FAST_OK", {"v": 1}, tenant_id="t1")
    assert runner.cancel(to_cancel["job_id"], tenant_id="t1") is True
    assert (
        st.get_async_job(to_cancel["job_id"], tenant_id="t1")["status"] == "CANCELLED"
    )

    active_job, _ = runner.submit("FAST_OK", {"v": 21}, tenant_id="t1")
    runner.start_background_workers()
    try:
        deadline = time.time() + 3.0
        while time.time() < deadline:
            cur = st.get_async_job(active_job["job_id"], tenant_id="t1")
            if cur and cur["status"] == "SUCCEEDED":
                break
            time.sleep(0.05)
        final = st.get_async_job(active_job["job_id"], tenant_id="t1")
        assert final is not None
        assert final["status"] == "SUCCEEDED"
        assert final["result"] == {"v": 42}
    finally:
        runner.stop_background_workers()
        st.close()


def test_case_reconciliation_pure_functions_and_safe_cache(tmp_path: Path):
    assert normalize_contract_no(" zscq - 2025 - 001 ") == "ZSCQ-2025-001"
    assert split_composite_contract_keys("HT2025001、HT2025002 / HT2025001") == [
        "HT2025001",
        "HT2025002",
    ]
    assert is_contract_in_year_scope("ZSCQ20250001") is True
    assert is_contract_in_year_scope("ZSCQ20210001") is False

    merged_att, removed = deduplicate_attachments_by_digest(
        e9_attachments=[
            AttachmentEntity("old_name.docx", "sha_same", 100, "WEAVER_E9", "e9:1"),
            AttachmentEntity("complement.pdf", "sha_only_e9", 200, "WEAVER_E9", "e9:2"),
        ],
        tender_attachments=[
            AttachmentEntity("new_name.docx", "sha_same", 100, "TENDER_NEW", "td:9"),
        ],
    )
    assert removed == 1
    assert len(merged_att) == 2
    assert merged_att[0].file_name == "new_name.docx"
    assert merged_att[1].file_name == "complement.pdf"

    e9_rows = [
        {
            "requestid": 101,
            "contract_no": "ZSCQ20250001",
            "title": "专利代理委托合同",
            "party_a": "示例甲公司",
            "total_amount": "10000.00",
        },
        {
            "requestid": 102,
            "contract_no": "ZSCQ20250002",
            "title": "仅旧库合同",
            "party_a": "旧库客户",
            "total_amount": "5000.00",
        },
    ]
    td_rows = [
        {
            "id": 9001,
            "contract_no": "ZSCQ20250001",
            "title": "专利代理委托合同（新）",
            "party_a": "示例甲公司（变更名）",
            "party_b": "示例乙代理所",
            "total_amount": "12000.00",
        },
        {
            "id": 9003,
            "contract_no": "ZSCQ20250003",
            "title": "仅新库合同",
            "party_a": "新库客户",
            "total_amount": "8000.00",
        },
    ]
    canonicals, report = reconcile_source_records(
        e9_rows, td_rows, tenant_id="t1", dry_run=True
    )
    assert report.total_unique_contracts == 3
    assert report.matched_both_count == 1
    assert report.only_in_e9 == ["ZSCQ20250002"]
    assert report.only_in_tender == ["ZSCQ20250003"]
    assert len(report.amount_conflicts) == 1
    assert len(report.party_conflicts) == 1
    assert len(canonicals) == 3

    cache_file = tmp_path / "safe_cache.json"
    SafeJsonCache.save(cache_file, {"items": [1, 2, 3], "cursor": "9003"})
    loaded = SafeJsonCache.load(cache_file)
    assert loaded == {"items": [1, 2, 3], "cursor": "9003"}

    # 篡改 payload 后触发完整性校验异常
    tampered = json.loads(cache_file.read_text(encoding="utf-8"))
    tampered["payload"]["cursor"] = "999999"
    cache_file.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="完整性校验失败"):
        SafeJsonCache.load(cache_file)


class _DummyCursor:
    def __init__(self, cols: list[str], rows: list[tuple]):
        self.description = [(c,) for c in cols]
        self._rows = rows
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params: tuple) -> None:
        self.executed.append((sql, params))

    def fetchall(self) -> list[tuple]:
        return self._rows


class _DummyConn:
    def __init__(self, cursor: _DummyCursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self) -> _DummyCursor:
        return self._cursor


def test_integration_adapters_and_orchestrator_dry_run_vs_commit(tmp_path: Path):
    st = Storage(tmp_path / "int.sqlite")
    wm = SyncWatermarkStore(tmp_path / "watermarks.json")

    e9_cur = _DummyCursor(
        ["requestid", "contract_no", "title", "createdate"],
        [(501, "ZSCQ20250100", "商标许可合同", "2025-03-01")],
    )
    td_cur = _DummyCursor(
        [
            "id",
            "contract_no",
            "title",
            "party_a",
            "party_b",
            "total_amount",
            "update_time",
        ],
        [
            (
                801,
                "ZSCQ20250100",
                "商标许可合同",
                "甲公司",
                "乙公司",
                "30000.00",
                "2025-03-02",
            )
        ],
    )
    orch = ContractIntegrationOrchestrator(
        storage=st,
        watermark_store=wm,
        e9_adapter=E9SqlServerAdapter(lambda: _DummyConn(e9_cur)),
        tender_adapter=TenderMysqlAdapter(lambda: _DummyConn(td_cur)),
    )

    # 1. dry_run=True 不得落库也不得推进持久化水位线
    dry = orch.sync_incremental_batch(tenant_id="t1", dry_run=True)
    assert dry["dry_run"] is True
    assert dry["persisted_canonical_count"] == 0
    assert st.list_canonical_contracts(tenant_id="t1")[1] == 0
    assert wm.get("weaver_e9").last_cursor == ""

    # 2. dry_run=False 原子写入 canonical_contracts 并推进双端水位线
    real = orch.sync_incremental_batch(tenant_id="t1", dry_run=False)
    assert real["dry_run"] is False
    assert real["persisted_canonical_count"] == 1
    items, total = st.list_canonical_contracts(tenant_id="t1")
    assert total == 1
    assert items[0]["contract_number"] == "ZSCQ20250100"
    assert wm.get("weaver_e9").last_cursor == "501"
    assert wm.get("tender_mysql").last_cursor == "801"

    # 3. MinioFileAdapter SHA-256 校验与限流测试
    content = b"sample-docx-binary-stream"
    good_sha = hashlib.sha256(content).hexdigest()
    minio = MinioFileAdapter(max_bytes=1024, opener=lambda _u, _t: io.BytesIO(content))
    dl_dry = minio.download_verified(
        "https://example.invalid/a.docx", tmp_path / "a.docx", dry_run=True
    )
    assert dl_dry["status"] == "DRY_RUN"

    dl_ok = minio.download_verified(
        "https://example.invalid/a.docx",
        tmp_path / "a.docx",
        expected_sha256=good_sha,
    )
    assert dl_ok["status"] == "DOWNLOADED"
    assert (tmp_path / "a.docx").read_bytes() == content

    with pytest.raises(ValueError, match="SHA-256"):
        minio.download_verified(
            "https://example.invalid/b.docx",
            tmp_path / "b.docx",
            expected_sha256="0" * 64,
        )
    assert not (tmp_path / "b.docx").exists()

    small_minio = MinioFileAdapter(
        max_bytes=5, opener=lambda _u, _t: io.BytesIO(content)
    )
    with pytest.raises(ValueError, match="超过安全上限"):
        small_minio.download_verified(
            "https://example.invalid/c.docx", tmp_path / "c.docx"
        )

    # 4. WeDriveReadOnlyAdapter 只读约束与 .docx 过滤
    with pytest.raises(ValueError, match="只读模式"):
        WeDriveReadOnlyAdapter(lambda _s: [], mode="WRITE_ALL")

    wd = WeDriveReadOnlyAdapter(
        lambda _s: [
            {"fileid": "f1", "file_name": "专利代理合同.docx", "file_size": 1200},
            {"fileid": "f2", "file_name": "无关表格.xlsx", "file_size": 800},
        ],
        mode="DRY_RUN",
    )
    inv = wd.probe_docx_inventory("s_test_space")
    assert len(inv) == 1
    assert inv[0]["file_name"] == "专利代理合同.docx"
    st.close()


def test_observability_redaction_and_metrics():
    raw = (
        "dsn=postgresql://admin:MyPwd123!@127.0.0.1:5432/db "
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.secret "
        "api_key='sk-live-9988776655' card=6222021001112223334 mobile=13812345678"
    )
    cleaned = redact_sensitive_text(raw)
    assert "MyPwd123!" not in cleaned
    assert "eyJhbGciOiJIUzI1NiJ9" not in cleaned
    assert "sk-live-9988776655" not in cleaned
    assert "6222021001112223334" not in cleaned
    assert "13812345678" not in cleaned
    assert "138****5678" in cleaned

    d_clean = redact_dict({"password": "p1", "nested": {"api_key": "k1", "ok": "yes"}})
    assert d_clean["password"] == "***REDACTED***"
    assert d_clean["nested"]["api_key"] == "***REDACTED***"
    assert d_clean["nested"]["ok"] == "yes"

    bind_trace_context(request_id="req_test_1", trace_id="trc_test_1")
    try:
        rec = logging.LogRecord(
            name="contract_kb.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg="token=abcdef123456",
            args=(),
            exc_info=None,
        )
        formatted = json.loads(StructuredJsonFormatter().format(rec))
        assert formatted["request_id"] == "req_test_1"
        assert formatted["trace_id"] == "trc_test_1"
        assert "abcdef123456" not in formatted["msg"]
    finally:
        clear_trace_context()

    mc = MetricsCollector()
    mc.record_review(
        status="COMPLETED",
        stages=[{"stage_name": "Parse", "duration_ms": 12.5}],
        issues=[{"rule_id": "AMOUNT_CASE_CONSISTENCY", "category": "AMOUNT"}],
    )
    mc.record_llm_call(outcome="timeout")
    mc.record_rag_query(hit_count=3)
    mc.record_db_query(4.2)
    mc.record_http_request("GET", "/healthz", 200)
    snap = mc.snapshot()
    assert snap["counters"]["review_total"] == 1
    assert snap["counters"]["llm_timeouts"] == 1
    assert snap["rates"]["rag_hit_rate"] == 1.0
    assert snap["stage_durations_ms"]["Parse"]["avg_ms"] == 12.5
    prom = mc.render_prometheus()
    assert "contract_kb_review_total 1" in prom
