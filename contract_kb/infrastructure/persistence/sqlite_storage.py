# -*- coding: utf-8 -*-
"""知识库存储层（四层知识）+ 混合检索（FTS5 + 向量 + RRF 融合 + Reranker）。

数据库适配说明（如实标注）：
- 本地/测试默认 **SQLite（FTS5 全文 + numpy 向量）**，全部测试真实执行；
- 生产拓扑为 PostgreSQL + pgvector（storage_pg.PgVectorStorage），通过 `create_storage()`
  统一工厂按 `CONTRACT_KB_STORAGE_BACKEND` / `CONTRACT_KB_PG_DSN` 注入（fail-closed，
  显式配置 PostgreSQL 时绝不静默回退到 SQLite）；
- 同一 Storage 接口下可替换 PgVectorStorage；Embedding 维度/提供方变更必须 rebuild_index（版本化），
  否则 EmbeddingConfigMismatch（fail-closed，禁止混用不同模型的向量）。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from ... import config
from ...application.security.idempotency import IdempotencyOutcome, RequestIdentity
from ...domain.models.execution import JobLease, JobOutcome
from ..ai.embedding_providers import (
    EmbeddingProvider,
    get_embedding_provider,
)
from ..observability.redaction import redact_sensitive_text
from .errors import (
    EmbeddingConfigMismatch,
    OptimisticLockConflictError,
    StorageConfigError,
)

__all__ = [
    "EmbeddingConfigMismatch",
    "OptimisticLockConflictError",
    "StorageConfigError",
    "Storage",
    "create_storage",
]

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    template_id TEXT,
    source_file TEXT, source_hash TEXT, source_version TEXT,
    tenant_id TEXT NOT NULL DEFAULT 'default', business_category TEXT, contract_subtype TEXT,
    document_type TEXT, imported_at REAL, status TEXT, raw_meta TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    UNIQUE (tenant_id, source_hash)
);
CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY, document_id TEXT, node_type TEXT, ord INTEGER,
    text TEXT, section TEXT, clause_number TEXT,
    table_index INTEGER, row_index INTEGER, column_index INTEGER,
    source_locator TEXT, extraction_status TEXT
);
CREATE TABLE IF NOT EXISTS clauses (
    clause_id TEXT PRIMARY KEY, document_id TEXT, clause_number TEXT,
    clause_category TEXT, clause_subcategory TEXT,
    original_text TEXT, normalized_summary TEXT,
    is_company_standard INTEGER, approval_status TEXT,
    source_node_ids TEXT, knowledge_type TEXT, effective_time TEXT,
    revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS fields (
    field_key TEXT, document_id TEXT, field_name TEXT, data_type TEXT,
    required_candidate INTEGER, required_approved INTEGER, meta TEXT
);
CREATE TABLE IF NOT EXISTS rules (
    rule_id TEXT, version INTEGER, name TEXT, rule_type TEXT,
    execution_mode TEXT, severity TEXT, priority INTEGER NOT NULL DEFAULT 100,
    contract_types TEXT, params TEXT, source_clauses TEXT, status TEXT,
    approved_by TEXT, effective_at REAL, effective_until REAL,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    PRIMARY KEY (rule_id, version)
);
CREATE TABLE IF NOT EXISTS review_tasks (
    task_id TEXT PRIMARY KEY, document_id TEXT, mode TEXT, status TEXT,
    created_at REAL, updated_at REAL, meta TEXT,
    tenant_id TEXT NOT NULL DEFAULT 'default', created_by TEXT,
    idempotency_key TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    rule_set_version TEXT,
    model_version TEXT,
    reproducibility_hash TEXT
);
CREATE TABLE IF NOT EXISTS review_issues (
    issue_id TEXT PRIMARY KEY, task_id TEXT, rule_id TEXT, severity TEXT,
    category TEXT, title TEXT, description TEXT,
    expected_value TEXT, actual_value TEXT, source_ref TEXT,
    knowledge_refs TEXT, suggestion TEXT, execution_mode TEXT,
    requires_human_review INTEGER, confidence REAL,
    human_state TEXT DEFAULT 'PENDING', human_note TEXT,
    finding_source TEXT NOT NULL DEFAULT 'DETERMINISTIC',
    check_status TEXT NOT NULL DEFAULT 'CHECKED'
);
CREATE TABLE IF NOT EXISTS rule_executions (
    exec_id TEXT PRIMARY KEY, rule_id TEXT, rule_version INTEGER, rule_type TEXT,
    result TEXT, severity TEXT, message TEXT, evidence TEXT, executed_at REAL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL, actor TEXT,
    action TEXT, target TEXT, detail TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS clauses_fts USING fts5(
    original_text, normalized_summary, clause_category,
    content='clauses', content_rowid='rowid'
);
CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id TEXT PRIMARY KEY, document_id TEXT, clause_id TEXT,
    business_category TEXT, contract_subtype TEXT, knowledge_type TEXT,
    approved_status TEXT, effective_time TEXT, source_hash TEXT,
    text TEXT, vec BLOB
);
CREATE TABLE IF NOT EXISTS embedding_index_meta (
    version INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT, dim INTEGER, model_spec TEXT,
    created_at REAL, status TEXT
);
CREATE TABLE IF NOT EXISTS format_standards (
    standard_id TEXT, version INTEGER, params TEXT, status TEXT,
    created_by TEXT, approved_by TEXT, approved_at REAL, effective_at REAL,
    note TEXT, tenant_id TEXT NOT NULL DEFAULT 'default',
    PRIMARY KEY (standard_id, version)
);
CREATE TABLE IF NOT EXISTS review_stage_states (
    task_id TEXT NOT NULL,
    stage_name TEXT NOT NULL,
    stage_order INTEGER NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    started_at REAL,
    finished_at REAL,
    duration_ms REAL NOT NULL DEFAULT 0,
    detail_json TEXT,
    error TEXT,
    PRIMARY KEY (task_id, stage_name)
);
CREATE TABLE IF NOT EXISTS human_overrides (
    override_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    issue_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT,
    previous_state TEXT,
    new_state TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS canonical_contract_versions (
    contract_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    tenant_id TEXT NOT NULL,
    snapshot TEXT,
    created_at REAL NOT NULL,
    PRIMARY KEY (contract_id, version)
);
CREATE INDEX IF NOT EXISTS idx_canonical_versions_lookup
    ON canonical_contract_versions(contract_id, version DESC);

CREATE TABLE IF NOT EXISTS contract_source_mappings (
    tenant_id TEXT NOT NULL,
    source_system TEXT NOT NULL,
    external_id TEXT NOT NULL,
    contract_id TEXT NOT NULL,
    external_code TEXT,
    source_hash TEXT,
    synced_at REAL NOT NULL,
    PRIMARY KEY (tenant_id, source_system, external_id)
);
CREATE INDEX IF NOT EXISTS idx_source_mappings_contract
    ON contract_source_mappings(contract_id);
CREATE TABLE IF NOT EXISTS canonical_contracts (
    contract_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    contract_number TEXT,
    entity_prefix TEXT,
    title TEXT,
    business_category TEXT,
    contract_subtype TEXT,
    lifecycle_status TEXT NOT NULL DEFAULT 'DRAFT',
    source_system TEXT NOT NULL DEFAULT 'UPLOAD',
    source_document_id TEXT,
    source_hash TEXT,
    total_amount TEXT,
    currency TEXT NOT NULL DEFAULT 'CNY',
    parties_json TEXT,
    amounts_json TEXT,
    source_mappings_json TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS async_jobs (
    job_id TEXT PRIMARY KEY,
    idempotency_key TEXT,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    payload_json TEXT,
    result_json TEXT,
    error_message TEXT,
    dead_letter_reason TEXT,
    progress INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    next_run_at REAL NOT NULL,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    temp_file_path TEXT,
    created_by TEXT,
    worker_id TEXT,
    lease_token TEXT,
    lease_until REAL NOT NULL DEFAULT 0.0,
    heartbeat_at REAL NOT NULL DEFAULT 0.0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS request_idempotency (
    tenant_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'IN_PROGRESS',
    resource_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (tenant_id, actor_id, operation, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_req_idem_lookup
    ON request_idempotency (tenant_id, resource_id);
CREATE TABLE IF NOT EXISTS auth_replay_nonces (
    nonce TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    user_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auth_nonces_expiry
    ON auth_replay_nonces(expires_at);
CREATE TABLE IF NOT EXISTS knowledge_approval_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    knowledge_id TEXT NOT NULL,
    knowledge_type TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT 'v1',
    approver_id TEXT NOT NULL,
    approval_action TEXT NOT NULL,
    approval_reason TEXT,
    clause_ids TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_approvals_lookup
    ON knowledge_approval_records(tenant_id, knowledge_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_clauses_approval_ktype
    ON clauses(approval_status, knowledge_type);
"""

_CLEAN_RE = re.compile(r"[\s\u3000，。；：、“”‘’（）()《》\-—_/%‰]")


def _norm_text(t: str) -> str:
    return _CLEAN_RE.sub("", t).lower()


def create_storage(
    backend: str | None = None,
    db_path: str | Path | None = None,
    dsn: str | None = None,
    emb: EmbeddingProvider | None = None,
    rebuild_on_mismatch: bool = False,
) -> Any:
    """统一存储工厂（fail-closed）：
    - backend='postgres'/'postgresql'/'pg' 或显式配置 dsn/CONTRACT_KB_PG_DSN 时返回 PgVectorStorage；
      若缺少 DSN 或连接失败则直接抛出异常，绝不静默回退到 SQLite。
    - 默认 backend='sqlite' 时返回本地 SQLite Storage。
    """
    raw_backend = (
        backend
        if backend is not None
        else os.environ.get("CONTRACT_KB_STORAGE_BACKEND", config.STORAGE_BACKEND)
    )
    resolved_backend = (raw_backend or "sqlite").strip().lower()
    resolved_dsn = (
        dsn if dsn is not None else os.environ.get("CONTRACT_KB_PG_DSN", config.PG_DSN)
    ).strip()
    if resolved_backend == "auto":
        resolved_backend = "postgres" if resolved_dsn else "sqlite"

    if resolved_backend in ("postgres", "postgresql", "pg") or (
        backend is None and bool(resolved_dsn) and resolved_backend == "sqlite"
    ):
        if not resolved_dsn:
            raise StorageConfigError(
                "已指定 PostgreSQL 存储后端但未配置 CONTRACT_KB_PG_DSN；"
                "fail-closed 拒绝静默回退到 SQLite。"
            )
        from .postgres_storage import PgVectorStorage

        return PgVectorStorage(
            resolved_dsn,
            emb=emb,
            rebuild_on_mismatch=rebuild_on_mismatch,
        )

    if resolved_backend == "sqlite":
        config.ensure_dirs()
        target_db = db_path if db_path is not None else config.DB_PATH
        return Storage(target_db, emb=emb, rebuild_on_mismatch=rebuild_on_mismatch)

    raise StorageConfigError(
        f"不支持的存储后端配置：{resolved_backend!r}（可选 sqlite | postgres）"
    )


def _base_template_id(tpl: str | None) -> str:
    """提取模板基础标识符，去除尾部版本标识后缀（如 _v1, _v2, _1, _2 等）。"""
    if not tpl:
        return ""
    return re.sub(r"(_[vV]?\d+)$", "", tpl)


class Storage:
    """SQLite 本地实现：文档/节点/条款/字段/规则/审核任务 + FTS5 + 向量表。"""

    def __init__(
        self,
        db_path: str | Path,
        emb: EmbeddingProvider | None = None,
        rebuild_on_mismatch: bool = False,
    ):
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._tx_depth = 0
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.executescript(SCHEMA_SQL)
            self.emb = emb or get_embedding_provider()
            self._apply_column_migrations()
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
        self.conn.execute(f"SAVEPOINT {sp_name}")

    def _savepoint_commit(self, sp_name: str) -> None:
        self.conn.execute(f"RELEASE SAVEPOINT {sp_name}")
        if self._tx_depth <= 1:
            self.conn.commit()

    def _savepoint_rollback(self, sp_name: str) -> None:
        self.conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
        self.conn.execute(f"RELEASE SAVEPOINT {sp_name}")
        if self._tx_depth <= 1:
            self.conn.rollback()

    def _savepoint_exit(self) -> None:
        self._tx_depth = max(0, self._tx_depth - 1)
        self._lock.release()

    def unit_of_work(self) -> Any:
        """创建支持嵌套 SAVEPOINT 的原子工作单元。"""
        from .uow import SqliteUnitOfWork

        return SqliteUnitOfWork(self)

    def _table_cols(self, table: str) -> set[str]:
        return {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}

    def _apply_column_migrations(self) -> None:
        """既有库的增量列迁移（幂等）：租户隔离列、优先级列、流水线与幂等列。"""
        task_cols = self._table_cols("review_tasks")
        if "tenant_id" not in task_cols:
            self.conn.execute(
                "ALTER TABLE review_tasks ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
            )
        if "created_by" not in task_cols:
            self.conn.execute("ALTER TABLE review_tasks ADD COLUMN created_by TEXT")
        if "idempotency_key" not in task_cols:
            self.conn.execute(
                "ALTER TABLE review_tasks ADD COLUMN idempotency_key TEXT"
            )
        if "cancel_requested" not in task_cols:
            self.conn.execute(
                "ALTER TABLE review_tasks ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0"
            )
        if "rule_set_version" not in task_cols:
            self.conn.execute(
                "ALTER TABLE review_tasks ADD COLUMN rule_set_version TEXT"
            )
        if "model_version" not in task_cols:
            self.conn.execute("ALTER TABLE review_tasks ADD COLUMN model_version TEXT")
        if "reproducibility_hash" not in task_cols:
            self.conn.execute(
                "ALTER TABLE review_tasks ADD COLUMN reproducibility_hash TEXT"
            )

        issue_cols = self._table_cols("review_issues")
        if "finding_source" not in issue_cols:
            self.conn.execute(
                "ALTER TABLE review_issues ADD COLUMN finding_source TEXT NOT NULL DEFAULT 'DETERMINISTIC'"
            )
        if "check_status" not in issue_cols:
            self.conn.execute(
                "ALTER TABLE review_issues ADD COLUMN check_status TEXT NOT NULL DEFAULT 'CHECKED'"
            )

        rule_cols = self._table_cols("rules")
        if "priority" not in rule_cols:
            self.conn.execute(
                "ALTER TABLE rules ADD COLUMN priority INTEGER NOT NULL DEFAULT 100"
            )
        if "effective_until" not in rule_cols:
            self.conn.execute("ALTER TABLE rules ADD COLUMN effective_until REAL")
        if "tenant_id" not in rule_cols:
            self.conn.execute(
                "ALTER TABLE rules ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
            )

        doc_cols = self._table_cols("documents")
        if "revision" not in doc_cols:
            self.conn.execute(
                "ALTER TABLE documents ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
            )
        if "template_id" not in doc_cols:
            self.conn.execute("ALTER TABLE documents ADD COLUMN template_id TEXT")
            self.conn.execute(
                "UPDATE documents SET template_id = document_id WHERE template_id IS NULL OR template_id = ''"
            )

        # 检查并迁移旧版 SQLite 数据库全局 source_hash UNIQUE 约束至 (tenant_id, source_hash)
        needs_doc_rebuild = False
        try:
            indexes = self.conn.execute("PRAGMA index_list('documents')").fetchall()
            for idx in indexes:
                if idx[2] == 1:  # unique
                    idx_name = idx[1]
                    cols = [
                        r[2]
                        for r in self.conn.execute(
                            f"PRAGMA index_info('{idx_name}')"
                        ).fetchall()
                    ]
                    if cols == ["source_hash"]:
                        needs_doc_rebuild = True
                        break
        except Exception:
            pass

        if needs_doc_rebuild:
            self.conn.execute("SAVEPOINT sp_doc_schema_migrate")
            try:
                self.conn.execute(
                    "UPDATE documents SET tenant_id='default' WHERE tenant_id IS NULL OR trim(tenant_id)=''"
                )
                self.conn.execute(
                    """
                    CREATE TABLE documents_new (
                        document_id TEXT PRIMARY KEY,
                        template_id TEXT,
                        source_file TEXT, source_hash TEXT, source_version TEXT,
                        tenant_id TEXT NOT NULL DEFAULT 'default', business_category TEXT, contract_subtype TEXT,
                        document_type TEXT, imported_at REAL, status TEXT, raw_meta TEXT,
                        revision INTEGER NOT NULL DEFAULT 1,
                        UNIQUE (tenant_id, source_hash)
                    )
                    """
                )
                cols_now = self._table_cols("documents")
                has_tpl = "template_id" in cols_now
                has_rev = "revision" in cols_now
                tpl_expr = "template_id" if has_tpl else "document_id"
                rev_expr = "revision" if has_rev else "1"
                self.conn.execute(
                    f"""
                    INSERT INTO documents_new (
                        document_id, template_id, source_file, source_hash, source_version,
                        tenant_id, business_category, contract_subtype, document_type,
                        imported_at, status, raw_meta, revision
                    )
                    SELECT
                        document_id, {tpl_expr}, source_file, source_hash, source_version,
                        COALESCE(NULLIF(tenant_id, ''), 'default'), business_category, contract_subtype, document_type,
                        imported_at, status, raw_meta, {rev_expr}
                    FROM documents
                    """
                )
                self.conn.execute("DROP TABLE documents")
                self.conn.execute("ALTER TABLE documents_new RENAME TO documents")
                self.conn.execute("RELEASE SAVEPOINT sp_doc_schema_migrate")
            except Exception:
                self.conn.execute("ROLLBACK TO SAVEPOINT sp_doc_schema_migrate")
                raise

        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_docs_template_tenant ON documents(template_id, tenant_id)"
        )
        clause_cols = self._table_cols("clauses")
        if "revision" not in clause_cols:
            self.conn.execute(
                "ALTER TABLE clauses ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
            )

        fmt_cols = self._table_cols("format_standards")
        if "tenant_id" not in fmt_cols:
            self.conn.execute(
                "ALTER TABLE format_standards ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
            )

        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_docs_tenant_status ON documents(tenant_id, status)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_review_tasks_tenant_idem ON review_tasks(tenant_id, idempotency_key)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_stage_states_task_order ON review_stage_states(task_id, stage_order)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_human_overrides_task ON human_overrides(task_id, created_at)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_canonical_contracts_tenant ON canonical_contracts(tenant_id, lifecycle_status)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_async_jobs_poll ON async_jobs(status, next_run_at)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_async_jobs_tenant_idem ON async_jobs(tenant_id, idempotency_key)"
        )
        job_cols = self._table_cols("async_jobs")
        if "worker_id" not in job_cols:
            self.conn.execute("ALTER TABLE async_jobs ADD COLUMN worker_id TEXT")
        if "lease_token" not in job_cols:
            self.conn.execute("ALTER TABLE async_jobs ADD COLUMN lease_token TEXT")
        if "lease_until" not in job_cols:
            self.conn.execute(
                "ALTER TABLE async_jobs ADD COLUMN lease_until REAL NOT NULL DEFAULT 0.0"
            )
        if "heartbeat_at" not in job_cols:
            self.conn.execute(
                "ALTER TABLE async_jobs ADD COLUMN heartbeat_at REAL NOT NULL DEFAULT 0.0"
            )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_async_jobs_lease_recovery ON async_jobs(status, lease_until, next_run_at)"
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS request_idempotency (
                tenant_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'IN_PROGRESS',
                resource_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (tenant_id, actor_id, operation, idempotency_key)
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_req_idem_lookup ON request_idempotency (tenant_id, resource_id)"
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_replay_nonces (
                nonce TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL DEFAULT 'default',
                user_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_nonces_expiry ON auth_replay_nonces(expires_at)"
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_approval_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                knowledge_id TEXT NOT NULL,
                knowledge_type TEXT NOT NULL,
                version TEXT NOT NULL DEFAULT 'v1',
                approver_id TEXT NOT NULL,
                approval_action TEXT NOT NULL,
                approval_reason TEXT,
                clause_ids TEXT,
                created_at REAL NOT NULL
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_approvals_lookup ON knowledge_approval_records(tenant_id, knowledge_id, created_at DESC)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_clauses_approval_ktype ON clauses(approval_status, knowledge_type)"
        )
        self.conn.commit()

    def _check_embedding_compat(self) -> None:
        """Embedding 维度/提供方校验：与已建索引不一致时 fail-closed，要求 rebuild_index。"""
        row = self.conn.execute(
            "SELECT provider, dim FROM embedding_index_meta WHERE status='ACTIVE' "
            "ORDER BY version DESC LIMIT 1"
        ).fetchone()
        if row and (
            row["provider"] != self.emb.name or int(row["dim"]) != int(self.emb.dim)
        ):
            raise EmbeddingConfigMismatch(
                f"Embedding 配置与现有索引不一致：索引={row['provider']}/dim={row['dim']}，"
                f"当前={self.emb.name}/dim={self.emb.dim}。请调用 storage.rebuild_index() "
                "重建索引（版本化留痕）后再使用。"
            )

    def _validate_vec(self, vec: Any, context: str) -> np.ndarray:
        """向量维度校验：任何写入/查询向量必须与当前 Embedding 维度一致。"""
        arr = np.asarray(vec, dtype=np.float32)
        if arr.ndim != 1 or arr.shape[0] != int(self.emb.dim):
            raise EmbeddingConfigMismatch(
                f"向量维度校验失败（{context}）：期望 dim={self.emb.dim}，实际 "
                f"shape={arr.shape}。禁止写入/检索维度不一致的向量。"
            )
        return arr

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # ---------- 导入（幂等 + 增量 + 原子事务） ----------
    def upsert_document(
        self,
        doc: dict,
        tenant: str | None = None,
        refresh_classification: bool = False,
    ) -> tuple[str, bool]:
        """按 source_hash 幂等：显式传入 tenant 时按 (tenant_id, source_hash) 隔离判重；单租户默认调用按全局哈希判重。"""
        effective_tenant = tenant if tenant is not None else config.TENANT_ID
        with self._lock:
            if tenant is not None:
                cur = self.conn.execute(
                    "SELECT document_id, status FROM documents WHERE source_hash=? AND tenant_id=?",
                    (doc["sha256"], tenant),
                )
            else:
                cur = self.conn.execute(
                    "SELECT document_id, status FROM documents WHERE source_hash=?",
                    (doc["sha256"],),
                )
            row = cur.fetchone()
            if row:
                if refresh_classification:
                    new_cat = doc.get("category", "")
                    new_sub = doc.get("subtype", "")
                    if new_cat or new_sub:
                        self.update_document_classification(
                            document_id=row["document_id"],
                            category=new_cat,
                            subtype=new_sub,
                            tenant_id=effective_tenant,
                        )
                return row["document_id"], False

            base_did = doc.get("template_id") or doc.get("document_id") or "doc_unknown"
            template_id = doc.get("template_id") or base_did

            existing_versions = self.conn.execute(
                "SELECT document_id, source_version, imported_at, status FROM documents WHERE (template_id=? OR document_id=?) AND tenant_id=? ORDER BY imported_at ASC",
                (template_id, template_id, effective_tenant),
            ).fetchall()

            existing_id = self.conn.execute(
                "SELECT tenant_id FROM documents WHERE document_id=?",
                (base_did,),
            ).fetchone()

            if existing_versions:
                if (
                    doc.get("document_id")
                    and not self.conn.execute(
                        "SELECT 1 FROM documents WHERE document_id=?",
                        (doc["document_id"],),
                    ).fetchone()
                ):
                    did = doc["document_id"]
                else:
                    ver_num = len(existing_versions) + 1
                    ver_tag = doc.get("template_version") or f"v{ver_num}"
                    did = f"{base_did}_{ver_tag}"
                    if self.conn.execute(
                        "SELECT 1 FROM documents WHERE document_id=?", (did,)
                    ).fetchone():
                        did = f"{base_did}_{ver_tag}_{doc['sha256'][:8]}"
                prefix_clause = True
                doc_status = doc.get("status") or "PENDING_REVIEW"
                if doc_status == "ACTIVE" and template_id:
                    self.conn.execute(
                        "UPDATE documents SET status='SUPERSEDED' WHERE (template_id=? OR document_id=?) AND tenant_id=? AND status='ACTIVE'",
                        (template_id, template_id, effective_tenant),
                    )
                source_ver = (
                    doc.get("template_version") or f"v{len(existing_versions) + 1}"
                )
            else:
                if existing_id and existing_id["tenant_id"] != effective_tenant:
                    did = f"{effective_tenant}:{base_did}"
                    prefix_clause = True
                else:
                    did = doc.get("document_id") or base_did
                    prefix_clause = did != base_did
                doc_status = doc.get("status", "ACTIVE")
                source_ver = doc.get("template_version", "v1")

            try:
                self.conn.execute(
                    "INSERT INTO documents (document_id, template_id, source_file, source_hash, source_version, "
                    "tenant_id, business_category, contract_subtype, document_type, imported_at, "
                    "status, raw_meta) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(document_id) DO UPDATE SET "
                    "template_id=excluded.template_id, source_file=excluded.source_file, source_hash=excluded.source_hash, "
                    "source_version=excluded.source_version, business_category=excluded.business_category, "
                    "contract_subtype=excluded.contract_subtype, document_type=excluded.document_type, "
                    "imported_at=excluded.imported_at, status=excluded.status, raw_meta=excluded.raw_meta, "
                    "revision=documents.revision+1",
                    (
                        did,
                        template_id,
                        doc["source_file"],
                        doc["sha256"],
                        source_ver,
                        effective_tenant,
                        doc.get("category", ""),
                        doc.get("subtype", ""),
                        doc.get("document_type", "contract_template"),
                        time.time(),
                        doc_status,
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
                    raw_cid = c["clause_id"]
                    if existing_versions:
                        cid = f"{did}:{raw_cid}"
                    elif prefix_clause:
                        cid = f"{effective_tenant}:{raw_cid}"
                    else:
                        existing_clause = self.conn.execute(
                            "SELECT document_id FROM clauses WHERE clause_id=?",
                            (raw_cid,),
                        ).fetchone()
                        if existing_clause and existing_clause["document_id"] != did:
                            cid = f"{did}:{raw_cid}"
                        else:
                            cid = raw_cid

                    clause_appr = "PENDING_REVIEW"
                    is_template_doc = doc.get("document_type") == "contract_template"
                    clause_ktype = c.get("knowledge_type") or (
                        "TEMPLATE" if is_template_doc else "HISTORICAL_CASE"
                    )
                    self.conn.execute(
                        "INSERT INTO clauses (clause_id, document_id, clause_number, "
                        "clause_category, clause_subcategory, original_text, normalized_summary, "
                        "is_company_standard, approval_status, source_node_ids, knowledge_type, "
                        "effective_time, revision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1) "
                        "ON CONFLICT(clause_id) DO UPDATE SET "
                        "clause_number=excluded.clause_number, "
                        "clause_category=excluded.clause_category, clause_subcategory=excluded.clause_subcategory, "
                        "original_text=excluded.original_text, normalized_summary=excluded.normalized_summary, "
                        "source_node_ids=excluded.source_node_ids, effective_time=excluded.effective_time",
                        (
                            cid,
                            did,
                            c.get("clause_number"),
                            c.get("clause_category"),
                            c.get("clause_subcategory"),
                            c["original_text"],
                            c.get("normalized_summary", ""),
                            1 if c.get("is_company_standard") else 0,
                            clause_appr,
                            json.dumps(
                                c.get("source_node_ids", []), ensure_ascii=False
                            ),
                            clause_ktype,
                            c.get("effective_time"),
                        ),
                    )
                self._index_clauses(did, commit=False)
                self._ensure_index_meta(commit=False)
                self._commit()
            except Exception:
                self._rollback()
                raise
            return did, True

    def _index_clauses(self, document_id: str, commit: bool = True) -> None:
        rows = self.conn.execute(
            "SELECT rowid, clause_id, original_text, normalized_summary, clause_category, "
            "clause_subcategory, approval_status, knowledge_type FROM clauses WHERE document_id=?",
            (document_id,),
        ).fetchall()
        meta = self.conn.execute(
            "SELECT business_category, contract_subtype, source_hash FROM documents WHERE document_id=?",
            (document_id,),
        ).fetchone()
        texts = []
        for r in rows:
            self.conn.execute(
                "INSERT INTO clauses_fts(rowid, original_text, normalized_summary, clause_category) "
                "VALUES (?,?,?,?)",
                (
                    r["rowid"],
                    r["original_text"],
                    r["normalized_summary"],
                    r["clause_category"],
                ),
            )
            texts.append((r, meta))
        if texts:
            vecs = np.asarray(
                self.emb.embed([r["original_text"] for r, _ in texts]), dtype=np.float32
            )
            if vecs.shape != (len(texts), int(self.emb.dim)):
                raise EmbeddingConfigMismatch(
                    f"Embedding 输出维度校验失败：期望 {(len(texts), int(self.emb.dim))}，实际 {vecs.shape}"
                )
            for (r, m), vec in zip(texts, vecs):
                ktype = r["knowledge_type"] if r["knowledge_type"] else "TEMPLATE"
                appr_status = (
                    r["approval_status"] if r["approval_status"] else "PENDING_REVIEW"
                )
                self.conn.execute(
                    "INSERT OR REPLACE INTO embeddings VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        f"{document_id}:{r['clause_id']}",
                        document_id,
                        r["clause_id"],
                        m["business_category"] if m else "",
                        m["contract_subtype"] if m else "",
                        ktype,
                        appr_status,
                        None,
                        m["source_hash"] if m else document_id,
                        r["original_text"],
                        self._validate_vec(vec, "index_write").tobytes(),
                    ),
                )
        if commit:
            self._commit()

    def _ensure_index_meta(self, commit: bool = True) -> int:
        """确保存在与当前 Embedding 一致的 ACTIVE 索引版本。"""
        row = self.conn.execute(
            "SELECT version, provider, dim FROM embedding_index_meta WHERE status='ACTIVE' "
            "ORDER BY version DESC LIMIT 1"
        ).fetchone()
        if not row:
            return self._bump_index_version(commit=commit)
        if row["provider"] != self.emb.name or int(row["dim"]) != int(self.emb.dim):
            raise EmbeddingConfigMismatch(
                f"Embedding 配置与现有索引不一致：索引={row['provider']}/dim={row['dim']}，"
                f"当前={self.emb.name}/dim={self.emb.dim}。请调用 rebuild_index() 重建。"
            )
        return int(row["version"])

    def _bump_index_version(self, commit: bool = True) -> int:
        self.conn.execute(
            "UPDATE embedding_index_meta SET status='SUPERSEDED' WHERE status='ACTIVE'"
        )
        self.conn.execute(
            "INSERT INTO embedding_index_meta (provider, dim, model_spec, created_at, status) "
            "VALUES (?,?,?,?,?)",
            (
                self.emb.name,
                int(self.emb.dim),
                getattr(self.emb, "name", ""),
                time.time(),
                "ACTIVE",
            ),
        )
        if commit:
            self._commit()
        return int(
            self.conn.execute(
                "SELECT MAX(version) AS v FROM embedding_index_meta"
            ).fetchone()["v"]
        )

    def get_index_version(self) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM embedding_index_meta WHERE status='ACTIVE' ORDER BY version DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def rebuild_index(self) -> int:
        """全量重建索引（幂等）：换 Embedding 模型/维度后必须调用，版本号递增留痕。"""
        with self._lock:
            try:
                self.conn.execute("DELETE FROM clauses_fts")
                self.conn.execute("DELETE FROM embeddings")
                for (did,) in self.conn.execute(
                    "SELECT document_id FROM documents"
                ).fetchall():
                    self._index_clauses(did, commit=False)
                ver = self._bump_index_version(commit=False)
                self._commit()
                return ver
            except Exception:
                self._rollback()
                raise

    def delete_document(self, document_id: str) -> None:
        with self._lock:
            for table in ("clauses", "nodes", "fields", "embeddings"):
                self.conn.execute(
                    f"DELETE FROM {table} WHERE document_id=?", (document_id,)
                )
            self.conn.execute(
                "DELETE FROM documents WHERE document_id=?", (document_id,)
            )
            self.conn.execute(
                "INSERT INTO audit_log VALUES (NULL,?,?,?,?,?)",
                (time.time(), "system", "delete_document", document_id, ""),
            )
            self._commit()
            self.rebuild_index()

    def rollback_to_version(
        self,
        document_id: str,
        source_hash: str,
        tenant_id: str | None = None,
        actor: str = "system",
    ) -> bool:
        """回滚：重新激活指定版本并重建索引。
        必须严格校验目标哈希与 document_id 属于同租户、同一模板版本链，
        且目标版本具备合法的已审批/已生效历史状态，禁止跨模板链串联或借回滚越权激活未审批版本。
        """
        effective_tenant = tenant_id if tenant_id is not None else config.TENANT_ID
        with self._lock:
            # 1. 查找调用者指定的当前/基准文档及其模板链
            cur_doc = self.conn.execute(
                "SELECT document_id, template_id, source_file, status, tenant_id FROM documents WHERE document_id=? AND tenant_id=?",
                (document_id, effective_tenant),
            ).fetchone()

            if not cur_doc and tenant_id is None and effective_tenant == "default":
                cur_doc = self.conn.execute(
                    "SELECT document_id, template_id, source_file, status, tenant_id FROM documents WHERE document_id=?",
                    (document_id,),
                ).fetchone()
                if cur_doc:
                    effective_tenant = cur_doc["tenant_id"]

            if not cur_doc:
                return False

            cur_did = cur_doc["document_id"]
            cur_tpl = cur_doc["template_id"] or cur_did
            cur_source_file = cur_doc["source_file"] or ""

            # 2. 在同租户下查找目标 source_hash 对应的目标文档
            target_doc = self.conn.execute(
                "SELECT document_id, template_id, source_file, status, tenant_id FROM documents WHERE source_hash=? AND tenant_id=?",
                (source_hash, effective_tenant),
            ).fetchone()

            if not target_doc:
                return False

            target_did = target_doc["document_id"]
            tgt_tpl = target_doc["template_id"] or target_did
            target_source_file = target_doc["source_file"] or ""

            # 3. 校验目标文档与 cur_doc 是否属于同一模板版本链
            cur_base = _base_template_id(cur_tpl)
            tgt_base = _base_template_id(tgt_tpl)
            is_same_chain = (cur_tpl == tgt_tpl) or bool(
                cur_base
                and tgt_base
                and cur_base == tgt_base
                and cur_source_file
                and target_source_file
                and cur_source_file == target_source_file
            )
            if not is_same_chain:
                return False

            # 4. 幂等检查：目标文档若已处于 ACTIVE 状态，无需重复流转
            if target_did == cur_did and cur_doc["status"] == "ACTIVE":
                return True

            # 5. 治理与审批约束校验：目标版本必须曾处于已审批/已生效生命周期（ACTIVE 或 SUPERSEDED）
            # 绝对禁止借回滚通道将未经审批的 PENDING_REVIEW / PENDING 或已废弃/撤销的 REVOKED / DELETED 激活
            if target_doc["status"] not in ("SUPERSEDED", "ACTIVE"):
                return False

            # 检查条款审批状态：目标版本存在未通过审批（REJECTED）或被撤销（REVOKED）条款时拒绝回滚
            unapproved = self.conn.execute(
                "SELECT 1 FROM clauses WHERE document_id=? AND approval_status IN ('REVOKED', 'REJECTED')",
                (target_did,),
            ).fetchone()
            if unapproved:
                return False

            # 6. 原子切换版本状态并同步重建索引（若重建失败，事务必须回滚）
            chain_tpl = cur_tpl or tgt_tpl
            try:
                self.conn.execute(
                    "UPDATE documents SET status='ACTIVE' WHERE document_id=? AND tenant_id=?",
                    (target_did, effective_tenant),
                )
                self.conn.execute(
                    "UPDATE documents SET status='SUPERSEDED' WHERE ("
                    "COALESCE(NULLIF(template_id, ''), document_id)=? "
                    "OR COALESCE(NULLIF(template_id, ''), document_id)=? "
                    ") AND document_id<>? AND tenant_id=? AND status='ACTIVE'",
                    (
                        cur_tpl,
                        tgt_tpl,
                        target_did,
                        effective_tenant,
                    ),
                )
                # 记录审计日志
                now_ts = time.time()
                detail = json.dumps(
                    {
                        "action": "rollback",
                        "from_document_id": document_id,
                        "to_document_id": target_doc["document_id"],
                        "target_hash": source_hash,
                        "template_id": chain_tpl,
                    },
                    ensure_ascii=False,
                )
                self.conn.execute(
                    "INSERT INTO audit_log (at, actor, action, target, detail) VALUES (?,?,?,?,?)",
                    (
                        now_ts,
                        actor,
                        "rollback_to_version",
                        target_doc["document_id"],
                        detail,
                    ),
                )
                self.rebuild_index()
                self._commit()
                return True
            except Exception:
                self._rollback()
                raise

    def update_document_classification(
        self,
        document_id: str,
        category: str,
        subtype: str,
        tenant_id: str | None = None,
        force: bool = False,
    ) -> bool:
        """原子更新文档及其分块索引中的分类元数据（不修改条款文本、ID 与已有审批状态）。"""
        effective_tenant = tenant_id if tenant_id is not None else config.TENANT_ID
        with self._lock:
            doc = self.get_document(document_id, tenant_id=effective_tenant)
            if not doc:
                return False

            raw_meta = {}
            if doc.get("raw_meta"):
                try:
                    raw_meta = json.loads(doc["raw_meta"])
                except Exception:
                    raw_meta = {}
            if not force and raw_meta.get("manual_classification_override"):
                return False

            old_cat = doc.get("business_category") or ""
            old_sub = doc.get("contract_subtype") or ""
            if old_cat == category and old_sub == subtype:
                return False

            try:
                self.conn.execute(
                    "UPDATE documents SET business_category=?, contract_subtype=? WHERE document_id=? AND tenant_id=?",
                    (category, subtype, document_id, effective_tenant),
                )
                self.conn.execute(
                    "UPDATE embeddings SET business_category=?, contract_subtype=? WHERE document_id=?",
                    (category, subtype, document_id),
                )
                now_ts = time.time()
                detail = json.dumps(
                    {
                        "document_id": document_id,
                        "old_category": old_cat,
                        "old_subtype": old_sub,
                        "new_category": category,
                        "new_subtype": subtype,
                        "tenant_id": effective_tenant,
                    },
                    ensure_ascii=False,
                )
                self.conn.execute(
                    "INSERT INTO audit_log (at, actor, action, target, detail) VALUES (?,?,?,?,?)",
                    (
                        now_ts,
                        "system",
                        "update_document_classification",
                        document_id,
                        detail,
                    ),
                )
                self._commit()
                return True
            except Exception:
                self._rollback()
                raise

    def log_audit(self, actor: str, action: str, target: str, detail: str = "") -> None:
        safe_detail = redact_sensitive_text(detail, max_len=1000)
        with self._lock:
            self.conn.execute(
                "INSERT INTO audit_log VALUES (NULL,?,?,?,?,?)",
                (time.time(), actor, action, target, safe_detail),
            )
            self._commit()

    # ---------- 文档/条款仓储查询方法（消除外部裸 .conn.execute） ----------
    def get_document(
        self, document_id: str, tenant_id: str | None = None
    ) -> dict | None:
        with self._lock:
            sql = "SELECT * FROM documents WHERE document_id=?"
            params: list[Any] = [document_id]
            if tenant_id is not None:
                sql += " AND tenant_id=?"
                params.append(tenant_id)
            row = self.conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def find_document_by_hash(
        self, source_hash: str, tenant_id: str | None = None
    ) -> dict | None:
        with self._lock:
            sql = "SELECT * FROM documents WHERE source_hash=?"
            params: list[Any] = [source_hash]
            if tenant_id is not None:
                sql += " AND tenant_id=?"
                params.append(tenant_id)
            row = self.conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def list_active_document_hashes(
        self, tenant_id: str | None = None
    ) -> dict[str, str]:
        with self._lock:
            sql = "SELECT source_hash, source_file FROM documents WHERE status='ACTIVE'"
            params: list[Any] = []
            if tenant_id is not None:
                sql += " AND tenant_id=?"
                params.append(tenant_id)
            rows = self.conn.execute(sql, params).fetchall()
            return {
                r["source_hash"]: r["source_file"] for r in rows if r["source_hash"]
            }

    def set_document_status(
        self, document_id: str, status: str, tenant_id: str | None = None
    ) -> bool:
        with self._lock:
            sql = "UPDATE documents SET status=? WHERE document_id=?"
            params: list[Any] = [status, document_id]
            if tenant_id is not None:
                sql += " AND tenant_id=?"
                params.append(tenant_id)
            cur = self.conn.execute(sql, params)
            self._commit()
            return cur.rowcount > 0

    def authorized_bulk_import_approved(
        self,
        doc: dict,
        tenant: str | None = None,
        *,
        approver_id: str,
        approval_reason: str,
    ) -> tuple[str, bool]:
        """独立授权迁移命令：批量导入既有法务已批准标准模板，验证审批身份并写入正式审批记录。"""
        if not approver_id or not approver_id.strip():
            raise ValueError("授权导入必须指定合法 approver_id")
        if not approval_reason or not approval_reason.strip():
            raise ValueError("授权导入必须说明审批理由 approval_reason")
        effective_tenant = tenant if tenant is not None else config.TENANT_ID
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
        """统一知识审批写入口：具备租户约束、CAS 乐观锁校验、幂等和审计追踪。"""
        with self._lock:
            doc = self.get_document(document_id, tenant_id=tenant_id)
            if not doc:
                raise ValueError(
                    f"文档不存在或跨租户未授权：{document_id} (tenant={tenant_id})"
                )
            if doc.get("status") not in ("ACTIVE", "PENDING", "PENDING_REVIEW"):
                raise ValueError(
                    f"仅 ACTIVE 或 PENDING 状态文档允许审批知识版本，当前状态：{doc.get('status')}"
                )

            current_rev = int(doc.get("revision") or 1)
            if expected_revision is not None and int(expected_revision) != current_rev:
                raise OptimisticLockConflictError(
                    f"知识版本审批冲突：预期 revision={expected_revision}，当前数据库 revision={current_rev}"
                )

            tpl_id = doc.get("template_id") or doc.get("document_id")
            if expected_revision is not None:
                cur = self.conn.execute(
                    "UPDATE documents SET status='ACTIVE', revision = revision + 1 WHERE document_id=? AND tenant_id=? AND revision=?",
                    (document_id, tenant_id, current_rev),
                )
                if cur.rowcount == 0:
                    raise OptimisticLockConflictError(
                        f"并发更新冲突：文档 {document_id} revision 已被修改"
                    )
            else:
                self.conn.execute(
                    "UPDATE documents SET status='ACTIVE', revision = revision + 1 WHERE document_id=? AND tenant_id=?",
                    (document_id, tenant_id),
                )

            if tpl_id:
                self.conn.execute(
                    "UPDATE documents SET status='SUPERSEDED' WHERE (template_id=? OR document_id=?) AND document_id<>? AND tenant_id=? AND status='ACTIVE'",
                    (tpl_id, tpl_id, document_id, tenant_id),
                )
            new_revision = current_rev + 1

            params: list[Any] = [new_revision, document_id]
            extra_clause = ""
            if clause_ids:
                placeholders = ",".join("?" for _ in clause_ids)
                extra_clause = f" AND clause_id IN ({placeholders})"
                params.extend(clause_ids)

            cur = self.conn.execute(
                f"UPDATE clauses SET approval_status='APPROVED', revision=? WHERE document_id=?{extra_clause}",
                params,
            )
            affected = cur.rowcount

            # 同步更新 embeddings 表中对应分块的审批状态
            emb_params: list[Any] = [document_id]
            emb_extra = ""
            if clause_ids:
                c_ph = ",".join("?" for _ in clause_ids)
                emb_extra = f" AND clause_id IN ({c_ph})"
                emb_params.extend(clause_ids)
            self.conn.execute(
                f"UPDATE embeddings SET approved_status='APPROVED' WHERE document_id=?{emb_extra}",
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
            self.conn.execute(
                "INSERT INTO audit_log (at, actor, action, target, detail) VALUES (?,?,?,?,?)",
                (now_ts, approver_id, "approve_knowledge_version", document_id, detail),
            )
            self.conn.execute(
                """
                INSERT INTO knowledge_approval_records (
                    tenant_id, knowledge_id, knowledge_type, version, approver_id,
                    approval_action, approval_reason, clause_ids, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
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

    def revoke_knowledge_version(
        self,
        tenant_id: str,
        document_id: str,
        revoker_id: str,
        revoke_reason: str = "",
        clause_ids: list[str] | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """统一撤销知识审批：撤销后立即丧失权威依据资格，支持 CAS 并发校验。"""
        with self._lock:
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

            if expected_revision is not None:
                cur = self.conn.execute(
                    "UPDATE documents SET revision = revision + 1 WHERE document_id=? AND tenant_id=? AND revision=?",
                    (document_id, tenant_id, current_rev),
                )
                if cur.rowcount == 0:
                    raise OptimisticLockConflictError(
                        f"并发更新冲突：文档 {document_id} revision 已被修改"
                    )
            else:
                self.conn.execute(
                    "UPDATE documents SET revision = revision + 1 WHERE document_id=? AND tenant_id=?",
                    (document_id, tenant_id),
                )
            new_revision = current_rev + 1

            params: list[Any] = [new_revision, document_id]
            extra_clause = ""
            if clause_ids:
                placeholders = ",".join("?" for _ in clause_ids)
                extra_clause = f" AND clause_id IN ({placeholders})"
                params.extend(clause_ids)

            cur = self.conn.execute(
                f"UPDATE clauses SET approval_status='REVOKED', revision=? WHERE document_id=?{extra_clause}",
                params,
            )
            affected = cur.rowcount

            emb_params: list[Any] = [document_id]
            emb_extra = ""
            if clause_ids:
                c_ph = ",".join("?" for _ in clause_ids)
                emb_extra = f" AND clause_id IN ({c_ph})"
                emb_params.extend(clause_ids)
            self.conn.execute(
                f"UPDATE embeddings SET approved_status='REVOKED' WHERE document_id=?{emb_extra}",
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
            self.conn.execute(
                "INSERT INTO audit_log (at, actor, action, target, detail) VALUES (?,?,?,?,?)",
                (now_ts, revoker_id, "revoke_knowledge_version", document_id, detail),
            )
            self.conn.execute(
                """
                INSERT INTO knowledge_approval_records (
                    tenant_id, knowledge_id, knowledge_type, version, approver_id,
                    approval_action, approval_reason, clause_ids, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
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

    def find_active_template(
        self,
        contract_subtype: str | None = None,
        business_category: str | None = None,
        tenant_id: str | None = None,
    ) -> dict | None:
        with self._lock:
            if contract_subtype:
                sql = (
                    "SELECT document_id, source_file, source_hash, business_category, "
                    "contract_subtype, tenant_id FROM documents "
                    "WHERE contract_subtype=? AND status='ACTIVE'"
                )
                params: list[Any] = [contract_subtype]
                if tenant_id is not None:
                    sql += " AND tenant_id=?"
                    params.append(tenant_id)
                sql += " ORDER BY CASE WHEN document_id LIKE 'MB%' THEN 0 ELSE 1 END, imported_at DESC LIMIT 1"
                row = self.conn.execute(sql, params).fetchone()
                if row:
                    return dict(row)
            if business_category:
                sql = (
                    "SELECT document_id, source_file, source_hash, business_category, "
                    "contract_subtype, tenant_id FROM documents "
                    "WHERE business_category=? AND status='ACTIVE'"
                )
                params = [business_category]
                if tenant_id is not None:
                    sql += " AND tenant_id=?"
                    params.append(tenant_id)
                sql += " ORDER BY CASE WHEN document_id LIKE 'MB%' THEN 0 ELSE 1 END, imported_at DESC LIMIT 1"
                row = self.conn.execute(sql, params).fetchone()
                if row:
                    return dict(row)
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
                "WHERE c.document_id=?"
            )
            params: list[Any] = [document_id]
            if clause_category:
                sql += " AND c.clause_category=?"
                params.append(clause_category)
            if tenant_id is not None:
                sql += " AND d.tenant_id=?"
                params.append(tenant_id)
            sql += " ORDER BY c.rowid"
            rows = self.conn.execute(sql, params).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                if isinstance(d.get("source_node_ids"), str):
                    try:
                        d["source_node_ids"] = json.loads(d["source_node_ids"])
                    except json.JSONDecodeError:
                        d["source_node_ids"] = []
                out.append(d)
            return out

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
        ev_str = (
            evidence
            if isinstance(evidence, str)
            else json.dumps(evidence, ensure_ascii=False)
        )
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO rule_executions (exec_id, rule_id, rule_version, "
                "rule_type, result, severity, message, evidence, executed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    exec_id,
                    rule_id,
                    int(rule_version),
                    rule_type,
                    result,
                    severity,
                    message,
                    ev_str,
                    executed_at if executed_at is not None else time.time(),
                ),
            )
            self._commit()

    def list_rule_executions(
        self, rule_id: str | None = None, limit: int = 100
    ) -> list[dict]:
        with self._lock:
            sql = "SELECT * FROM rule_executions"
            params: list[Any] = []
            if rule_id:
                sql += " WHERE rule_id=?"
                params.append(rule_id)
            sql += " ORDER BY executed_at DESC LIMIT ?"
            params.append(int(limit))
            rows = self.conn.execute(sql, params).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                if isinstance(d.get("evidence"), str) and d["evidence"]:
                    try:
                        d["evidence"] = json.loads(d["evidence"])
                    except json.JSONDecodeError:
                        pass
                out.append(d)
            return out

    # ---------- 混合检索 ----------
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
            fts_hits = self._fts_search(
                keyword or query,
                top_k * 3,
                business_category,
                contract_subtype,
                clause_number,
                knowledge_type,
                approved_only,
                tenant_id=tenant_id,
            )
            vec_hits = self._vector_search(
                query,
                top_k * 3,
                business_category,
                contract_subtype,
                knowledge_type,
                approved_only,
                tenant_id=tenant_id,
            )
            fused = self._rrf(fts_hits, vec_hits)
            if clause_number:
                fused = [
                    h
                    for h in fused
                    if (
                        h.get("clause_number") == clause_number
                        or self._clause_num(h["clause_id"]) == clause_number
                    )
                ]
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
        """独立词法召回通道（Channel A: FTS5 全文索引）。"""
        with self._lock:
            hits = self._fts_search(
                query,
                top_k,
                business_category,
                contract_subtype,
                clause_number,
                knowledge_type,
                approved_only,
                tenant_id=tenant_id,
            )
            return hits[:top_k]

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
        """独立稠密语义召回通道（Channel B: 向量索引）。"""
        with self._lock:
            hits = self._vector_search(
                query,
                top_k,
                business_category,
                contract_subtype,
                knowledge_type,
                approved_only,
                tenant_id=tenant_id,
            )
            return hits[:top_k]

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
            where_clauses = ["documents.status='ACTIVE'", "c.clause_number=?"]
            params: list[Any] = [clause_number]
            if business_category:
                where_clauses.append("documents.business_category=?")
                params.append(business_category)
            if contract_subtype:
                where_clauses.append("documents.contract_subtype=?")
                params.append(contract_subtype)
            if approved_only:
                where_clauses.append("c.approval_status='APPROVED'")
            if tenant_id is not None:
                where_clauses.append("documents.tenant_id=?")
                params.append(tenant_id)

            sql = (
                "SELECT c.clause_id, c.document_id, c.original_text, c.clause_number, "
                "c.clause_category, c.clause_id AS chunk_id, documents.business_category, documents.contract_subtype, "
                "c.knowledge_type, c.approval_status, documents.status AS doc_status, documents.tenant_id "
                "FROM clauses c "
                "JOIN documents ON documents.document_id=c.document_id "
                f"WHERE {' AND '.join(where_clauses)} LIMIT ?"
            )
            rows = self.conn.execute(sql, params + [top_k]).fetchall()
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
                        "approved_status": r["approval_status"],
                        "doc_status": r["doc_status"],
                        "tenant_id": r["tenant_id"],
                    }
                )
            return out

    def _filters(
        self,
        business_category: str | None,
        contract_subtype: str | None,
        knowledge_type: str | None,
        approved_only: bool,
        alias: str = "e",
        tenant_id: str | None = None,
    ) -> tuple[str, list[Any]]:
        sql = " AND documents.status='ACTIVE'"
        params: list[Any] = []
        if business_category:
            sql += f" AND {alias}.business_category=?"
            params.append(business_category)
        if contract_subtype:
            sql += f" AND {alias}.contract_subtype=?"
            params.append(contract_subtype)
        if knowledge_type:
            sql += f" AND {alias}.knowledge_type=?"
            params.append(knowledge_type)
        if approved_only:
            sql += f" AND {alias}.approved_status='APPROVED'"
        if tenant_id is not None:
            sql += " AND documents.tenant_id=?"
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
        tenant_id: str | None = None,
    ) -> list[dict]:
        words = [w for w in re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z0-9]+", query)][:8]
        for w in list(words):
            if len(w) >= 3:
                words.extend(w[i : i + 2] for i in range(len(w) - 1))
        words = words[:24]
        if not words:
            return []
        match = " OR ".join(f'"{w}"' for w in words)
        fsql, fparams = self._filters(
            business_category,
            contract_subtype,
            knowledge_type,
            approved_only,
            tenant_id=tenant_id,
        )
        extra = ""
        params = list(fparams)
        if clause_number:
            extra += " AND c.clause_number=?"
            params.append(clause_number)
        try:
            rows = self.conn.execute(
                "SELECT c.clause_id, c.document_id, c.original_text, c.clause_number, "
                "c.clause_category, e.chunk_id, e.business_category, e.contract_subtype, "
                "e.knowledge_type, e.approved_status, documents.status AS doc_status, documents.tenant_id FROM clauses_fts f "
                "JOIN clauses c ON c.rowid=f.rowid "
                "JOIN embeddings e ON e.clause_id=c.clause_id "
                "JOIN documents ON documents.document_id=c.document_id "
                "WHERE clauses_fts MATCH ?" + fsql + extra + " LIMIT ?",
                [match] + params + [limit],
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
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
                    "channel": "fts",
                    "business_category": r["business_category"],
                    "contract_subtype": r["contract_subtype"],
                    "knowledge_type": r["knowledge_type"],
                    "approved_status": r["approved_status"],
                    "doc_status": r["doc_status"],
                    "tenant_id": r["tenant_id"],
                }
            )
        return out

    def _vector_search(
        self,
        query: str,
        limit: int,
        business_category: str | None,
        contract_subtype: str | None,
        knowledge_type: str | None,
        approved_only: bool,
        tenant_id: str | None = None,
    ) -> list[dict]:
        fsql, fparams = self._filters(
            business_category,
            contract_subtype,
            knowledge_type,
            approved_only,
            alias="embeddings",
            tenant_id=tenant_id,
        )
        rows = self.conn.execute(
            "SELECT embeddings.*, clauses.clause_number, clauses.clause_category, documents.status AS doc_status, documents.tenant_id FROM embeddings "
            "JOIN documents ON documents.document_id=embeddings.document_id "
            "LEFT JOIN clauses ON clauses.clause_id=embeddings.clause_id WHERE 1=1"
            + fsql,
            fparams,
        ).fetchall()
        if not rows:
            return []
        qv = self._validate_vec(self.emb.embed([query])[0], "query")
        mat = np.stack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows])
        sims = mat @ qv
        order = np.argsort(-sims)[:limit]
        out = []
        for i in order:
            r = rows[int(i)]
            out.append(
                {
                    "chunk_id": r["chunk_id"],
                    "clause_id": r["clause_id"],
                    "document_id": r["document_id"],
                    "text": r["text"],
                    "clause_number": r["clause_number"],
                    "clause_category": r["clause_category"],
                    "channel": "vector",
                    "score": float(sims[int(i)]),
                    "business_category": r["business_category"],
                    "contract_subtype": r["contract_subtype"],
                    "knowledge_type": r["knowledge_type"],
                    "approved_status": r["approved_status"],
                    "doc_status": r["doc_status"],
                    "tenant_id": r["tenant_id"],
                }
            )
        return out

    def _clause_num(self, clause_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT clause_number FROM clauses WHERE clause_id=?", (clause_id,)
        ).fetchone()
        return row["clause_number"] if row else None

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
                c["channel"]
                for c in [h for h in fts_hits + vec_hits if h["chunk_id"] == cid]
            ]
            out.append(item)
        return out

    @staticmethod
    def _rerank(query: str, hits: list[dict]) -> list[dict]:
        """轻量确定性 Reranker：字符 2-gram 覆盖度 + 数值匹配加权。"""
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

    # ---------- 查询辅助 ----------
    def find_clauses_by_category(
        self, category: str, tenant_id: str | None = None
    ) -> list[dict]:
        with self._lock:
            sql = (
                "SELECT c.clause_id, c.original_text, c.clause_category "
                "FROM clauses c "
                "JOIN documents d ON d.document_id = c.document_id "
                "WHERE c.clause_category = ?"
            )
            params: list[Any] = [category]
            if tenant_id is not None:
                sql += " AND d.tenant_id = ?"
                params.append(tenant_id)
            rows = self.conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def get_clause(self, clause_id: str, tenant_id: str | None = None) -> Any:
        with self._lock:
            sql = (
                "SELECT c.*, d.status AS doc_status, d.tenant_id, d.source_hash, "
                "d.source_version FROM clauses c "
                "JOIN documents d ON d.document_id=c.document_id "
                "WHERE c.clause_id=?"
            )
            params: list[Any] = [clause_id]
            if tenant_id is not None:
                sql += " AND d.tenant_id=?"
                params.append(tenant_id)
            return self.conn.execute(sql, params).fetchone()

    def list_documents(self, tenant_id: str | None = None) -> list[dict]:
        """按租户过滤（SQL 级强制）。tenant_id=None 仅供内部评估/管理使用。"""
        with self._lock:
            sql = (
                "SELECT document_id, source_file, source_hash, business_category, contract_subtype, "
                "status, imported_at, tenant_id FROM documents"
            )
            params: tuple[Any, ...] = ()
            if tenant_id is not None:
                sql += " WHERE tenant_id=?"
                params = (tenant_id,)
            sql += " ORDER BY imported_at"
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    # ---------- 规则治理（支持租户隔离与版本递增） ----------
    def save_rule(self, rule: dict, tenant_id: str | None = None) -> int:
        resolved_tenant = tenant_id or rule.get("tenant_id") or "default"
        with self._lock:
            version = rule.get("version")
            if version is None:
                row = self.conn.execute(
                    "SELECT MAX(version) AS v FROM rules WHERE rule_id=?",
                    (rule["rule_id"],),
                ).fetchone()
                version = int((row["v"] if row and row["v"] is not None else 0) + 1)
            else:
                existing = self.conn.execute(
                    "SELECT tenant_id FROM rules WHERE rule_id=? AND version=?",
                    (rule["rule_id"], int(version)),
                ).fetchone()
                if existing and existing["tenant_id"] != resolved_tenant:
                    row = self.conn.execute(
                        "SELECT MAX(version) AS v FROM rules WHERE rule_id=?",
                        (rule["rule_id"],),
                    ).fetchone()
                    version = int((row["v"] if row and row["v"] is not None else 0) + 1)

            params_obj = (
                rule["parameters"] if "parameters" in rule else rule.get("params", {})
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO rules (rule_id, version, name, rule_type, execution_mode, "
                "severity, priority, contract_types, params, source_clauses, status, approved_by, "
                "effective_at, effective_until, tenant_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                    json.dumps(rule.get("source_clauses", []), ensure_ascii=False),
                    rule.get("status", "DRAFT"),
                    rule.get("approved_by"),
                    rule.get("effective_at"),
                    rule.get("effective_until"),
                    resolved_tenant,
                ),
            )
            self._commit()
            return int(version)

    def approve_rule(
        self,
        rule_id: str,
        approver: str,
        version: int | None = 1,
        tenant_id: str | None = None,
    ) -> bool:
        with self._lock:
            if version is None:
                sql_v = "SELECT MAX(version) AS v FROM rules WHERE rule_id=? AND status='DRAFT'"
                p_v: list[Any] = [rule_id]
                if tenant_id is not None:
                    sql_v += " AND tenant_id=?"
                    p_v.append(tenant_id)
                rv = self.conn.execute(sql_v, p_v).fetchone()
                if not rv or rv["v"] is None:
                    return False
                version = int(rv["v"])
            sql = (
                "UPDATE rules SET status='APPROVED', approved_by=?, effective_at=? "
                "WHERE rule_id=? AND version=? AND status='DRAFT'"
            )
            params: list[Any] = [approver, time.time(), rule_id, int(version)]
            if tenant_id is not None:
                sql += " AND tenant_id=?"
                params.append(tenant_id)
            cur = self.conn.execute(sql, params)
            self._commit()
            if cur.rowcount:
                self.log_audit(approver, "approve_rule", rule_id, f"version={version}")
                return True
            return False

    def get_rules(
        self, status: str | None = None, tenant_id: str | None = None
    ) -> list[dict]:
        with self._lock:
            sql = "SELECT * FROM rules WHERE 1=1"
            params: list[Any] = []
            if status:
                sql += " AND status=?"
                params.append(status)
            if tenant_id is not None:
                sql += " AND tenant_id=?"
                params.append(tenant_id)
            sql += " ORDER BY priority ASC, rule_id ASC, version DESC"
            rows = self.conn.execute(sql, params).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["contract_types"] = json.loads(d["contract_types"])
                d["params"] = json.loads(d["params"])
                d["source_clauses"] = json.loads(d["source_clauses"])
                out.append(d)
            return out

    # ---------- 审核任务与问题（含人工反馈、阶段持久化、幂等与取消） ----------
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
        now = time.time()
        with self._lock:
            existing = self.conn.execute(
                "SELECT created_at, cancel_requested, idempotency_key, rule_set_version, "
                "model_version, reproducibility_hash FROM review_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            created_ts = existing["created_at"] if existing else now
            cancel_req = int(existing["cancel_requested"] or 0) if existing else 0
            idem = (
                idempotency_key
                if idempotency_key is not None
                else (existing["idempotency_key"] if existing else None)
            )
            rsv = (
                rule_set_version
                if rule_set_version is not None
                else (existing["rule_set_version"] if existing else None)
            )
            mv = (
                model_version
                if model_version is not None
                else (existing["model_version"] if existing else None)
            )
            repro = (
                reproducibility_hash
                if reproducibility_hash is not None
                else (existing["reproducibility_hash"] if existing else None)
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO review_tasks (task_id, document_id, mode, status, created_at, "
                "updated_at, meta, tenant_id, created_by, idempotency_key, cancel_requested, "
                "rule_set_version, model_version, reproducibility_hash) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    document_id,
                    mode,
                    status,
                    created_ts,
                    now,
                    json.dumps(meta, ensure_ascii=False),
                    tenant_id,
                    created_by,
                    idem,
                    cancel_req,
                    rsv,
                    mv,
                    repro,
                ),
            )
            self._commit()

    def get_task(self, task_id: str) -> dict | None:
        with self._lock:
            r = self.conn.execute(
                "SELECT * FROM review_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if not r:
                return None
            d = dict(r)
            d["cancel_requested"] = bool(d.get("cancel_requested", 0))
            if isinstance(d.get("meta"), str) and d["meta"]:
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
            sql = "SELECT * FROM review_tasks WHERE idempotency_key=?"
            params: list[Any] = [idempotency_key]
            if tenant_id is not None:
                sql += " AND tenant_id=?"
                params.append(tenant_id)
            if created_by is not None:
                sql += " AND created_by=?"
                params.append(created_by)
            sql += " ORDER BY created_at DESC LIMIT 1"
            r = self.conn.execute(sql, params).fetchone()
            if not r:
                return None
            d = dict(r)
            d["cancel_requested"] = bool(d.get("cancel_requested", 0))
            if isinstance(d.get("meta"), str) and d["meta"]:
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
                where += " AND tenant_id=?"
                params.append(tenant_id)
            if status:
                where += " AND status=?"
                params.append(status)
            if created_by is not None:
                where += " AND created_by=?"
                params.append(created_by)
            total = int(
                self.conn.execute(
                    "SELECT COUNT(*) AS c FROM review_tasks" + where, params
                ).fetchone()["c"]
            )
            rows = self.conn.execute(
                "SELECT * FROM review_tasks"
                + where
                + " ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params + [int(limit), int(offset)],
            ).fetchall()
            items = []
            for r in rows:
                d = dict(r)
                d["cancel_requested"] = bool(d.get("cancel_requested", 0))
                if isinstance(d.get("meta"), str) and d["meta"]:
                    try:
                        d["meta"] = json.loads(d["meta"])
                    except json.JSONDecodeError:
                        pass
                items.append(d)
            return items, total

    def request_cancel_task(self, task_id: str, tenant_id: str | None = None) -> bool:
        with self._lock:
            sql = "UPDATE review_tasks SET cancel_requested=1, updated_at=? WHERE task_id=?"
            params: list[Any] = [time.time(), task_id]
            if tenant_id is not None:
                sql += " AND tenant_id=?"
                params.append(tenant_id)
            cur = self.conn.execute(sql, params)
            self._commit()
            return cur.rowcount > 0

    def is_task_cancelled(self, task_id: str) -> bool:
        with self._lock:
            r = self.conn.execute(
                "SELECT cancel_requested, status FROM review_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if not r:
                return False
            return bool(r["cancel_requested"]) or r["status"] == "CANCELLED"

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
        now = time.time()
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO review_stage_states (task_id, stage_name, stage_order, "
                "status, attempt, started_at, finished_at, duration_ms, detail_json, error) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    stage_name,
                    int(stage_order),
                    status,
                    int(attempt),
                    started_at if started_at is not None else now,
                    finished_at,
                    float(duration_ms),
                    json.dumps(detail or {}, ensure_ascii=False),
                    error,
                ),
            )
            self._commit()

    def get_stage_states(self, task_id: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM review_stage_states WHERE task_id=? ORDER BY stage_order ASC",
                (task_id,),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                raw = d.pop("detail_json", None)
                d["detail"] = json.loads(raw) if raw else {}
                out.append(d)
            return out

    def save_issues(self, task_id: str, issues: list[dict]) -> None:
        with self._lock:
            for it in issues:
                self.conn.execute(
                    "INSERT OR REPLACE INTO review_issues (issue_id, task_id, rule_id, severity, "
                    "category, title, description, expected_value, actual_value, source_ref, "
                    "knowledge_refs, suggestion, execution_mode, requires_human_review, confidence, "
                    "human_state, human_note, finding_source, check_status) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        it["issue_id"],
                        task_id,
                        it.get("rule_id"),
                        it["severity"],
                        it["category"],
                        it["title"],
                        it["description"],
                        json.dumps(it.get("expected_value"), ensure_ascii=False),
                        json.dumps(it.get("actual_value"), ensure_ascii=False),
                        json.dumps(it.get("source_reference"), ensure_ascii=False),
                        json.dumps(
                            it.get("knowledge_references", []), ensure_ascii=False
                        ),
                        it.get("suggestion", ""),
                        it.get("execution_mode", "DETERMINISTIC"),
                        1 if it.get("requires_human_review") else 0,
                        it.get("confidence"),
                        it.get("human_state", "PENDING"),
                        it.get("human_note"),
                        it.get("finding_source", "DETERMINISTIC"),
                        it.get("check_status", "CHECKED"),
                    ),
                )
            self._commit()

    def get_issues(self, task_id: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM review_issues WHERE task_id=?", (task_id,)
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                for k in (
                    "source_ref",
                    "knowledge_refs",
                    "expected_value",
                    "actual_value",
                ):
                    d[k] = json.loads(d[k]) if d[k] else None
                out.append(d)
            return out

    def get_issue(self, issue_id: str) -> dict | None:
        """问题详情 + 所属任务的租户/创建者（供所有权检查）。"""
        with self._lock:
            r = self.conn.execute(
                "SELECT i.*, t.tenant_id AS task_tenant_id, t.created_by AS task_created_by "
                "FROM review_issues i JOIN review_tasks t ON t.task_id=i.task_id "
                "WHERE i.issue_id=?",
                (issue_id,),
            ).fetchone()
            return dict(r) if r else None

    def feedback_issue(
        self,
        issue_id: str,
        action: str,
        actor: str,
        note: str = "",
        expected_state: str | None = None,
    ) -> bool:
        """人工反馈：ACCEPT / IGNORE / EDIT / NOTE / FALSE_POSITIVE / FALSE_NEGATIVE。"""
        valid = {"ACCEPT", "IGNORE", "EDIT", "NOTE", "FALSE_POSITIVE", "FALSE_NEGATIVE"}
        if action not in valid:
            return False
        with self._lock:
            sql = "UPDATE review_issues SET human_state=?, human_note=?, finding_source='HUMAN' WHERE issue_id=?"
            params: list[Any] = [action, note, issue_id]
            if expected_state is not None:
                sql += " AND (human_state=? OR (human_state IS NULL AND ?='PENDING'))"
                params.extend([expected_state, expected_state])
            cur = self.conn.execute(sql, params)
            if cur.rowcount > 0:
                self.log_audit(
                    actor, "issue_feedback", issue_id, f"{action} {note[:80]}"
                )
                self._commit()
                return True
            return False

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
        with self._lock:
            self.conn.execute(
                "INSERT INTO human_overrides (override_id, task_id, issue_id, tenant_id, actor, "
                "action, reason, previous_state, new_state, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
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
                    created_at if created_at is not None else time.time(),
                ),
            )
            self._commit()

    def list_human_overrides(self, task_id: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM human_overrides WHERE task_id=? ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ---------- 统一标准合同实体（Canonical Contract） ----------
    def save_canonical_version(
        self, outcome: Any, expected_version: int | None = None
    ) -> bool:
        contract = outcome.contract
        cid = contract.contract_id
        now = time.time()
        tenant = getattr(contract, "tenant_id", "default") or "default"
        contract_dict = (
            contract.to_dict() if hasattr(contract, "to_dict") else dict(contract)
        )

        with self._lock:
            if expected_version is not None:
                cur_row = self.conn.execute(
                    "SELECT version FROM canonical_contracts WHERE contract_id=?",
                    (cid,),
                ).fetchone()
                if not cur_row or int(cur_row["version"]) != int(expected_version):
                    return False

            self.upsert_canonical_contract(contract_dict)

            snap_str = json.dumps(contract_dict, ensure_ascii=False)
            self.conn.execute(
                "INSERT OR REPLACE INTO canonical_contract_versions (contract_id, version, tenant_id, snapshot, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (cid, contract.version, tenant, snap_str, now),
            )

            for m in getattr(contract, "source_mappings", []):
                m_dict = m.to_dict() if hasattr(m, "to_dict") else dict(m)
                self.conn.execute(
                    "INSERT OR REPLACE INTO contract_source_mappings "
                    "(tenant_id, source_system, external_id, contract_id, external_code, source_hash, synced_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        tenant,
                        m_dict.get("source_system"),
                        m_dict.get("external_id"),
                        cid,
                        m_dict.get("external_code"),
                        m_dict.get("checksum"),
                        now,
                    ),
                )
            self._commit()
            return True

    def get_canonical_versions(self, contract_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT contract_id, version, tenant_id, snapshot, created_at "
                "FROM canonical_contract_versions WHERE contract_id=? ORDER BY version ASC",
                (contract_id,),
            ).fetchall()
            result: list[dict[str, Any]] = []
            for r in rows:
                raw_snap = r["snapshot"]
                snap = json.loads(raw_snap) if raw_snap else {}
                result.append(
                    {
                        "contract_id": r["contract_id"],
                        "version": int(r["version"]),
                        "tenant_id": r["tenant_id"],
                        "snapshot": snap,
                        "created_at": float(r["created_at"]),
                    }
                )
            return result

    def find_canonical_by_source(
        self, tenant_id: str, source_system: str, external_id: str
    ) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT contract_id FROM contract_source_mappings "
                "WHERE tenant_id=? AND source_system=? AND external_id=?",
                (tenant_id, source_system, external_id),
            ).fetchone()
            if not row:
                return None
            return self.get_canonical_contract(row["contract_id"], tenant_id=tenant_id)

    def find_canonical_by_contract_number(
        self, tenant_id: str, contract_number: str
    ) -> dict | None:
        if not contract_number:
            return None
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM canonical_contracts WHERE tenant_id=? AND contract_number=?",
                (tenant_id, contract_number),
            ).fetchone()
            if not row:
                return None
            return self._decode_canonical_row(dict(row))

    def upsert_canonical_contract(self, contract: dict) -> str:
        cid = contract["contract_id"]
        now = time.time()
        tenant = contract.get("tenant_id") or "default"
        with self._lock:
            existing = self.conn.execute(
                "SELECT version, created_at FROM canonical_contracts WHERE contract_id=?",
                (cid,),
            ).fetchone()
            version = int(
                contract.get("version")
                or ((existing["version"] + 1) if existing else 1)
            )
            created_ts = existing["created_at"] if existing else now
            total_amt = contract.get("total_amount")
            if total_amt is None and isinstance(contract.get("amounts"), dict):
                total_amt = contract["amounts"].get("total_amount")
            self.conn.execute(
                "INSERT OR REPLACE INTO canonical_contracts (contract_id, tenant_id, contract_number, "
                "entity_prefix, title, business_category, contract_subtype, lifecycle_status, "
                "source_system, source_document_id, source_hash, total_amount, currency, "
                "parties_json, amounts_json, source_mappings_json, version, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                    json.dumps(contract.get("source_mappings", []), ensure_ascii=False),
                    version,
                    created_ts,
                    now,
                ),
            )
            self._commit()
            return cid

    def get_canonical_contract(
        self, contract_id: str, tenant_id: str | None = None
    ) -> dict | None:
        with self._lock:
            sql = "SELECT * FROM canonical_contracts WHERE contract_id=?"
            params: list[Any] = [contract_id]
            if tenant_id is not None:
                sql += " AND tenant_id=?"
                params.append(tenant_id)
            r = self.conn.execute(sql, params).fetchone()
            if not r:
                return None
            return self._decode_canonical_row(dict(r))

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
                where += " AND tenant_id=?"
                params.append(tenant_id)
            total = int(
                self.conn.execute(
                    "SELECT COUNT(*) AS c FROM canonical_contracts" + where, params
                ).fetchone()["c"]
            )
            rows = self.conn.execute(
                "SELECT * FROM canonical_contracts"
                + where
                + " ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                params + [int(limit), int(offset)],
            ).fetchall()
            return [self._decode_canonical_row(dict(r)) for r in rows], total

    @staticmethod
    def _decode_canonical_row(d: dict) -> dict:
        d["parties"] = json.loads(d.pop("parties_json") or "[]")
        d["amounts"] = json.loads(d.pop("amounts_json") or "{}")
        d["source_mappings"] = json.loads(d.pop("source_mappings_json") or "[]")
        return d

    # ---------- 轻量持久化异步任务队列（async_jobs） ----------
    def enqueue_async_job(self, job: dict) -> tuple[str, bool]:
        now = time.time()
        tenant = job.get("tenant_id") or "default"
        idem = job.get("idempotency_key")
        created_by = job.get("created_by", "system")
        with self._lock:
            if idem:
                ex = self.conn.execute(
                    "SELECT job_id FROM async_jobs WHERE tenant_id=? AND created_by=? AND idempotency_key=?",
                    (tenant, created_by, idem),
                ).fetchone()
                if ex:
                    return ex["job_id"], False
            jid = job.get("job_id") or f"job_{uuid.uuid4().hex[:12]}"
            self.conn.execute(
                "INSERT INTO async_jobs (job_id, idempotency_key, tenant_id, job_type, status, "
                "payload_json, result_json, error_message, dead_letter_reason, progress, attempts, "
                "max_attempts, next_run_at, cancel_requested, temp_file_path, created_by, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                    float(job.get("next_run_at", now)),
                    1 if job.get("cancel_requested") else 0,
                    job.get("temp_file_path"),
                    job.get("created_by", "system"),
                    now,
                    now,
                ),
            )
            self._commit()
            return jid, True

    def get_async_job(self, job_id: str, tenant_id: str | None = None) -> dict | None:
        with self._lock:
            sql = "SELECT * FROM async_jobs WHERE job_id=?"
            params: list[Any] = [job_id]
            if tenant_id is not None:
                sql += " AND tenant_id=?"
                params.append(tenant_id)
            r = self.conn.execute(sql, params).fetchone()
            return self._decode_job_row(dict(r)) if r else None

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
                where += " AND tenant_id=?"
                params.append(tenant_id)
            if status:
                where += " AND status=?"
                params.append(status)
            if created_by is not None:
                where += " AND created_by=?"
                params.append(created_by)
            total = int(
                self.conn.execute(
                    "SELECT COUNT(*) AS c FROM async_jobs" + where, params
                ).fetchone()["c"]
            )
            rows = self.conn.execute(
                "SELECT * FROM async_jobs"
                + where
                + " ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params + [int(limit), int(offset)],
            ).fetchall()
            return [self._decode_job_row(dict(r)) for r in rows], total

    def claim_job(
        self,
        worker_id: str,
        lease_seconds: int = 60,
        job_types: list[str] | None = None,
    ) -> JobLease | None:
        now = time.time()
        lease_until = now + max(1, int(lease_seconds))
        new_token = uuid.uuid4().hex
        with self._lock:
            sql = (
                "SELECT * FROM async_jobs WHERE ("
                "(status IN ('PENDING', 'RETRYING') AND next_run_at <= ?) "
                "OR (status = 'RUNNING' AND lease_until < ?)"
                ")"
            )
            params: list[Any] = [now, now]
            if job_types:
                placeholders = ",".join("?" for _ in job_types)
                sql += f" AND job_type IN ({placeholders})"
                params.extend(job_types)
            sql += " ORDER BY next_run_at ASC, created_at ASC"
            rows = self.conn.execute(sql, params).fetchall()
            for r in rows:
                job_id = r["job_id"]
                if r["cancel_requested"]:
                    self.conn.execute(
                        "UPDATE async_jobs SET status='CANCELLED', updated_at=? WHERE job_id=?",
                        (now, job_id),
                    )
                    self._commit()
                    continue

                curr_attempts = int(r["attempts"] or 0)
                max_attempts = int(r["max_attempts"] or 3)

                # 如果处于 RUNNING 且租约已过期，检查是否耗尽重试次数
                if r["status"] == "RUNNING" and curr_attempts >= max_attempts:
                    dlq_reason = f"lease_expired_attempts_exhausted({max_attempts})"
                    self.conn.execute(
                        "UPDATE async_jobs SET status='DEAD_LETTER', dead_letter_reason=?, updated_at=? WHERE job_id=? AND status='RUNNING'",
                        (dlq_reason, now, job_id),
                    )
                    self._commit()
                    continue

                new_attempts = curr_attempts + 1
                if new_attempts > max_attempts:
                    dlq_reason = f"lease_expired_attempts_exhausted({max_attempts})"
                    self.conn.execute(
                        "UPDATE async_jobs SET status='DEAD_LETTER', dead_letter_reason=?, updated_at=? WHERE job_id=? AND status=?",
                        (dlq_reason, now, job_id, r["status"]),
                    )
                    self._commit()
                    continue

                if r["status"] in ("PENDING", "RETRYING"):
                    cur = self.conn.execute(
                        "UPDATE async_jobs SET status='RUNNING', worker_id=?, lease_token=?, lease_until=?, heartbeat_at=?, attempts=?, updated_at=? WHERE job_id=? AND status IN ('PENDING', 'RETRYING')",
                        (
                            worker_id,
                            new_token,
                            lease_until,
                            now,
                            new_attempts,
                            now,
                            job_id,
                        ),
                    )
                else:
                    cur = self.conn.execute(
                        "UPDATE async_jobs SET status='RUNNING', worker_id=?, lease_token=?, lease_until=?, heartbeat_at=?, attempts=?, updated_at=? WHERE job_id=? AND status='RUNNING' AND lease_until <= ?",
                        (
                            worker_id,
                            new_token,
                            lease_until,
                            now,
                            new_attempts,
                            now,
                            job_id,
                            now,
                        ),
                    )
                if cur.rowcount == 1:
                    self._commit()
                    decoded = self.get_async_job(job_id)
                    return JobLease(
                        job_id=job_id,
                        worker_id=worker_id,
                        lease_token=new_token,
                        lease_until=lease_until,
                        attempt=new_attempts,
                        job_type=r["job_type"],
                        tenant_id=r["tenant_id"],
                        created_by=str(r["created_by"] or "system"),
                        max_attempts=max_attempts,
                        payload=decoded.get("payload") or {} if decoded else {},
                        temp_file_path=r["temp_file_path"],
                    )
            return None

    def heartbeat_job(
        self, job_id: str, lease_token: str, extension_seconds: int = 60
    ) -> bool:
        now = time.time()
        new_until = now + max(1, int(extension_seconds))
        with self._lock:
            cur = self.conn.execute(
                "UPDATE async_jobs SET lease_until=?, heartbeat_at=?, updated_at=? WHERE job_id=? AND lease_token=? AND status='RUNNING' AND lease_until >= ?",
                (new_until, now, now, job_id, lease_token, now),
            )
            ok = cur.rowcount > 0
            if ok:
                self._commit()
            return ok

    def finish_job(self, job_id: str, lease_token: str, outcome: JobOutcome) -> bool:
        now = time.time()
        with self._lock:
            res_json = (
                json.dumps(outcome.result, ensure_ascii=False)
                if outcome.result is not None
                else None
            )
            cur = self.conn.execute(
                "UPDATE async_jobs SET status=?, result_json=?, error_message=?, dead_letter_reason=?, progress=?, next_run_at=COALESCE(?, next_run_at), updated_at=? WHERE job_id=? AND lease_token=?",
                (
                    outcome.status,
                    res_json,
                    outcome.error_message,
                    outcome.dead_letter_reason,
                    outcome.progress,
                    outcome.next_run_at,
                    now,
                    job_id,
                    lease_token,
                ),
            )
            ok = cur.rowcount > 0
            if ok:
                self._commit()
                if outcome.status == "SUCCEEDED":
                    self.finalize_idempotency_key(
                        resource_id=job_id, status="COMPLETED"
                    )
            return ok

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
        now = time.time()
        with self._lock:
            sql = (
                "SELECT * FROM async_jobs WHERE status IN ('PENDING', 'RETRYING') "
                "AND next_run_at <= ?"
            )
            params: list[Any] = [now]
            if job_types:
                placeholders = ",".join("?" for _ in job_types)
                sql += f" AND job_type IN ({placeholders})"
                params.extend(job_types)
            sql += " ORDER BY next_run_at ASC, created_at ASC LIMIT 1"
            row = self.conn.execute(sql, params).fetchone()
            if not row:
                return None
            if row["cancel_requested"]:
                self.conn.execute(
                    "UPDATE async_jobs SET status='CANCELLED', updated_at=? WHERE job_id=?",
                    (now, row["job_id"]),
                )
                self._commit()
                return None
            new_attempts = int(row["attempts"] or 0) + 1
            self.conn.execute(
                "UPDATE async_jobs SET status='RUNNING', attempts=?, updated_at=? WHERE job_id=?",
                (new_attempts, now, row["job_id"]),
            )
            self._commit()
            return self.get_async_job(row["job_id"])

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
        assignments: list[str] = ["updated_at=?"]
        params: list[Any] = [time.time()]
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "result":
                assignments.append("result_json=?")
                params.append(
                    json.dumps(v, ensure_ascii=False) if v is not None else None
                )
            elif k == "cancel_requested":
                assignments.append("cancel_requested=?")
                params.append(1 if v else 0)
            else:
                assignments.append(f"{k}=?")
                params.append(v)
        params.append(job_id)
        with self._lock:
            cur = self.conn.execute(
                f"UPDATE async_jobs SET {', '.join(assignments)} WHERE job_id=?",
                params,
            )
            self._commit()
            return cur.rowcount > 0

    def cancel_async_job(self, job_id: str, tenant_id: str | None = None) -> bool:
        now = time.time()
        with self._lock:
            row = self.get_async_job(job_id, tenant_id=tenant_id)
            if not row:
                return False
            new_status = (
                "CANCELLED"
                if row["status"] in ("PENDING", "RETRYING")
                else row["status"]
            )
            self.conn.execute(
                "UPDATE async_jobs SET cancel_requested=1, status=?, updated_at=? WHERE job_id=?",
                (new_status, now, job_id),
            )
            self._commit()
            return True

    @staticmethod
    def _decode_job_row(d: dict) -> dict:
        d["payload"] = json.loads(d.pop("payload_json") or "{}")
        raw_res = d.pop("result_json", None)
        d["result"] = json.loads(raw_res) if raw_res else None
        d["cancel_requested"] = bool(d.get("cancel_requested", 0))
        return d

    # ---------- 公司格式标准（可配置 / 可审批 / 可版本化 / 租户隔离） ----------
    def save_format_standard(
        self,
        standard_id: str,
        params: dict,
        created_by: str,
        version: int | None = None,
        note: str = "",
        tenant_id: str | None = None,
    ) -> dict:
        """保存格式标准版本。version 不传则在既有最新版本上 +1（版本化留痕，不覆盖旧版）。"""
        resolved_tenant = tenant_id or "default"
        with self._lock:
            if version is None:
                row = self.conn.execute(
                    "SELECT MAX(version) AS v FROM format_standards WHERE standard_id=?",
                    (standard_id,),
                ).fetchone()
                version = int((row["v"] or 0) + 1)
            self.conn.execute(
                "INSERT INTO format_standards (standard_id, version, params, status, created_by, "
                "approved_by, approved_at, effective_at, note, tenant_id) "
                "VALUES (?,?,?,?,?,NULL,NULL,NULL,?,?)",
                (
                    standard_id,
                    int(version),
                    json.dumps(params, ensure_ascii=False),
                    "DRAFT",
                    created_by,
                    note,
                    resolved_tenant,
                ),
            )
            self._commit()
            self.log_audit(
                created_by, "save_format_standard", standard_id, f"version={version}"
            )
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
        """审批：仅 DRAFT 可审批；审批后成为 ACTIVE（同租户同标准旧 ACTIVE 版本转 SUPERSEDED）。"""
        with self._lock:
            row = self.conn.execute(
                "SELECT tenant_id FROM format_standards WHERE standard_id=? AND version=? AND status='DRAFT'",
                (standard_id, int(version)),
            ).fetchone()
            if not row:
                return False
            if tenant_id is not None and row["tenant_id"] != tenant_id:
                return False
            target_tenant = row["tenant_id"]
            self.conn.execute(
                "UPDATE format_standards SET status='SUPERSEDED' "
                "WHERE standard_id=? AND status='ACTIVE' AND tenant_id=?",
                (standard_id, target_tenant),
            )
            now = time.time()
            self.conn.execute(
                "UPDATE format_standards SET status='ACTIVE', approved_by=?, approved_at=?, "
                "effective_at=? WHERE standard_id=? AND version=?",
                (approver, now, now, standard_id, int(version)),
            )
            self._commit()
            self.log_audit(
                approver, "approve_format_standard", standard_id, f"version={version}"
            )
            return True

    def get_format_standard(
        self,
        standard_id: str,
        version: int | None = None,
        tenant_id: str | None = None,
    ) -> dict | None:
        with self._lock:
            if version is None:
                sql = "SELECT * FROM format_standards WHERE standard_id=?"
                params: list[Any] = [standard_id]
                if tenant_id is not None:
                    sql += " AND tenant_id=?"
                    params.append(tenant_id)
                sql += " ORDER BY version DESC LIMIT 1"
                r = self.conn.execute(sql, params).fetchone()
            else:
                sql = "SELECT * FROM format_standards WHERE standard_id=? AND version=?"
                params = [standard_id, int(version)]
                if tenant_id is not None:
                    sql += " AND tenant_id=?"
                    params.append(tenant_id)
                r = self.conn.execute(sql, params).fetchone()
            if not r:
                return None
            d = dict(r)
            d["params"] = json.loads(d["params"])
            return d

    def get_active_format_standard(
        self, standard_id: str = "company_format", tenant_id: str | None = None
    ) -> dict | None:
        """仅返回审批通过（ACTIVE）的标准；未审批标准不得用于产生正式违规结论。"""
        with self._lock:
            sql = (
                "SELECT * FROM format_standards WHERE standard_id=? AND status='ACTIVE'"
            )
            params: list[Any] = [standard_id]
            if tenant_id is not None:
                sql += " AND tenant_id=?"
                params.append(tenant_id)
            sql += " ORDER BY version DESC LIMIT 1"
            r = self.conn.execute(sql, params).fetchone()
            if not r:
                return None
            d = dict(r)
            d["params"] = json.loads(d["params"])
            return d

    # ---------- 请求级幂等预约控制（Request Idempotency） ----------
    def reserve_idempotency_key(
        self,
        identity: RequestIdentity,
        resource_id: str,
    ) -> IdempotencyOutcome:
        """为特定租户、操作人和操作原子化预约幂等键（SQLite）。"""
        now = time.time()
        with self._lock:
            cur = self.conn.execute(
                "SELECT request_fingerprint, status, resource_id FROM request_idempotency "
                "WHERE tenant_id=? AND actor_id=? AND operation=? AND idempotency_key=?",
                (
                    identity.tenant_id,
                    identity.actor_id,
                    identity.operation,
                    identity.key,
                ),
            )
            row = cur.fetchone()
            if row:
                if row["request_fingerprint"] != identity.fingerprint:
                    return IdempotencyOutcome(
                        status="CONFLICT",
                        resource_id=row["resource_id"],
                        message=f"Idempotency key conflict: request fingerprint mismatch for key {identity.key}",
                    )
                if row["status"] == "COMPLETED":
                    return IdempotencyOutcome(
                        status="REPLAY",
                        resource_id=row["resource_id"],
                    )
                return IdempotencyOutcome(
                    status="IN_PROGRESS",
                    resource_id=row["resource_id"],
                )

            try:
                self.conn.execute(
                    "INSERT INTO request_idempotency "
                    "(tenant_id, actor_id, operation, idempotency_key, request_fingerprint, status, resource_id, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, 'IN_PROGRESS', ?, ?, ?)",
                    (
                        identity.tenant_id,
                        identity.actor_id,
                        identity.operation,
                        identity.key,
                        identity.fingerprint,
                        resource_id,
                        now,
                        now,
                    ),
                )
                self._commit()
                return IdempotencyOutcome(
                    status="CREATED",
                    resource_id=resource_id,
                )
            except sqlite3.IntegrityError:
                cur = self.conn.execute(
                    "SELECT request_fingerprint, status, resource_id FROM request_idempotency "
                    "WHERE tenant_id=? AND actor_id=? AND operation=? AND idempotency_key=?",
                    (
                        identity.tenant_id,
                        identity.actor_id,
                        identity.operation,
                        identity.key,
                    ),
                )
                existing = cur.fetchone()
                if existing:
                    if existing["request_fingerprint"] != identity.fingerprint:
                        return IdempotencyOutcome(
                            status="CONFLICT",
                            resource_id=existing["resource_id"],
                            message=f"Idempotency key conflict: request fingerprint mismatch for key {identity.key}",
                        )
                    if existing["status"] == "COMPLETED":
                        return IdempotencyOutcome(
                            status="REPLAY",
                            resource_id=existing["resource_id"],
                        )
                    return IdempotencyOutcome(
                        status="IN_PROGRESS",
                        resource_id=existing["resource_id"],
                    )
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
        """完成或更新幂等记录的状态（SQLite）。"""
        now = time.time()
        t_id = identity.tenant_id if identity else (tenant_id or "default")
        a_id = identity.actor_id if identity else (actor_id or "")
        op = identity.operation if identity else (operation or "")
        key = identity.key if identity else idempotency_key
        with self._lock:
            if key and a_id and op:
                self.conn.execute(
                    "UPDATE request_idempotency SET status=?, updated_at=? "
                    "WHERE tenant_id=? AND actor_id=? AND operation=? AND idempotency_key=?",
                    (status, now, t_id, a_id, op, key),
                )
            elif resource_id:
                self.conn.execute(
                    "UPDATE request_idempotency SET status=?, updated_at=? "
                    "WHERE tenant_id=? AND resource_id=?",
                    (status, now, t_id, resource_id),
                )
            self._commit()

    def record_and_check_nonce(
        self, nonce: str, tenant_id: str, user_id: str, expires_at: float
    ) -> bool:
        """记录并校验防重放 Nonce（返回 True 表示首次使用，False 表示重放冲突）。"""
        now = time.time()
        with self._lock:
            try:
                self.conn.execute(
                    "DELETE FROM auth_replay_nonces WHERE expires_at < ?", (now,)
                )
            except Exception:
                pass
            try:
                self.conn.execute(
                    "INSERT INTO auth_replay_nonces (nonce, tenant_id, user_id, created_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (nonce, tenant_id, user_id, now, expires_at),
                )
                self._commit()
                return True
            except sqlite3.IntegrityError:
                return False
