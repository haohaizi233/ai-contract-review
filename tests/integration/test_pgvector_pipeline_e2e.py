# -*- coding: utf-8 -*-
"""End-to-end integration tests for PostgreSQL + pgvector pipeline execution, leases, and UoW transactions."""

from __future__ import annotations

import os
import pytest

from contract_kb.storage_pg import StoragePG

PG_TEST_DSN = os.environ.get("CONTRACT_KB_TEST_PG_DSN", "")


def _mask_dsn(dsn: str) -> str:
    """Mask credentials in PostgreSQL connection strings to prevent credential leaks."""
    from urllib.parse import urlparse

    try:
        p = urlparse(dsn)
        if p.password:
            return dsn.replace(f":{p.password}@", ":******@")
    except Exception:
        pass
    return dsn


def _is_pg_configured_and_reachable(dsn: str) -> bool:
    if not dsn:
        return False
    try:
        import psycopg2

        conn = psycopg2.connect(dsn, connect_timeout=3)
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.close()
        return True
    except Exception:
        return False


def test_pgvector_migration_and_pipeline_e2e():
    """Verify PostgreSQL execution and schema migrations when CONTRACT_KB_TEST_PG_DSN is provided."""
    if not PG_TEST_DSN:
        pytest.skip(
            "CONTRACT_KB_TEST_PG_DSN is not configured in local environment; "
            "requires isolated PostgreSQL test deployment (see deployment-and-rollback-runbook.md)"
        )

    if not _is_pg_configured_and_reachable(PG_TEST_DSN):
        masked = _mask_dsn(PG_TEST_DSN)
        pytest.fail(
            f"CONTRACT_KB_TEST_PG_DSN was declared ({masked}) but connection failed; cannot fake green."
        )

    from contract_kb.application.security.idempotency import RequestIdentity

    st = StoragePG(PG_TEST_DSN)
    try:
        # 1. Unit of work test with task creation and idempotency
        identity1 = RequestIdentity(
            tenant_id="tenant_pg",
            actor_id="user_pg",
            operation="CREATE_REVIEW",
            key="pg_test_key_001",
            fingerprint="hash_001",
        )
        with st.unit_of_work():
            st.reserve_idempotency_key(identity1, resource_id="res_001")
            st.save_task(
                {
                    "task_id": "res_001",
                    "tenant_id": "tenant_pg",
                    "created_by": "user_pg",
                    "idempotency_key": "pg_test_key_001",
                    "status": "COMPLETED",
                    "source_file": "pg_test.docx",
                    "review_gate": "PASS",
                }
            )
        rec = st.find_task_by_idempotency_key(
            "pg_test_key_001", tenant_id="tenant_pg", created_by="user_pg"
        )
        assert rec is not None
        assert rec["task_id"] == "res_001"

        # 2. Rollback test: transaction abort must undo both task and idempotency
        identity_rb = RequestIdentity(
            tenant_id="tenant_pg",
            actor_id="user_pg",
            operation="CREATE_REVIEW",
            key="pg_test_key_rollback",
            fingerprint="hash_rb",
        )
        try:
            with st.unit_of_work():
                st.reserve_idempotency_key(identity_rb, resource_id="res_rb")
                st.save_task(
                    {
                        "task_id": "res_rb",
                        "tenant_id": "tenant_pg",
                        "created_by": "user_pg",
                        "idempotency_key": "pg_test_key_rollback",
                        "status": "COMPLETED",
                        "source_file": "pg_rollback.docx",
                    }
                )
                raise RuntimeError("Simulated transaction failure")
        except RuntimeError:
            pass

        rec_rb = st.find_task_by_idempotency_key(
            "pg_test_key_rollback", tenant_id="tenant_pg", created_by="user_pg"
        )
        assert rec_rb is None, (
            "Rollback must undo uncommitted idempotency key in PostgreSQL"
        )
        assert st.get_task("res_rb") is None, (
            "Rollback must undo uncommitted task record in PostgreSQL"
        )

        # 3. Vector indexing and search verification
        doc_data = {
            "document_id": "doc_pg_test_01",
            "source_file": "pg_template.docx",
            "sha256": "hash_pg_template_01",
            "template_version": "v1",
            "category": "LIABILITY",
            "subtype": "LIABILITY",
            "document_type": "contract_template",
            "status": "ACTIVE",
            "clauses": [
                {
                    "clause_id": "clause_pg_01",
                    "clause_number": "1.1",
                    "clause_category": "LIABILITY",
                    "original_text": "乙方逾期交付成果的，每日应按合同总金额的千分之三向甲方支付违约金。",
                    "is_company_standard": True,
                    "approval_status": "APPROVED",
                    "knowledge_type": "TEMPLATE",
                }
            ],
        }
        st.upsert_document(doc_data, tenant="tenant_pg")
        hits = st.search(
            "逾期交付 违约金",
            top_k=2,
            tenant_id="tenant_pg",
            rerank=False,
        )
        assert len(hits) >= 1
        assert any(h["clause_id"] == "clause_pg_01" for h in hits)

    finally:
        st.close()
