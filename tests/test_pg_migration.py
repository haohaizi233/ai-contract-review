# -*- coding: utf-8 -*-
"""test_pg_migration / test_pg_storage

- 迁移 SQL 语法校验：pglast（PostgreSQL 解析器）在无 PG 实例环境下真实解析 SQL；
- 行为测试：配置 CONTRACT_KB_TEST_PG_DSN 后启用（独立测试库），否则 skip 并如实标注。
"""

import os
import re
from pathlib import Path

import pytest

MIGRATION = (
    Path(__file__).resolve().parent.parent / "migrations" / "001_init_pgvector.sql"
)
PG_DSN = os.environ.get("CONTRACT_KB_TEST_PG_DSN", "")


# ---------------- 迁移 SQL 静态校验（真实解析） ----------------


def test_migration_file_exists():
    assert MIGRATION.exists()


def test_migration_no_hash_comments():
    """PostgreSQL 不支持 # 注释（原缺陷）。"""
    text = MIGRATION.read_text(encoding="utf-8")
    for i, line in enumerate(text.splitlines(), 1):
        assert not line.strip().startswith("#"), f"第{i}行含非法 # 注释"


def test_migration_parses_with_pglast():
    """用 PostgreSQL 语法解析器真实校验迁移脚本。"""
    pglast = pytest.importorskip("pglast")
    sql = MIGRATION.read_text(encoding="utf-8")
    try:
        stmts = pglast.parse_sql(sql)
    except Exception as e:
        pytest.fail(f"迁移 SQL 解析失败：{e}")
    assert len(stmts) >= 10


def test_migration_vector_dim_matches_embedding():
    """向量维度必须与默认 Embedding 维度一致。"""
    from contract_kb import config

    text = MIGRATION.read_text(encoding="utf-8")
    m = re.search(r"vec vector\((\d+)\)", text)
    assert m, "缺少 vec 列定义"
    assert int(m.group(1)) == config.EMBEDDING_DIM, (
        f"向量维度 {m.group(1)} != EMBEDDING_DIM {config.EMBEDDING_DIM}"
    )


def test_migration_has_tenant_isolation():
    text = MIGRATION.read_text(encoding="utf-8")
    assert "tenant_id" in text
    assert "idx_docs_tenant" in text


def test_migration_rule_versioning():
    text = MIGRATION.read_text(encoding="utf-8")
    assert "PRIMARY KEY (rule_id, version)" in text
    assert "effective_until" in text


# ---------------- PG 行为测试（需独立测试库 DSN） ----------------

pytestmark_pg = pytest.mark.skipif(
    not PG_DSN, reason="CONTRACT_KB_TEST_PG_DSN 未配置（无 PG 实例，行为未验证）"
)


@pytest.fixture(scope="module")
def pg_storage():
    if not PG_DSN:
        pytest.skip("CONTRACT_KB_TEST_PG_DSN 未配置（无 PG 实例，行为未验证）")
    from contract_kb.storage_pg import PgVectorStorage

    st = PgVectorStorage(PG_DSN)
    # 幂等预清理固定 ID/固定哈希样例（保证跨运行可重复执行，不改变用例断言）
    for did in ("pg_test_doc_1", "pg_test_doc_2"):
        st.delete_document(did)
    with st.conn.cursor() as cur:
        cur.execute(
            "DELETE FROM documents WHERE source_hash IN (%s, %s)", ("a" * 64, "b" * 64)
        )
    st.conn.commit()
    st.rebuild_index()
    yield st
    st.close()


@pytest.fixture(scope="module")
def sample_doc():
    return {
        "template_id": "pg_test_doc_1",
        "source_file": "synth.docx",
        "sha256": "a" * 64,
        "template_version": "v1",
        "category": "PATENT",
        "subtype": "PATENT_ANNUAL_FEE",
        "document_type": "contract_template",
        "clause_count": 2,
        "clauses": [
            {
                "clause_id": "pc1",
                "clause_number": "4.1",
                "clause_category": "FEE_AND_PAYMENT",
                "clause_subcategory": None,
                "original_text": "甲方向乙方支付费用总额为人民币 壹仟圆整（¥1,000 元）。",
                "normalized_summary": "[FEE] 金额=1000",
                "is_company_standard": True,
                "approval_status": "PENDING_REVIEW",
                "source_node_ids": ["n1"],
            },
            {
                "clause_id": "pc2",
                "clause_number": "6.2",
                "clause_category": "LIABILITY",
                "clause_subcategory": "LIQUIDATED_DAMAGES",
                "original_text": "逾期支付的甲方应按逾期付款金额日率3‰向乙方支付违约金。",
                "normalized_summary": "[LIAB] 比率=3‰",
                "is_company_standard": True,
                "approval_status": "PENDING_REVIEW",
                "source_node_ids": ["n2"],
            },
        ],
    }


@pytestmark_pg
def test_pg_migrate_idempotent(pg_storage):
    pg_storage.migrate()
    pg_storage.migrate()  # 二次执行不报错


@pytestmark_pg
def test_pg_upsert_and_idempotent(pg_storage, sample_doc):
    did, new1 = pg_storage.upsert_document(sample_doc)
    _, new2 = pg_storage.upsert_document(sample_doc)
    assert new1 is True and new2 is False
    tenant = pg_storage.get_document(did)["tenant_id"]
    pg_storage.approve_knowledge_version(tenant, did, "legal_admin")


@pytestmark_pg
def test_pg_incremental_update(pg_storage, sample_doc):
    updated = dict(sample_doc, sha256="b" * 64, template_id="pg_test_doc_2")
    did, new = pg_storage.upsert_document(updated)
    assert new is True
    tenant = pg_storage.get_document(did)["tenant_id"]
    pg_storage.approve_knowledge_version(tenant, did, "legal_admin")


@pytestmark_pg
def test_pg_search_returns_results(pg_storage):
    hits = pg_storage.search("违约金 逾期", top_k=5)
    assert hits
    for h in hits:
        assert h["clause_id"] and h["text"]


@pytestmark_pg
def test_pg_vector_dimension(pg_storage):
    with pg_storage.conn.cursor() as cur:
        cur.execute("SELECT vec FROM embeddings LIMIT 1")
        row = cur.fetchone()
    if row:
        import json
        from contract_kb import config

        assert len(json.loads(row[0])) == config.EMBEDDING_DIM


@pytestmark_pg
def test_pg_rollback(pg_storage):
    ok = pg_storage.rollback_to_version("pg_test_doc_2", "a" * 64)
    assert ok


@pytestmark_pg
def test_pg_delete_cascade(pg_storage):
    pg_storage.delete_document("pg_test_doc_1")
    with pg_storage.conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM clauses WHERE document_id='pg_test_doc_1'")
        assert cur.fetchone()[0] == 0


# ---------------- SQLite vs PostgreSQL 行为对比（无 PG 时输出对比框架） ----------------


def test_sqlite_pg_interface_parity():
    """两实现必须暴露同一核心方法集（接口一致性，不要求 PG 在线）。"""
    from contract_kb.storage import Storage
    from contract_kb.storage_pg import PgVectorStorage

    required = [
        "upsert_document",
        "search",
        "rebuild_index",
        "delete_document",
        "rollback_to_version",
        "save_rule",
        "approve_rule",
        "get_rules",
        "list_documents",
        "log_audit",
        "feedback_issue",
    ]
    for m in required:
        assert hasattr(Storage, m), f"SQLite 缺 {m}"
        assert hasattr(PgVectorStorage, m), f"PgVector 缺 {m}"
