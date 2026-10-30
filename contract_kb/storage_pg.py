# -*- coding: utf-8 -*-
"""PostgreSQL + pgvector 存储适配器（Phase 1.3）。

与 SQLite Storage 同接口（upsert_document / search / rebuild_index / delete_document /
rollback_to_version / save_rule / approve_rule / feedback_issue ...），可直接替换。

验证状态（如实标注）：
- SQL 迁移脚本已用 pglast（PostgreSQL 语法解析器）在 CI/测试中校验（tests/test_pg_migration.py）；
- 本工作区无 PostgreSQL 实例，运行时行为（ivfflat 索引、vector 相似度、事务并发）
  **未在真实 PG 上验证**——测试通过 CONTRACT_KB_TEST_PG_DSN 环境变量启用，
  未配置时自动 skip，配置后可执行全套行为一致性测试（tests/test_pg_storage.py）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

from .embeddings import EmbeddingProvider, get_embedding_provider

MIGRATION_FILE = Path(__file__).resolve().parent.parent / "migrations" / "001_init_pgvector.sql"
_CLEAN_RE = re.compile(r"[\s\u3000，。；：、“”‘’（）()《》\-—_/%‰]")


def _norm_text(t: str) -> str:
    return _CLEAN_RE.sub("", t).lower()


class PgVectorStorage:
    """PostgreSQL + pgvector 实现。需要 psycopg2 与已执行迁移的库。"""

    def __init__(self, dsn: str, emb: EmbeddingProvider | None = None, migrate: bool = True):
        import psycopg2  # 延迟导入：无 PG 环境不阻塞其他功能
        import psycopg2.extras
        self._psycopg2 = psycopg2
        self.conn = psycopg2.connect(dsn)
        self.conn.autocommit = False
        self.emb = emb or get_embedding_provider()
        if migrate:
            self.migrate()

    # ---------- 迁移 ----------
    def migrate(self):
        sql = MIGRATION_FILE.read_text(encoding="utf-8")
        with self.conn.cursor() as cur:
            cur.execute(sql)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ---------- 导入（幂等） ----------
    def upsert_document(self, doc: dict, tenant: str = "default") -> tuple[str, bool]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT document_id FROM documents WHERE source_hash=%s",
                        (doc["sha256"],))
            row = cur.fetchone()
            if row:
                return row[0], False
            did = doc["template_id"]
            cur.execute(
                "INSERT INTO documents (document_id, source_file, source_hash, source_version,"
                "tenant_id, business_category, contract_subtype, document_type, status, raw_meta)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVE',%s)",
                (did, doc["source_file"], doc["sha256"], doc.get("template_version", "v1"),
                 tenant, doc.get("category", ""), doc.get("subtype", ""),
                 doc.get("document_type", "contract_template"),
                 json.dumps({"clause_count": doc.get("clause_count", 0)}, ensure_ascii=False)))
            for c in doc.get("clauses", []):
                cur.execute(
                    "INSERT INTO clauses (clause_id, document_id, clause_number, clause_category,"
                    "clause_subcategory, original_text, normalized_summary, is_company_standard,"
                    "approval_status, source_node_ids, knowledge_type)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'LAYER_A_RAW_CLAUSE')",
                    (c["clause_id"], did, c.get("clause_number"), c.get("clause_category"),
                     c.get("clause_subcategory"), c["original_text"], c.get("normalized_summary", ""),
                     bool(c.get("is_company_standard")), c.get("approval_status", "PENDING_REVIEW"),
                     json.dumps(c.get("source_node_ids", []), ensure_ascii=False)))
        self.conn.commit()
        self._index_clauses(did)
        return did, True

    def _index_clauses(self, document_id: str):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT c.clause_id, c.original_text, d.business_category, d.contract_subtype, d.source_hash "
                "FROM clauses c JOIN documents d ON d.document_id=c.document_id "
                "WHERE c.document_id=%s", (document_id,))
            rows = cur.fetchall()
        if not rows:
            return
        vecs = self.emb.embed([r[1] for r in rows])
        with self.conn.cursor() as cur:
            for (cid, text, bc, st, sh), vec in zip(rows, vecs):
                chunk_id = f"{document_id}:{cid}"
                cur.execute(
                    "INSERT INTO embeddings (chunk_id, document_id, clause_id, business_category,"
                    "contract_subtype, knowledge_type, approved_status, source_hash, text, vec)"
                    " VALUES (%s,%s,%s,%s,%s,'LAYER_D_RAG','PENDING_REVIEW',%s,%s,%s)"
                    " ON CONFLICT (chunk_id) DO UPDATE SET text=EXCLUDED.text, vec=EXCLUDED.vec",
                    (chunk_id, document_id, cid, bc, st, sh, text,
                     np.asarray(vec, dtype=np.float32).tolist()))
        self.conn.commit()

    def rebuild_index(self):
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM embeddings")
            cur.execute("SELECT document_id FROM documents")
            ids = [r[0] for r in cur.fetchall()]
        self.conn.commit()
        for did in ids:
            self._index_clauses(did)

    def delete_document(self, document_id: str):
        with self.conn.cursor() as cur:  # CASCADE 处理 clauses/nodes/embeddings
            cur.execute("DELETE FROM documents WHERE document_id=%s", (document_id,))
            cur.execute("INSERT INTO audit_log (actor, action, target, detail)"
                        " VALUES ('system','delete_document',%s,'')", (document_id,))
        self.conn.commit()
        self.rebuild_index()

    def rollback_to_version(self, document_id: str, source_hash: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("SELECT source_file FROM documents WHERE source_hash=%s", (source_hash,))
            row = cur.fetchone()
            if not row:
                return False
            cur.execute("UPDATE documents SET status='ACTIVE' WHERE source_hash=%s", (source_hash,))
            cur.execute("UPDATE documents SET status='SUPERSEDED' WHERE source_file=%s AND source_hash<>%s",
                        (row[0], source_hash))
        self.conn.commit()
        self.rebuild_index()
        return True

    def log_audit(self, actor: str, action: str, target: str, detail: str = ""):
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO audit_log (actor, action, target, detail) VALUES (%s,%s,%s,%s)",
                        (actor, action, target, detail))
        self.conn.commit()

    # ---------- 混合检索（FTS + 向量 + RRF） ----------
    def search(self, query: str, top_k: int = 8, business_category: str | None = None,
               contract_subtype: str | None = None, clause_number: str | None = None,
               keyword: str | None = None, knowledge_type: str | None = None,
               approved_only: bool = False, rerank: bool = True,
               tenant_id: str | None = None) -> list[dict]:
        fts = self._fts_search(keyword or query, top_k * 3, business_category, contract_subtype,
                               clause_number, approved_only, tenant_id)
        vec = self._vector_search(query, top_k * 3, business_category, contract_subtype,
                                  approved_only, tenant_id)
        fused = self._rrf(fts, vec)
        if clause_number:
            fused = [h for h in fused if h.get("clause_number") == clause_number]
        if rerank:
            fused = self._rerank(query, fused)
        return fused[:top_k]

    def _where(self, business_category, contract_subtype, approved_only, tenant_id, alias="e"):
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

    def _fts_search(self, query, limit, business_category, contract_subtype,
                    clause_number, approved_only, tenant_id):
        words = [w for w in re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z0-9]+", query)][:8]
        if not words:
            return []
        match = " | ".join(words)
        where, params = self._where(business_category, contract_subtype, approved_only, tenant_id)
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
                + where + extra + " LIMIT %s",
                [match] + params + eparams + [limit])
            rows = cur.fetchall()
        return [{"chunk_id": r[4], "clause_id": r[0], "document_id": r[1], "text": r[2],
                 "clause_number": r[3], "channel": "fts",
                 "business_category": r[5], "contract_subtype": r[6]} for r in rows]

    def _vector_search(self, query, limit, business_category, contract_subtype,
                       approved_only, tenant_id):
        where, params = self._where(business_category, contract_subtype, approved_only, tenant_id)
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT e.chunk_id, e.clause_id, e.document_id, e.text, e.vec::text,"
                " e.business_category, e.contract_subtype FROM embeddings e "
                "JOIN documents d ON d.document_id=e.document_id WHERE 1=1" + where, params)
            rows = cur.fetchall()
        if not rows:
            return []
        qv = self.emb.embed([query])[0]
        out = []
        for chunk_id, cid, did, text, vec_text, bc, st in rows:
            vec = np.array(json.loads(vec_text), dtype=np.float32)
            sim = float(vec @ qv)
            out.append({"chunk_id": chunk_id, "clause_id": cid, "document_id": did,
                        "text": text, "clause_number": None, "channel": "vector",
                        "score": sim, "business_category": bc, "contract_subtype": st})
        out.sort(key=lambda h: -h["score"])
        return out[:limit]

    @staticmethod
    def _rrf(fts_hits, vec_hits, k: int = 60):
        scores, merged = {}, {}
        for rank, h in enumerate(fts_hits):
            scores[h["chunk_id"]] = scores.get(h["chunk_id"], 0.0) + 1.0 / (k + rank + 1)
            merged[h["chunk_id"]] = h
        for rank, h in enumerate(vec_hits):
            scores[h["chunk_id"]] = scores.get(h["chunk_id"], 0.0) + 1.0 / (k + rank + 1)
            merged.setdefault(h["chunk_id"], h)
        out = []
        for cid, s in sorted(scores.items(), key=lambda kv: -kv[1]):
            item = dict(merged[cid])
            item["rrf_score"] = s
            item["channels"] = [x["channel"] for x in fts_hits + vec_hits if x["chunk_id"] == cid]
            out.append(item)
        return out

    @staticmethod
    def _rerank(query: str, hits: list[dict]):
        qn = _norm_text(query)
        qgrams = {qn[i:i + 2] for i in range(len(qn) - 1)} or {qn}
        for h in hits:
            tn = _norm_text(h["text"])
            tgrams = {tn[i:i + 2] for i in range(len(tn) - 1)} or {tn}
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
                (rule["rule_id"], rule.get("version", 1), rule["name"], rule["rule_type"],
                 rule["execution_mode"], rule["severity"], rule.get("priority", 100),
                 json.dumps(rule.get("contract_types", ["*"])),
                 json.dumps(rule.get("parameters", {}), ensure_ascii=False),
                 json.dumps(rule.get("source_clauses", []), ensure_ascii=False),
                 rule.get("status", "DRAFT"), rule.get("approved_by"), rule.get("effective_at")))
        self.conn.commit()

    def approve_rule(self, rule_id: str, approver: str, version: int = 1) -> bool:
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE rules SET status='APPROVED', approved_by=%s, effective_at=now() "
                "WHERE rule_id=%s AND version=%s AND status='DRAFT'",
                (approver, rule_id, version))
            updated = cur.rowcount
            if updated:
                cur.execute("INSERT INTO audit_log (actor, action, target, detail)"
                            " VALUES (%s,'approve_rule',%s,%s)",
                            (approver, rule_id, f"version={version}"))
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

    def list_documents(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT document_id, source_file, source_hash, business_category,"
                        " contract_subtype, status, imported_at FROM documents ORDER BY imported_at")
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def save_task(self, task_id, document_id, mode, status, meta):
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO review_tasks (task_id, document_id, mode, status, meta)"
                        " VALUES (%s,%s,%s,%s,%s) ON CONFLICT (task_id) DO UPDATE SET status=EXCLUDED.status",
                        (task_id, document_id, mode, status, json.dumps(meta, ensure_ascii=False)))
        self.conn.commit()

    def get_task(self, task_id):
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM review_tasks WHERE task_id=%s", (task_id,))
            row = cur.fetchone()
        return dict(zip([d[0] for d in cur.description], row)) if row else None

    def feedback_issue(self, issue_id: str, action: str, actor: str, note: str = "") -> bool:
        valid = {"ACCEPT", "IGNORE", "EDIT", "NOTE", "FALSE_POSITIVE", "FALSE_NEGATIVE"}
        if action not in valid:
            return False
        with self.conn.cursor() as cur:
            cur.execute("UPDATE review_issues SET human_state=%s, human_note=%s WHERE issue_id=%s",
                        (action, note, issue_id))
            updated = cur.rowcount
            if updated:
                cur.execute("INSERT INTO audit_log (actor, action, target, detail)"
                            " VALUES (%s,'issue_feedback',%s,%s)", (actor, issue_id, action))
        self.conn.commit()
        return bool(updated)
