# -*- coding: utf-8 -*-
"""Phase 11-R / Phase 12-R: Storage Parity Behavioral Contract Test.

严格验证 SQLite (Storage) 与 PostgreSQL (PgVectorStorage) 在关键安全与生命周期契约上的一致性：
1. 普通导入零信任：即使传入 approval_status='APPROVED'，落库状态必须为 PENDING_REVIEW (P0-01)；
2. 乐观锁 CAS 并发保护：版本冲突时必须抛出 OptimisticLockConflictError (P0-02)；
3. 严格租户隔离：跨租户查询坚决返回 None；
4. 审批与撤销生命周期：未审批或撤销后，approved_only 检索坚决不可见；
5. 文档失效级联阻断：文档置为 SUPERSEDED / ARCHIVED 时，旗下条款自动在检索中失效。
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
import pytest

from contract_kb.infrastructure.persistence.sqlite_storage import Storage
from contract_kb.infrastructure.persistence.postgres_storage import PgVectorStorage
from contract_kb.infrastructure.persistence.errors import OptimisticLockConflictError

PG_DSN = os.environ.get(
    "CONTRACT_KB_TEST_PG_DSN", "postgresql://postgres:postgres@localhost:5432/postgres"
)


def _is_pg_reachable(dsn: str) -> bool:
    try:
        import psycopg2

        conn = psycopg2.connect(dsn, connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


def _run_lifecycle_and_security_parity_suite(
    st: Storage | PgVectorStorage, tenant: str
) -> None:
    """在指定存储后端上执行全套一致性契约检验。"""
    doc_id = f"doc_{uuid.uuid4().hex[:8]}"
    clause_id = f"c_{uuid.uuid4().hex[:8]}"

    # Contract 1: 普通导入零信任 (P0-01) - 外部 APPROVED 不得直接生效
    doc = {
        "template_id": doc_id,
        "source_file": "template.docx",
        "sha256": "h" * 64,
        "template_version": "v1",
        "status": "ACTIVE",
        "document_type": "contract_template",
        "clauses": [
            {
                "clause_id": clause_id,
                "original_text": "乙方应严格履行保密义务，不得泄露商业秘密。",
                "clause_category": "CONFIDENTIALITY",
                "approval_status": "APPROVED",  # 尝试绕过
            }
        ],
    }
    did, is_new = st.upsert_document(doc, tenant=tenant)
    assert is_new is True

    c_init = st.get_clause(clause_id, tenant_id=tenant)
    assert c_init is not None
    assert c_init["approval_status"] == "PENDING_REVIEW", (
        "导入不可直接授予 APPROVED 权限"
    )

    # Contract 2: 严格租户隔离
    c_other = st.get_clause(clause_id, tenant_id=f"other_{tenant}")
    assert c_other is None, "跨租户查询必须隔离并返回 None"

    # Contract 3: 未经正式审批，approved_only 检索不可见
    hits_unapproved = st.search("保密义务", approved_only=True, tenant_id=tenant)
    assert not any(h["clause_id"] == clause_id for h in hits_unapproved)

    # Contract 4: 乐观锁 CAS 并发校验 (P0-02) - 期望错误修订号必须冲突
    with pytest.raises(OptimisticLockConflictError):
        st.approve_knowledge_version(
            tenant_id=tenant,
            document_id=did,
            approver_id="legal_admin",
            expected_revision=999,  # 错误的版本号
        )

    # Contract 5: 正确修订号审批通过并生成审计
    cur_doc = st.get_document(did, tenant_id=tenant)
    curr_rev = cur_doc["revision"]
    appr_res = st.approve_knowledge_version(
        tenant_id=tenant,
        document_id=did,
        approver_id="legal_admin",
        expected_revision=curr_rev,
    )
    assert appr_res["status"] == "APPROVED"
    assert appr_res["revision"] == curr_rev + 1

    # Contract 6: 审批后 approved_only 成功可见
    hits_approved = st.search("保密义务", approved_only=True, tenant_id=tenant)
    assert any(h["clause_id"] == clause_id for h in hits_approved)

    # Contract 7: 撤销审批乐观锁与检索屏蔽
    cur_doc = st.get_document(did, tenant_id=tenant)
    curr_rev2 = cur_doc["revision"]
    with pytest.raises(OptimisticLockConflictError):
        st.revoke_knowledge_version(
            tenant_id=tenant,
            document_id=did,
            revoker_id="legal_admin",
            expected_revision=curr_rev2 + 5,
        )

    rev_res = st.revoke_knowledge_version(
        tenant_id=tenant,
        document_id=did,
        revoker_id="legal_admin",
        expected_revision=curr_rev2,
    )
    assert rev_res["status"] == "REVOKED"
    hits_revoked = st.search("保密义务", approved_only=True, tenant_id=tenant)
    assert not any(h["clause_id"] == clause_id for h in hits_revoked)

    # Contract 8: 文档生命周期失效级联 (SUPERSEDED 阻断)
    # 重新审批
    cur_doc = st.get_document(did, tenant_id=tenant)
    st.approve_knowledge_version(
        tenant_id=tenant,
        document_id=did,
        approver_id="legal_admin",
        expected_revision=cur_doc["revision"],
    )
    assert any(
        h["clause_id"] == clause_id
        for h in st.search("保密义务", approved_only=True, tenant_id=tenant)
    )

    # 置为 SUPERSEDED 后自动被排除
    st.set_document_status(did, "SUPERSEDED", tenant_id=tenant)
    hits_superseded = st.search("保密义务", approved_only=True, tenant_id=tenant)
    assert not any(h["clause_id"] == clause_id for h in hits_superseded)


def test_sqlite_storage_security_parity_contract(tmp_path: Path) -> None:
    """验证 SQLite 存储引擎完全遵循双库安全契约。"""
    st = Storage(tmp_path / "sqlite_parity.db")
    try:
        _run_lifecycle_and_security_parity_suite(
            st, tenant=f"tenant_sqlite_{uuid.uuid4().hex[:6]}"
        )
    finally:
        st.close()


def test_postgres_storage_security_parity_contract() -> None:
    """验证 PostgreSQL (PgVectorStorage) 存储引擎完全遵循双库安全契约。"""
    if not _is_pg_reachable(PG_DSN):
        pytest.skip(f"PostgreSQL 实例未就绪 ({PG_DSN})，跳过 PG 物理一致性测试")

    st = PgVectorStorage(PG_DSN)
    try:
        _run_lifecycle_and_security_parity_suite(
            st, tenant=f"tenant_pg_{uuid.uuid4().hex[:6]}"
        )
    finally:
        st.close()
