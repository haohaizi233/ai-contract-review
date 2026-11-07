# -*- coding: utf-8 -*-
"""执行检查点 (ExecutionCheckpoints) 与断点恢复存储（T09 / F09 / ADR-001/002）。

记录每个阶段的输入指纹、环境指纹、输出快照及状态，支撑断点安全跳过与合规审计。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any


class CheckpointError(Exception):
    """Base exception for pipeline checkpoint operations."""


class CheckpointCorruptedError(CheckpointError):
    """Raised when checkpoint data or snapshot is missing, corrupted, or cannot be rehydrated."""


class ManifestMismatchError(CheckpointError):
    """Raised when existing checkpoints have a different manifest hash than the current run."""


class CheckpointIncompatibleError(CheckpointError):
    """Raised when checkpoint snapshot schema is incompatible with current code/configuration."""


@dataclass
class ExecutionCheckpoint:
    """阶段执行检查点。"""

    task_id: str
    stage_name: str
    stage_order: int
    input_hash: str
    manifest_hash: str
    output_snapshot: dict[str, Any] = field(default_factory=dict)
    status: str = "COMPLETED"  # "RUNNING", "COMPLETED", "FAILED"
    lease_token: str | None = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CheckpointStore:
    """持久化检查点读写适配器（兼容 SQLite 与 PostgreSQL）。"""

    def __init__(self, storage: Any):
        self.storage = storage
        self._ensure_table()

    def _ensure_table(self) -> None:
        conn = getattr(self.storage, "conn", None)
        if conn is None:
            return
        is_sqlite = hasattr(self.storage, "_lock") and hasattr(conn, "execute")
        if is_sqlite:
            with self.storage._lock:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS execution_checkpoints (
                        task_id TEXT NOT NULL,
                        stage_name TEXT NOT NULL,
                        stage_order INTEGER NOT NULL,
                        input_hash TEXT NOT NULL,
                        manifest_hash TEXT NOT NULL,
                        output_snapshot TEXT,
                        status TEXT NOT NULL DEFAULT 'COMPLETED',
                        lease_token TEXT,
                        created_at REAL NOT NULL,
                        PRIMARY KEY (task_id, stage_name)
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_exec_checkpoints_lookup
                        ON execution_checkpoints(task_id, stage_order);
                    """
                )
                if hasattr(self.storage, "_commit"):
                    self.storage._commit()
        else:
            # PostgreSQL
            with self.storage._lock:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS execution_checkpoints (
                            task_id TEXT NOT NULL,
                            stage_name TEXT NOT NULL,
                            stage_order INTEGER NOT NULL,
                            input_hash TEXT NOT NULL,
                            manifest_hash TEXT NOT NULL,
                            output_snapshot JSONB,
                            status TEXT NOT NULL DEFAULT 'COMPLETED',
                            lease_token TEXT,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                            PRIMARY KEY (task_id, stage_name)
                        );
                        CREATE INDEX IF NOT EXISTS idx_exec_checkpoints_lookup
                            ON execution_checkpoints(task_id, stage_order);
                        """
                    )
                self.storage._commit()

    def save_checkpoint(self, cp: ExecutionCheckpoint) -> None:
        conn = getattr(self.storage, "conn", None)
        if conn is None:
            return
        is_sqlite = hasattr(self.storage, "_lock") and hasattr(conn, "execute")
        now = time.time()
        snap_str = json.dumps(cp.output_snapshot, ensure_ascii=False)

        if is_sqlite:
            with self.storage._lock:
                conn.execute(
                    """
                    INSERT INTO execution_checkpoints (
                        task_id, stage_name, stage_order, input_hash,
                        manifest_hash, output_snapshot, status, lease_token, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id, stage_name) DO UPDATE SET
                        input_hash=excluded.input_hash,
                        manifest_hash=excluded.manifest_hash,
                        output_snapshot=excluded.output_snapshot,
                        status=excluded.status,
                        lease_token=excluded.lease_token,
                        created_at=excluded.created_at;
                    """,
                    (
                        cp.task_id,
                        cp.stage_name,
                        cp.stage_order,
                        cp.input_hash,
                        cp.manifest_hash,
                        snap_str,
                        cp.status,
                        cp.lease_token,
                        now,
                    ),
                )
                if hasattr(self.storage, "_commit"):
                    self.storage._commit()
        else:
            with self.storage._lock:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO execution_checkpoints (
                            task_id, stage_name, stage_order, input_hash,
                            manifest_hash, output_snapshot, status, lease_token, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                        ON CONFLICT(task_id, stage_name) DO UPDATE SET
                            input_hash=EXCLUDED.input_hash,
                            manifest_hash=EXCLUDED.manifest_hash,
                            output_snapshot=EXCLUDED.output_snapshot,
                            status=EXCLUDED.status,
                            lease_token=EXCLUDED.lease_token,
                            created_at=now();
                        """,
                        (
                            cp.task_id,
                            cp.stage_name,
                            cp.stage_order,
                            cp.input_hash,
                            cp.manifest_hash,
                            snap_str,
                            cp.status,
                            cp.lease_token,
                        ),
                    )
                self.storage._commit()

    def get_checkpoints(self, task_id: str) -> list[ExecutionCheckpoint]:
        conn = getattr(self.storage, "conn", None)
        if conn is None:
            return []
        is_sqlite = hasattr(self.storage, "_lock") and hasattr(conn, "execute")
        with self.storage._lock:
            if is_sqlite:
                rows = conn.execute(
                    "SELECT task_id, stage_name, stage_order, input_hash, manifest_hash, output_snapshot, status, lease_token, created_at "
                    "FROM execution_checkpoints WHERE task_id = ? ORDER BY stage_order ASC",
                    (task_id,),
                ).fetchall()
                result: list[ExecutionCheckpoint] = []
                for r in rows:
                    raw_snap = r["output_snapshot"]
                    snap = json.loads(raw_snap) if raw_snap else {}
                    result.append(
                        ExecutionCheckpoint(
                            task_id=r["task_id"],
                            stage_name=r["stage_name"],
                            stage_order=r["stage_order"],
                            input_hash=r["input_hash"],
                            manifest_hash=r["manifest_hash"],
                            output_snapshot=snap,
                            status=r["status"],
                            lease_token=r["lease_token"],
                            created_at=float(r["created_at"]),
                        )
                    )
                return result
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT task_id, stage_name, stage_order, input_hash, manifest_hash, output_snapshot, status, lease_token, EXTRACT(EPOCH FROM created_at) "
                        "FROM execution_checkpoints WHERE task_id = %s ORDER BY stage_order ASC",
                        (task_id,),
                    )
                    rows = cur.fetchall()
                    result = []
                    for r in rows:
                        snap = (
                            r[5]
                            if isinstance(r[5], dict)
                            else (json.loads(r[5]) if r[5] else {})
                        )
                        result.append(
                            ExecutionCheckpoint(
                                task_id=r[0],
                                stage_name=r[1],
                                stage_order=r[2],
                                input_hash=r[3],
                                manifest_hash=r[4],
                                output_snapshot=snap,
                                status=r[6],
                                lease_token=r[7],
                                created_at=float(r[8]),
                            )
                        )
                    return result
