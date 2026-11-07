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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .embeddings import EmbeddingProvider, get_embedding_provider
from .storage_errors import EmbeddingConfigMismatch

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
            self.migrate()
        try:
            self._check_embedding_compat()
        except EmbeddingConfigMismatch:
            if not rebuild_on_mismatch:
                raise
            self.rebuild_index()

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
                self.conn.commit()
                self._col_cache.clear()
            except Exception:
                self.conn.rollback()
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
                self.conn.commit()
            except Exception:
                self.conn.rollback()
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
            self.conn.commit()
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
            self.conn.commit()
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
                        self.conn.commit()
                        return row[0], False

                    base_did = doc["template_id"]
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
                                c.get("approval_status", "PENDING_REVIEW"),
                                json.dumps(
                                    c.get("source_node_ids", []), ensure_ascii=False
                                ),
                                c.get("knowledge_type") or "LAYER_A_RAW_CLAUSE",
                            ),
                        )
                self._index_clauses(did, commit=False)
                self._ensure_index_meta(commit=False)
                self.conn.commit()
                return did, True
            except Exception:
                self.conn.rollback()
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
                resolved_ktype = (
                    ktype if ktype and ktype != "LAYER_A_RAW_CLAUSE" else "LAYER_D_RAG"
                )
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
            self.conn.commit()

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
                self.conn.commit()
                return ver
            except Exception:
                self.conn.rollback()
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
                self.conn.commit()
                self.rebuild_index()
            except Exception:
                self.conn.rollback()
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
                        self.conn.commit()
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
                self.conn.commit()
                self.rebuild_index()
                return True
            except Exception:
                self.conn.rollback()
                raise

    def log_audit(self, actor: str, action: str, target: str, detail: str = "") -> None:
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO audit_log (actor, action, target, detail) VALUES (%s,%s,%s,%s)",
                        (actor, action, target, detail),
                    )
                self.conn.commit()
            except Exception:
                self.conn.rollback()
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
            self.conn.commit()
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
            self.conn.commit()
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
            self.conn.commit()
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
                self.conn.commit()
                return bool(updated)
            except Exception:
                self.conn.rollback()
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
                        self.conn.commit()
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
                        self.conn.commit()
                        return dict(zip(cols, row))
            self.conn.commit()
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
            self.conn.commit()
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

    def get_clause(self, clause_id: str) -> dict | None:
        with self._lock:
            with self.conn.cursor() as cur:
                cur.execute("SELECT * FROM clauses WHERE clause_id=%s", (clause_id,))
                row = cur.fetchone()
                cols = [d[0] for d in cur.description] if cur.description else []
            self.conn.commit()
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
                self.conn.commit()
            except Exception:
                self.conn.rollback()
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
            self.conn.commit()
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
            fts = self._fts_search(
                keyword or query,
                top_k * 3,
                business_category,
                contract_subtype,
                clause_number,
                knowledge_type,
                approved_only,
                tenant_id,
            )
            vec = self._vector_search(
                query,
                top_k * 3,
                business_category,
                contract_subtype,
                knowledge_type,
                approved_only,
                tenant_id,
            )
            self.conn.commit()
            fused = self._rrf(fts, vec)
            if clause_number:
                fused = [h for h in fused if h.get("clause_number") == clause_number]
            if rerank:
                fused = self._rerank(query, fused)
            return fused[:top_k]

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
    ) -> list[dict]:
        words = [w for w in re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z0-9]+", query)][:8]
        for w in list(words):
            if len(w) >= 3:
                words.extend(w[i : i + 2] for i in range(len(w) - 1))
        words = words[:24]
        if not words:
            return []
        match = " | ".join(words)
        where, params = self._where(
            business_category,
            contract_subtype,
            knowledge_type,
            approved_only,
            tenant_id,
        )
        extra, eparams = "", []
        if clause_number:
            extra = " AND c.clause_number=%s"
            eparams.append(clause_number)
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT c.clause_id, c.document_id, c.original_text, c.clause_number,"
                " c.clause_category, e.chunk_id, e.business_category, e.contract_subtype,"
                " e.knowledge_type, e.approved_status FROM clauses c "
                "JOIN embeddings e ON e.clause_id=c.clause_id "
                "JOIN documents d ON d.document_id=c.document_id "
                "WHERE to_tsvector('simple', coalesce(c.original_text,'')) @@ to_tsquery('simple', %s)"
                + where
                + extra
                + " LIMIT %s",
                [match] + params + eparams + [limit],
            )
            rows = cur.fetchall()
        return [
            {
                "chunk_id": r[5],
                "clause_id": r[0],
                "document_id": r[1],
                "text": r[2],
                "clause_number": r[3],
                "clause_category": r[4],
                "channel": "fts",
                "business_category": r[6],
                "contract_subtype": r[7],
                "knowledge_type": r[8],
                "approved_status": r[9],
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
            " c.clause_number, c.clause_category, e.knowledge_type, e.approved_status "
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
                                rule.get("effective_at"),
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
                                rule.get("effective_at"),
                            ),
                        )
                self.conn.commit()
                return int(version)
            except Exception:
                self.conn.rollback()
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
                            self.conn.commit()
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
                self.conn.commit()
                return bool(updated)
            except Exception:
                self.conn.rollback()
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
            self.conn.commit()
            out = []
            for r in rows:
                d = dict(zip(cols, r))
                for k in ("contract_types", "params", "source_clauses"):
                    if isinstance(d.get(k), str):
                        d[k] = json.loads(d[k])
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
            self.conn.commit()
            return rows

    def save_task(
        self,
        task_id: str,
        document_id: str,
        mode: str,
        status: str,
        meta: dict,
        tenant_id: str = "default",
        created_by: str = "system",
    ) -> None:
        with self._lock:
            try:
                with self.conn.cursor() as cur:
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
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def get_task(self, task_id: str) -> dict | None:
        with self._lock:
            with self.conn.cursor() as cur:
                cur.execute("SELECT * FROM review_tasks WHERE task_id=%s", (task_id,))
                row = cur.fetchone()
                cols = [d[0] for d in cur.description] if cur.description else []
            self.conn.commit()
            if not row:
                return None
            d = dict(zip(cols, row))
            if isinstance(d.get("meta"), str):
                try:
                    d["meta"] = json.loads(d["meta"])
                except json.JSONDecodeError:
                    pass
            return d

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
            self.conn.commit()
            return dict(zip(cols, row)) if row else None

    def save_issues(self, task_id: str, issues: list[dict]) -> None:
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    for it in issues:
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
                                json.dumps(it.get("actual_value"), ensure_ascii=False),
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
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def get_issues(self, task_id: str) -> list[dict]:
        with self._lock:
            with self.conn.cursor() as cur:
                cur.execute("SELECT * FROM review_issues WHERE task_id=%s", (task_id,))
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description]
            self.conn.commit()
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
        self, issue_id: str, action: str, actor: str, note: str = ""
    ) -> bool:
        valid = {"ACCEPT", "IGNORE", "EDIT", "NOTE", "FALSE_POSITIVE", "FALSE_NEGATIVE"}
        if action not in valid:
            return False
        with self._lock:
            try:
                with self.conn.cursor() as cur:
                    cur.execute(
                        "UPDATE review_issues SET human_state=%s, human_note=%s WHERE issue_id=%s",
                        (action, note, issue_id),
                    )
                    updated = cur.rowcount
                    if updated:
                        cur.execute(
                            "INSERT INTO audit_log (actor, action, target, detail)"
                            " VALUES (%s,'issue_feedback',%s,%s)",
                            (actor, issue_id, f"{action} {note[:80]}"),
                        )
                self.conn.commit()
                return bool(updated)
            except Exception:
                self.conn.rollback()
                raise

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
                self.conn.commit()
            except Exception:
                self.conn.rollback()
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
                            self.conn.commit()
                            return False
                        if tenant_id is not None and row[0] != tenant_id:
                            self.conn.commit()
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
                            self.conn.commit()
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
                self.conn.commit()
                return True
            except Exception:
                self.conn.rollback()
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
            self.conn.commit()
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
            self.conn.commit()
            if not row:
                return None
            d = dict(zip(cols, row))
            if isinstance(d.get("params"), str):
                d["params"] = json.loads(d["params"])
            return d
