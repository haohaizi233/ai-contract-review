# -*- coding: utf-8 -*-
"""知识库存储层（四层知识）+ 混合检索（FTS5 + 向量 + RRF 融合 + Reranker）。

数据库适配说明（如实标注）：
- 本地/测试默认 **SQLite（FTS5 全文 + numpy 向量）**，全部测试真实执行；
- 生产拓扑为 PostgreSQL + pgvector（storage_pg.PgVectorStorage），迁移脚本
  migrations/001_init_pgvector.sql + 002_embedding_meta_task_tenant_format_std.sql，
  运行时行为**已在真实 PG + pgvector 实例验证**（迁移/导入/检索/回滚/维度校验/租户）；
- 同一 Storage 接口下可替换 PgVectorStorage；Embedding 维度/提供方变更必须 rebuild_index（版本化），
  否则 EmbeddingConfigMismatch（fail-closed，禁止混用不同模型的向量）。
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

import numpy as np

from .embeddings import EmbeddingProvider, get_embedding_provider

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    source_file TEXT, source_hash TEXT UNIQUE, source_version TEXT,
    tenant_id TEXT, business_category TEXT, contract_subtype TEXT,
    document_type TEXT, imported_at REAL, status TEXT, raw_meta TEXT
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
    source_node_ids TEXT, knowledge_type TEXT, effective_time TEXT
);
CREATE TABLE IF NOT EXISTS fields (
    field_key TEXT, document_id TEXT, field_name TEXT, data_type TEXT,
    required_candidate INTEGER, required_approved INTEGER, meta TEXT
);
CREATE TABLE IF NOT EXISTS rules (
    rule_id TEXT, version INTEGER, name TEXT, rule_type TEXT,
    execution_mode TEXT, severity TEXT, contract_types TEXT,
    params TEXT, source_clauses TEXT, status TEXT,
    approved_by TEXT, effective_at REAL, PRIMARY KEY (rule_id, version)
);
CREATE TABLE IF NOT EXISTS review_tasks (
    task_id TEXT PRIMARY KEY, document_id TEXT, mode TEXT, status TEXT,
    created_at REAL, updated_at REAL, meta TEXT,
    tenant_id TEXT NOT NULL DEFAULT 'default', created_by TEXT
);
CREATE TABLE IF NOT EXISTS review_issues (
    issue_id TEXT PRIMARY KEY, task_id TEXT, rule_id TEXT, severity TEXT,
    category TEXT, title TEXT, description TEXT,
    expected_value TEXT, actual_value TEXT, source_ref TEXT,
    knowledge_refs TEXT, suggestion TEXT, execution_mode TEXT,
    requires_human_review INTEGER, confidence REAL,
    human_state TEXT DEFAULT 'PENDING', human_note TEXT
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
    note TEXT, PRIMARY KEY (standard_id, version)
);
"""


# 嵌入索引配置不一致（换模型/换维度）时必须显式 rebuild_index，禁止静默混用
class EmbeddingConfigMismatch(RuntimeError):
    pass


_CLEAN_RE = re.compile(r"[\s\u3000，。；：、“”‘’（）()《》\-—_/%‰]")


def _norm_text(t: str) -> str:
    return _CLEAN_RE.sub("", t).lower()


class Storage:
    """SQLite 本地实现：文档/节点/条款/字段/规则/审核任务 + FTS5 + 向量表。"""

    def __init__(
        self,
        db_path: str | Path,
        emb: EmbeddingProvider | None = None,
        rebuild_on_mismatch: bool = False,
    ):
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA_SQL)
        self.emb = emb or get_embedding_provider()
        self._apply_column_migrations()
        try:
            self._check_embedding_compat()
        except EmbeddingConfigMismatch:
            if not rebuild_on_mismatch:
                raise
            # 显式授权后全量重建索引（版本递增留痕）
            self.rebuild_index()

    def _apply_column_migrations(self):
        """既有库的增量列迁移（幂等）：任务租户/创建者、向量维度登记。"""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(review_tasks)")}
        if "tenant_id" not in cols:
            self.conn.execute(
                "ALTER TABLE review_tasks ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
            )
        if "created_by" not in cols:
            self.conn.execute("ALTER TABLE review_tasks ADD COLUMN created_by TEXT")
        self.conn.commit()

    def _check_embedding_compat(self):
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

    def _validate_vec(self, vec, context: str):
        """向量维度校验：任何写入/查询向量必须与当前 Embedding 维度一致。"""
        arr = np.asarray(vec, dtype=np.float32)
        if arr.ndim != 1 or arr.shape[0] != int(self.emb.dim):
            raise EmbeddingConfigMismatch(
                f"向量维度校验失败（{context}）：期望 dim={self.emb.dim}，实际 "
                f"shape={arr.shape}。禁止写入/检索维度不一致的向量。"
            )
        return arr

    def close(self):
        self.conn.close()

    # ---------- 导入（幂等 + 增量） ----------
    def upsert_document(self, doc: dict, tenant: str = "default") -> tuple[str, bool]:
        """按 source_hash 幂等：同哈希→跳过（返回 inserted=False）；新哈希→插入新版本。"""
        cur = self.conn.execute(
            "SELECT document_id, status FROM documents WHERE source_hash=?",
            (doc["sha256"],),
        )
        row = cur.fetchone()
        if row:
            return row["document_id"], False
        did = doc["template_id"]
        self.conn.execute(
            "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                did,
                doc["source_file"],
                doc["sha256"],
                doc.get("template_version", "v1"),
                tenant,
                doc.get("category", ""),
                doc.get("subtype", ""),
                doc.get("document_type", "contract_template"),
                time.time(),
                "ACTIVE",
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
            self.conn.execute(
                "INSERT OR REPLACE INTO clauses (clause_id, document_id, clause_number, "
                "clause_category, clause_subcategory, original_text, normalized_summary, "
                "is_company_standard, approval_status, source_node_ids, knowledge_type, "
                "effective_time) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    c["clause_id"],
                    did,
                    c.get("clause_number"),
                    c.get("clause_category"),
                    c.get("clause_subcategory"),
                    c["original_text"],
                    c.get("normalized_summary", ""),
                    1 if c.get("is_company_standard") else 0,
                    c.get("approval_status", "PENDING_REVIEW"),
                    json.dumps(c.get("source_node_ids", []), ensure_ascii=False),
                    "LAYER_A_RAW_CLAUSE",
                    None,
                ),
            )
        self.conn.commit()
        self._index_clauses(did)
        self._ensure_index_meta()
        return did, True

    def _index_clauses(self, document_id: str):
        rows = self.conn.execute(
            "SELECT rowid, clause_id, original_text, normalized_summary, clause_category, "
            "clause_subcategory FROM clauses WHERE document_id=?",
            (document_id,),
        ).fetchall()
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
            meta = self.conn.execute(
                "SELECT business_category, contract_subtype FROM documents WHERE document_id=?",
                (document_id,),
            ).fetchone()
            texts.append((r, meta))
        vecs = np.asarray(
            self.emb.embed([r["original_text"] for r, _ in texts]), dtype=np.float32
        )
        if len(texts) and vecs.shape != (len(texts), int(self.emb.dim)):
            raise EmbeddingConfigMismatch(
                f"Embedding 输出维度校验失败：期望 {(len(texts), int(self.emb.dim))}，实际 {vecs.shape}"
            )
        for (r, meta), vec in zip(texts, vecs):
            self.conn.execute(
                "INSERT OR REPLACE INTO embeddings VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"{document_id}:{r['clause_id']}",
                    document_id,
                    r["clause_id"],
                    meta["business_category"] if meta else "",
                    meta["contract_subtype"] if meta else "",
                    "LAYER_D_RAG",
                    "PENDING_REVIEW",
                    None,
                    document_id,
                    r["original_text"],
                    self._validate_vec(vec, "index_write").tobytes(),
                ),
            )
        self.conn.commit()

    def _ensure_index_meta(self):
        """确保存在与当前 Embedding 一致的 ACTIVE 索引版本：
        - 无记录 → 登记 version=1；
        - 记录不一致 → fail-closed 报错（换模型必须 rebuild_index）；
        - 一致 → 直接返回当前版本。"""
        row = self.conn.execute(
            "SELECT version, provider, dim FROM embedding_index_meta WHERE status='ACTIVE' "
            "ORDER BY version DESC LIMIT 1"
        ).fetchone()
        if not row:
            return self._bump_index_version()
        if row["provider"] != self.emb.name or int(row["dim"]) != int(self.emb.dim):
            raise EmbeddingConfigMismatch(
                f"Embedding 配置与现有索引不一致：索引={row['provider']}/dim={row['dim']}，"
                f"当前={self.emb.name}/dim={self.emb.dim}。请调用 rebuild_index() 重建。"
            )
        return int(row["version"])

    def _bump_index_version(self) -> int:
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
        self.conn.commit()
        return int(
            self.conn.execute(
                "SELECT MAX(version) AS v FROM embedding_index_meta"
            ).fetchone()["v"]
        )

    def get_index_version(self) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM embedding_index_meta WHERE status='ACTIVE' ORDER BY version DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def rebuild_index(self):
        """全量重建索引（幂等）：换 Embedding 模型/维度后必须调用，版本号递增留痕。"""
        self.conn.execute("DELETE FROM clauses_fts")
        self.conn.execute("DELETE FROM embeddings")
        self.conn.commit()
        for (did,) in self.conn.execute("SELECT document_id FROM documents").fetchall():
            self._index_clauses(did)
        return self._bump_index_version()

    def delete_document(self, document_id: str):
        for table in ("clauses", "nodes", "fields", "embeddings"):
            self.conn.execute(
                f"DELETE FROM {table} WHERE document_id=?", (document_id,)
            )
        self.conn.execute("DELETE FROM documents WHERE document_id=?", (document_id,))
        self.conn.execute(
            "INSERT INTO audit_log VALUES (NULL,?,?,?,?,?)",
            (time.time(), "system", "delete_document", document_id, ""),
        )
        self.conn.commit()
        self.rebuild_index()

    def rollback_to_version(self, document_id: str, source_hash: str) -> bool:
        """回滚：旧哈希仍保留在库（文档版本化），重新激活指定版本并重建索引。"""
        row = self.conn.execute(
            "SELECT * FROM documents WHERE source_hash=?", (source_hash,)
        ).fetchone()
        if not row:
            return False
        self.conn.execute(
            "UPDATE documents SET status='ACTIVE' WHERE source_hash=?", (source_hash,)
        )
        self.conn.execute(
            "UPDATE documents SET status='SUPERSEDED' WHERE document_id<>? AND source_file=?",
            (row["document_id"], row["source_file"]),
        )
        self.conn.commit()
        self.rebuild_index()
        return True

    def log_audit(self, actor: str, action: str, target: str, detail: str = ""):
        self.conn.execute(
            "INSERT INTO audit_log VALUES (NULL,?,?,?,?,?)",
            (time.time(), actor, action, target, detail),
        )
        self.conn.commit()

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
        # tenant_id 过滤：embeddings 层已带 document_id，租户约束在文档准入时生效；
        # 此处显式接受参数保持与 PgVectorStorage 接口一致。
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

    def _filters(
        self,
        business_category,
        contract_subtype,
        knowledge_type,
        approved_only,
        alias="e",
        tenant_id=None,
    ):
        sql, params = "", []
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
        if tenant_id:
            sql += " AND documents.tenant_id=?"
            params.append(tenant_id)
        return sql, params

    def _fts_search(
        self,
        query,
        limit,
        business_category,
        contract_subtype,
        clause_number,
        knowledge_type,
        approved_only,
        tenant_id=None,
    ) -> list[dict]:
        # 中文 FTS5 默认分词不足：提取中文词组 + 字符双字组合做 OR 查询
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
                "e.chunk_id, e.business_category, e.contract_subtype FROM clauses_fts f "
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
                    "channel": "fts",
                    "business_category": r["business_category"],
                    "contract_subtype": r["contract_subtype"],
                }
            )
        return out

    def _vector_search(
        self,
        query,
        limit,
        business_category,
        contract_subtype,
        knowledge_type,
        approved_only,
        tenant_id=None,
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
            "SELECT embeddings.*, ? AS clause_number FROM embeddings "
            "JOIN documents ON documents.document_id=embeddings.document_id WHERE 1=1"
            + fsql,
            [None] + fparams,
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
                    "clause_number": self._clause_num(r["clause_id"]),
                    "channel": "vector",
                    "score": float(sims[int(i)]),
                    "business_category": r["business_category"],
                    "contract_subtype": r["contract_subtype"],
                }
            )
        return out

    def _clause_num(self, clause_id: str):
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
        """轻量确定性 Reranker：字符 2-gram 覆盖度 + 数值匹配加权。

        （生产建议替换为 cross-encoder reranker，接口已隔离在此方法内。）
        """
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
    def get_clause(self, clause_id: str):
        return self.conn.execute(
            "SELECT * FROM clauses WHERE clause_id=?", (clause_id,)
        ).fetchone()

    def list_documents(self, tenant_id: str | None = None):
        """按租户过滤（SQL 级强制）。tenant_id=None 仅供内部评估/管理使用；
        API 层必须传入调用者租户——零匹配时返回空列表，绝不回退到全量数据。"""
        sql = (
            "SELECT document_id, source_file, source_hash, business_category, contract_subtype, "
            "status, imported_at, tenant_id FROM documents"
        )
        params: tuple = ()
        if tenant_id is not None:
            sql += " WHERE tenant_id=?"
            params = (tenant_id,)
        sql += " ORDER BY imported_at"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def save_rule(self, rule: dict):
        self.conn.execute(
            "INSERT OR REPLACE INTO rules (rule_id, version, name, rule_type, execution_mode, "
            "severity, contract_types, params, source_clauses, status, approved_by, effective_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rule["rule_id"],
                rule.get("version", 1),
                rule["name"],
                rule["rule_type"],
                rule["execution_mode"],
                rule["severity"],
                json.dumps(rule.get("contract_types", ["*"])),
                json.dumps(rule.get("parameters", {}), ensure_ascii=False),
                json.dumps(rule.get("source_clauses", []), ensure_ascii=False),
                rule.get("status", "DRAFT"),
                rule.get("approved_by"),
                rule.get("effective_at"),
            ),
        )
        self.conn.commit()

    def approve_rule(self, rule_id: str, approver: str, version: int = 1) -> bool:
        cur = self.conn.execute(
            "UPDATE rules SET status='APPROVED', approved_by=?, effective_at=? "
            "WHERE rule_id=? AND version=? AND status='DRAFT'",
            (approver, time.time(), rule_id, version),
        )
        self.conn.commit()
        if cur.rowcount:
            self.log_audit(approver, "approve_rule", rule_id, f"version={version}")
            return True
        return False

    def get_rules(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM rules WHERE status=?", (status,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM rules").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["contract_types"] = json.loads(d["contract_types"])
            d["params"] = json.loads(d["params"])
            d["source_clauses"] = json.loads(d["source_clauses"])
            out.append(d)
        return out

    # 审核任务与问题（含人工反馈）
    def save_task(
        self,
        task_id: str,
        document_id: str,
        mode: str,
        status: str,
        meta: dict,
        tenant_id: str = "default",
        created_by: str = "system",
    ):
        self.conn.execute(
            "INSERT OR REPLACE INTO review_tasks (task_id, document_id, mode, status, created_at, "
            "updated_at, meta, tenant_id, created_by) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                task_id,
                document_id,
                mode,
                status,
                time.time(),
                time.time(),
                json.dumps(meta, ensure_ascii=False),
                tenant_id,
                created_by,
            ),
        )
        self.conn.commit()

    def get_task(self, task_id: str):
        r = self.conn.execute(
            "SELECT * FROM review_tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        return dict(r) if r else None

    def save_issues(self, task_id: str, issues: list[dict]):
        for it in issues:
            self.conn.execute(
                "INSERT OR REPLACE INTO review_issues (issue_id, task_id, rule_id, severity, "
                "category, title, description, expected_value, actual_value, source_ref, "
                "knowledge_refs, suggestion, execution_mode, requires_human_review, confidence, "
                "human_state, human_note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                    json.dumps(it["source_reference"], ensure_ascii=False),
                    json.dumps(it.get("knowledge_references", []), ensure_ascii=False),
                    it.get("suggestion", ""),
                    it.get("execution_mode", "DETERMINISTIC"),
                    1 if it.get("requires_human_review") else 0,
                    it.get("confidence"),
                    "PENDING",
                    None,
                ),
            )
        self.conn.commit()

    def get_issues(self, task_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM review_issues WHERE task_id=?", (task_id,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for k in ("source_ref", "knowledge_refs", "expected_value", "actual_value"):
                d[k] = json.loads(d[k]) if d[k] else None
            out.append(d)
        return out

    def get_issue(self, issue_id: str) -> dict | None:
        """问题详情 + 所属任务的租户/创建者（供所有权检查）。"""
        r = self.conn.execute(
            "SELECT i.*, t.tenant_id AS task_tenant_id, t.created_by AS task_created_by "
            "FROM review_issues i JOIN review_tasks t ON t.task_id=i.task_id "
            "WHERE i.issue_id=?",
            (issue_id,),
        ).fetchone()
        return dict(r) if r else None

    def feedback_issue(
        self, issue_id: str, action: str, actor: str, note: str = ""
    ) -> bool:
        """人工反馈：ACCEPT / IGNORE / EDIT / NOTE / FALSE_POSITIVE / FALSE_NEGATIVE。
        仅记录状态，不自动修改企业正式规则（规则升级需单独审批）。"""
        valid = {"ACCEPT", "IGNORE", "EDIT", "NOTE", "FALSE_POSITIVE", "FALSE_NEGATIVE"}
        if action not in valid:
            return False
        cur = self.conn.execute(
            "UPDATE review_issues SET human_state=?, human_note=? WHERE issue_id=?",
            (action, note, issue_id),
        )
        self.conn.commit()
        self.log_audit(actor, "issue_feedback", issue_id, f"{action} {note[:80]}")
        return cur.rowcount > 0

    # ---------- 公司格式标准（可配置 / 可审批 / 可版本化） ----------
    def save_format_standard(
        self,
        standard_id: str,
        params: dict,
        created_by: str,
        version: int | None = None,
        note: str = "",
    ) -> dict:
        """保存格式标准版本。version 不传则在既有最新版本上 +1（版本化留痕，不覆盖旧版）。"""
        if version is None:
            row = self.conn.execute(
                "SELECT MAX(version) AS v FROM format_standards WHERE standard_id=?",
                (standard_id,),
            ).fetchone()
            version = (row["v"] or 0) + 1
        self.conn.execute(
            "INSERT INTO format_standards (standard_id, version, params, status, created_by, "
            "approved_by, approved_at, effective_at, note) VALUES (?,?,?,?,?,NULL,NULL,NULL,?)",
            (
                standard_id,
                int(version),
                json.dumps(params, ensure_ascii=False),
                "DRAFT",
                created_by,
                note,
            ),
        )
        self.conn.commit()
        self.log_audit(
            created_by, "save_format_standard", standard_id, f"version={version}"
        )
        rec = self.get_format_standard(standard_id, int(version))
        if rec is None:
            raise RuntimeError(
                f"格式标准保存失败（写入后无法读取）：{standard_id} v{version}"
            )
        return rec

    def approve_format_standard(
        self, standard_id: str, version: int, approver: str
    ) -> bool:
        """审批：仅 DRAFT 可审批；审批后成为 ACTIVE（同标准旧 ACTIVE 版本转 SUPERSEDED）。"""
        cur = self.conn.execute(
            "UPDATE format_standards SET status='APPROVED' WHERE standard_id=? "
            "AND version=? AND status='DRAFT'",
            (standard_id, int(version)),
        )
        if not cur.rowcount:
            self.conn.commit()
            return False
        self.conn.execute(
            "UPDATE format_standards SET status='SUPERSEDED' WHERE standard_id=? AND status='ACTIVE'",
            (standard_id,),
        )
        self.conn.execute(
            "UPDATE format_standards SET status='ACTIVE', approved_by=?, approved_at=?, "
            "effective_at=? WHERE standard_id=? AND version=?",
            (approver, time.time(), time.time(), standard_id, int(version)),
        )
        self.conn.commit()
        self.log_audit(
            approver, "approve_format_standard", standard_id, f"version={version}"
        )
        return True

    def get_format_standard(
        self, standard_id: str, version: int | None = None
    ) -> dict | None:
        if version is None:
            r = self.conn.execute(
                "SELECT * FROM format_standards WHERE standard_id=? ORDER BY version DESC LIMIT 1",
                (standard_id,),
            ).fetchone()
        else:
            r = self.conn.execute(
                "SELECT * FROM format_standards WHERE standard_id=? AND version=?",
                (standard_id, int(version)),
            ).fetchone()
        if not r:
            return None
        d = dict(r)
        d["params"] = json.loads(d["params"])
        return d

    def get_active_format_standard(
        self, standard_id: str = "company_format"
    ) -> dict | None:
        """仅返回审批通过（ACTIVE）的标准；未审批标准不得用于产生正式违规结论。"""
        r = self.conn.execute(
            "SELECT * FROM format_standards WHERE standard_id=? AND status='ACTIVE' "
            "ORDER BY version DESC LIMIT 1",
            (standard_id,),
        ).fetchone()
        if not r:
            return None
        d = dict(r)
        d["params"] = json.loads(d["params"])
        return d
