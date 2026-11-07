#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dry Run 知识审批与证据来源数据审计工具 (Audit Knowledge Approval Data).

检查现有数据库中可能存在的错误 APPROVED 记录、审批证据缺失记录，
提供统计指标、差异报告、数据备份与回滚方案。

用法：
    python scripts/audit_knowledge_approval_data.py --db-path data/contract_kb.db --dry-run
    python scripts/audit_knowledge_approval_data.py --db-path data/contract_kb.db --generate-sql
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any


def audit_sqlite(db_path: Path, *, dry_run: bool = True) -> dict[str, Any]:
    if not db_path.exists():
        return {"error": f"数据库文件不存在：{db_path}"}

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    report: dict[str, Any] = {
        "db_type": "sqlite",
        "db_path": str(db_path),
        "audit_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dry_run": dry_run,
        "statistics": {},
        "anomalies": [],
        "remediation_plan": {},
    }

    try:
        cur = conn.cursor()

        # 1. 基础体量统计
        total_docs = cur.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        total_clauses = cur.execute("SELECT COUNT(*) FROM clauses").fetchone()[0]
        total_embs = 0
        try:
            total_embs = cur.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        except sqlite3.OperationalError:
            pass

        # 2. 审批状态分布
        appr_dist = dict(
            cur.execute(
                "SELECT COALESCE(approval_status, 'NULL'), COUNT(*) FROM clauses GROUP BY 1"
            ).fetchall()
        )

        # 3. 检查是否有知识审批凭据表
        has_approval_tbl = (
            cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_approval_records'"
            ).fetchone()
            is not None
        )

        # 4. 异常检测 A：非 ACTIVE 文档中存在 APPROVED 条款
        stale_approved = cur.execute(
            """
            SELECT c.clause_id, c.document_id, d.status AS doc_status, c.approval_status
            FROM clauses c
            JOIN documents d ON d.document_id = c.document_id
            WHERE c.approval_status = 'APPROVED' AND d.status <> 'ACTIVE'
            """
        ).fetchall()

        # 5. 异常检测 B：标记为 APPROVED 但缺乏可信审计留痕的记录
        unverified_approved = []
        if has_approval_tbl:
            unverified_approved = cur.execute(
                """
                SELECT c.clause_id, c.document_id, c.approval_status
                FROM clauses c
                WHERE c.approval_status = 'APPROVED'
                  AND c.document_id NOT IN (
                      SELECT knowledge_id FROM knowledge_approval_records WHERE approval_action='APPROVE'
                  )
                """
            ).fetchall()
        else:
            # 无审批记录表时，检查 audit_log
            has_audit_log = (
                cur.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit_log'"
                ).fetchone()
                is not None
            )
            if has_audit_log:
                unverified_approved = cur.execute(
                    """
                    SELECT c.clause_id, c.document_id, c.approval_status
                    FROM clauses c
                    WHERE c.approval_status = 'APPROVED'
                      AND c.document_id NOT IN (
                          SELECT target FROM audit_log WHERE action='approve_knowledge_version'
                      )
                    """
                ).fetchall()

        report["statistics"] = {
            "total_documents": total_docs,
            "total_clauses": total_clauses,
            "total_embeddings": total_embs,
            "clause_approval_distribution": appr_dist,
        }

        report["anomalies"] = {
            "stale_document_approved_clauses_count": len(stale_approved),
            "stale_document_approved_samples": [dict(r) for r in stale_approved[:10]],
            "unverified_approved_clauses_count": len(unverified_approved),
            "unverified_approved_samples": [dict(r) for r in unverified_approved[:10]],
        }

        # 6. 生成修复 SQL 与回滚方案
        remediation_sql = [
            "-- 1. 将非 ACTIVE 文档下的 APPROVED 条款置为 REVOKED",
            "UPDATE clauses SET approval_status='REVOKED' WHERE clause_id IN ("
            "  SELECT c.clause_id FROM clauses c JOIN documents d ON d.document_id=c.document_id"
            "  WHERE c.approval_status='APPROVED' AND d.status<>'ACTIVE'"
            ");",
            "-- 2. 将缺乏审批证据的条款归入待复核队列 PENDING_REVIEW",
            "UPDATE clauses SET approval_status='PENDING_REVIEW' WHERE clause_id IN ("
            "  SELECT clause_id FROM clauses WHERE approval_status='APPROVED' AND document_id NOT IN ("
            "    SELECT target FROM audit_log WHERE action='approve_knowledge_version'"
            "  )"
            ");",
            "-- 3. 同步同步 embeddings 索引",
            "UPDATE embeddings SET approved_status = ("
            "  SELECT c.approval_status FROM clauses c WHERE c.clause_id=embeddings.clause_id"
            ") WHERE EXISTS (SELECT 1 FROM clauses c WHERE c.clause_id=embeddings.clause_id);",
        ]

        report["remediation_plan"] = {
            "proposed_sql": remediation_sql,
            "backup_recommendation": f"cp {db_path} {db_path}.backup_{int(time.time())}",
            "rollback_sql": [
                "-- 回滚时使用备份文件直接覆盖即可：",
                f"-- cp {db_path}.backup_<timestamp> {db_path}",
            ],
        }

    finally:
        conn.close()

    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 11: 知识审批可信审计与干运行工具"
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path("data/contract_kb.db"),
        help="SQLite 数据库文件路径",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="以 Dry Run 模式审计，不修改数据库",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="输出 JSON 审计报告路径",
    )

    args = parser.parse_args()
    res = audit_sqlite(args.db_path, dry_run=args.dry_run)
    formatted = json.dumps(res, indent=2, ensure_ascii=False)
    print(formatted)

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(formatted, encoding="utf-8")
        print(f"报告已保存至：{args.output_json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
