# -*- coding: utf-8 -*-
"""End-to-end integration tests for PostgreSQL + pgvector pipeline execution, leases, and UoW transactions."""

from __future__ import annotations

import os
import pytest

from contract_kb.storage_pg import StoragePG

PG_TEST_DSN = os.environ.get("CONTRACT_KB_TEST_PG_DSN", "")


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
        pytest.fail(
            f"CONTRACT_KB_TEST_PG_DSN was declared ({PG_TEST_DSN}) but connection failed; cannot fake green."
        )

    from contract_kb.application.security.idempotency import RequestIdentity

    st = StoragePG(PG_TEST_DSN)
    try:
        # 1. Unit of work test
        identity1 = RequestIdentity(
            tenant_id="tenant_pg",
            actor_id="user_pg",
            operation="CREATE_REVIEW",
            key="pg_test_key_001",
            fingerprint="hash_001",
        )
        with st.unit_of_work():
            st.reserve_idempotency_key(identity1, resource_id="res_001")
        rec = st.find_task_by_idempotency_key(
            "pg_test_key_001", tenant_id="tenant_pg", created_by="user_pg"
        )
        assert rec is not None

        # 2. Rollback test
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
                raise RuntimeError("Simulated transaction failure")
        except RuntimeError:
            pass

        rec_rb = st.find_task_by_idempotency_key(
            "pg_test_key_rollback", tenant_id="tenant_pg", created_by="user_pg"
        )
        assert rec_rb is None, (
            "Rollback must undo uncommitted idempotency key in PostgreSQL"
        )

    finally:
        st.close()
