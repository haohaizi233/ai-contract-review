# -*- coding: utf-8 -*-
"""PostgreSQL + pgvector 存储适配器（Phase 1.3 + 第三轮整改）。

与 SQLite Storage 同接口（upsert_document / search / rebuild_index / delete_document /
rollback_to_version / save_rule / approve_rule / feedback_issue ...），可直接替换。

整改要点（本轮）：
- 向量检索改为 **pgvector SQL 相似度排序 + LIMIT**（vec <=> %s::vector ORDER BY ... LIMIT），
  不再全量载入内存用 NumPy 计算；
- 迁移脚本按 migrations/*.sql 顺序执行（001 初始化 + 002 索引版本/租户列/格式标准）；
- Embedding 维度校验与索引版本管理（embedding_index_meta），换模型必须 rebuild_index；
- review_tasks 带 tenant_id / created_by，供 API 层强制租户与资源所有权检查。

验证状态（如实标注）：
- SQL 迁移脚本用 pglast 解析校验（tests/test_pg_migration.py）；
- 行为测试经 CONTRACT_KB_TEST_PG_DSN 在**真实 PostgreSQL + pgvector 实例**上执行
  （tests/test_pg_storage.py：迁移/导入/检索/回滚/维度校验），未配置 DSN 时 skip。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

from .embeddings import EmbeddingProvider, get_embedding_provider
from .storage import EmbeddingConfigMismatch

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
MIGRATION_FILE = MIGRATIONS_DIR / "001_init_pgvector.sql"
_CLEAN_RE = re.compile(r"[\s\u3000，。；：、“”‘’（）()《》\-—_/%‰]")


def _norm_text(t: str) -> str:
    return _CLEAN_RE.sub("", t).lower()


class PgVectorStorage:
    """PostgreSQL + pgvector 实现。需要 psycopg2 与已执行迁移的库。"""

    def __init__(
        self,
        dsn: str,
        emb: EmbeddingProvider | None = None,
        migrate: bool = True,
        rebuild_on_mismatch: bool = False,
    ):
        import psycopg2  # 延迟导入：无 PG 环境不阻塞其他功能
        import psycopg2.extras

        self._psycopg2 = psycopg2
        self.conn = psycopg2.connect(dsn)
        self.conn.autocommit = False
        # pgvector ivfflat 小语料陷阱：lists=100 而行数少于 lists 时，索引扫描可能返回空结果。
        # 会话级 probes=lists 保证精确检索（生产可按召回/延迟在会话内调整）。
        with self.conn.cursor() as cur:
            cur.execute("SET ivfflat.probes = 100")
        self.conn.commit()
        self.emb = emb or get_embedding_provider()
        if migrate:
            self.migrate()
        try:
            self._check_embedding_compat()
        except EmbeddingConfigMismatch:
            if not rebuild_on_mismatch:
                raise
            self.rebuild_index()

    # ---------- 迁移 ----------
    def migrate(self):
        """按文件名顺序执行 migrations/*.sql（幂等脚本，可重复执行）。"""
        for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
            sql = f.read_text(encoding="utf-8")
            with self.conn.cursor() as cur:
                cur.execute(sql)
            self.conn.commit()

    def _check_embedding_compat(self):
        """Embedding 维度/提供方校验（fail-closed）：
        - 向量列维度（atttypmod）必须与当前 Embedding 维度一致；
        - 已建索引的 ACTIVE 版本记录必须与当前提供方/维度一致，否则要求 rebuild_index。"""
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT atttypmod FROM pg_attribute WHERE attrelid='embeddings'::regclass "
                "AND attname='vec' AND NOT attisdropped"
            )
            row = cur.fetchone()
        col_dim = int(row[0]) if row and row[0] and int(row[0]) > 0 else None
        if col_dim is not None and col_dim != int(self.emb.dim):
            raise EmbeddingConfigMismatch(
                f"PG 向量列维度 {col_dim} != 当前 Embedding 维度 {self.emb.dim}"
                f"（{self.emb.name}）。请调整迁移中的 vector(N) 并 rebuild_index。"
            )
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT provider, dim FROM embedding_index_meta WHERE status='ACTIVE' "
                "ORDER BY version DESC LIMIT 1"
            )
            meta = cur.fetchone()
        if meta and (meta[0] != self.emb.name or int(meta[1]) != int(self.emb.dim)):
            raise EmbeddingConfigMismatch(
                f"Embedding 配置与现有索引不一致：索引={meta[0]}/dim={meta[1]}，"
                f"当前={self.emb.name}/dim={self.emb.dim}。请调用 rebuild_index() 重建。"
            )

    def _validate_vec(self, vec, context: str) -> np.ndarray:
        arr = np.asarray(vec, dtype=np.float32)
        if arr.ndim != 1 or arr.shape[0] != int(self.emb.dim):
            raise EmbeddingConfigMismatch(
                f"向量维度校验失败（{context}）：期望 dim={self.emb.dim}，实际 shape={arr.shape}"
            )
        return arr

    @staticmethod
    def _vec_literal(vec: np.ndarray) -> str:
        return "[" + ",".join(f"{float(x):.6f}" for x in vec.tolist()) + "]"

    def _ensure_index_meta(self):
        """无记录 → 登记 v1；记录不一致 → fail-closed（换模型必须 rebuild_index）。"""
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT version, provider, dim FROM embedding_index_meta WHERE status='ACTIVE' "
                "ORDER BY version DESC LIMIT 1"
            )
            row = cur.fetchone()
            if not row:
                return self._bump_index_version()
            if row[1] != self.emb.name or int(row[2]) != int(self.emb.dim):
                raise EmbeddingConfigMismatch(
                    f"Embedding 配置与现有索引不一致：索引={row[1]}/dim={row[2]}，"
                    f"当前={self.emb.name}/dim={self.emb.dim}。请调用 rebuild_index() 重建。"
                )
            return int(row[0])

    def _bump_index_version(self) -> int:
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
        self.conn.commit()
        return int(v)

    def get_index_version(self) -> dict | None:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT version, provider, dim, model_spec, created_at, status "
                "FROM embedding_index_meta WHERE status='ACTIVE' ORDER BY version DESC LIMIT 1"
            )
            row = cur.fetchone()
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

    def close(self):
        self.conn.close()

    # ---------- 导入（幂等） ----------
    def upsert_document(self, doc: dict, tenant: str = "default") -> tuple[str, bool]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT document_id FROM documents WHERE source_hash=%s",
                (doc["sha256"],),
            )
            row = cur.fetchone()
            if row:
                return row[0], False
            did = doc["template_id"]
            cur.execute(
                "INSERT INTO documents (document_id, source_file, source_hash, source_version,"
                "tenant_id, business_category, contract_subtype, document_type, status, raw_meta)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVE',%s)",
                (
                    did,
                    doc["source_file"],
                    doc["sha256"],
                    doc.get("template_version", "v1"),
                    tenant,
                    doc.get("category", ""),
                    doc.get("subtype", ""),
                    doc.get("document_type", "contract_template"),
                    json.dumps(
                        {"clause_count": doc.get("clause_count", 0)}, ensure_ascii=False
                    ),
                ),
            )
            for c in doc.get("clauses", []):
                cur.execute(
                    "INSERT INTO clauses (clause_id, document_id, clause_number, clause_category,"
                    "clause_subcategory, original_text, normalized_summary, is_company_standard,"
                    "approval_status, source_node_ids, knowledge_type)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'LAYER_A_RAW_CLAUSE')"
                    " ON CONFLICT (clause_id) DO UPDATE SET document_id=EXCLUDED.document_id,"
                    " original_text=EXCLUDED.original_text, normalized_summary=EXCLUDED.normalized_summary",
                    (
                        c["clause_id"],
                        did,
                        c.get("clause_number"),
                        c.get("clause_category"),
                        c.get("clause_subcategory"),
                        c["original_text"],
                        c.get("normalized_summary", ""),
                        bool(c.get("is_company_standard")),
                        c.get("approval_status", "PENDING_REVIEW"),
                        json.dumps(c.get("source_node_ids", []), ensure_ascii=False),
                    ),
                )
        self.conn.commit()
        self._index_clauses(did)
        self._ensure_index_meta()
        return did, True

    def _index_clauses(self, document_id: str):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT c.clause_id, c.original_text, d.business_category, d.contract_subtype, d.source_hash "
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
            for (cid, text, bc, st, sh), vec in zip(rows, vecs):
                chunk_id = f"{document_id}:{cid}"
                cur.execute(
                    "INSERT INTO embeddings (chunk_id, document_id, clause_id, business_category,"
                    "contract_subtype, knowledge_type, approved_status, source_hash, text, vec)"
                    " VALUES (%s,%s,%s,%s,%s,'LAYER_D_RAG','PENDING_REVIEW',%s,%s,%s)"
                    " ON CONFLICT (chunk_id) DO UPDATE SET text=EXCLUDED.text, vec=EXCLUDED.vec",
                    (
                        chunk_id,
                        document_id,
                        cid,
                        bc,
                        st,
                        sh,
                        text,
                        self._vec_literal(self._validate_vec(vec, "index_write")),
                    ),
                )
        self.conn.commit()

    def rebuild_index(self):
        """全量重建向量索引（幂等）：换 Embedding 模型/维度后调用，版本递增留痕。"""
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM embeddings")
            cur.execute("SELECT document_id FROM documents")
            ids = [r[0] for r in cur.fetchall()]
        self.conn.commit()
        for did in ids:
            self._index_clauses(did)
        return self._bump_index_version()

    def delete_document(self, document_id: str):
        with self.conn.cursor() as cur:  # CASCADE 处理 clauses/nodes/embeddings
            cur.execute("DELETE FROM documents WHERE document_id=%s", (document_id,))
            cur.execute(
                "INSERT INTO audit_log (actor, action, target, detail)"
                " VALUES ('system','delete_document',%s,'')",
                (document_id,),
            )
        self.conn.commit()
        self.rebuild_index()

    def rollback_to_version(self, document_id: str, source_hash: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT source_file FROM documents WHERE source_hash=%s", (source_hash,)
            )
            row = cur.fetchone()
            if not row:
                return False
            cur.execute(
                "UPDATE documents SET status='ACTIVE' WHERE source_hash=%s",
                (source_hash,),
            )
            cur.execute(
                "UPDATE documents SET status='SUPERSEDED' WHERE source_file=%s AND source_hash<>%s",
                (row[0], source_hash),
            )
        self.conn.commit()
        self.rebuild_index()
        return True

    def log_audit(self, actor: str, action: str, target: str, detail: str = ""):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO audit_log (actor, action, target, detail) VALUES (%s,%s,%s,%s)",
                (actor, action, target, detail),
            )
        self.conn.commit()

    # ---------- 混合检索（FTS + 向量 + RRF） ----------
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
        fts = self._fts_search(
            keyword or query,
            top_k * 3,
            business_category,
            contract_subtype,
            clause_number,
            approved_only,
            tenant_id,
        )
        vec = self._vector_search(
            query,
            top_k * 3,
            business_category,
            contract_subtype,
            approved_only,
            tenant_id,
        )
        fused = self._rrf(fts, vec)
        if clause_number:
            fused = [h for h in fused if h.get("clause_number") == clause_number]
        if rerank:
            fused = self._rerank(query, fused)
        return fused[:top_k]

    def _where(
        self, business_category, contract_subtype, approved_only, tenant_id, alias="e"
    ):
        sql, params = "", []
        if business_category:
            sql += f" AND {alias}.business_category=%s"
            params.append(business_category)
        if contract_subtype:
            sql += f" AND {alias}.contract_subtype=%s"
            params.append(contract_subtype)
        if approved_only:
            sql += f" AND {alias}.approved_status='APPROVED'"
        if tenant_id:
            sql += " AND d.tenant_id=%s"
            params.append(tenant_id)
        return sql, params

    def _fts_search(
        self,
        query,
        limit,
        business_category,
        contract_subtype,
        clause_number,
        approved_only,
        tenant_id,
    ):
        words = [w for w in re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z0-9]+", query)][:8]
        if not words:
            return []
        match = " | ".join(words)
        where, params = self._where(
            business_category, contract_subtype, approved_only, tenant_id
        )
        extra, eparams = "", []
        if clause_number:
            extra = " AND c.clause_number=%s"
            eparams.append(clause_number)
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT c.clause_id, c.document_id, c.original_text, c.clause_number,"
                " e.chunk_id, e.business_category, e.contract_subtype FROM clauses c "
                "JOIN embeddings e ON e.clause_id=c.clause_id "
                "JOIN documents d ON d.document_id=c.document_id "
                "WHERE to_tsvector('simple', coalesce(c.original_text,'')) @@ plainto_tsquery('simple', %s)"
                + where
                + extra
                + " LIMIT %s",
                [match] + params + eparams + [limit],
            )
            rows = cur.fetchall()
        return [
            {
                "chunk_id": r[4],
                "clause_id": r[0],
                "document_id": r[1],
                "text": r[2],
                "clause_number": r[3],
                "channel": "fts",
                "business_category": r[5],
                "contract_subtype": r[6],
            }
            for r in rows
        ]

    def _vector_search(
        self,
        query,
        limit,
        business_category,
        contract_subtype,
        approved_only,
        tenant_id,
    ):
        """pgvector SQL 相似度排序 + LIMIT（不在应用层全量载入向量计算）。

        vec <=> q 为余弦距离，score = 1 - 距离；排序与截断全部下推数据库执行。
        """
        where, params = self._where(
            business_category, contract_subtype, approved_only, tenant_id
        )
        qv = self._validate_vec(self.emb.embed([query])[0], "query")
        q_lit = self._vec_literal(qv)
        sql = (
            "SELECT e.chunk_id, e.clause_id, e.document_id, e.text,"
            " 1-(e.vec <=> %s::vector) AS score, e.business_category, e.contract_subtype,"
            " c.clause_number FROM embeddings e "
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
                "channel": "vector",
            }
            for r in rows
        ]

    @staticmethod
    def _rrf(fts_hits, vec_hits, k: int = 60):
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
    def _rerank(query: str, hits: list[dict]):
        qn = _norm_text(query)
        qgrams = {qn[i : i + 2] for i in range(len(qn) - 1)} or {qn}
        for h in hits:
            tn = _norm_text(h["text"])
            tgrams = {tn[i : i + 2] for i in range(len(tn) - 1)} or {tn}
            h["rerank_score"] = len(qgrams & tgrams) / max(1, len(qgrams))
        return sorted(hits, key=lambda h: -h["rerank_score"])

    # ---------- 规则 / 任务 / 反馈（同 SQLite 语义） ----------
    def save_rule(self, rule: dict):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO rules (rule_id, version, name, rule_type, execution_mode, severity,"
                " priority, contract_types, params, source_clauses, status, approved_by, effective_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (rule_id, version) DO UPDATE"
                " SET status=EXCLUDED.status, params=EXCLUDED.params",
                (
                    rule["rule_id"],
                    rule.get("version", 1),
                    rule["name"],
                    rule["rule_type"],
                    rule["execution_mode"],
                    rule["severity"],
                    rule.get("priority", 100),
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
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE rules SET status='APPROVED', approved_by=%s, effective_at=now() "
                "WHERE rule_id=%s AND version=%s AND status='DRAFT'",
                (approver, rule_id, version),
            )
            updated = cur.rowcount
            if updated:
                cur.execute(
                    "INSERT INTO audit_log (actor, action, target, detail)"
                    " VALUES (%s,'approve_rule',%s,%s)",
                    (approver, rule_id, f"version={version}"),
                )
        self.conn.commit()
        return bool(updated)

    def get_rules(self, status: str | None = None) -> list[dict]:
        with self.conn.cursor() as cur:
            if status:
                cur.execute("SELECT * FROM rules WHERE status=%s", (status,))
            else:
                cur.execute("SELECT * FROM rules")
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        out = []
        for r in rows:
            d = dict(zip(cols, r))
            for k in ("contract_types", "params", "source_clauses"):
                if isinstance(d.get(k), str):
                    d[k] = json.loads(d[k])
            out.append(d)
        return out

    def list_documents(self, tenant_id: str | None = None):
        sql = (
            "SELECT document_id, source_file, source_hash, business_category,"
            " contract_subtype, status, imported_at, tenant_id FROM documents"
        )
        params: tuple = ()
        if tenant_id is not None:
            sql += " WHERE tenant_id=%s"
            params = (tenant_id,)
        sql += " ORDER BY imported_at"
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def save_task(
        self,
        task_id,
        document_id,
        mode,
        status,
        meta,
        tenant_id: str = "default",
        created_by: str = "system",
    ):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO review_tasks (task_id, document_id, mode, status, meta, tenant_id,"
                " created_by) VALUES (%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (task_id) DO UPDATE SET status=EXCLUDED.status",
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

    def get_task(self, task_id):
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM review_tasks WHERE task_id=%s", (task_id,))
            row = cur.fetchone()
            cols = [d[0] for d in cur.description]
        return dict(zip(cols, row)) if row else None

    def get_issue(self, issue_id: str) -> dict | None:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT i.*, t.tenant_id AS task_tenant_id, t.created_by AS task_created_by "
                "FROM review_issues i JOIN review_tasks t ON t.task_id=i.task_id "
                "WHERE i.issue_id=%s",
                (issue_id,),
            )
            row = cur.fetchone()
            cols = [d[0] for d in cur.description]
        return dict(zip(cols, row)) if row else None

    def save_issues(self, task_id: str, issues: list[dict]):
        with self.conn.cursor() as cur:
            for it in issues:
                cur.execute(
                    "INSERT INTO review_issues (issue_id, task_id, rule_id, severity, category,"
                    " title, description, expected_value, actual_value, source_ref, knowledge_refs,"
                    " suggestion, execution_mode, requires_human_review, confidence, human_state,"
                    " human_note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PENDING',NULL)"
                    " ON CONFLICT (issue_id) DO UPDATE SET human_state=EXCLUDED.human_state",
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
                        bool(it.get("requires_human_review")),
                        it.get("confidence"),
                    ),
                )
        self.conn.commit()

    def get_issues(self, task_id: str) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM review_issues WHERE task_id=%s", (task_id,))
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]

    def feedback_issue(
        self, issue_id: str, action: str, actor: str, note: str = ""
    ) -> bool:
        valid = {"ACCEPT", "IGNORE", "EDIT", "NOTE", "FALSE_POSITIVE", "FALSE_NEGATIVE"}
        if action not in valid:
            return False
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
                    (actor, issue_id, action),
                )
        self.conn.commit()
        return bool(updated)

    # ---------- 公司格式标准（与 SQLite 语义一致） ----------
    def save_format_standard(
        self,
        standard_id: str,
        params: dict,
        created_by: str,
        version: int | None = None,
        note: str = "",
    ) -> dict:
        with self.conn.cursor() as cur:
            if version is None:
                cur.execute(
                    "SELECT MAX(version) FROM format_standards WHERE standard_id=%s",
                    (standard_id,),
                )
                version = (cur.fetchone()[0] or 0) + 1
            cur.execute(
                "INSERT INTO format_standards (standard_id, version, params, status, created_by,"
                " note) VALUES (%s,%s,%s,'DRAFT',%s,%s)",
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
        rec = self.get_format_standard(standard_id, int(version))
        if rec is None:
            raise RuntimeError(
                f"格式标准保存失败（写入后无法读取）：{standard_id} v{version}"
            )
        return rec

    def approve_format_standard(
        self, standard_id: str, version: int, approver: str
    ) -> bool:
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE format_standards SET status='APPROVED' WHERE standard_id=%s"
                " AND version=%s AND status='DRAFT'",
                (standard_id, int(version)),
            )
            updated = cur.rowcount
            if not updated:
                self.conn.commit()
                return False
            cur.execute(
                "UPDATE format_standards SET status='SUPERSEDED'"
                " WHERE standard_id=%s AND status='ACTIVE'",
                (standard_id,),
            )
            cur.execute(
                "UPDATE format_standards SET status='ACTIVE', approved_by=%s, approved_at=now(),"
                " effective_at=now() WHERE standard_id=%s AND version=%s",
                (approver, standard_id, int(version)),
            )
            cur.execute(
                "INSERT INTO audit_log (actor, action, target, detail)"
                " VALUES (%s,'approve_format_standard',%s,%s)",
                (approver, standard_id, f"version={version}"),
            )
        self.conn.commit()
        return True

    def get_format_standard(
        self, standard_id: str, version: int | None = None
    ) -> dict | None:
        with self.conn.cursor() as cur:
            if version is None:
                cur.execute(
                    "SELECT * FROM format_standards WHERE standard_id=%s"
                    " ORDER BY version DESC LIMIT 1",
                    (standard_id,),
                )
            else:
                cur.execute(
                    "SELECT * FROM format_standards WHERE standard_id=%s AND version=%s",
                    (standard_id, int(version)),
                )
            row = cur.fetchone()
            cols = [c[0] for c in cur.description]
        if not row:
            return None
        d = dict(zip(cols, row))
        if isinstance(d.get("params"), str):
            d["params"] = json.loads(d["params"])
        return d

    def get_active_format_standard(
        self, standard_id: str = "company_format"
    ) -> dict | None:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM format_standards WHERE standard_id=%s AND status='ACTIVE'"
                " ORDER BY version DESC LIMIT 1",
                (standard_id,),
            )
            row = cur.fetchone()
            cols = [c[0] for c in cur.description]
        if not row:
            return None
        d = dict(zip(cols, row))
        if isinstance(d.get("params"), str):
            d["params"] = json.loads(d["params"])
        return d
