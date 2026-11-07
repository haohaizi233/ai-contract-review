# -*- coding: utf-8 -*-
"""Phase C 集成测试：SQLite + 真实 PostgreSQL 18.6 统一存储端口、Unit of Work 原子事务、SAVEPOINT 与多租户关系契约。"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest

from contract_kb.domain.models.canonical_contract import (
    CanonicalContract,
    ContractAmountSummary,
    ContractLifecycleStatus,
    ContractNumber,
    ContractParty,
)
from contract_kb.modules.rules import HumanOverrideService
from contract_kb.review_engine import ReviewEngine
from contract_kb.storage import Storage

PG_TEST_DSN = os.environ.get(
    "CONTRACT_KB_TEST_PG_DSN", "postgresql://postgres@127.0.0.1:5432/postgres"
)


def _pg_reachable(dsn: str) -> bool:
    try:
        import psycopg2

        conn = psycopg2.connect(dsn, connect_timeout=2)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT version()")
                return bool(cur.fetchone())
        finally:
            conn.close()
    except Exception:
        return False


@pytest.fixture
def sqlite_repo(tmp_path):
    st = Storage(tmp_path / "uow_test.sqlite")
    yield st
    st.close()


@pytest.fixture
def pg18_relational_repo():
    if not _pg_reachable(PG_TEST_DSN):
        pytest.skip("本地或 CI PostgreSQL 实例不可达，跳过真实 PG 关系型集成测试")

    from contract_kb.storage_pg import PgVectorStorage

    schema_name = f"test_rel_{uuid.uuid4().hex[:10]}"
    st = PgVectorStorage(PG_TEST_DSN, migrate=False)
    st.migrate_relational(schema=schema_name)
    try:
        yield st
    finally:
        try:
            with st.conn.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")
            st.conn.commit()
        finally:
            st.close()


# ---------------- SQLite Unit of Work 测试 ----------------


def test_sqlite_uow_atomic_rollback_and_nested_savepoint(sqlite_repo):
    """SQLite UnitOfWork：多表写入抛错必须整体回滚；内层 SAVEPOINT 回滚不影响外层事务提交。"""
    with pytest.raises(ValueError, match="abort_outer_tx"):
        with sqlite_repo.unit_of_work():
            sqlite_repo.save_rule(
                {
                    "rule_id": "TX_RULE_1",
                    "version": 1,
                    "name": "事务回滚测试规则",
                    "rule_type": "CLAUSE_VARIATION",
                    "execution_mode": "DETERMINISTIC",
                    "severity": "HIGH",
                    "status": "DRAFT",
                },
                tenant_id="tenant_tx",
            )
            sqlite_repo.upsert_canonical_contract(
                CanonicalContract(
                    contract_id="CC_TX_1",
                    tenant_id="tenant_tx",
                    title="未提交合同",
                ).to_dict()
            )
            raise ValueError("abort_outer_tx")

    assert sqlite_repo.get_canonical_contract("CC_TX_1", tenant_id="tenant_tx") is None
    assert not sqlite_repo.get_rules(tenant_id="tenant_tx")

    # 嵌套 SAVEPOINT：外层写入成功，内层失败被捕获后回滚内层，外层正常提交
    with sqlite_repo.unit_of_work():
        sqlite_repo.upsert_canonical_contract(
            CanonicalContract(
                contract_id="CC_OUTER_OK",
                tenant_id="tenant_tx",
                title="外层保留合同",
            ).to_dict()
        )
        with pytest.raises(RuntimeError, match="inner_savepoint_fail"):
            with sqlite_repo.unit_of_work():
                sqlite_repo.upsert_canonical_contract(
                    CanonicalContract(
                        contract_id="CC_INNER_FAIL",
                        tenant_id="tenant_tx",
                        title="内层回滚合同",
                    ).to_dict()
                )
                raise RuntimeError("inner_savepoint_fail")

    assert (
        sqlite_repo.get_canonical_contract("CC_OUTER_OK", tenant_id="tenant_tx")
        is not None
    )
    assert (
        sqlite_repo.get_canonical_contract("CC_INNER_FAIL", tenant_id="tenant_tx")
        is None
    )


# ---------------- 真实 PostgreSQL 18.6 集成测试 ----------------


def test_pg18_uow_atomic_rollback_and_nested_savepoint(pg18_relational_repo):
    """真实 PostgreSQL 18.6：验证 SAVEPOINT 嵌套事务、读操作不提前提交外层事务及异常整体回滚。"""
    st = pg18_relational_repo

    with pytest.raises(RuntimeError, match="pg_outer_rollback"):
        with st.unit_of_work():
            st.save_rule(
                {
                    "rule_id": "PG_TX_R1",
                    "version": 1,
                    "name": "PG 原子回滚规则",
                    "rule_type": "CLAUSE_VARIATION",
                    "execution_mode": "DETERMINISTIC",
                    "severity": "MEDIUM",
                    "status": "DRAFT",
                },
                tenant_id="pg_tenant",
            )
            # 在事务中执行读取（验证 _commit 不会提前提交当前 SAVEPOINT）
            in_tx_rules = st.get_rules(tenant_id="pg_tenant")
            assert len(in_tx_rules) == 1
            raise RuntimeError("pg_outer_rollback")

    assert st.get_rules(tenant_id="pg_tenant") == []

    # 嵌套 SAVEPOINT：内层回滚，外层提交
    with st.unit_of_work():
        st.upsert_canonical_contract(
            CanonicalContract(
                contract_id="PG_CC_OUTER",
                tenant_id="pg_tenant",
                contract_number=ContractNumber.parse("PGCORP-20260501-001"),
                title="PG 外层合同",
                lifecycle_status=ContractLifecycleStatus.APPROVED.value,
                amounts=ContractAmountSummary.from_values(total="88000.50"),
            ).to_dict()
        )
        with pytest.raises(ValueError, match="pg_inner_rollback"):
            with st.unit_of_work():
                st.upsert_canonical_contract(
                    CanonicalContract(
                        contract_id="PG_CC_INNER",
                        tenant_id="pg_tenant",
                        title="PG 内层回滚合同",
                    ).to_dict()
                )
                raise ValueError("pg_inner_rollback")

    outer = st.get_canonical_contract("PG_CC_OUTER", tenant_id="pg_tenant")
    assert outer is not None
    assert Decimal(str(outer["total_amount"])) == Decimal("88000.50")
    assert st.get_canonical_contract("PG_CC_INNER", tenant_id="pg_tenant") is None


def test_pg18_full_review_pipeline_tenant_isolation_and_async_jobs(
    pg18_relational_repo, golden_samples
):
    """真实 PostgreSQL 18.6：运行 14 阶段 ReviewPipeline、人工覆写、多租户隔离与异步队列 SKIP LOCKED 认领。"""
    st = pg18_relational_repo

    # 租户 A 创建并审批规则，租户 B 不得可见
    st.save_rule(
        {
            "rule_id": "PG_LATE_FEE_STRICT",
            "version": 1,
            "name": "违约金不低于千分之三",
            "rule_type": "CLAUSE_VARIATION",
            "execution_mode": "DETERMINISTIC",
            "severity": "HIGH",
            "contract_types": ["*"],
            "parameters": {"clause_category": "LIABILITY", "must_contain": ["3‰"]},
            "status": "DRAFT",
        },
        tenant_id="tenant_alpha",
    )
    assert st.approve_rule(
        "PG_LATE_FEE_STRICT", "pg_approver", tenant_id="tenant_alpha"
    )
    assert len(st.get_rules(status="APPROVED", tenant_id="tenant_alpha")) == 1
    assert len(st.get_rules(status="APPROVED", tenant_id="tenant_beta")) == 0

    # 在真实 PG18 上完整执行 14 阶段审核管线
    engine = ReviewEngine(st)
    res = engine.review_result(
        golden_samples["dir"] / "mut_11_late_fee_change.docx",
        mode="COMBINED",
        tenant_id="tenant_alpha",
        created_by="pg_reviewer",
        idempotency_key="pg-idem-001",
    )
    assert res.status == "COMPLETED"
    assert len(res.stages) == 14
    assert any(
        i["rule_id"] == "PG_LATE_FEE_STRICT" and i["category"] == "RULE_VIOLATION"
        for i in res.issues
    )

    # 验证 PG18 幂等键查询与阶段持久化
    idem_hit = st.find_task_by_idempotency_key("pg-idem-001", tenant_id="tenant_alpha")
    assert idem_hit is not None and idem_hit["task_id"] == res.task_id
    assert (
        st.find_task_by_idempotency_key("pg-idem-001", tenant_id="tenant_beta") is None
    )
    pg_stages = st.get_stage_states(res.task_id)
    assert len(pg_stages) == 14

    # 验证 PG18 人工覆写服务与审计日志
    target_issue = res.issues[0]["issue_id"]
    ov = HumanOverrideService().apply_override(
        st,
        issue_id=target_issue,
        action="FALSE_POSITIVE",
        actor="pg_legal_lead",
        reason="PG18 真实集成复核通过",
        tenant_id="tenant_alpha",
    )
    assert ov["override_id"]
    assert len(st.list_human_overrides(res.task_id)) == 1

    # 验证 PG18 标准合同持久化与租户隔离
    st.upsert_canonical_contract(
        CanonicalContract(
            contract_id="PG_CC_ALPHA_1",
            tenant_id="tenant_alpha",
            contract_number=ContractNumber.parse("ALPHA-20261107-001"),
            title="Alpha 专利代理合同",
            parties=[ContractParty(role="PARTY_A", name="Alpha 科技公司")],
            amounts=ContractAmountSummary.from_values(total="36000.00"),
        ).to_dict()
    )
    alpha_items, alpha_total = st.list_canonical_contracts(tenant_id="tenant_alpha")
    beta_items, beta_total = st.list_canonical_contracts(tenant_id="tenant_beta")
    assert alpha_total == 1 and len(alpha_items) == 1
    assert beta_total == 0 and len(beta_items) == 0

    # 验证 PG18 async_jobs 幂等入队与 FOR UPDATE SKIP LOCKED 认领
    jid_1, created_1 = st.enqueue_async_job(
        {
            "job_type": "CONTRACT_REVIEW",
            "payload": {"sample": "mut_11"},
            "tenant_id": "tenant_alpha",
            "idempotency_key": "job-idem-1",
        }
    )
    jid_2, created_2 = st.enqueue_async_job(
        {
            "job_type": "CONTRACT_REVIEW",
            "payload": {"sample": "mut_11"},
            "tenant_id": "tenant_alpha",
            "idempotency_key": "job-idem-1",
        }
    )
    assert created_1 is True and created_2 is False
    assert jid_1 == jid_2

    claimed = st.claim_next_async_job()
    assert claimed is not None
    assert claimed["job_id"] == jid_1
    assert claimed["status"] == "RUNNING"
    assert claimed["attempts"] == 1
    assert st.claim_next_async_job() is None

    st.update_async_job(jid_1, status="SUCCEEDED", progress=100, result={"ok": True})
    done_job = st.get_async_job(jid_1, tenant_id="tenant_alpha")
    assert done_job is not None and done_job["status"] == "SUCCEEDED"
    assert st.get_async_job(jid_1, tenant_id="tenant_beta") is None
