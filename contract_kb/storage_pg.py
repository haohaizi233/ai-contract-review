# -*- coding: utf-8 -*-
"""PostgreSQL + pgvector 存储适配器。

与 SQLite Storage 同接口（upsert_document / search / rebuild_index / delete_document /
rollback_to_version / get_document / find_active_template / get_document_clauses /
record_rule_execution / save_rule / approve_rule / feedback_issue ...），可通过
`storage.create_storage()` 直接注入替换。

设计要点：
- 向量检索采用 **pgvector SQL 相似度排序 + LIMIT**（vec <=> %s::vector ORDER BY ... LIMIT），
  不在应用层全量载入向量；
- 迁移脚本按 migrations/*.sql 顺序幂等执行；
- 写操作在异常时强制 rollback()，防止连接停留在 aborted transaction 状态；
- Embedding 维度校验与索引版本管理（embedding_index_meta），换模型必须 rebuild_index。
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .application.security.idempotency import IdempotencyOutcome, RequestIdentity
from .domain.models.execution import JobLease, JobOutcome
from .embeddings import EmbeddingProvider, get_embedding_provider
from .infrastructure.observability.redaction import redact_sensitive_text
from .storage_errors import EmbeddingConfigMismatch, OptimisticLockConflictError

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
MIGRATION_FILE = MIGRATIONS_DIR / "001_init_pgvector.sql"
_CLEAN_RE = re.compile(r"[\s\u3000，。；：、“”‘’（）()《》\-—_/%‰]")


def _norm_text(t: str) -> str:
    return _CLEAN_RE.sub("", t).lower()


class PgVectorStorage:
    """PostgreSQL + pgvector 实现。"""

    def __init__(
        self,
        dsn: str,
        emb: EmbeddingProvider | None = None,
        migrate: bool = True,
        rebuild_on_mismatch: bool = False,
    ):
        import psycopg2
        import psycopg2.extras

        self._psycopg2 = psycopg2
        self._lock = threading.RLock()
        self._tx_depth = 0
        self.dsn = dsn
        self.conn = psycopg2.connect(dsn)
        self.conn.autocommit = False
        self._col_cache: dict[str, set[str]] = {}
        try:
            with self.conn.cursor() as cur:
                cur.execute("SET ivfflat.probes = 100")
            self.conn.commit()
        except psycopg2.Error:
            self.conn.rollback()

        self.emb = emb or get_embedding_provider()
        if migrate:
            try:
                self.migrate()
            except Exception as e:
                if "vector" in str(e).lower() or "extension" in str(e).lower():
                    self.migrate_relational()
                else:
                    raise
        try:
            self._check_embedding_compat()
        except EmbeddingConfigMismatch:
            if not rebuild_on_mismatch:
                raise
            self.rebuild_index()

    def _commit(self) -> None:
        if self._tx_depth == 0:
            self.conn.commit()

    def _rollback(self) -> None:
        if self._tx_depth == 0:
            self.conn.rollback()

    def _savepoint_begin(self, sp_name: str) -> None:
        self._lock.acquire()
        self._tx_depth += 1
        with self.conn.cursor() as cur:
            cur.execute(f"SAVEPOINT {sp_name}")

    def _savepoint_commit(self, sp_name: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(f"RELEASE SAVEPOINT {sp_name}")
        if self._tx_depth <= 1:
            self.conn.commit()

    def _savepoint_rollback(self, sp_name: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
            cur.execute(f"RELEASE SAVEPOINT {sp_name}")
        if self._tx_depth <= 1:
            self.conn.rollback()

    def _savepoint_exit(self) -> None:
        self._tx_depth = max(0, self._tx_depth - 1)
        self._lock.release()

    def unit_of_work(self) -> Any:
        """创建支持嵌套 SAVEPOINT 的原子工作单元。"""
        from .infrastructure.persistence.uow import PostgresUnitOfWork

        return PostgresUnitOfWork(self)

    def _table_cols(self, table: str) -> set[str]:
        if table not in self._col_cache:
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = %s",
                    (table,),
                )
                self._col_cache[table] = {r[0] for r in cur.fetchall()}
        return self._col_cache[table]

    # ---------- 迁移 ----------
    def migrate(self) -> None:
        """按文件名顺序执行 migrations/*.sql（幂等脚本，可重复执行）。"""
        with self._lock:
            try:
                for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
                    sql = f.read_text(encoding="utf-8")
                    with self.conn.cursor() as cur:
                        cur.execute(sql)
                self._commit()
                self._col_cache.clear()
            except Exception:
                self._rollback()
                raise

    def migrate_relational(self, schema: str | None = None) -> None:
        """在未安装 pgvector 扩展的 PostgreSQL 实例上执行全部关系型表迁移（跳过 vector 列与索引）。"""
        if schema is not None and not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", schema):
            raise ValueError(f"非法 schema 标识符：{schema!r}")
        import pglast

        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    if schema:
                        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
                        cur.execute(f"SET search_path TO {schema}, public")
                    cur.execute(
                        "SELECT name FROM pg_available_extensions WHERE name IN ('vector', 'pg_trgm')"
                    )
                    avail_exts = {r[0] for r in cur.fetchall()}
                    has_vector = "vector" in avail_exts

                    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
                        raw_sql = f.read_text(encoding="utf-8")
                        for stmt in pglast.split(raw_sql):
                            s = stmt.strip()
                            if not s:
                                continue
                            low = s.lower()
                            if not has_vector:
                                if "extension" in low and "vector" in low:
                                    continue
                                if "embeddings" in low and (
                                    "create table" in low or "create index" in low
                                ):
                                    continue
                            if (
                                "extension" in low
                                and "pg_trgm" in low
                                and "pg_trgm" not in avail_exts
                            ):
                                continue
                            cur.execute(s)
                self._commit()
                self._col_cache.clear()
            except Exception:
                self._rollback()
                raise

    def _check_embedding_compat(self) -> None:
        """Embedding 维度/提供方校验（fail-closed）。"""
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    cur.execute(
                        "SELECT to_regclass('embeddings'), to_regclass('embedding_index_meta')"
                    )
                    emb_reg, meta_reg = cur.fetchone()
                    col_dim = None
                    if emb_reg is not None:
                        cur.execute(
                            "SELECT atttypmod FROM pg_attribute "
                            "WHERE attrelid='embeddings'::regclass "
                            "AND attname='vec' AND NOT attisdropped"
                        )
                        row = cur.fetchone()
                        col_dim = (
                            int(row[0]) if row and row[0] and int(row[0]) > 0 else None
                        )
                    meta = None
                    if meta_reg is not None:
                        cur.execute(
                            "SELECT provider, dim FROM embedding_index_meta "
                            "WHERE status='ACTIVE' ORDER BY version DESC LIMIT 1"
                        )
                        meta = cur.fetchone()
                self._commit()
            except Exception:
                self._rollback()
                raise

        if col_dim is not None and col_dim != int(self.emb.dim):
            raise EmbeddingConfigMismatch(
                f"PG 向量列维度 {col_dim} != 当前 Embedding 维度 {self.emb.dim}"
                f"（{self.emb.name}）。请调整迁移中的 vector(N) 并 rebuild_index。"
            )
        if meta and (meta[0] != self.emb.name or int(meta[1]) != int(self.emb.dim)):
            raise EmbeddingConfigMismatch(
                f"Embedding 配置与现有索引不一致：索引={meta[0]}/dim={meta[1]}，"
                f"当前={self.emb.name}/dim={self.emb.dim}。请调用 rebuild_index() 重建。"
            )

    def _validate_vec(self, vec: Any, context: str) -> np.ndarray:
        arr = np.asarray(vec, dtype=np.float32)
        if arr.ndim != 1 or arr.shape[0] != int(self.emb.dim):
            raise EmbeddingConfigMismatch(
                f"向量维度校验失败（{context}）：期望 dim={self.emb.dim}，实际 shape={arr.shape}"
            )
        return arr

    @staticmethod
    def _vec_literal(vec: np.ndarray) -> str:
        return "[" + ",".join(f"{float(x):.6f}" for x in vec.tolist()) + "]"

    def _ensure_index_meta(self, commit: bool = True) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT version, provider, dim FROM embedding_index_meta "
                "WHERE status='ACTIVE' ORDER BY version DESC LIMIT 1"
            )
            row = cur.fetchone()
            if not row:
                return self._bump_index_version(commit=commit)
            if row[1] != self.emb.name or int(row[2]) != int(self.emb.dim):
                raise EmbeddingConfigMismatch(
                    f"Embedding 配置与现有索引不一致：索引={row[1]}/dim={row[2]}，"
                    f"当前={self.emb.name}/dim={self.emb.dim}。请调用 rebuild_index() 重建。"
                )
            return int(row[0])

    def _bump_index_version(self, commit: bool = True) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE embedding_index_meta SET status='SUPERSEDED' WHERE status='ACTIVE'"
            )
            cur.execute(
                "INSERT INTO embedding_index_meta (provider, dim, model_spec, status) "
                "VALUES (%s,%s,%s,'ACTIVE')",
                (self.emb.name, int(self.emb.dim), getattr(self.emb, "name", "")),
            )
            cur.execute("SELECT MAX(version) FROM embedding_index_meta")
            v = cur.fetchone()[0]
        if commit:
            self._commit()
        return int(v)

    def get_index_version(self) -> dict | None:
        with self._lock:
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT version, provider, dim, model_spec, created_at, status "
                    "FROM embedding_index_meta WHERE status='ACTIVE' "
                    "ORDER BY version DESC LIMIT 1"
                )
                row = cur.fetchone()
            self._commit()
            if not row:
                return None
            return {
                "version": row[0],
                "provider": row[1],
                "dim": row[2],
                "model_spec": row[3],
                "created_at": row[4],
                "status": row[5],
            }

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # ---------- 导入（幂等 + 原子事务） ----------
    def upsert_document(self, doc: dict, tenant: str | None = None) -> tuple[str, bool]:
        effective_tenant = tenant if tenant is not None else "default"
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    if tenant is not None:
                        cur.execute(
                            "SELECT document_id FROM documents WHERE source_hash=%s AND tenant_id=%s",
                            (doc["sha256"], tenant),
                        )
                    else:
                        cur.execute(
                            "SELECT document_id FROM documents WHERE source_hash=%s",
                            (doc["sha256"],),
                        )
                    row = cur.fetchone()
                    if row:
                        self._commit()
                        return row[0], False

                    base_did = doc.get("template_id") or doc.get("document_id") or "doc_unknown"
                    cur.execute(
                        "SELECT tenant_id FROM documents WHERE document_id=%s",
                        (base_did,),
                    )
                    existing_id = cur.fetchone()
                    did = (
                        f"{effective_tenant}:{base_did}"
                        if existing_id and existing_id[0] != effective_tenant
                        else base_did
                    )
                    prefix_clause = did != base_did

                    cur.execute(
                        "INSERT INTO documents (document_id, source_file, source_hash, source_version,"
                        "tenant_id, business_category, contract_subtype, document_type, status, raw_meta)"
                        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            did,
                            doc["source_file"],
                            doc["sha256"],
                            doc.get("template_version", "v1"),
                            effective_tenant,
                            doc.get("category", ""),
                            doc.get("subtype", ""),
                            doc.get("document_type", "contract_template"),
                            doc.get("status", "ACTIVE"),
                            json.dumps(
                                {
                                    "clause_count": doc.get("clause_count", 0),
                                    "table_count": doc.get("table_count", 0),
                                    "paragraph_count": doc.get("paragraph_count", 0),
                                    "unhandled": doc.get("unhandled_structures", []),
                                },
                                ensure_ascii=False,
                            ),
                        ),
                    )
                    for c in doc.get("clauses", []):
                        cid = (
                            f"{effective_tenant}:{c['clause_id']}"
                            if prefix_clause
                            else c["clause_id"]
                        )
                        cur.execute(
                            "INSERT INTO clauses (clause_id, document_id, clause_number, clause_category,"
                            "clause_subcategory, original_text, normalized_summary, is_company_standard,"
                            "approval_status, source_node_ids, knowledge_type)"
                            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                            " ON CONFLICT (clause_id) DO UPDATE SET document_id=EXCLUDED.document_id,"
                            " original_text=EXCLUDED.original_text, normalized_summary=EXCLUDED.normalized_summary",
                            (
                                cid,
                                did,
                                c.get("clause_number"),
                                c.get("clause_category"),
                                c.get("clause_subcategory"),
                                c["original_text"],
                                c.get("normalized_summary", ""),
                                bool(c.get("is_company_standard")),
                                "PENDING_REVIEW",
                                json.dumps(
                                    c.get("source_node_ids", []), ensure_ascii=False
                                ),
                                c.get("knowledge_type") or "LAYER_A_RAW_CLAUSE",
                            ),
                        )
                self._index_clauses(did, commit=False)
                self._ensure_index_meta(commit=False)
                self._commit()
                return did, True
            except Exception:
                self._rollback()
                raise

    def _index_clauses(self, document_id: str, commit: bool = True) -> None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT to_regclass('embeddings')")
            if cur.fetchone()[0] is None:
                return
            cur.execute(
                "SELECT c.clause_id, c.original_text, d.business_category, d.contract_subtype, "
                "d.source_hash, c.approval_status, c.knowledge_type "
                "FROM clauses c JOIN documents d ON d.document_id=c.document_id "
                "WHERE c.document_id=%s",
                (document_id,),
            )
            rows = cur.fetchall()
        if not rows:
            return
        vecs = np.asarray(self.emb.embed([r[1] for r in rows]), dtype=np.float32)
        if vecs.shape != (len(rows), int(self.emb.dim)):
            raise EmbeddingConfigMismatch(
                f"Embedding 输出维度校验失败：期望 {(len(rows), int(self.emb.dim))}，实际 {vecs.shape}"
            )
        with self.conn.cursor() as cur:
            for (cid, text, bc, st, sh, app_status, ktype), vec in zip(rows, vecs):
                resolved_ktype = ktype if ktype else "TEMPLATE"
                chunk_id = f"{document_id}:{cid}"
                cur.execute(
                    "INSERT INTO embeddings (chunk_id, document_id, clause_id, business_category,"
                    "contract_subtype, knowledge_type, approved_status, source_hash, text, vec)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                    " ON CONFLICT (chunk_id) DO UPDATE SET text=EXCLUDED.text, vec=EXCLUDED.vec,"
                    " approved_status=EXCLUDED.approved_status, knowledge_type=EXCLUDED.knowledge_type",
                    (
                        chunk_id,
                        document_id,
                        cid,
                        bc,
                        st,
                        resolved_ktype,
                        app_status or "PENDING_REVIEW",
                        sh,
                        text,
                        self._vec_literal(self._validate_vec(vec, "index_write")),
                    ),
                )
        if commit:
            self._commit()

    def rebuild_index(self) -> int:
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('embeddings')")
                    has_emb = cur.fetchone()[0] is not None
                    if has_emb:
                        cur.execute("DELETE FROM embeddings")
                    cur.execute("SELECT document_id FROM documents")
                    ids = [r[0] for r in cur.fetchall()]
                if has_emb:
                    for did in ids:
                        self._index_clauses(did, commit=False)
                ver = self._bump_index_version(commit=False)
                self._commit()
                return ver
            except Exception:
                self._rollback()
                raise

    def delete_document(self, document_id: str) -> None:
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM documents WHERE document_id=%s", (document_id,)
                    )
                    cur.execute(
                        "INSERT INTO audit_log (actor, action, target, detail)"
                        " VALUES ('system','delete_document',%s,'')",
                        (document_id,),
                    )
                self._commit()
                self.rebuild_index()
            except Exception:
                self._rollback()
                raise

    def rollback_to_version(self, document_id: str, source_hash: str) -> bool:
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    cur.execute(
                        "SELECT source_file, tenant_id FROM documents WHERE source_hash=%s",
                        (source_hash,),
                    )
                    row = cur.fetchone()
                    if not row:
                        self._commit()
                        return False
                    cur.execute(
                        "UPDATE documents SET status='ACTIVE' WHERE source_hash=%s",
                        (source_hash,),
                    )
                    cur.execute(
                        "UPDATE documents SET status='SUPERSEDED' "
                        "WHERE source_file=%s AND source_hash<>%s AND tenant_id=%s",
                        (row[0], source_hash, row[1]),
                    )
                self._commit()
                self.rebuild_index()
                return True
            except Exception:
                self._rollback()
                raise

    def log_audit(self, actor: str, action: str, target: str, detail: str = "") -> None:
        safe_detail = redact_sensitive_text(detail, max_len=1000)
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO audit_log (actor, action, target, detail) VALUES (%s,%s,%s,%s)",
                        (actor, action, target, safe_detail),
                    )
                self._commit()
            except Exception:
                self._rollback()
                raise

    # ---------- 文档/条款仓储查询方法 ----------
    def get_document(
        self, document_id: str, tenant_id: str | None = None
    ) -> dict | None:
        with self._lock:
            sql = "SELECT * FROM documents WHERE document_id=%s"
            params: list[Any] = [document_id]
            if tenant_id is not None:
                sql += " AND tenant_id=%s"
                params.append(tenant_id)
            with self.conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                cols = [d[0] for d in cur.description] if cur.description else []
            self._commit()
            return dict(zip(cols, row)) if row else None

    def find_document_by_hash(
        self, source_hash: str, tenant_id: str | None = None
    ) -> dict | None:
        with self._lock:
            sql = "SELECT * FROM documents WHERE source_hash=%s"
            params: list[Any] = [source_hash]
            if tenant_id is not None:
                sql += " AND tenant_id=%s"
                params.append(tenant_id)
            with self.conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                cols = [d[0] for d in cur.description] if cur.description else []
            self._commit()
            return dict(zip(cols, row)) if row else None

    def list_active_document_hashes(
        self, tenant_id: str | None = None
    ) -> dict[str, str]:
        with self._lock:
            sql = "SELECT source_hash, source_file FROM documents WHERE status='ACTIVE'"
            params: list[Any] = []
            if tenant_id is not None:
                sql += " AND tenant_id=%s"
                params.append(tenant_id)
            with self.conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
            self._commit()
            return {r[0]: r[1] for r in rows if r[0]}

    def set_document_status(
        self, document_id: str, status: str, tenant_id: str | None = None
    ) -> bool:
        with self._lock:
            try:
                sql = "UPDATE documents SET status=%s WHERE document_id=%s"
                params: list[Any] = [status, document_id]
                if tenant_id is not None:
                    sql += " AND tenant_id=%s"
                    params.append(tenant_id)
                with self.conn.cursor() as cur:
                    cur.execute(sql, params)
                    updated = cur.rowcount
                self._commit()
                return bool(updated)
            except Exception:
                self._rollback()
                raise


    def authorized_bulk_import_approved(
        self,
        doc: dict,
        tenant: str | None = None,
        *,
        approver_id: str,
        approval_reason: str,
    ) -> tuple[str, bool]:
        """独立授权迁移命令 (PostgreSQL)：批量导入既有法务已批准标准模板，验证审批身份并写入正式审批记录。"""
        if not approver_id or not approver_id.strip():
            raise ValueError("授权导入必须指定合法 approver_id")
        if not approval_reason or not approval_reason.strip():
            raise ValueError("授权导入必须说明审批理由 approval_reason")
        effective_tenant = tenant if tenant is not None else "default"
        did, is_new = self.upsert_document(doc, tenant=effective_tenant)
        self.approve_knowledge_version(
            tenant_id=effective_tenant,
            document_id=did,
            approver_id=approver_id,
            approval_action="AUTHORIZE_HISTORICAL_IMPORT",
            approval_reason=approval_reason,
        )
        return did, is_new

    def approve_knowledge_version(
        self,
        tenant_id: str,
        document_id: str,
        approver_id: str,
        approval_action: str = "APPROVE",
        approval_reason: str = "",
        clause_ids: list[str] | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """统一知识审批写入口 (PostgreSQL)：具备租户隔离、CAS 乐观锁校验和审计留痕。"""
        with self._lock:
            try:
                doc = self.get_document(document_id, tenant_id=tenant_id)
                if not doc:
                    raise ValueError(
                        f"文档不存在或跨租户未授权：{document_id} (tenant={tenant_id})"
                    )
                if doc.get("status") != "ACTIVE":
                    raise ValueError(
                        f"仅 ACTIVE 状态文档允许审批知识版本，当前状态：{doc.get('status')}"
                    )

                current_rev = int(doc.get("revision") or 1)
                if expected_revision is not None and int(expected_revision) != current_rev:
                    raise OptimisticLockConflictError(
                        f"知识版本审批冲突：预期 revision={expected_revision}，当前数据库 revision={current_rev}"
                    )

                with self.conn.cursor() as cur:
                    if expected_revision is not None:
                        cur.execute(
                            "UPDATE documents SET revision = revision + 1 WHERE document_id=%s AND tenant_id=%s AND revision=%s",
                            (document_id, tenant_id, current_rev),
                        )
                        if cur.rowcount == 0:
                            raise OptimisticLockConflictError(
                                f"并发更新冲突：文档 {document_id} revision 已被修改"
                            )
                    else:
                        cur.execute(
                            "UPDATE documents SET revision = revision + 1 WHERE document_id=%s AND tenant_id=%s",
                            (document_id, tenant_id),
                        )
                    new_revision = current_rev + 1

                    params: list[Any] = [new_revision, document_id]
                    extra = ""
                    if clause_ids:
                        extra = " AND clause_id = ANY(%s)"
                        params.append(clause_ids)
                    cur.execute(
                        f"UPDATE clauses SET approval_status='APPROVED', revision=%s WHERE document_id=%s{extra}",
                        params,
                    )
                    affected = cur.rowcount

                    has_emb = self._has_embeddings_table()
                    if has_emb:
                        emb_params: list[Any] = [document_id]
                        emb_extra = ""
                        if clause_ids:
                            emb_extra = " AND clause_id = ANY(%s)"
                            emb_params.append(clause_ids)
                        cur.execute(
                            f"UPDATE embeddings SET approved_status='APPROVED' WHERE document_id=%s{emb_extra}",
                            emb_params,
                        )

                    now_ts = time.time()
                    detail = json.dumps(
                        {
                            "tenant_id": tenant_id,
                            "document_id": document_id,
                            "approver_id": approver_id,
                            "action": approval_action,
                            "reason": approval_reason,
                            "affected_clauses": affected,
                            "revision": new_revision,
                            "timestamp": now_ts,
                        },
                        ensure_ascii=False,
                    )
                    cur.execute(
                        "INSERT INTO audit_log (actor, action, target, detail) VALUES (%s,%s,%s,%s)",
                        (
                            approver_id,
                            "approve_knowledge_version",
                            document_id,
                            detail,
                        ),
                    )
                    cur.execute(
                        """
                        INSERT INTO knowledge_approval_records (
                            tenant_id, knowledge_id, knowledge_type, version, approver_id,
                            approval_action, approval_reason, clause_ids, created_at
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,to_timestamp(%s))
                        """,
                        (
                            tenant_id,
                            document_id,
                            doc.get("document_type", "TEMPLATE"),
                            f"rev_{new_revision}",
                            approver_id,
                            approval_action,
                            approval_reason,
                            json.dumps(clause_ids or []),
                            now_ts,
                        ),
                    )
                self._commit()
                return {
                    "tenant_id": tenant_id,
                    "document_id": document_id,
                    "approved_count": affected,
                    "approver_id": approver_id,
                    "status": "APPROVED",
                    "revision": new_revision,
                }
            except Exception:
                self._rollback()
                raise

    def revoke_knowledge_version(
        self,
        tenant_id: str,
        document_id: str,
        revoker_id: str,
        revoke_reason: str = "",
        clause_ids: list[str] | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """统一撤销知识审批 (PostgreSQL)：撤销后立即丧失权威依据资格，支持 CAS 并发校验。"""
        with self._lock:
            try:
                doc = self.get_document(document_id, tenant_id=tenant_id)
                if not doc:
                    raise ValueError(
                        f"文档不存在或跨租户未授权：{document_id} (tenant={tenant_id})"
                    )

                current_rev = int(doc.get("revision") or 1)
                if expected_revision is not None and int(expected_revision) != current_rev:
                    raise OptimisticLockConflictError(
                        f"知识版本撤销冲突：预期 revision={expected_revision}，当前数据库 revision={current_rev}"
                    )

                with self.conn.cursor() as cur:
                    if expected_revision is not None:
                        cur.execute(
                            "UPDATE documents SET revision = revision + 1 WHERE document_id=%s AND tenant_id=%s AND revision=%s",
                            (document_id, tenant_id, current_rev),
                        )
                        if cur.rowcount == 0:
                            raise OptimisticLockConflictError(
                                f"并发更新冲突：文档 {document_id} revision 已被修改"
                            )
                    else:
                        cur.execute(
                            "UPDATE documents SET revision = revision + 1 WHERE document_id=%s AND tenant_id=%s",
                            (document_id, tenant_id),
                        )
                    new_revision = current_rev + 1

                    params: list[Any] = [new_revision, document_id]
                    extra = ""
                    if clause_ids:
                        extra = " AND clause_id = ANY(%s)"
                        params.append(clause_ids)
                    cur.execute(
                        f"UPDATE clauses SET approval_status='REVOKED', revision=%s WHERE document_id=%s{extra}",
                        params,
                    )
                    affected = cur.rowcount

                    has_emb = self._has_embeddings_table()
                    if has_emb:
                        emb_params: list[Any] = [document_id]
                        emb_extra = ""
                        if clause_ids:
                            emb_extra = " AND clause_id = ANY(%s)"
                            emb_params.append(clause_ids)
                        cur.execute(
                            f"UPDATE embeddings SET approved_status='REVOKED' WHERE document_id=%s{emb_extra}",
                            emb_params,
                        )

                    now_ts = time.time()
                    detail = json.dumps(
                        {
                            "tenant_id": tenant_id,
                            "document_id": document_id,
                            "revoker_id": revoker_id,
                            "action": "REVOKE",
                            "reason": revoke_reason,
                            "affected_clauses": affected,
                            "revision": new_revision,
                            "timestamp": now_ts,
                        },
                        ensure_ascii=False,
                    )
                    cur.execute(
                        "INSERT INTO audit_log (actor, action, target, detail) VALUES (%s,%s,%s,%s)",
                        (
                            revoker_id,
                            "revoke_knowledge_version",
                            document_id,
                            detail,
                        ),
                    )
                    cur.execute(
                        """
                        INSERT INTO knowledge_approval_records (
                            tenant_id, knowledge_id, knowledge_type, version, approver_id,
                            approval_action, approval_reason, clause_ids, created_at
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,to_timestamp(%s))
                        """,
                        (
                            tenant_id,
                            document_id,
                            doc.get("document_type", "TEMPLATE"),
                            f"rev_{new_revision}",
                            revoker_id,
                            "REVOKE",
                            revoke_reason,
                            json.dumps(clause_ids or []),
                            now_ts,
                        ),
                    )
                self._commit()
                return {
                    "tenant_id": tenant_id,
                    "document_id": document_id,
                    "revoked_count": affected,
                    "revoker_id": revoker_id,
                    "status": "REVOKED",
                    "revision": new_revision,
                }
            except Exception:
                self._rollback()
                raise

    def find_active_template(
        self,
        contract_subtype: str | None = None,
        business_category: str | None = None,
        tenant_id: str | None = None,
    ) -> dict | None:
        with self._lock:
            with self.conn.cursor() as cur:
                if contract_subtype:
                    sql = (
                        "SELECT document_id, source_file, source_hash, business_category, "
                        "contract_subtype, tenant_id FROM documents "
                        "WHERE contract_subtype=%s AND status='ACTIVE'"
                    )
                    params: list[Any] = [contract_subtype]
                    if tenant_id is not None:
                        sql += " AND tenant_id=%s"
                        params.append(tenant_id)
                    sql += " ORDER BY imported_at DESC LIMIT 1"
                    cur.execute(sql, params)
                    row = cur.fetchone()
                    if row:
                        cols = [d[0] for d in cur.description]
                        self._commit()
                        return dict(zip(cols, row))
                if business_category:
                    sql = (
                        "SELECT document_id, source_file, source_hash, business_category, "
                        "contract_subtype, tenant_id FROM documents "
                        "WHERE business_category=%s AND status='ACTIVE'"
                    )
                    params = [business_category]
                    if tenant_id is not None:
                        sql += " AND tenant_id=%s"
                        params.append(tenant_id)
                    sql += " ORDER BY imported_at DESC LIMIT 1"
                    cur.execute(sql, params)
                    row = cur.fetchone()
                    if row:
                        cols = [d[0] for d in cur.description]
                        self._commit()
                        return dict(zip(cols, row))
            self._commit()
            return None

    def get_document_clauses(
        self,
        document_id: str,
        clause_category: str | None = None,
        tenant_id: str | None = None,
    ) -> list[dict]:
        with self._lock:
            sql = (
                "SELECT c.clause_id, c.document_id, c.clause_number, c.clause_category, "
                "c.clause_subcategory, c.original_text, c.normalized_summary, "
                "c.is_company_standard, c.approval_status, c.source_node_ids, "
                "c.knowledge_type, c.effective_time "
                "FROM clauses c JOIN documents d ON d.document_id=c.document_id "
                "WHERE c.document_id=%s"
            )
            params: list[Any] = [document_id]
            if clause_category:
                sql += " AND c.clause_category=%s"
                params.append(clause_category)
            if tenant_id is not None:
                sql += " AND d.tenant_id=%s"
                params.append(tenant_id)
            sql += " ORDER BY c.clause_id"
            with self.conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description]
            self._commit()
            out = []
            for r in rows:
                d = dict(zip(cols, r))
                if isinstance(d.get("source_node_ids"), str):
                    try:
                        d["source_node_ids"] = json.loads(d["source_node_ids"])
                    except json.JSONDecodeError:
                        d["source_node_ids"] = []
                out.append(d)
            return out

    def find_clauses_by_category(
        self, category: str, tenant_id: str | None = None
    ) -> list[dict]:
        with self._lock:
            sql = (
                "SELECT c.clause_id, c.original_text, c.clause_category "
                "FROM clauses c "
                "JOIN documents d ON d.document_id = c.document_id "
                "WHERE c.clause_category = %s"
            )
            params: list[Any] = [category]
            if tenant_id is not None:
                sql += " AND d.tenant_id = %s"
                params.append(tenant_id)
            with self.conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
                return [
                    {
                        "clause_id": r[0],
                        "original_text": r[1],
                        "clause_category": r[2],
                    }
                    for r in rows
                ]

    def get_clause(self, clause_id: str, tenant_id: str | None = None) -> dict | None:
        with self._lock:
            sql = (
                "SELECT c.*, d.status AS doc_status, d.tenant_id, d.source_hash, "
                "d.source_version FROM clauses c "
                "JOIN documents d ON d.document_id=c.document_id "
                "WHERE c.clause_id=%s"
            )
            params: list[Any] = [clause_id]
            if tenant_id is not None:
                sql += " AND d.tenant_id=%s"
                params.append(tenant_id)
            with self.conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                cols = [d[0] for d in cur.description] if cur.description else []
            self._commit()
            return dict(zip(cols, row)) if row else None

    def record_rule_execution(
        self,
        exec_id: str,
        rule_id: str,
        rule_version: int,
        rule_type: str,
        result: str,
        severity: str,
        message: str,
        evidence: dict | list | str,
        executed_at: float | None = None,
    ) -> None:
        ev_json = (
            evidence
            if isinstance(evidence, str)
            else json.dumps(evidence, ensure_ascii=False)
        )
        ts = datetime.fromtimestamp(
            executed_at if executed_at is not None else time.time(),
            tz=timezone.utc,
        )
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO rule_executions (exec_id, rule_id, rule_version, rule_type, "
                        "result, severity, message, evidence, executed_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (exec_id) DO UPDATE SET result=EXCLUDED.result, "
                        "message=EXCLUDED.message, evidence=EXCLUDED.evidence",
                        (
                            exec_id,
                            rule_id,
                            int(rule_version),
                            rule_type,
                            result,
                            severity,
                            message,
                            ev_json,
                            ts,
                        ),
                    )
                self._commit()
            except Exception:
                self._rollback()
                raise

    def list_rule_executions(
        self, rule_id: str | None = None, limit: int = 100
    ) -> list[dict]:
        with self._lock:
            sql = "SELECT * FROM rule_executions"
            params: list[Any] = []
            if rule_id:
                sql += " WHERE rule_id=%s"
                params.append(rule_id)
            sql += " ORDER BY executed_at DESC LIMIT %s"
            params.append(int(limit))
            with self.conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description]
            self._commit()
            out = []
            for r in rows:
                d = dict(zip(cols, r))
                if isinstance(d.get("evidence"), str) and d["evidence"]:
                    try:
                        d["evidence"] = json.loads(d["evidence"])
                    except json.JSONDecodeError:
                        pass
                out.append(d)
            return out

    # ---------- 混合检索（FTS + 向量 + RRF + Rerank） ----------
    def _has_embeddings_table(self) -> bool:
        return bool(self._table_cols("embeddings"))

    def search(
        self,
        query: str,
        top_k: int = 8,
        business_category: str | None = None,
        contract_subtype: str | None = None,
        clause_number: str | None = None,
        keyword: str | None = None,
        knowledge_type: str | None = None,
        approved_only: bool = False,
        rerank: bool = True,
        tenant_id: str | None = None,
    ) -> list[dict]:
        with self._lock:
            try:
                has_emb = self._has_embeddings_table()
                fts = self._fts_search(
                    keyword or query,
                    top_k * 3,
                    business_category,
                    contract_subtype,
                    clause_number,
                    knowledge_type,
                    approved_only,
                    tenant_id,
                    use_embeddings_table=has_emb,
                )
                vec = (
                    self._vector_search(
                        query,
                        top_k * 3,
                        business_category,
                        contract_subtype,
                        knowledge_type,
                        approved_only,
                        tenant_id,
                    )
                    if has_emb
                    else []
                )
                self._commit()
            except Exception:
                self._rollback()
                raise
            fused = self._rrf(fts, vec)
            if clause_number:
                fused = [h for h in fused if h.get("clause_number") == clause_number]
            if rerank:
                fused = self._rerank(query, fused)
            return fused[:top_k]

    def search_lexical_candidates(
        self,
        query: str,
        top_k: int = 8,
        business_category: str | None = None,
        contract_subtype: str | None = None,
        clause_number: str | None = None,
        knowledge_type: str | None = None,
        approved_only: bool = False,
        tenant_id: str | None = None,
    ) -> list[dict]:
        """独立词法召回通道（Channel A: PG FTS）。"""
        with self._lock:
            try:
                has_emb = self._has_embeddings_table()
                hits = self._fts_search(
                    query,
                    top_k,
                    business_category,
                    contract_subtype,
                    clause_number,
                    knowledge_type,
                    approved_only,
                    tenant_id,
                    use_embeddings_table=has_emb,
                )
                self._commit()
                return hits[:top_k]
            except Exception:
                self._rollback()
                raise

    def search_dense_candidates(
        self,
        query: str,
        top_k: int = 8,
        business_category: str | None = None,
        contract_subtype: str | None = None,
        knowledge_type: str | None = None,
        approved_only: bool = False,
        tenant_id: str | None = None,
    ) -> list[dict]:
        """独立稠密语义召回通道（Channel B: pgvector 向量索引）。"""
        with self._lock:
            try:
                has_emb = self._has_embeddings_table()
                if not has_emb:
                    return []
                hits = self._vector_search(
                    query,
                    top_k,
                    business_category,
                    contract_subtype,
                    knowledge_type,
                    approved_only,
                    tenant_id,
                )
                self._commit()
                return hits[:top_k]
            except Exception:
                self._rollback()
                raise

    def search_structured_candidates(
        self,
        clause_number: str,
        top_k: int = 4,
        business_category: str | None = None,
        contract_subtype: str | None = None,
        approved_only: bool = False,
        tenant_id: str | None = None,
    ) -> list[dict]:
        """独立结构化编号/元数据精确召回通道（Channel D: 条款号精确匹配）。"""
        with self._lock:
            try:
                fsql, fparams = self._where(
                    business_category,
                    contract_subtype,
                    None,
                    approved_only,
                    tenant_id,
                )
                sql = (
                    "SELECT c.clause_id, c.document_id, c.original_text, c.clause_number, "
                    "c.clause_category, e.chunk_id, e.business_category, e.contract_subtype, "
                    "e.knowledge_type, e.approved_status, d.status AS doc_status, d.tenant_id "
                    "FROM clauses c "
                    "JOIN embeddings e ON e.clause_id=c.clause_id "
                    "JOIN documents d ON d.document_id=c.document_id "
                    "WHERE c.clause_number=%s" + fsql + " LIMIT %s"
                )
                with self.conn.cursor() as cur:
                    cur.execute(sql, [clause_number] + fparams + [top_k])
                    rows = cur.fetchall()
                self._commit()
                out = []
                for r in rows:
                    out.append(
                        {
                            "chunk_id": r["chunk_id"],
                            "clause_id": r["clause_id"],
                            "document_id": r["document_id"],
                            "text": r["original_text"],
                            "clause_number": r["clause_number"],
                            "clause_category": r["clause_category"],
                            "channel": "structured",
                            "business_category": r["business_category"],
                            "contract_subtype": r["contract_subtype"],
                            "knowledge_type": r["knowledge_type"],
                            "approved_status": r["approved_status"],
                            "doc_status": r["doc_status"],
                            "tenant_id": r["tenant_id"],
                        }
                    )
                return out
            except Exception:
                self._rollback()
                raise

    def _where(
        self,
        business_category: str | None,
        contract_subtype: str | None,
        knowledge_type: str | None,
        approved_only: bool,
        tenant_id: str | None,
        alias: str = "e",
    ) -> tuple[str, list[Any]]:
        sql = " AND d.status='ACTIVE'"
        params: list[Any] = []
        if business_category:
            sql += f" AND {alias}.business_category=%s"
            params.append(business_category)
        if contract_subtype:
            sql += f" AND {alias}.contract_subtype=%s"
            params.append(contract_subtype)
        if knowledge_type:
            sql += f" AND {alias}.knowledge_type=%s"
            params.append(knowledge_type)
        if approved_only:
            sql += f" AND {alias}.approved_status='APPROVED'"
        if tenant_id is not None:
            sql += " AND d.tenant_id=%s"
            params.append(tenant_id)
        return sql, params

    def _fts_search(
        self,
        query: str,
        limit: int,
        business_category: str | None,
        contract_subtype: str | None,
        clause_number: str | None,
        knowledge_type: str | None,
        approved_only: bool,
        tenant_id: str | None,
        use_embeddings_table: bool = True,
    ) -> list[dict]:
        words = [w for w in re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z0-9]+", query)][:8]
        for w in list(words):
            if len(w) >= 3:
                words.extend(w[i : i + 2] for i in range(len(w) - 1))
        words = words[:24]
        if not words:
            return []
        match = " | ".join(words)
        extra, eparams = "", []
        if clause_number:
            extra = " AND c.clause_number=%s"
            eparams.append(clause_number)
        if not use_embeddings_table:
            d_where = " AND d.status='ACTIVE'"
            d_params: list[Any] = []
            if business_category:
                d_where += " AND d.business_category=%s"
                d_params.append(business_category)
            if contract_subtype:
                d_where += " AND d.contract_subtype=%s"
                d_params.append(contract_subtype)
            if tenant_id is not None:
                d_where += " AND d.tenant_id=%s"
                d_params.append(tenant_id)
            if knowledge_type:
                d_where += " AND c.knowledge_type=%s"
                d_params.append(knowledge_type)
            if approved_only:
                d_where += " AND c.approval_status='APPROVED'"
            with self.conn.cursor() as cur:
                like_words = [f"%{w}%" for w in words]
                cur.execute(
                    "SELECT c.clause_id, c.document_id, c.original_text, c.clause_number,"
                    " c.clause_category, c.clause_id, d.business_category, d.contract_subtype,"
                    " c.knowledge_type, c.approval_status, d.status, d.tenant_id FROM clauses c "
                    "JOIN documents d ON d.document_id=c.document_id "
                    "WHERE (to_tsvector('simple', coalesce(c.original_text,'')) @@ to_tsquery('simple', %s) OR c.original_text ILIKE ANY(%s))"
                    + d_where
                    + extra
                    + " LIMIT %s",
                    [match, like_words] + d_params + eparams + [limit],
                )
                rows = cur.fetchall()
        else:
            where, params = self._where(
                business_category,
                contract_subtype,
                knowledge_type,
                approved_only,
                tenant_id,
            )
            with self.conn.cursor() as cur:
                like_words = [f"%{w}%" for w in words]
                cur.execute(
                    "SELECT c.clause_id, c.document_id, c.original_text, c.clause_number,"
                    " c.clause_category, e.chunk_id, e.business_category, e.contract_subtype,"
                    " e.knowledge_type, e.approved_status, d.status, d.tenant_id FROM clauses c "
                    "JOIN embeddings e ON e.clause_id=c.clause_id "
                    "JOIN documents d ON d.document_id=c.document_id "
                    "WHERE (to_tsvector('simple', coalesce(c.original_text,'')) @@ to_tsquery('simple', %s) OR c.original_text ILIKE ANY(%s))"
                    + where
                    + extra
                    + " LIMIT %s",
                    [match, like_words] + params + eparams + [limit],
                )
                rows = cur.fetchall()
        channel_name = "fts" if use_embeddings_table else "fts_fallback"
        return [
            {
                "chunk_id": r[5],
                "clause_id": r[0],
                "document_id": r[1],
                "text": r[2],
                "clause_number": r[3],
                "clause_category": r[4],
                "channel": channel_name,
                "business_category": r[6],
                "contract_subtype": r[7],
                "knowledge_type": r[8],
                "approved_status": r[9],
                "doc_status": r[10],
                "tenant_id": r[11],
                **(
                    {"degraded": True, "degradation_reason": "NO_EMBEDDINGS_TABLE"}
                    if not use_embeddings_table
                    else {}
                ),
            }
            for r in rows
        ]

    def _vector_search(
        self,
        query: str,
        limit: int,
        business_category: str | None,
        contract_subtype: str | None,
        knowledge_type: str | None,
        approved_only: bool,
        tenant_id: str | None,
    ) -> list[dict]:
        """pgvector SQL 相似度排序 + LIMIT（不在应用层全量载入向量计算）。

        vec <=> q 为余弦距离，score = 1 - 距离；排序与截断全部下推数据库执行。
        """
        where, params = self._where(
            business_category,
            contract_subtype,
            knowledge_type,
            approved_only,
            tenant_id,
        )
        qv = self._validate_vec(self.emb.embed([query])[0], "query")
        q_lit = self._vec_literal(qv)
        sql = (
            "SELECT e.chunk_id, e.clause_id, e.document_id, e.text,"
            " 1-(e.vec <=> %s::vector) AS score, e.business_category, e.contract_subtype,"
            " c.clause_number, c.clause_category, e.knowledge_type, e.approved_status, d.status, d.tenant_id "
            "FROM embeddings e "
            "JOIN documents d ON d.document_id=e.document_id "
            "LEFT JOIN clauses c ON c.clause_id=e.clause_id "
            "WHERE 1=1" + where + " ORDER BY e.vec <=> %s::vector LIMIT %s"
        )
        with self.conn.cursor() as cur:
            cur.execute(sql, [q_lit] + params + [q_lit, int(limit)])
            rows = cur.fetchall()
        return [
            {
                "chunk_id": r[0],
                "clause_id": r[1],
                "document_id": r[2],
                "text": r[3],
                "score": float(r[4]),
                "business_category": r[5],
                "contract_subtype": r[6],
                "clause_number": r[7],
                "clause_category": r[8],
                "knowledge_type": r[9],
                "approved_status": r[10],
                "doc_status": r[11],
                "tenant_id": r[12],
                "channel": "vector",
            }
            for r in rows
        ]

    @staticmethod
    def _rrf(fts_hits: list[dict], vec_hits: list[dict], k: int = 60) -> list[dict]:
        scores: dict[str, float] = {}
        merged: dict[str, dict] = {}
        for rank, h in enumerate(fts_hits):
            scores[h["chunk_id"]] = scores.get(h["chunk_id"], 0.0) + 1.0 / (
                k + rank + 1
            )
            merged[h["chunk_id"]] = h
        for rank, h in enumerate(vec_hits):
            scores[h["chunk_id"]] = scores.get(h["chunk_id"], 0.0) + 1.0 / (
                k + rank + 1
            )
            merged.setdefault(h["chunk_id"], h)
        out = []
        for cid, s in sorted(scores.items(), key=lambda kv: -kv[1]):
            item = dict(merged[cid])
            item["rrf_score"] = s
            item["channels"] = [
                x["channel"] for x in fts_hits + vec_hits if x["chunk_id"] == cid
            ]
            out.append(item)
        return out

    @staticmethod
    def _rerank(query: str, hits: list[dict]) -> list[dict]:
        qn = _norm_text(query)
        qgrams = {qn[i : i + 2] for i in range(len(qn) - 1)} or {qn}
        qnums = set(re.findall(r"\d+(?:\.\d+)?", query))
        for h in hits:
            tn = _norm_text(h["text"])
            tgrams = {tn[i : i + 2] for i in range(len(tn) - 1)} or {tn}
            cov = len(qgrams & tgrams) / max(1, len(qgrams))
            tnums = set(re.findall(r"\d+(?:\.\d+)?", h["text"]))
            num_bonus = 0.15 * len(qnums & tnums)
            h["rerank_score"] = cov + num_bonus
        return sorted(hits, key=lambda h: -h["rerank_score"])

    # ---------- 规则 / 任务 / 反馈（同 SQLite 语义） ----------
    def save_rule(self, rule: dict, tenant_id: str | None = None) -> int:
        resolved_tenant = tenant_id or rule.get("tenant_id") or "default"
        has_tenant = "tenant_id" in self._table_cols("rules")
        params_obj = (
            rule["parameters"] if "parameters" in rule else rule.get("params", {})
        )
        eff_raw = rule.get("effective_at")
        eff_pg = (
            datetime.fromtimestamp(eff_raw, tz=timezone.utc)
            if isinstance(eff_raw, (int, float))
            else eff_raw
        )
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    version = rule.get("version")
                    if version is None:
                        cur.execute(
                            "SELECT MAX(version) FROM rules WHERE rule_id=%s",
                            (rule["rule_id"],),
                        )
                        version = int((cur.fetchone()[0] or 0) + 1)
                    elif has_tenant:
                        cur.execute(
                            "SELECT tenant_id FROM rules WHERE rule_id=%s AND version=%s",
                            (rule["rule_id"], int(version)),
                        )
                        ex = cur.fetchone()
                        if ex and ex[0] != resolved_tenant:
                            cur.execute(
                                "SELECT MAX(version) FROM rules WHERE rule_id=%s",
                                (rule["rule_id"],),
                            )
                            version = int((cur.fetchone()[0] or 0) + 1)

                    if has_tenant:
                        cur.execute(
                            "INSERT INTO rules (rule_id, version, name, rule_type, execution_mode, severity,"
                            " priority, contract_types, params, source_clauses, status, approved_by,"
                            " effective_at, tenant_id)"
                            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (rule_id, version)"
                            " DO UPDATE SET status=EXCLUDED.status, params=EXCLUDED.params",
                            (
                                rule["rule_id"],
                                int(version),
                                rule["name"],
                                rule["rule_type"],
                                rule["execution_mode"],
                                rule["severity"],
                                int(rule.get("priority", 100)),
                                json.dumps(rule.get("contract_types", ["*"])),
                                json.dumps(params_obj, ensure_ascii=False),
                                json.dumps(
                                    rule.get("source_clauses", []), ensure_ascii=False
                                ),
                                rule.get("status", "DRAFT"),
                                rule.get("approved_by"),
                                eff_pg,
                                resolved_tenant,
                            ),
                        )
                    else:
                        cur.execute(
                            "INSERT INTO rules (rule_id, version, name, rule_type, execution_mode, severity,"
                            " priority, contract_types, params, source_clauses, status, approved_by, effective_at)"
                            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (rule_id, version)"
                            " DO UPDATE SET status=EXCLUDED.status, params=EXCLUDED.params",
                            (
                                rule["rule_id"],
                                int(version),
                                rule["name"],
                                rule["rule_type"],
                                rule["execution_mode"],
                                rule["severity"],
                                int(rule.get("priority", 100)),
                                json.dumps(rule.get("contract_types", ["*"])),
                                json.dumps(params_obj, ensure_ascii=False),
                                json.dumps(
                                    rule.get("source_clauses", []), ensure_ascii=False
                                ),
                                rule.get("status", "DRAFT"),
                                rule.get("approved_by"),
                                eff_pg,
                            ),
                        )
                self._commit()
                return int(version)
            except Exception:
                self._rollback()
                raise

    def approve_rule(
        self,
        rule_id: str,
        approver: str,
        version: int | None = 1,
        tenant_id: str | None = None,
    ) -> bool:
        has_tenant = "tenant_id" in self._table_cols("rules")
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    if version is None:
                        sql_v = "SELECT MAX(version) FROM rules WHERE rule_id=%s AND status='DRAFT'"
                        p_v: list[Any] = [rule_id]
                        if tenant_id is not None and has_tenant:
                            sql_v += " AND tenant_id=%s"
                            p_v.append(tenant_id)
                        cur.execute(sql_v, p_v)
                        rv = cur.fetchone()
                        if not rv or rv[0] is None:
                            self._commit()
                            return False
                        version = int(rv[0])

                    sql = (
                        "UPDATE rules SET status='APPROVED', approved_by=%s, effective_at=now() "
                        "WHERE rule_id=%s AND version=%s AND status='DRAFT'"
                    )
                    params: list[Any] = [approver, rule_id, int(version)]
                    if tenant_id is not None and has_tenant:
                        sql += " AND tenant_id=%s"
                        params.append(tenant_id)
                    cur.execute(sql, params)
                    updated = cur.rowcount
                    if updated:
                        cur.execute(
                            "INSERT INTO audit_log (actor, action, target, detail)"
                            " VALUES (%s,'approve_rule',%s,%s)",
                            (approver, rule_id, f"version={version}"),
                        )
                self._commit()
                return bool(updated)
            except Exception:
                self._rollback()
                raise

    def get_rules(
        self, status: str | None = None, tenant_id: str | None = None
    ) -> list[dict]:
        has_tenant = "tenant_id" in self._table_cols("rules")
        with self._lock:
            sql = "SELECT * FROM rules WHERE 1=1"
            params: list[Any] = []
            if status:
                sql += " AND status=%s"
                params.append(status)
            if tenant_id is not None and has_tenant:
                sql += " AND tenant_id=%s"
                params.append(tenant_id)
            sql += " ORDER BY priority ASC, rule_id ASC, version DESC"
            with self.conn.cursor() as cur:
                cur.execute(sql, params)
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
            self._commit()
            out = []
            for r in rows:
                d = dict(zip(cols, r))
                for k in ("contract_types", "params", "source_clauses"):
                    if isinstance(d.get(k), str):
                        d[k] = json.loads(d[k])
                if isinstance(d.get("effective_at"), datetime):
                    d["effective_at"] = d["effective_at"].timestamp()
                out.append(d)
            return out

    def list_documents(self, tenant_id: str | None = None) -> list[dict]:
        with self._lock:
            sql = (
                "SELECT document_id, source_file, source_hash, business_category,"
                " contract_subtype, status, imported_at, tenant_id FROM documents"
            )
            params: tuple[Any, ...] = ()
            if tenant_id is not None:
                sql += " WHERE tenant_id=%s"
                params = (tenant_id,)
            sql += " ORDER BY imported_at"
            with self.conn.cursor() as cur:
                cur.execute(sql, params)
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            self._commit()
            return rows

    def save_task(
        self,
        task_id: str | dict,
        document_id: str = "",
        mode: str = "review",
        status: str = "PENDING",
        meta: dict | None = None,
        tenant_id: str = "default",
        created_by: str = "system",
        idempotency_key: str | None = None,
        rule_set_version: str | None = None,
        model_version: str | None = None,
        reproducibility_hash: str | None = None,
    ) -> None:
        if isinstance(task_id, dict):
            d = task_id
            task_id = str(d.get("task_id") or "")
            document_id = str(d.get("document_id") or "")
            mode = str(d.get("mode") or "review")
            status = str(d.get("status") or "PENDING")
            meta = d.get("meta") or {}
            tenant_id = str(d.get("tenant_id") or "default")
            created_by = str(d.get("created_by") or "system")
            idempotency_key = d.get("idempotency_key")
            rule_set_version = d.get("rule_set_version")
            model_version = d.get("model_version")
            reproducibility_hash = d.get("reproducibility_hash")
        if meta is None:
            meta = {}
        task_cols = self._table_cols("review_tasks")
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    if "idempotency_key" in task_cols:
                        cur.execute(
                            "INSERT INTO review_tasks (task_id, document_id, mode, status, meta, tenant_id,"
                            " created_by, idempotency_key, rule_set_version, model_version, reproducibility_hash)"
                            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                            " ON CONFLICT (task_id) DO UPDATE SET status=EXCLUDED.status,"
                            " meta=EXCLUDED.meta, updated_at=now(),"
                            " idempotency_key=COALESCE(EXCLUDED.idempotency_key, review_tasks.idempotency_key),"
                            " rule_set_version=COALESCE(EXCLUDED.rule_set_version, review_tasks.rule_set_version),"
                            " model_version=COALESCE(EXCLUDED.model_version, review_tasks.model_version),"
                            " reproducibility_hash=COALESCE(EXCLUDED.reproducibility_hash, review_tasks.reproducibility_hash)",
                            (
                                task_id,
                                document_id,
                                mode,
                                status,
                                json.dumps(meta, ensure_ascii=False),
                                tenant_id,
                                created_by,
                                idempotency_key,
                                rule_set_version,
                                model_version,
                                reproducibility_hash,
                            ),
                        )
                    else:
                        cur.execute(
                            "INSERT INTO review_tasks (task_id, document_id, mode, status, meta, tenant_id,"
                            " created_by) VALUES (%s,%s,%s,%s,%s,%s,%s)"
                            " ON CONFLICT (task_id) DO UPDATE SET status=EXCLUDED.status,"
                            " meta=EXCLUDED.meta, updated_at=now()",
                            (
                                task_id,
                                document_id,
                                mode,
                                status,
                                json.dumps(meta, ensure_ascii=False),
                                tenant_id,
                                created_by,
                            ),
                        )
                self._commit()
            except Exception:
                self._rollback()
                raise

    def get_task(self, task_id: str) -> dict | None:
        with self._lock:
            with self.conn.cursor() as cur:
                cur.execute("SELECT * FROM review_tasks WHERE task_id=%s", (task_id,))
                row = cur.fetchone()
                cols = [d[0] for d in cur.description] if cur.description else []
            self._commit()
            if not row:
                return None
            d = dict(zip(cols, row))
            d["cancel_requested"] = bool(d.get("cancel_requested", False))
            if isinstance(d.get("meta"), str):
                try:
                    d["meta"] = json.loads(d["meta"])
                except json.JSONDecodeError:
                    pass
            return d

    def find_task_by_idempotency_key(
        self,
        idempotency_key: str,
        tenant_id: str | None = None,
        created_by: str | None = None,
    ) -> dict | None:
        if not idempotency_key:
            return None
        with self._lock:
            sql = "SELECT * FROM review_tasks WHERE idempotency_key=%s"
            params: list[Any] = [idempotency_key]
            if tenant_id is not None:
                sql += " AND tenant_id=%s"
                params.append(tenant_id)
            if created_by is not None:
                sql += " AND created_by=%s"
                params.append(created_by)
            sql += " ORDER BY created_at DESC LIMIT 1"
            with self.conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                cols = [d[0] for d in cur.description] if cur.description else []
            self._commit()
            if not row:
                return None
            d = dict(zip(cols, row))
            d["cancel_requested"] = bool(d.get("cancel_requested", False))
            if isinstance(d.get("meta"), str):
                try:
                    d["meta"] = json.loads(d["meta"])
                except json.JSONDecodeError:
                    pass
            return d

    def list_tasks(
        self,
        tenant_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        created_by: str | None = None,
    ) -> tuple[list[dict], int]:
        with self._lock:
            where = " WHERE 1=1"
            params: list[Any] = []
            if tenant_id is not None:
                where += " AND tenant_id=%s"
                params.append(tenant_id)
            if status:
                where += " AND status=%s"
                params.append(status)
            if created_by is not None:
                where += " AND created_by=%s"
                params.append(created_by)
            with self.conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM review_tasks" + where, params)
                total = int(cur.fetchone()[0])
                cur.execute(
                    "SELECT * FROM review_tasks"
                    + where
                    + " ORDER BY created_at DESC LIMIT %s OFFSET %s",
                    params + [int(limit), int(offset)],
                )
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description] if cur.description else []
            self._commit()
            items = []
            for r in rows:
                d = dict(zip(cols, r))
                d["cancel_requested"] = bool(d.get("cancel_requested", False))
                if isinstance(d.get("meta"), str):
                    try:
                        d["meta"] = json.loads(d["meta"])
                    except json.JSONDecodeError:
                        pass
                items.append(d)
            return items, total

    def request_cancel_task(self, task_id: str, tenant_id: str | None = None) -> bool:
        with self._lock:
            try:
                sql = "UPDATE review_tasks SET cancel_requested=TRUE, updated_at=now() WHERE task_id=%s"
                params: list[Any] = [task_id]
                if tenant_id is not None:
                    sql += " AND tenant_id=%s"
                    params.append(tenant_id)
                with self.conn.cursor() as cur:
                    cur.execute(sql, params)
                    updated = cur.rowcount
                self._commit()
                return bool(updated)
            except Exception:
                self._rollback()
                raise

    def is_task_cancelled(self, task_id: str) -> bool:
        with self._lock:
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT cancel_requested, status FROM review_tasks WHERE task_id=%s",
                    (task_id,),
                )
                row = cur.fetchone()
            self._commit()
            if not row:
                return False
            return bool(row[0]) or row[1] == "CANCELLED"

    def save_stage_state(
        self,
        task_id: str,
        stage_name: str,
        stage_order: int,
        status: str,
        attempt: int = 1,
        started_at: float | None = None,
        finished_at: float | None = None,
        duration_ms: float = 0.0,
        detail: dict | None = None,
        error: str | None = None,
    ) -> None:
        s_ts = datetime.fromtimestamp(
            started_at if started_at is not None else time.time(), tz=timezone.utc
        )
        f_ts = (
            datetime.fromtimestamp(finished_at, tz=timezone.utc)
            if finished_at is not None
            else None
        )
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO review_stage_states (task_id, stage_name, stage_order, status,"
                        " attempt, started_at, finished_at, duration_ms, detail_json, error)"
                        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                        " ON CONFLICT (task_id, stage_name) DO UPDATE SET status=EXCLUDED.status,"
                        " attempt=EXCLUDED.attempt, started_at=EXCLUDED.started_at,"
                        " finished_at=EXCLUDED.finished_at, duration_ms=EXCLUDED.duration_ms,"
                        " detail_json=EXCLUDED.detail_json, error=EXCLUDED.error",
                        (
                            task_id,
                            stage_name,
                            int(stage_order),
                            status,
                            int(attempt),
                            s_ts,
                            f_ts,
                            float(duration_ms),
                            json.dumps(detail or {}, ensure_ascii=False),
                            error,
                        ),
                    )
                self._commit()
            except Exception:
                self._rollback()
                raise

    def get_stage_states(self, task_id: str) -> list[dict]:
        with self._lock:
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM review_stage_states WHERE task_id=%s ORDER BY stage_order ASC",
                    (task_id,),
                )
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description] if cur.description else []
            self._commit()
            out = []
            for r in rows:
                d = dict(zip(cols, r))
                raw = d.pop("detail_json", None)
                if isinstance(raw, str):
                    d["detail"] = json.loads(raw)
                else:
                    d["detail"] = raw or {}
                out.append(d)
            return out

    def get_issue(self, issue_id: str) -> dict | None:
        with self._lock:
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT i.*, t.tenant_id AS task_tenant_id, t.created_by AS task_created_by "
                    "FROM review_issues i JOIN review_tasks t ON t.task_id=i.task_id "
                    "WHERE i.issue_id=%s",
                    (issue_id,),
                )
                row = cur.fetchone()
                cols = [d[0] for d in cur.description] if cur.description else []
            self._commit()
            return dict(zip(cols, row)) if row else None

    def save_issues(self, task_id: str, issues: list[dict]) -> None:
        issue_cols = self._table_cols("review_issues")
        has_ext = "finding_source" in issue_cols
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    for it in issues:
                        if has_ext:
                            cur.execute(
                                "INSERT INTO review_issues (issue_id, task_id, rule_id, severity, category,"
                                " title, description, expected_value, actual_value, source_ref, knowledge_refs,"
                                " suggestion, execution_mode, requires_human_review, confidence, human_state,"
                                " human_note, finding_source, check_status)"
                                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                                " ON CONFLICT (issue_id) DO UPDATE SET human_state=EXCLUDED.human_state,"
                                " human_note=EXCLUDED.human_note, finding_source=EXCLUDED.finding_source,"
                                " check_status=EXCLUDED.check_status",
                                (
                                    it["issue_id"],
                                    task_id,
                                    it.get("rule_id"),
                                    it["severity"],
                                    it["category"],
                                    it["title"],
                                    it["description"],
                                    json.dumps(
                                        it.get("expected_value"), ensure_ascii=False
                                    ),
                                    json.dumps(
                                        it.get("actual_value"), ensure_ascii=False
                                    ),
                                    json.dumps(
                                        it.get("source_reference"), ensure_ascii=False
                                    ),
                                    json.dumps(
                                        it.get("knowledge_references", []),
                                        ensure_ascii=False,
                                    ),
                                    it.get("suggestion", ""),
                                    it.get("execution_mode", "DETERMINISTIC"),
                                    bool(it.get("requires_human_review")),
                                    it.get("confidence"),
                                    it.get("human_state", "PENDING"),
                                    it.get("human_note"),
                                    it.get("finding_source", "DETERMINISTIC"),
                                    it.get("check_status", "CHECKED"),
                                ),
                            )
                        else:
                            cur.execute(
                                "INSERT INTO review_issues (issue_id, task_id, rule_id, severity, category,"
                                " title, description, expected_value, actual_value, source_ref, knowledge_refs,"
                                " suggestion, execution_mode, requires_human_review, confidence, human_state,"
                                " human_note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                                " ON CONFLICT (issue_id) DO UPDATE SET human_state=EXCLUDED.human_state,"
                                " human_note=EXCLUDED.human_note",
                                (
                                    it["issue_id"],
                                    task_id,
                                    it.get("rule_id"),
                                    it["severity"],
                                    it["category"],
                                    it["title"],
                                    it["description"],
                                    json.dumps(
                                        it.get("expected_value"), ensure_ascii=False
                                    ),
                                    json.dumps(
                                        it.get("actual_value"), ensure_ascii=False
                                    ),
                                    json.dumps(
                                        it.get("source_reference"), ensure_ascii=False
                                    ),
                                    json.dumps(
                                        it.get("knowledge_references", []),
                                        ensure_ascii=False,
                                    ),
                                    it.get("suggestion", ""),
                                    it.get("execution_mode", "DETERMINISTIC"),
                                    bool(it.get("requires_human_review")),
                                    it.get("confidence"),
                                    it.get("human_state", "PENDING"),
                                    it.get("human_note"),
                                ),
                            )
                self._commit()
            except Exception:
                self._rollback()
                raise

    def get_issues(self, task_id: str) -> list[dict]:
        with self._lock:
            with self.conn.cursor() as cur:
                cur.execute("SELECT * FROM review_issues WHERE task_id=%s", (task_id,))
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description]
            self._commit()
            out = []
            for r in rows:
                d = dict(zip(cols, r))
                for k in (
                    "source_ref",
                    "knowledge_refs",
                    "expected_value",
                    "actual_value",
                ):
                    if isinstance(d.get(k), str):
                        d[k] = json.loads(d[k])
                out.append(d)
            return out

    def feedback_issue(
        self,
        issue_id: str,
        action: str,
        actor: str,
        note: str = "",
        expected_state: str | None = None,
    ) -> bool:
        valid = {"ACCEPT", "IGNORE", "EDIT", "NOTE", "FALSE_POSITIVE", "FALSE_NEGATIVE"}
        if action not in valid:
            return False
        has_source = "finding_source" in self._table_cols("review_issues")
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    sql = "UPDATE review_issues SET human_state=%s, human_note=%s"
                    if has_source:
                        sql += ", finding_source='HUMAN'"
                    sql += " WHERE issue_id=%s"
                    params: list[Any] = [action, note, issue_id]
                    if expected_state is not None:
                        sql += " AND (human_state=%s OR (human_state IS NULL AND %s='PENDING'))"
                        params.extend([expected_state, expected_state])
                    cur.execute(sql, tuple(params))
                    updated = cur.rowcount
                    if updated:
                        cur.execute(
                            "INSERT INTO audit_log (actor, action, target, detail)"
                            " VALUES (%s,'issue_feedback',%s,%s)",
                            (actor, issue_id, f"{action} {note[:80]}"),
                        )
                self._commit()
                return bool(updated)
            except Exception:
                self._rollback()
                raise

    def record_human_override(
        self,
        override_id: str,
        task_id: str,
        issue_id: str,
        tenant_id: str,
        actor: str,
        action: str,
        reason: str,
        previous_state: str,
        new_state: str,
        created_at: float | None = None,
    ) -> None:
        c_ts = datetime.fromtimestamp(
            created_at if created_at is not None else time.time(), tz=timezone.utc
        )
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO human_overrides (override_id, task_id, issue_id, tenant_id, actor,"
                        " action, reason, previous_state, new_state, created_at)"
                        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            override_id,
                            task_id,
                            issue_id,
                            tenant_id or "default",
                            actor,
                            action,
                            reason,
                            previous_state,
                            new_state,
                            c_ts,
                        ),
                    )
                self._commit()
            except Exception:
                self._rollback()
                raise

    def list_human_overrides(self, task_id: str) -> list[dict]:
        with self._lock:
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM human_overrides WHERE task_id=%s ORDER BY created_at ASC",
                    (task_id,),
                )
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description] if cur.description else []
            self._commit()
            return [dict(zip(cols, r)) for r in rows]

    # ---------- 统一标准合同实体（Canonical Contract） ----------
    def save_canonical_version(
        self, outcome: Any, expected_version: int | None = None
    ) -> bool:
        contract = outcome.contract
        cid = contract.contract_id
        time.time()
        tenant = getattr(contract, "tenant_id", "default") or "default"
        contract_dict = (
            contract.to_dict() if hasattr(contract, "to_dict") else dict(contract)
        )

        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    if expected_version is not None:
                        cur.execute(
                            "SELECT version FROM canonical_contracts WHERE contract_id=%s",
                            (cid,),
                        )
                        row = cur.fetchone()
                        if not row or int(row[0]) != int(expected_version):
                            self._commit()
                            return False

                    self.upsert_canonical_contract(contract_dict)

                    snap_str = json.dumps(contract_dict, ensure_ascii=False)
                    cur.execute(
                        "INSERT INTO canonical_contract_versions (contract_id, version, tenant_id, snapshot, created_at) "
                        "VALUES (%s, %s, %s, %s, now()) "
                        "ON CONFLICT (contract_id, version) DO UPDATE SET snapshot=EXCLUDED.snapshot",
                        (cid, contract.version, tenant, snap_str),
                    )

                    for m in getattr(contract, "source_mappings", []):
                        m_dict = m.to_dict() if hasattr(m, "to_dict") else dict(m)
                        cur.execute(
                            "INSERT INTO contract_source_mappings "
                            "(tenant_id, source_system, external_id, contract_id, external_code, source_hash, synced_at) "
                            "VALUES (%s, %s, %s, %s, %s, %s, now()) "
                            "ON CONFLICT (tenant_id, source_system, external_id) DO UPDATE SET "
                            "contract_id=EXCLUDED.contract_id, external_code=EXCLUDED.external_code, "
                            "source_hash=EXCLUDED.source_hash, synced_at=now()",
                            (
                                tenant,
                                m_dict.get("source_system"),
                                m_dict.get("external_id"),
                                cid,
                                m_dict.get("external_code"),
                                m_dict.get("checksum"),
                            ),
                        )
                self._commit()
                return True
            except Exception:
                self._rollback()
                raise

    def get_canonical_versions(self, contract_id: str) -> list[dict[str, Any]]:
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    cur.execute(
                        "SELECT contract_id, version, tenant_id, snapshot, EXTRACT(EPOCH FROM created_at) "
                        "FROM canonical_contract_versions WHERE contract_id=%s ORDER BY version ASC",
                        (contract_id,),
                    )
                    rows = cur.fetchall()
                    result = []
                    for r in rows:
                        snap = (
                            r[3]
                            if isinstance(r[3], dict)
                            else (json.loads(r[3]) if r[3] else {})
                        )
                        result.append(
                            {
                                "contract_id": r[0],
                                "version": int(r[1]),
                                "tenant_id": r[2],
                                "snapshot": snap,
                                "created_at": float(r[4]),
                            }
                        )
                    return result
            except Exception:
                self._rollback()
                raise

    def find_canonical_by_source(
        self, tenant_id: str, source_system: str, external_id: str
    ) -> dict | None:
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    cur.execute(
                        "SELECT contract_id FROM contract_source_mappings "
                        "WHERE tenant_id=%s AND source_system=%s AND external_id=%s",
                        (tenant_id, source_system, external_id),
                    )
                    row = cur.fetchone()
                    if not row:
                        return None
                    return self.get_canonical_contract(row[0], tenant_id=tenant_id)
            except Exception:
                self._rollback()
                raise

    def find_canonical_by_contract_number(
        self, tenant_id: str, contract_number: str
    ) -> dict | None:
        if not contract_number:
            return None
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM canonical_contracts WHERE tenant_id=%s AND contract_number=%s",
                        (tenant_id, contract_number),
                    )
                    row = cur.fetchone()
                    if not row:
                        return None
                    return self._decode_canonical_row(dict(row))
            except Exception:
                self._rollback()
                raise

    def upsert_canonical_contract(self, contract: dict) -> str:
        cid = contract["contract_id"]
        tenant = contract.get("tenant_id") or "default"
        total_amt = contract.get("total_amount")
        if total_amt is None and isinstance(contract.get("amounts"), dict):
            total_amt = contract["amounts"].get("total_amount")
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    cur.execute(
                        "SELECT version FROM canonical_contracts WHERE contract_id=%s",
                        (cid,),
                    )
                    ex = cur.fetchone()
                    version = int(contract.get("version") or ((ex[0] + 1) if ex else 1))
                    cur.execute(
                        "INSERT INTO canonical_contracts (contract_id, tenant_id, contract_number,"
                        " entity_prefix, title, business_category, contract_subtype, lifecycle_status,"
                        " source_system, source_document_id, source_hash, total_amount, currency,"
                        " parties_json, amounts_json, source_mappings_json, version)"
                        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                        " ON CONFLICT (contract_id) DO UPDATE SET"
                        " contract_number=EXCLUDED.contract_number,"
                        " entity_prefix=EXCLUDED.entity_prefix,"
                        " title=EXCLUDED.title,"
                        " business_category=EXCLUDED.business_category,"
                        " contract_subtype=EXCLUDED.contract_subtype,"
                        " lifecycle_status=EXCLUDED.lifecycle_status,"
                        " source_system=EXCLUDED.source_system,"
                        " source_document_id=EXCLUDED.source_document_id,"
                        " source_hash=EXCLUDED.source_hash,"
                        " total_amount=EXCLUDED.total_amount,"
                        " currency=EXCLUDED.currency,"
                        " parties_json=EXCLUDED.parties_json,"
                        " amounts_json=EXCLUDED.amounts_json,"
                        " source_mappings_json=EXCLUDED.source_mappings_json,"
                        " version=EXCLUDED.version,"
                        " updated_at=now()",
                        (
                            cid,
                            tenant,
                            contract.get("contract_number"),
                            contract.get("entity_prefix"),
                            contract.get("title", ""),
                            contract.get("business_category", ""),
                            contract.get("contract_subtype", ""),
                            contract.get("lifecycle_status", "DRAFT"),
                            contract.get("source_system", "UPLOAD"),
                            contract.get("source_document_id"),
                            contract.get("source_hash"),
                            str(total_amt) if total_amt is not None else None,
                            contract.get("currency", "CNY"),
                            json.dumps(contract.get("parties", []), ensure_ascii=False),
                            json.dumps(contract.get("amounts", {}), ensure_ascii=False),
                            json.dumps(
                                contract.get("source_mappings", []), ensure_ascii=False
                            ),
                            version,
                        ),
                    )
                self._commit()
                return cid
            except Exception:
                self._rollback()
                raise

    def get_canonical_contract(
        self, contract_id: str, tenant_id: str | None = None
    ) -> dict | None:
        with self._lock:
            sql = "SELECT * FROM canonical_contracts WHERE contract_id=%s"
            params: list[Any] = [contract_id]
            if tenant_id is not None:
                sql += " AND tenant_id=%s"
                params.append(tenant_id)
            with self.conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                cols = [d[0] for d in cur.description] if cur.description else []
            self._commit()
            if not row:
                return None
            return self._decode_canonical_row(dict(zip(cols, row)))

    def list_canonical_contracts(
        self,
        tenant_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        with self._lock:
            where = " WHERE 1=1"
            params: list[Any] = []
            if tenant_id is not None:
                where += " AND tenant_id=%s"
                params.append(tenant_id)
            with self.conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM canonical_contracts" + where, params)
                total = int(cur.fetchone()[0])
                cur.execute(
                    "SELECT * FROM canonical_contracts"
                    + where
                    + " ORDER BY updated_at DESC LIMIT %s OFFSET %s",
                    params + [int(limit), int(offset)],
                )
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description] if cur.description else []
            self._commit()
            return [self._decode_canonical_row(dict(zip(cols, r))) for r in rows], total

    @staticmethod
    def _decode_canonical_row(d: dict) -> dict:
        p = d.pop("parties_json", None)
        a = d.pop("amounts_json", None)
        s = d.pop("source_mappings_json", None)
        d["parties"] = json.loads(p) if isinstance(p, str) else (p or [])
        d["amounts"] = json.loads(a) if isinstance(a, str) else (a or {})
        d["source_mappings"] = json.loads(s) if isinstance(s, str) else (s or [])
        if d.get("total_amount") is not None:
            d["total_amount"] = str(d["total_amount"])
        return d

    # ---------- 轻量持久化异步任务队列（async_jobs） ----------
    def enqueue_async_job(self, job: dict) -> tuple[str, bool]:
        tenant = job.get("tenant_id") or "default"
        idem = job.get("idempotency_key")
        created_by = job.get("created_by", "system")
        next_ts = datetime.fromtimestamp(
            float(job.get("next_run_at", time.time())), tz=timezone.utc
        )
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    if idem:
                        cur.execute(
                            "SELECT job_id FROM async_jobs WHERE tenant_id=%s AND created_by=%s AND idempotency_key=%s",
                            (tenant, created_by, idem),
                        )
                        ex = cur.fetchone()
                        if ex:
                            self._commit()
                            return ex[0], False
                    jid = job.get("job_id") or f"job_{uuid.uuid4().hex[:12]}"
                    cur.execute(
                        "INSERT INTO async_jobs (job_id, idempotency_key, tenant_id, job_type, status,"
                        " payload_json, result_json, error_message, dead_letter_reason, progress,"
                        " attempts, max_attempts, next_run_at, cancel_requested, temp_file_path, created_by)"
                        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            jid,
                            idem,
                            tenant,
                            job["job_type"],
                            job.get("status", "PENDING"),
                            json.dumps(job.get("payload", {}), ensure_ascii=False),
                            json.dumps(job.get("result"), ensure_ascii=False)
                            if job.get("result") is not None
                            else None,
                            job.get("error_message"),
                            job.get("dead_letter_reason"),
                            int(job.get("progress", 0)),
                            int(job.get("attempts", 0)),
                            int(job.get("max_attempts", 3)),
                            next_ts,
                            bool(job.get("cancel_requested", False)),
                            job.get("temp_file_path"),
                            job.get("created_by", "system"),
                        ),
                    )
                self._commit()
                return jid, True
            except Exception:
                self._rollback()
                raise

    def get_async_job(self, job_id: str, tenant_id: str | None = None) -> dict | None:
        with self._lock:
            sql = "SELECT * FROM async_jobs WHERE job_id=%s"
            params: list[Any] = [job_id]
            if tenant_id is not None:
                sql += " AND tenant_id=%s"
                params.append(tenant_id)
            with self.conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                cols = [d[0] for d in cur.description] if cur.description else []
            self._commit()
            return self._decode_job_row(dict(zip(cols, row))) if row else None

    def list_async_jobs(
        self,
        tenant_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        created_by: str | None = None,
    ) -> tuple[list[dict], int]:
        with self._lock:
            where = " WHERE 1=1"
            params: list[Any] = []
            if tenant_id is not None:
                where += " AND tenant_id=%s"
                params.append(tenant_id)
            if status:
                where += " AND status=%s"
                params.append(status)
            if created_by is not None:
                where += " AND created_by=%s"
                params.append(created_by)
            with self.conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM async_jobs" + where, params)
                total = int(cur.fetchone()[0])
                cur.execute(
                    "SELECT * FROM async_jobs"
                    + where
                    + " ORDER BY created_at DESC LIMIT %s OFFSET %s",
                    params + [int(limit), int(offset)],
                )
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description] if cur.description else []
            self._commit()
            return [self._decode_job_row(dict(zip(cols, r))) for r in rows], total

    def claim_job(
        self,
        worker_id: str,
        lease_seconds: int = 60,
        job_types: list[str] | None = None,
    ) -> JobLease | None:
        now_dt = datetime.fromtimestamp(time.time(), tz=timezone.utc)
        lease_until_dt = datetime.fromtimestamp(
            time.time() + max(1, int(lease_seconds)), tz=timezone.utc
        )
        new_token = uuid.uuid4().hex
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    sql = (
                        "SELECT job_id, job_type, tenant_id, created_by, payload_json, cancel_requested, attempts, max_attempts, status, temp_file_path "
                        "FROM async_jobs "
                        "WHERE ((status IN ('PENDING', 'RETRYING') AND next_run_at <= %s) "
                        "OR (status = 'RUNNING' AND lease_until < %s))"
                    )
                    params: list[Any] = [now_dt, now_dt]
                    if job_types:
                        placeholders = ",".join("%s" for _ in job_types)
                        sql += f" AND job_type IN ({placeholders})"
                        params.extend(job_types)
                    sql += " ORDER BY next_run_at ASC, created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED"
                    cur.execute(sql, params)
                    row = cur.fetchone()
                    if not row:
                        self._commit()
                        return None
                    (
                        jid,
                        jtype,
                        tid,
                        cby,
                        pjson,
                        creq,
                        attempts,
                        max_att,
                        status,
                        temp_path,
                    ) = row
                    if creq:
                        cur.execute(
                            "UPDATE async_jobs SET status='CANCELLED', updated_at=now() WHERE job_id=%s",
                            (jid,),
                        )
                        self._commit()
                        return None

                    curr_att = int(attempts or 0)
                    m_att = int(max_att or 3)
                    if status == "RUNNING" and curr_att >= m_att:
                        dlq_reason = f"lease_expired_attempts_exhausted({m_att})"
                        cur.execute(
                            "UPDATE async_jobs SET status='DEAD_LETTER', dead_letter_reason=%s, updated_at=now() WHERE job_id=%s",
                            (dlq_reason, jid),
                        )
                        self._commit()
                        return None

                    new_att = curr_att + 1
                    if new_att > m_att:
                        dlq_reason = f"lease_expired_attempts_exhausted({m_att})"
                        cur.execute(
                            "UPDATE async_jobs SET status='DEAD_LETTER', dead_letter_reason=%s, updated_at=now() WHERE job_id=%s",
                            (dlq_reason, jid),
                        )
                        self._commit()
                        return None

                    cur.execute(
                        "UPDATE async_jobs SET status='RUNNING', worker_id=%s, lease_token=%s, lease_until=%s, heartbeat_at=now(), attempts=%s, updated_at=now() WHERE job_id=%s",
                        (worker_id, new_token, lease_until_dt, new_att, jid),
                    )
                self._commit()
                payload = (
                    pjson
                    if isinstance(pjson, dict)
                    else (json.loads(pjson) if pjson else {})
                )
                return JobLease(
                    job_id=jid,
                    worker_id=worker_id,
                    lease_token=new_token,
                    lease_until=time.time() + max(1, int(lease_seconds)),
                    attempt=new_att,
                    job_type=jtype,
                    tenant_id=tid,
                    created_by=str(cby or "system"),
                    max_attempts=m_att,
                    payload=payload,
                    temp_file_path=temp_path,
                )
            except Exception:
                self._rollback()
                raise

    def heartbeat_job(
        self, job_id: str, lease_token: str, extension_seconds: int = 60
    ) -> bool:
        now_dt = datetime.fromtimestamp(time.time(), tz=timezone.utc)
        until_dt = datetime.fromtimestamp(
            time.time() + max(1, int(extension_seconds)), tz=timezone.utc
        )
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    cur.execute(
                        "UPDATE async_jobs SET lease_until=%s, heartbeat_at=%s, updated_at=now() "
                        "WHERE job_id=%s AND lease_token=%s AND status='RUNNING' AND lease_until >= %s",
                        (until_dt, now_dt, job_id, lease_token, now_dt),
                    )
                    ok = cur.rowcount > 0
                self._commit()
                return ok
            except Exception:
                self._rollback()
                raise

    def finish_job(self, job_id: str, lease_token: str, outcome: JobOutcome) -> bool:
        next_dt = (
            datetime.fromtimestamp(outcome.next_run_at, tz=timezone.utc)
            if outcome.next_run_at is not None
            else None
        )
        res_json = (
            json.dumps(outcome.result, ensure_ascii=False)
            if outcome.result is not None
            else None
        )
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    cur.execute(
                        "UPDATE async_jobs SET status=%s, result_json=%s, error_message=%s, "
                        "dead_letter_reason=%s, progress=%s, "
                        "next_run_at=COALESCE(%s, next_run_at), updated_at=now() "
                        "WHERE job_id=%s AND lease_token=%s",
                        (
                            outcome.status,
                            res_json,
                            outcome.error_message,
                            outcome.dead_letter_reason,
                            outcome.progress,
                            next_dt,
                            job_id,
                            lease_token,
                        ),
                    )
                    ok = cur.rowcount > 0
                self._commit()
                if ok and outcome.status == "SUCCEEDED":
                    self.finalize_idempotency_key(
                        resource_id=job_id, status="COMPLETED"
                    )
                return ok
            except Exception:
                self._rollback()
                raise

    def claim_next_async_job(self, job_types: list[str] | None = None) -> dict | None:
        lease = self.claim_job(
            worker_id="default", lease_seconds=60, job_types=job_types
        )
        if lease:
            return self.get_async_job(lease.job_id)
        return None

    def _legacy_claim_next_async_job(
        self, job_types: list[str] | None = None
    ) -> dict | None:
        now_ts = datetime.fromtimestamp(time.time(), tz=timezone.utc)
        with self._lock:
            try:
                sql = (
                    "SELECT job_id, cancel_requested, attempts FROM async_jobs"
                    " WHERE status IN ('PENDING', 'RETRYING') AND next_run_at <= %s"
                )
                params: list[Any] = [now_ts]
                if job_types:
                    placeholders = ",".join("%s" for _ in job_types)
                    sql += f" AND job_type IN ({placeholders})"
                    params.extend(job_types)
                sql += " ORDER BY next_run_at ASC, created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED"
                with self.conn.cursor() as cur:
                    cur.execute(sql, params)
                    row = cur.fetchone()
                    if not row:
                        self._commit()
                        return None
                    jid, cancel_req, attempts = row
                    if cancel_req:
                        cur.execute(
                            "UPDATE async_jobs SET status='CANCELLED', updated_at=now() WHERE job_id=%s",
                            (jid,),
                        )
                        self._commit()
                        return None
                    cur.execute(
                        "UPDATE async_jobs SET status='RUNNING', attempts=%s, updated_at=now() WHERE job_id=%s",
                        (int(attempts or 0) + 1, jid),
                    )
                self._commit()
                return self.get_async_job(jid)
            except Exception:
                self._rollback()
                raise

    def update_async_job(self, job_id: str, **fields: Any) -> bool:
        if not fields:
            return False
        allowed = {
            "status",
            "result",
            "error_message",
            "dead_letter_reason",
            "progress",
            "attempts",
            "next_run_at",
            "cancel_requested",
            "temp_file_path",
        }
        assignments: list[str] = ["updated_at=now()"]
        params: list[Any] = []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "result":
                assignments.append("result_json=%s")
                params.append(
                    json.dumps(v, ensure_ascii=False) if v is not None else None
                )
            elif k == "next_run_at":
                assignments.append("next_run_at=%s")
                params.append(datetime.fromtimestamp(float(v), tz=timezone.utc))
            elif k == "cancel_requested":
                assignments.append("cancel_requested=%s")
                params.append(bool(v))
            else:
                assignments.append(f"{k}=%s")
                params.append(v)
        params.append(job_id)
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    cur.execute(
                        f"UPDATE async_jobs SET {', '.join(assignments)} WHERE job_id=%s",
                        params,
                    )
                    updated = cur.rowcount
                self._commit()
                return bool(updated)
            except Exception:
                self._rollback()
                raise

    def cancel_async_job(self, job_id: str, tenant_id: str | None = None) -> bool:
        with self._lock:
            try:
                row = self.get_async_job(job_id, tenant_id=tenant_id)
                if not row:
                    return False
                new_status = (
                    "CANCELLED"
                    if row["status"] in ("PENDING", "RETRYING")
                    else row["status"]
                )
                with self.conn.cursor() as cur:
                    cur.execute(
                        "UPDATE async_jobs SET cancel_requested=TRUE, status=%s, updated_at=now() WHERE job_id=%s",
                        (new_status, job_id),
                    )
                self._commit()
                return True
            except Exception:
                self._rollback()
                raise

    @staticmethod
    def _decode_job_row(d: dict) -> dict:
        p = d.pop("payload_json", None)
        r = d.pop("result_json", None)
        d["payload"] = json.loads(p) if isinstance(p, str) else (p or {})
        d["result"] = json.loads(r) if isinstance(r, str) else r
        d["cancel_requested"] = bool(d.get("cancel_requested", False))
        return d

    # ---------- 公司格式标准（与 SQLite 语义一致） ----------
    def save_format_standard(
        self,
        standard_id: str,
        params: dict,
        created_by: str,
        version: int | None = None,
        note: str = "",
        tenant_id: str | None = None,
    ) -> dict:
        resolved_tenant = tenant_id or "default"
        has_tenant = "tenant_id" in self._table_cols("format_standards")
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    if version is None:
                        cur.execute(
                            "SELECT MAX(version) FROM format_standards WHERE standard_id=%s",
                            (standard_id,),
                        )
                        version = int((cur.fetchone()[0] or 0) + 1)
                    if has_tenant:
                        cur.execute(
                            "INSERT INTO format_standards (standard_id, version, params, status,"
                            " created_by, note, tenant_id) VALUES (%s,%s,%s,'DRAFT',%s,%s,%s)",
                            (
                                standard_id,
                                int(version),
                                json.dumps(params, ensure_ascii=False),
                                created_by,
                                note,
                                resolved_tenant,
                            ),
                        )
                    else:
                        cur.execute(
                            "INSERT INTO format_standards (standard_id, version, params, status,"
                            " created_by, note) VALUES (%s,%s,%s,'DRAFT',%s,%s)",
                            (
                                standard_id,
                                int(version),
                                json.dumps(params, ensure_ascii=False),
                                created_by,
                                note,
                            ),
                        )
                    cur.execute(
                        "INSERT INTO audit_log (actor, action, target, detail)"
                        " VALUES (%s,'save_format_standard',%s,%s)",
                        (created_by, standard_id, f"version={version}"),
                    )
                self._commit()
            except Exception:
                self._rollback()
                raise
            rec = self.get_format_standard(
                standard_id, int(version), tenant_id=tenant_id
            )
            if rec is None:
                raise RuntimeError(
                    f"格式标准保存失败（写入后无法读取）：{standard_id} v{version}"
                )
            return rec

    def approve_format_standard(
        self,
        standard_id: str,
        version: int,
        approver: str,
        tenant_id: str | None = None,
    ) -> bool:
        has_tenant = "tenant_id" in self._table_cols("format_standards")
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    if has_tenant:
                        cur.execute(
                            "SELECT tenant_id FROM format_standards WHERE standard_id=%s"
                            " AND version=%s AND status='DRAFT'",
                            (standard_id, int(version)),
                        )
                        row = cur.fetchone()
                        if not row:
                            self._commit()
                            return False
                        if tenant_id is not None and row[0] != tenant_id:
                            self._commit()
                            return False
                        cur.execute(
                            "UPDATE format_standards SET status='SUPERSEDED'"
                            " WHERE standard_id=%s AND status='ACTIVE' AND tenant_id=%s",
                            (standard_id, row[0]),
                        )
                    else:
                        cur.execute(
                            "UPDATE format_standards SET status='APPROVED' WHERE standard_id=%s"
                            " AND version=%s AND status='DRAFT'",
                            (standard_id, int(version)),
                        )
                        if not cur.rowcount:
                            self._commit()
                            return False
                        cur.execute(
                            "UPDATE format_standards SET status='SUPERSEDED'"
                            " WHERE standard_id=%s AND status='ACTIVE'",
                            (standard_id,),
                        )
                    cur.execute(
                        "UPDATE format_standards SET status='ACTIVE', approved_by=%s,"
                        " approved_at=now(), effective_at=now() WHERE standard_id=%s AND version=%s",
                        (approver, standard_id, int(version)),
                    )
                    cur.execute(
                        "INSERT INTO audit_log (actor, action, target, detail)"
                        " VALUES (%s,'approve_format_standard',%s,%s)",
                        (approver, standard_id, f"version={version}"),
                    )
                self._commit()
                return True
            except Exception:
                self._rollback()
                raise

    def get_format_standard(
        self,
        standard_id: str,
        version: int | None = None,
        tenant_id: str | None = None,
    ) -> dict | None:
        has_tenant = "tenant_id" in self._table_cols("format_standards")
        with self._lock:
            with self.conn.cursor() as cur:
                if version is None:
                    sql = "SELECT * FROM format_standards WHERE standard_id=%s"
                    params: list[Any] = [standard_id]
                    if tenant_id is not None and has_tenant:
                        sql += " AND tenant_id=%s"
                        params.append(tenant_id)
                    sql += " ORDER BY version DESC LIMIT 1"
                    cur.execute(sql, params)
                else:
                    sql = "SELECT * FROM format_standards WHERE standard_id=%s AND version=%s"
                    params = [standard_id, int(version)]
                    if tenant_id is not None and has_tenant:
                        sql += " AND tenant_id=%s"
                        params.append(tenant_id)
                    cur.execute(sql, params)
                row = cur.fetchone()
                cols = [c[0] for c in cur.description] if cur.description else []
            self._commit()
            if not row:
                return None
            d = dict(zip(cols, row))
            if isinstance(d.get("params"), str):
                d["params"] = json.loads(d["params"])
            return d

    def get_active_format_standard(
        self, standard_id: str = "company_format", tenant_id: str | None = None
    ) -> dict | None:
        has_tenant = "tenant_id" in self._table_cols("format_standards")
        with self._lock:
            with self.conn.cursor() as cur:
                sql = "SELECT * FROM format_standards WHERE standard_id=%s AND status='ACTIVE'"
                params: list[Any] = [standard_id]
                if tenant_id is not None and has_tenant:
                    sql += " AND tenant_id=%s"
                    params.append(tenant_id)
                sql += " ORDER BY version DESC LIMIT 1"
                cur.execute(sql, params)
                row = cur.fetchone()
                cols = [c[0] for c in cur.description] if cur.description else []
            self._commit()
            if not row:
                return None
            d = dict(zip(cols, row))
            if isinstance(d.get("params"), str):
                d["params"] = json.loads(d["params"])
            return d

    # ---------- 请求级幂等预约控制（Request Idempotency - PostgreSQL） ----------
    def reserve_idempotency_key(
        self,
        identity: RequestIdentity,
        resource_id: str,
    ) -> IdempotencyOutcome:
        """为特定租户、操作人和操作原子化预约幂等键（PostgreSQL）。"""
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    cur.execute(
                        "SELECT request_fingerprint, status, resource_id "
                        "FROM request_idempotency "
                        "WHERE tenant_id=%s AND actor_id=%s AND operation=%s AND idempotency_key=%s "
                        "FOR UPDATE",
                        (
                            identity.tenant_id,
                            identity.actor_id,
                            identity.operation,
                            identity.key,
                        ),
                    )
                    row = cur.fetchone()
                    if row:
                        fingerprint, status, res_id = row[0], row[1], row[2]
                        if fingerprint != identity.fingerprint:
                            self._commit()
                            return IdempotencyOutcome(
                                status="CONFLICT",
                                resource_id=res_id,
                                message=f"Idempotency key conflict: request fingerprint mismatch for key {identity.key}",
                            )
                        if status == "COMPLETED":
                            self._commit()
                            return IdempotencyOutcome(
                                status="REPLAY",
                                resource_id=res_id,
                            )
                        self._commit()
                        return IdempotencyOutcome(
                            status="IN_PROGRESS",
                            resource_id=res_id,
                        )

                    cur.execute(
                        "INSERT INTO request_idempotency "
                        "(tenant_id, actor_id, operation, idempotency_key, request_fingerprint, status, resource_id, created_at, updated_at) "
                        "VALUES (%s, %s, %s, %s, %s, 'IN_PROGRESS', %s, now(), now()) "
                        "ON CONFLICT (tenant_id, actor_id, operation, idempotency_key) DO NOTHING",
                        (
                            identity.tenant_id,
                            identity.actor_id,
                            identity.operation,
                            identity.key,
                            identity.fingerprint,
                            resource_id,
                        ),
                    )
                    if cur.rowcount > 0:
                        self._commit()
                        return IdempotencyOutcome(
                            status="CREATED",
                            resource_id=resource_id,
                        )
                    else:
                        cur.execute(
                            "SELECT request_fingerprint, status, resource_id "
                            "FROM request_idempotency "
                            "WHERE tenant_id=%s AND actor_id=%s AND operation=%s AND idempotency_key=%s",
                            (
                                identity.tenant_id,
                                identity.actor_id,
                                identity.operation,
                                identity.key,
                            ),
                        )
                        existing = cur.fetchone()
                        self._commit()
                        if existing:
                            if existing[0] != identity.fingerprint:
                                return IdempotencyOutcome(
                                    status="CONFLICT",
                                    resource_id=existing[2],
                                    message=f"Idempotency key conflict: request fingerprint mismatch for key {identity.key}",
                                )
                            if existing[1] == "COMPLETED":
                                return IdempotencyOutcome(
                                    status="REPLAY",
                                    resource_id=existing[2],
                                )
                            return IdempotencyOutcome(
                                status="IN_PROGRESS",
                                resource_id=existing[2],
                            )
                        return IdempotencyOutcome(
                            status="CREATED", resource_id=resource_id
                        )
            except Exception:
                self._rollback()
                raise

    def finalize_idempotency_key(
        self,
        identity: RequestIdentity | None = None,
        resource_id: str | None = None,
        status: str = "COMPLETED",
        *,
        tenant_id: str | None = None,
        actor_id: str | None = None,
        operation: str | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        """完成或更新幂等记录的状态（PostgreSQL）。"""
        t_id = identity.tenant_id if identity else (tenant_id or "default")
        a_id = identity.actor_id if identity else (actor_id or "")
        op = identity.operation if identity else (operation or "")
        key = identity.key if identity else idempotency_key
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    if key and a_id and op:
                        cur.execute(
                            "UPDATE request_idempotency SET status=%s, updated_at=now() "
                            "WHERE tenant_id=%s AND actor_id=%s AND operation=%s AND idempotency_key=%s",
                            (status, t_id, a_id, op, key),
                        )
                    elif resource_id:
                        cur.execute(
                            "UPDATE request_idempotency SET status=%s, updated_at=now() "
                            "WHERE tenant_id=%s AND resource_id=%s",
                            (status, t_id, resource_id),
                        )
                self._commit()
            except Exception:
                self._rollback()
                raise

    def record_and_check_nonce(
        self, nonce: str, tenant_id: str, user_id: str, expires_at: float
    ) -> bool:
        """记录并校验防重放 Nonce（PostgreSQL）。"""
        now_ts = datetime.fromtimestamp(time.time(), tz=timezone.utc)
        exp_ts = datetime.fromtimestamp(expires_at, tz=timezone.utc)
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM auth_replay_nonces WHERE expires_at < %s",
                        (now_ts,),
                    )
                    cur.execute(
                        "INSERT INTO auth_replay_nonces (nonce, tenant_id, user_id, created_at, expires_at) "
                        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (nonce) DO NOTHING",
                        (nonce, tenant_id, user_id, now_ts, exp_ts),
                    )
                    rc = cur.rowcount
                self._commit()
                return rc > 0
            except Exception:
                self._rollback()
                raise


# Clean Architecture alias
StoragePG = PgVectorStorage
