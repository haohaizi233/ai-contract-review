# -*- coding: utf-8 -*-
"""基于关系数据库持久化的轻量异步任务执行器（PersistentTaskRunner）。

设计目标（见 ADR-004）：
- 不引入无必要的 Kafka / Redis / Celery 外部运维依赖；
- 基于 SQLite 原子锁 / PostgreSQL `FOR UPDATE SKIP LOCKED` 实现并发安全消费；
- 完整支持：租户级幂等键（idempotency_key）、进度上报、取消信号、指数退避重试、
  死信诊断落盘（DEAD_LETTER）、线程池并发度上限以及终态临时上传文件自动清理。
"""

from __future__ import annotations

import concurrent.futures
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ...application.security.idempotency import (
    IdempotencyConflictError,
    IdempotencyOutcome,
    RequestIdentity,
    compute_request_fingerprint,
)
from ..observability import GLOBAL_METRICS, get_redacted_logger, redact_sensitive_text

logger = get_redacted_logger("contract_kb.task_queue")


@dataclass
class JobExecutionContext:
    job_id: str
    tenant_id: str
    created_by: str
    attempt: int
    max_attempts: int
    update_progress: Callable[[int], None]
    is_cancelled: Callable[[], bool]


JobHandler = Callable[[dict[str, Any], JobExecutionContext], dict[str, Any]]


class PersistentTaskRunner:
    """轻量持久化异步任务调度与执行器。"""

    def __init__(
        self,
        storage: Any,
        *,
        max_workers: int = 2,
        base_backoff_seconds: float = 1.0,
        poll_interval_seconds: float = 0.25,
    ):
        self.storage = storage
        self.max_workers = max(1, int(max_workers))
        self.base_backoff_seconds = max(0.0, float(base_backoff_seconds))
        self.poll_interval_seconds = max(0.05, float(poll_interval_seconds))
        self._handlers: dict[str, JobHandler] = {}
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._pool: concurrent.futures.ThreadPoolExecutor | None = None
        self._register_builtin_handlers()

    def _register_builtin_handlers(self) -> None:
        self.register_handler("CONTRACT_REVIEW", self._builtin_review_handler)

    def _builtin_review_handler(
        self, payload: dict[str, Any], ctx: JobExecutionContext
    ) -> dict[str, Any]:
        from ...review_engine import ReviewEngine

        docx_path = payload["docx_path"]
        mode = str(payload.get("mode") or "STANDARD")
        custom_reqs = list(payload.get("custom_requirements") or [])
        ctx.update_progress(15)
        if ctx.is_cancelled():
            return {"status": "CANCELLED"}
        engine = ReviewEngine(self.storage)
        res = engine.review_result(
            docx_path,
            mode=mode,
            custom_requirements=custom_reqs,
            tenant_id=ctx.tenant_id,
            created_by=ctx.created_by,
            idempotency_key=payload.get("idempotency_key"),
        )
        ctx.update_progress(95)
        GLOBAL_METRICS.record_review(
            status=res.status, stages=res.stages, issues=res.issues
        )
        return res.to_dict()

    def register_handler(self, job_type: str, handler: JobHandler) -> None:
        self._handlers[job_type] = handler

    def submit(
        self,
        job_type: str,
        payload: dict[str, Any],
        *,
        tenant_id: str = "default",
        created_by: str = "system",
        idempotency_key: str | None = None,
        max_attempts: int = 3,
        temp_file_path: str | Path | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if job_type not in self._handlers:
            raise ValueError(f"未注册的异步任务类型：{job_type!r}")
        job_id = f"job_{uuid.uuid4().hex[:12]}"

        if idempotency_key:
            raw_bytes: bytes | None = None
            if temp_file_path and Path(temp_file_path).exists():
                try:
                    raw_bytes = Path(temp_file_path).read_bytes()
                except OSError:
                    pass
            elif "docx_path" in payload and Path(payload["docx_path"]).exists():
                try:
                    raw_bytes = Path(payload["docx_path"]).read_bytes()
                except OSError:
                    pass

            extra = {
                k: v
                for k, v in payload.items()
                if k
                not in (
                    "docx_path",
                    "temp_file_path",
                    "idempotency_key",
                    "mode",
                    "custom_requirements",
                )
            }
            fp = compute_request_fingerprint(
                content_bytes=raw_bytes,
                mode=str(payload.get("mode", "")),
                custom_requirements=payload.get("custom_requirements"),
                operation=job_type,
                extra_params=extra if extra else None,
            )
            ident = RequestIdentity(
                tenant_id=tenant_id,
                actor_id=created_by,
                operation=job_type,
                key=idempotency_key,
                fingerprint=fp,
            )

            reserve_fn = getattr(self.storage, "reserve_idempotency_key", None)
            if reserve_fn is not None:
                outcome: IdempotencyOutcome = reserve_fn(ident, job_id)
                if outcome.status == "CONFLICT":
                    if temp_file_path:
                        self._cleanup_temp_file(str(temp_file_path))
                    raise IdempotencyConflictError(
                        outcome.message
                        or f"Idempotency key conflict: request fingerprint mismatch for key {idempotency_key}"
                    )
                elif outcome.status in ("IN_PROGRESS", "REPLAY"):
                    if temp_file_path:
                        self._cleanup_temp_file(str(temp_file_path))
                    job = self.storage.get_async_job(outcome.resource_id, tenant_id=tenant_id)
                    if job:
                        return job, False
                elif outcome.status == "CREATED":
                    job_id = outcome.resource_id or job_id

        persisted_id, created = self.storage.enqueue_async_job(
            {
                "job_id": job_id,
                "job_type": job_type,
                "payload": payload,
                "tenant_id": tenant_id,
                "created_by": created_by,
                "idempotency_key": idempotency_key,
                "max_attempts": max(1, int(max_attempts)),
                "temp_file_path": str(temp_file_path) if temp_file_path else None,
            }
        )
        if created:
            GLOBAL_METRICS.record_async_job_event("async_jobs_enqueued")
        elif temp_file_path:
            # 幂等命中既有任务时，立即清理本次冗余上传临时文件
            self._cleanup_temp_file(str(temp_file_path))
        job = self.storage.get_async_job(persisted_id, tenant_id=tenant_id)
        return job or {"job_id": persisted_id}, created

    def cancel(self, job_id: str, *, tenant_id: str | None = None) -> bool:
        job = self.storage.get_async_job(job_id, tenant_id=tenant_id)
        if not job:
            return False
        ok = bool(self.storage.cancel_async_job(job_id, tenant_id=tenant_id))
        updated = self.storage.get_async_job(job_id, tenant_id=tenant_id)
        if updated and updated.get("status") == "CANCELLED":
            self._cleanup_temp_file(updated.get("temp_file_path"))
        return ok

    def process_next(self) -> dict[str, Any] | None:
        """认领并同步执行下一条到期任务（便于确定性单测及内嵌轻量调度）。"""
        supported_types = list(self._handlers.keys())
        job = self.storage.claim_next_async_job(job_types=supported_types)
        if job is None:
            return None
        return self._execute_claimed_job(job)

    def drain(self, *, max_jobs: int = 50) -> list[dict[str, Any]]:
        """连续消费所有当前可执行任务（最多 max_jobs 条）。"""
        completed: list[dict[str, Any]] = []
        for _ in range(max_jobs):
            res = self.process_next()
            if res is None:
                break
            completed.append(res)
        return completed

    def _execute_claimed_job(self, job: dict[str, Any]) -> dict[str, Any]:
        job_id = job["job_id"]
        tenant_id = job.get("tenant_id", "default")
        job_type = job["job_type"]
        attempt = int(job.get("attempts", 1))
        max_attempts = int(job.get("max_attempts", 3))
        temp_path = job.get("temp_file_path")

        def _update_progress(pct: int) -> None:
            clamped = max(0, min(100, int(pct)))
            self.storage.update_async_job(job_id, progress=clamped)

        def _is_cancelled() -> bool:
            latest = self.storage.get_async_job(job_id)
            return bool(
                latest
                and (
                    latest.get("cancel_requested")
                    or latest.get("status") == "CANCELLED"
                )
            )

        if _is_cancelled():
            self.storage.update_async_job(job_id, status="CANCELLED")
            self._cleanup_temp_file(temp_path)
            return self.storage.get_async_job(job_id) or job

        handler = self._handlers[job_type]
        ctx = JobExecutionContext(
            job_id=job_id,
            tenant_id=tenant_id,
            created_by=str(job.get("created_by") or "system"),
            attempt=attempt,
            max_attempts=max_attempts,
            update_progress=_update_progress,
            is_cancelled=_is_cancelled,
        )

        try:
            result = handler(job.get("payload") or {}, ctx)
            if _is_cancelled():
                self.storage.update_async_job(job_id, status="CANCELLED")
                self._cleanup_temp_file(temp_path)
            else:
                self.storage.update_async_job(
                    job_id,
                    status="SUCCEEDED",
                    progress=100,
                    result=result,
                    error_message=None,
                )
                finalize_fn = getattr(self.storage, "finalize_idempotency_key", None)
                if finalize_fn is not None:
                    finalize_fn(resource_id=job_id, status="COMPLETED", tenant_id=job.get("tenant_id"))
                GLOBAL_METRICS.record_async_job_event("async_jobs_succeeded")
                self._cleanup_temp_file(temp_path)
        except Exception as exc:
            err_msg = redact_sensitive_text(f"{type(exc).__name__}: {exc}", max_len=400)
            if attempt < max_attempts:
                backoff = self.base_backoff_seconds * (2 ** max(0, attempt - 1))
                next_ts = time.time() + backoff
                self.storage.update_async_job(
                    job_id,
                    status="RETRYING",
                    error_message=err_msg,
                    next_run_at=next_ts,
                )
                logger.warning(
                    f"异步任务 {job_id} 第 {attempt}/{max_attempts} 次失败，退避 {backoff:.2f}s 后重试：{err_msg}"
                )
            else:
                dlq_reason = f"exhausted_max_attempts({max_attempts}): {err_msg}"
                self.storage.update_async_job(
                    job_id,
                    status="DEAD_LETTER",
                    error_message=err_msg,
                    dead_letter_reason=dlq_reason,
                )
                GLOBAL_METRICS.record_async_job_event("async_jobs_dead_letter")
                self._cleanup_temp_file(temp_path)
                logger.error(
                    f"异步任务 {job_id} 进入死信状态（DEAD_LETTER）：{dlq_reason}"
                )

        return self.storage.get_async_job(job_id) or job

    def start_background_workers(self) -> None:
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        self._stop_event.clear()
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers)
        self._worker_thread = threading.Thread(
            target=self._poll_loop, name="contract-kb-job-runner", daemon=True
        )
        self._worker_thread.start()

    def stop_background_workers(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=timeout)
            self._worker_thread = None
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None

    def _poll_loop(self) -> None:
        futures: set[concurrent.futures.Future[Any]] = set()
        while not self._stop_event.is_set():
            futures = {f for f in futures if not f.done()}
            while len(futures) < self.max_workers and not self._stop_event.is_set():
                claimed = self.storage.claim_next_async_job(
                    job_types=list(self._handlers.keys())
                )
                if claimed is None or self._pool is None:
                    break
                futures.add(self._pool.submit(self._execute_claimed_job, claimed))
            self._stop_event.wait(self.poll_interval_seconds)

    @staticmethod
    def _cleanup_temp_file(temp_file_path: str | None) -> None:
        if not temp_file_path:
            return
        retention = int(os.environ.get("CONTRACT_KB_UPLOAD_RETENTION_SECONDS", "0"))
        if retention > 0:
            return
        try:
            p = Path(temp_file_path)
            if p.exists() and p.is_file():
                p.unlink()
        except OSError:
            pass
