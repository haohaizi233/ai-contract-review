# -*- coding: utf-8 -*-
"""租户水位线、更新捕获与原子同步批次服务（T11 / F06/F12）。

提供：
1. 外部数据源 (updated_at, external_id) 复合游标支持；
2. 租户隔离的多数据源增量水位线维护；
3. 基于数据库 UnitOfWork 的原子批次提交与自动回滚（保证数据与水位线严格原子一致）；
4. 乐观游标并发冲突拦截与幂等重复批次检测。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Any

from ...domain.models.canonical_contract import (
    CanonicalContract,
    ContractAmountSummary,
    ContractNumber,
    ContractParty,
    SourceContractRecord,
    SourceRecordMapping,
    merge_canonical,
)


class SyncBatchConflictError(Exception):
    """同步游标乐观锁冲突或乱序提交。"""

    pass


@dataclass(frozen=True)
class SyncCursor:
    """复合同步游标（按更新时间与外部主键单调推进）。"""

    tenant_id: str
    source_system: str
    source_scope: str
    last_updated_at: float = 0.0
    last_external_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SyncBatchResult:
    """批次同步执行结果。"""

    batch_id: str
    success: bool
    records_synced: int
    cursor: SyncCursor
    error_message: str | None = None


class SyncWatermarkService:
    """增量同步水位线与原子批次调度器。"""

    def __init__(self, storage: Any):
        self.storage = storage
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        conn = getattr(self.storage, "conn", None)
        if conn is None:
            return
        is_sqlite = hasattr(self.storage, "_lock") and hasattr(conn, "execute")
        if is_sqlite:
            with self.storage._lock:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sync_watermarks (
                        tenant_id TEXT NOT NULL,
                        source_system TEXT NOT NULL,
                        source_scope TEXT NOT NULL,
                        last_updated_at REAL NOT NULL DEFAULT 0.0,
                        last_external_id TEXT NOT NULL DEFAULT '',
                        updated_at REAL NOT NULL,
                        PRIMARY KEY (tenant_id, source_system, source_scope)
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sync_batch_history (
                        batch_id TEXT PRIMARY KEY,
                        tenant_id TEXT NOT NULL,
                        source_system TEXT NOT NULL,
                        source_scope TEXT NOT NULL,
                        record_count INTEGER NOT NULL DEFAULT 0,
                        cursor_snapshot TEXT,
                        status TEXT NOT NULL DEFAULT 'COMMITTED',
                        created_at REAL NOT NULL
                    );
                    """
                )
                if hasattr(self.storage, "_commit"):
                    self.storage._commit()
        else:
            with self.storage._lock:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS sync_watermarks (
                            tenant_id TEXT NOT NULL,
                            source_system TEXT NOT NULL,
                            source_scope TEXT NOT NULL,
                            last_updated_at DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                            last_external_id TEXT NOT NULL DEFAULT '',
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                            PRIMARY KEY (tenant_id, source_system, source_scope)
                        );
                        CREATE TABLE IF NOT EXISTS sync_batch_history (
                            batch_id TEXT PRIMARY KEY,
                            tenant_id TEXT NOT NULL,
                            source_system TEXT NOT NULL,
                            source_scope TEXT NOT NULL,
                            record_count INTEGER NOT NULL DEFAULT 0,
                            cursor_snapshot JSONB,
                            status TEXT NOT NULL DEFAULT 'COMMITTED',
                            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                        );
                        """
                    )
                self.storage._commit()

    def get_cursor(
        self, tenant_id: str, source_system: str, source_scope: str
    ) -> SyncCursor:
        conn = getattr(self.storage, "conn", None)
        if conn is None:
            return SyncCursor(tenant_id, source_system, source_scope, 0.0, "")

        is_sqlite = hasattr(self.storage, "_lock") and hasattr(conn, "execute")
        with self.storage._lock:
            if is_sqlite:
                row = conn.execute(
                    "SELECT last_updated_at, last_external_id FROM sync_watermarks "
                    "WHERE tenant_id=? AND source_system=? AND source_scope=?",
                    (tenant_id, source_system, source_scope),
                ).fetchone()
                if not row:
                    return SyncCursor(tenant_id, source_system, source_scope, 0.0, "")
                return SyncCursor(
                    tenant_id=tenant_id,
                    source_system=source_system,
                    source_scope=source_scope,
                    last_updated_at=float(row["last_updated_at"]),
                    last_external_id=str(row["last_external_id"] or ""),
                )
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT last_updated_at, last_external_id FROM sync_watermarks "
                        "WHERE tenant_id=%s AND source_system=%s AND source_scope=%s",
                        (tenant_id, source_system, source_scope),
                    )
                    row = cur.fetchone()
                    if not row:
                        return SyncCursor(
                            tenant_id, source_system, source_scope, 0.0, ""
                        )
                    return SyncCursor(
                        tenant_id=tenant_id,
                        source_system=source_system,
                        source_scope=source_scope,
                        last_updated_at=float(row[0]),
                        last_external_id=str(row[1] or ""),
                    )

    def commit_sync_batch(
        self,
        batch_id: str,
        records: list[SourceContractRecord],
        expected_cursor: SyncCursor | None,
        new_cursor: SyncCursor,
    ) -> SyncBatchResult:
        """在单一数据库工作单元 (UoW) 内原子写入数据并推进水位线。"""
        # 1. 检查重复批次（幂等拦截）
        conn = self.storage.conn
        is_sqlite = hasattr(self.storage, "_lock") and hasattr(conn, "execute")

        with self.storage._lock:
            if is_sqlite:
                hist = conn.execute(
                    "SELECT batch_id, record_count FROM sync_batch_history WHERE batch_id=?",
                    (batch_id,),
                ).fetchone()
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT batch_id, record_count FROM sync_batch_history WHERE batch_id=%s",
                        (batch_id,),
                    )
                    hist = cur.fetchone()

            if hist:
                return SyncBatchResult(
                    batch_id=batch_id,
                    success=True,
                    records_synced=int(hist["record_count"] if is_sqlite else hist[1]),
                    cursor=new_cursor,
                )

        # 2. 事务内执行：乐观游标校验 -> 数据写入与合流 -> 水位线推进 -> 批次留痕
        with self.storage.unit_of_work():
            # (a) 乐观锁校验
            cur_cursor = self.get_cursor(
                new_cursor.tenant_id, new_cursor.source_system, new_cursor.source_scope
            )
            if expected_cursor is not None:
                if (
                    abs(cur_cursor.last_updated_at - expected_cursor.last_updated_at)
                    > 1e-6
                    or cur_cursor.last_external_id != expected_cursor.last_external_id
                ):
                    raise SyncBatchConflictError(
                        f"同步游标冲突：期望游标 {expected_cursor}，实际当前游标 {cur_cursor}"
                    )

            # (b) 数据合流与持久化
            for rec in records:
                existing_dict = self.storage.find_canonical_by_source(
                    rec.tenant_id, rec.source_system, rec.external_id
                )
                existing_canonical: CanonicalContract | None = None
                if existing_dict:
                    existing_canonical = CanonicalContract(
                        contract_id=existing_dict["contract_id"],
                        tenant_id=existing_dict.get("tenant_id", "default"),
                        contract_number=ContractNumber.parse(
                            existing_dict.get("contract_number")
                        ),
                        title=existing_dict.get("title", ""),
                        business_category=existing_dict.get("business_category", ""),
                        contract_subtype=existing_dict.get("contract_subtype", ""),
                        lifecycle_status=existing_dict.get("lifecycle_status", "DRAFT"),
                        source_system=existing_dict.get("source_system", "UPLOAD"),
                        source_document_id=existing_dict.get("source_document_id", ""),
                        source_hash=existing_dict.get("source_hash", ""),
                        parties=[
                            ContractParty(**p) for p in existing_dict.get("parties", [])
                        ],
                        amounts=ContractAmountSummary(
                            total_amount=existing_dict.get("amounts", {}).get(
                                "total_amount"
                            )
                        ),
                        source_mappings=[
                            SourceRecordMapping(**m)
                            for m in existing_dict.get("source_mappings", [])
                        ],
                        version=int(existing_dict.get("version", 1)),
                        created_at=float(existing_dict.get("created_at", 0.0)),
                        updated_at=float(existing_dict.get("updated_at", 0.0)),
                    )

                outcome = merge_canonical(existing_canonical, rec)
                saved = self.storage.save_canonical_version(
                    outcome,
                    expected_version=(
                        existing_canonical.version if existing_canonical else None
                    ),
                )
                if not saved:
                    exp_ver = existing_canonical.version if existing_canonical else None
                    raise SyncBatchConflictError(
                        f"并发版本冲突：保存标准合同版本失败 (contract_id={outcome.contract.contract_id}, "
                        f"external_id={rec.external_id}, expected_version={exp_ver})"
                    )

            # (c) 推进水位线
            now = time.time()
            if is_sqlite:
                conn.execute(
                    """
                    INSERT INTO sync_watermarks (
                        tenant_id, source_system, source_scope, last_updated_at, last_external_id, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(tenant_id, source_system, source_scope) DO UPDATE SET
                        last_updated_at=excluded.last_updated_at,
                        last_external_id=excluded.last_external_id,
                        updated_at=excluded.updated_at;
                    """,
                    (
                        new_cursor.tenant_id,
                        new_cursor.source_system,
                        new_cursor.source_scope,
                        new_cursor.last_updated_at,
                        new_cursor.last_external_id,
                        now,
                    ),
                )
                # (d) 记录批次历史
                cursor_snap = json.dumps(new_cursor.to_dict(), ensure_ascii=False)
                conn.execute(
                    """
                    INSERT INTO sync_batch_history (
                        batch_id, tenant_id, source_system, source_scope, record_count, cursor_snapshot, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'COMMITTED', ?)
                    """,
                    (
                        batch_id,
                        new_cursor.tenant_id,
                        new_cursor.source_system,
                        new_cursor.source_scope,
                        len(records),
                        cursor_snap,
                        now,
                    ),
                )
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO sync_watermarks (
                            tenant_id, source_system, source_scope, last_updated_at, last_external_id, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, now())
                        ON CONFLICT(tenant_id, source_system, source_scope) DO UPDATE SET
                            last_updated_at=EXCLUDED.last_updated_at,
                            last_external_id=EXCLUDED.last_external_id,
                            updated_at=now();
                        """,
                        (
                            new_cursor.tenant_id,
                            new_cursor.source_system,
                            new_cursor.source_scope,
                            new_cursor.last_updated_at,
                            new_cursor.last_external_id,
                        ),
                    )
                    cursor_snap = json.dumps(new_cursor.to_dict(), ensure_ascii=False)
                    cur.execute(
                        """
                        INSERT INTO sync_batch_history (
                            batch_id, tenant_id, source_system, source_scope, record_count, cursor_snapshot, status, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, 'COMMITTED', now())
                        """,
                        (
                            batch_id,
                            new_cursor.tenant_id,
                            new_cursor.source_system,
                            new_cursor.source_scope,
                            len(records),
                            cursor_snap,
                        ),
                    )

        return SyncBatchResult(
            batch_id=batch_id,
            success=True,
            records_synced=len(records),
            cursor=new_cursor,
        )
