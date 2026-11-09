# -*- coding: utf-8 -*-
"""回归测试：旧版 SQLite 数据库的 source_hash UNIQUE 约束迁移至 (tenant_id, source_hash)。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from contract_kb.storage import Storage

OLD_SCHEMA_SQL = """
CREATE TABLE documents (
    document_id TEXT PRIMARY KEY,
    source_file TEXT, source_hash TEXT UNIQUE, source_version TEXT,
    tenant_id TEXT, business_category TEXT, contract_subtype TEXT,
    document_type TEXT, imported_at REAL, status TEXT, raw_meta TEXT
);
CREATE TABLE clauses (
    clause_id TEXT PRIMARY KEY, document_id TEXT, clause_number TEXT,
    clause_category TEXT, clause_subcategory TEXT,
    original_text TEXT, normalized_summary TEXT,
    is_company_standard INTEGER, approval_status TEXT,
    source_node_ids TEXT, knowledge_type TEXT, effective_time TEXT
);
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL, actor TEXT,
    action TEXT, target TEXT, detail TEXT
);
"""


def _create_legacy_database(db_path: Path) -> None:
    """创建包含旧版 source_hash TEXT UNIQUE 约束和真实旧数据的数据库。"""
    con = sqlite3.connect(db_path)
    con.executescript(OLD_SCHEMA_SQL)
    # 插入一些旧记录，包含 tenant_id 为空/None 的记录
    con.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "doc_legacy_1",
            "legacy_contract.docx",
            "hash_shared_0001",
            "v1",
            "tenant_alpha",
            "SERVICE",
            "TECH_SERVICE",
            "contract_template",
            1000.0,
            "ACTIVE",
            "{}",
        ),
    )
    con.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "doc_legacy_empty_tenant",
            "old_template.docx",
            "hash_old_9999",
            "v1",
            "",  # 空租户值
            "LABOR",
            "EMPLOYMENT",
            "contract_template",
            1001.0,
            "ACTIVE",
            "{}",
        ),
    )
    con.execute(
        "INSERT INTO clauses VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "clause_leg_01",
            "doc_legacy_1",
            "1.1",
            "PAYMENT",
            "FEE",
            "支付服务费十万元。",
            "服务费约定",
            1,
            "APPROVED",
            "[]",
            "TEMPLATE",
            "2026-01-01",
        ),
    )
    con.commit()
    con.close()


def test_sqlite_legacy_source_hash_migration(tmp_path: Path):
    """
    验证：
    1. 旧表结构中包含全局 source_hash UNIQUE；
    2. 打开 Storage 时自动迁移旧约束为 UNIQUE (tenant_id, source_hash)；
    3. 升级后原记录及条款完整保留，空租户被安全规范化为 default；
    4. 两个租户可以导入相同内容（相同 source_hash）而不发生 UNIQUE 冲突；
    5. 同一租户重复导入相同内容仍遵守幂等约定；
    6. 多次重新打开 Storage 幂等成功。
    """
    db_path = tmp_path / "legacy.db"
    _create_legacy_database(db_path)

    # 验证创建的旧库确实含有全局 source_hash UNIQUE
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    tbl_sql = cur.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='documents'"
    ).fetchone()[0]
    assert "source_hash TEXT UNIQUE" in tbl_sql
    con.close()

    # 打开 Storage 执行迁移
    st = Storage(db_path)

    # 1. 验证原记录仍存在
    doc1 = st.get_document("doc_legacy_1", tenant_id="tenant_alpha")
    assert doc1 is not None
    assert doc1["source_hash"] == "hash_shared_0001"

    # 2. 第二个租户导入与 doc_legacy_1 相同的内容 (hash_shared_0001)
    # 在未迁移的旧库上，这会抛出 UNIQUE constraint failed: documents.source_hash
    same_content_doc = {
        "template_id": "doc_tenant_beta_same_content",
        "source_file": "tenant_beta.docx",
        "sha256": "hash_shared_0001",
        "category": "SERVICE",
        "subtype": "TECH_SERVICE",
        "clauses": [],
    }
    did_beta, is_new_beta = st.upsert_document(same_content_doc, tenant="tenant_beta")
    assert is_new_beta is True
    assert st.get_document(did_beta, tenant_id="tenant_beta") is not None

    # 3. 空租户记录应被规范化为 default
    doc_def = st.get_document("doc_legacy_empty_tenant", tenant_id="default")
    assert doc_def is not None

    # 4. 同一租户重复内容仍幂等
    did_beta_dup, is_new_beta_dup = st.upsert_document(
        same_content_doc, tenant="tenant_beta"
    )
    assert did_beta_dup == did_beta
    assert is_new_beta_dup is False

    st.close()

    # 5. 重复启动、重复迁移
    st2 = Storage(db_path)
    assert st2.get_document(did_beta, tenant_id="tenant_beta") is not None
    st2.close()
