# -*- coding: utf-8 -*-
"""第三轮整改回归（缺陷 3/4）：PostgreSQL + pgvector 真实实例行为验证。

- 静态部分（无需 PG）：全部迁移脚本 pglast 解析；向量检索必须 SQL 相似度排序 + LIMIT
  （回归防护：不得退化为全量载入 NumPy 计算）；
- 行为部分：需 CONTRACT_KB_TEST_PG_DSN 指向**真实 PG + pgvector** 测试库，
  覆盖 迁移 / 导入 / 检索（排序与 LIMIT）/ 回滚 / 维度校验 / 索引版本 / 租户列 / 格式标准。
"""

import hashlib
import inspect
import os
import uuid
from pathlib import Path

import numpy as np
import pytest

from contract_kb.storage import EmbeddingConfigMismatch

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
PG_DSN = os.environ.get("CONTRACT_KB_TEST_PG_DSN", "")


def _sha(*parts: str) -> str:
    """运行期唯一 source_hash（避免跨运行幂等缓存干扰行为断言）。"""
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


# ---------------- 静态校验（无 PG 也执行） ----------------


def test_all_migrations_parse_with_pglast():
    pglast = pytest.importorskip("pglast")
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    assert len(files) >= 2, "应包含 001 初始化 + 002 索引版本/租户/格式标准迁移"
    for f in files:
        stmts = pglast.parse_sql(f.read_text(encoding="utf-8"))
        assert stmts, f"{f.name} 无可解析语句"


def test_migration_002_has_tenant_and_meta_tables():
    text = (MIGRATIONS_DIR / "002_embedding_meta_task_tenant_format_std.sql").read_text(
        encoding="utf-8"
    )
    assert "tenant_id" in text and "created_by" in text
    assert "embedding_index_meta" in text
    assert "format_standards" in text


def test_pg_vector_search_pushes_order_and_limit_to_sql():
    """向量检索必须在 SQL 内完成相似度排序与截断（pgvector 运算符 + ORDER BY + LIMIT）。"""
    from contract_kb import storage_pg

    src = inspect.getsource(storage_pg.PgVectorStorage._vector_search)
    assert "vec <=>" in src, "必须使用 pgvector 余弦距离运算符在 SQL 内排序"
    assert "ORDER BY" in src and "LIMIT" in src
    assert "np.stack" not in src and "argsort" not in src, (
        "禁止全量载入向量在应用层排序"
    )


# ---------------- 行为测试（真实 PG + pgvector） ----------------

pytestmark = pytest.mark.skipif(
    not PG_DSN, reason="CONTRACT_KB_TEST_PG_DSN 未配置（真实 PG 行为未验证）"
)


@pytest.fixture(scope="module")
def pg():
    from contract_kb.storage_pg import PgVectorStorage

    st = PgVectorStorage(PG_DSN)
    yield st
    st.close()


def _doc(
    suffix: str, sha: str, texts: list[str], source_file: str = "pg_synth.docx"
) -> dict:
    return {
        "template_id": f"pgs_{suffix}",
        "source_file": source_file,
        "sha256": sha,
        "template_version": "v1",
        "category": "PATENT",
        "subtype": "PATENT_ANNUAL_FEE",
        "document_type": "contract_template",
        "clause_count": len(texts),
        "clauses": [
            {
                "clause_id": f"pgs_{suffix}_c{i}",
                "clause_number": f"4.{i}",
                "clause_category": "LIABILITY" if "违约" in t else "FEE_AND_PAYMENT",
                "clause_subcategory": None,
                "original_text": t,
                "normalized_summary": "",
                "is_company_standard": True,
                "approval_status": "PENDING_REVIEW",
                "source_node_ids": [f"n{i}"],
            }
            for i, t in enumerate(texts)
        ],
    }


def test_pg_migrations_idempotent(pg):
    pg.migrate()
    pg.migrate()


def test_pg_import_idempotent_and_incremental(pg):
    sid = uuid.uuid4().hex[:8]
    d = _doc(sid, _sha(sid, "a"), ["逾期支付的应按日率3‰支付违约金。"])
    _, new1 = pg.upsert_document(d, tenant="t1")
    _, new2 = pg.upsert_document(d, tenant="t1")
    assert new1 is True and new2 is False, "同哈希导入必须幂等"
    d2 = _doc(
        sid,
        _sha(sid, "b"),
        ["逾期支付的应按日率3‰支付违约金。"],
        source_file=f"pg_synth_{sid}.docx",
    )
    d2["template_id"] = f"pgs_{sid}_v2"
    for i, c in enumerate(d2["clauses"]):
        c["clause_id"] = f"pgs_{sid}_v2_c{i}"
    _, new3 = pg.upsert_document(d2, tenant="t1")
    assert new3 is True, "新哈希必须作为新版本导入"
    pg.delete_document(f"pgs_{sid}")
    pg.delete_document(f"pgs_{sid}_v2")


def test_pg_vector_search_sql_order_and_limit(pg):
    sid = uuid.uuid4().hex[:8]
    texts = [
        "逾期支付违约金按日率3‰计算。",
        "逾期付款违约金比率为日3‰。",
        "保密义务条款，双方不得泄露。",
        "发票开具与税率约定。",
    ]
    d = _doc(sid, _sha(sid, "c"), texts)
    pg.upsert_document(d, tenant="t1")
    hits = pg.search("违约金 逾期", top_k=2, rerank=False)
    assert len(hits) <= 2, "SQL LIMIT 必须生效"
    assert hits, "应有检索命中"
    for h in hits:
        assert h["clause_id"] and h["text"]
    # 语义命中应排前（SQL 相似度排序）
    assert any("违约金" in h["text"] for h in hits)
    pg.delete_document(f"pgs_{sid}")


def test_pg_tenant_filtering(pg):
    sid = uuid.uuid4().hex[:8]
    d = _doc(sid, _sha(sid, "d"), ["TenantB 专属条款：保密义务。"])
    pg.upsert_document(d, tenant="t2")
    hits_b = pg.search("保密义务", top_k=5, rerank=False, tenant_id="t2")
    hits_a = pg.search("保密义务", top_k=5, rerank=False, tenant_id="t1")
    assert any(h["document_id"] == f"pgs_{sid}" for h in hits_b)
    assert all(h["document_id"] != f"pgs_{sid}" for h in hits_a), "跨租户必须零命中"
    docs_b = pg.list_documents(tenant_id="t2")
    assert any(x["document_id"] == f"pgs_{sid}" for x in docs_b)
    assert all(
        x["document_id"] != f"pgs_{sid}" for x in pg.list_documents(tenant_id="t1")
    )
    pg.delete_document(f"pgs_{sid}")


def test_pg_index_version_and_rebuild(pg):
    meta1 = pg.get_index_version()
    assert meta1 and meta1["dim"] == pg.emb.dim
    v2 = pg.rebuild_index()
    assert v2 == meta1["version"] + 1
    meta2 = pg.get_index_version()
    assert meta2["version"] == v2 and meta2["status"] == "ACTIVE"


def test_pg_dimension_validation_fail_closed(pg):
    class _Broken:
        name = "local_hash_256"  # 与索引登记一致，专测写入路径维度校验
        dim = 256

        def embed(self, texts):
            return np.ones((len(texts), 4), dtype=np.float32)

    from contract_kb.storage_pg import PgVectorStorage

    sid = uuid.uuid4().hex[:8]
    broken = PgVectorStorage(PG_DSN, emb=_Broken())
    with pytest.raises(EmbeddingConfigMismatch):
        broken.upsert_document(_doc(sid, _sha(sid, "e"), ["维度校验测试条款。"]))
    broken.close()


def test_pg_rollback_and_delete(pg):
    sid = uuid.uuid4().hex[:8]
    src = f"pg_roll_{sid}.docx"
    h1, h2 = _sha(sid, "f"), _sha(sid, "1")
    v1 = _doc(sid, h1, ["回滚测试条款 v1：三个工作日内支付。"], source_file=src)
    v1["template_id"] = f"pgs_{sid}_v1"
    v1["clauses"][0]["clause_id"] = f"pgs_{sid}_v1_c0"
    v2 = _doc(sid, h2, ["回滚测试条款 v2：三十个工作日内支付。"], source_file=src)
    v2["template_id"] = f"pgs_{sid}_v2"
    v2["clauses"][0]["clause_id"] = f"pgs_{sid}_v2_c0"
    did_v1, _ = pg.upsert_document(v1, tenant="t1")
    did_v2, _ = pg.upsert_document(v2, tenant="t1")
    assert pg.rollback_to_version(did_v2, h1, tenant_id="t1") is False, (
        "未审批历史条款不得通过跨版本回滚激活"
    )
    pg.approve_knowledge_version("t1", did_v1, "legal_admin")
    pg.approve_knowledge_version("t1", did_v2, "legal_admin")
    assert pg.rollback_to_version(did_v2, h1, tenant_id="t1") is True, (
        "已审批历史版本回滚必须成功"
    )
    pg.delete_document(f"pgs_{sid}_v1")
    pg.delete_document(f"pgs_{sid}_v2")


def test_pg_task_tenant_and_issue_ownership(pg):
    tid = "pgt_" + uuid.uuid4().hex[:8]
    pg.save_task(
        tid,
        "doc_x",
        "COMBINED",
        "COMPLETED",
        {"k": "v"},
        tenant_id="t1",
        created_by="rev_a",
    )
    task = pg.get_task(tid)
    assert task["tenant_id"] == "t1" and task["created_by"] == "rev_a"
    pg.save_issues(
        tid,
        [
            {
                "issue_id": tid + "_i1",
                "rule_id": "R",
                "severity": "LOW",
                "category": "TEST",
                "title": "t",
                "description": "d",
                "requires_human_review": False,
            }
        ],
    )
    issue = pg.get_issue(tid + "_i1")
    assert issue["task_tenant_id"] == "t1" and issue["task_created_by"] == "rev_a"


def test_pg_format_standards_versioned_and_approved(pg):
    sid = "fmt_" + uuid.uuid4().hex[:8]
    v1 = pg.save_format_standard(sid, {"body_font_size_pt": 12}, "admin_f")
    assert v1["status"] == "DRAFT"
    assert pg.get_active_format_standard(sid) is None
    assert pg.approve_format_standard(sid, v1["version"], "legal_f")
    active = pg.get_active_format_standard(sid)
    assert active["version"] == v1["version"] and active["approved_by"] == "legal_f"
    v2 = pg.save_format_standard(sid, {"body_font_size_pt": 14}, "admin_f")
    assert v2["version"] == v1["version"] + 1
    assert pg.approve_format_standard(sid, v2["version"], "legal_f")
    assert pg.get_active_format_standard(sid)["version"] == v2["version"]
    assert pg.get_format_standard(sid, v1["version"])["status"] == "SUPERSEDED"
