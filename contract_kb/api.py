# -*- coding: utf-8 -*-
"""FastAPI 服务（企业级加固与 v1 版本治理版）：
鉴权/授权/租户过滤/流式限额/仓储端口/异步任务/全链路可观测性与审计。

核心架构约束：
- 身份来自受信任认证层（`auth.TrustedHeaderAuth`），不接受客户端自报 actor/approver；
- RBAC：viewer 只读 / reviewer 审核+反馈+异步任务 / admin 规则+标准+导入+运维指标；
- `tenant_id` 全链路强制过滤，任务与异步作业强制校验创建者所有权（或 admin）；
- 同时暴露 `/api/v1/*`（标准企业契约，支持分页、幂等键、阶段查询、取消与异步作业）与
  `/api/*`（平滑兼容期路由，自动注入 RFC 8594 `Deprecation` / `Link` 响应头）；
- 通过 `storage.create_storage()` 统一存储工厂注入 SQLite 或 PostgreSQL + pgvector。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import threading
import time
from typing import Any
import uuid

from fastapi import (
    APIRouter,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from . import config
from .application.security.idempotency import IdempotencyConflictError
from .auth import (
    ROLE_ADMIN,
    ROLE_REVIEWER,
    Principal,
    TrustedHeaderAuth,
    authorize,
    enforce_input_limits,
)
from .infrastructure.observability import GLOBAL_METRICS
from .infrastructure.task_queue import PersistentTaskRunner
from .interfaces.http import (
    CompareReq,
    ErrorEnvelope,
    FeedbackIn,
    FormatStandardApproveIn,
    FormatStandardIn,
    HealthResponse,
    PaginatedResponse,
    ReadinessResponse,
    ReviewTaskIn,
    RuleIn,
    SearchReq,
    install_observability_and_error_handlers,
)
from .modules.rules.governance import HumanOverrideConflictError, HumanOverrideService
from .review_engine import ReviewEngine
from .storage import Storage, create_storage

_lock = threading.Lock()
_storage: Any = None
_runner: PersistentTaskRunner | None = None
_runner_storage_ref: Any = None
_auth = TrustedHeaderAuth()
_override_service = HumanOverrideService()

UPLOAD_CHUNK_SIZE = 64 * 1024


def get_storage() -> Storage:
    global _storage
    with _lock:
        if _storage is None:
            _storage = create_storage()
        return _storage


def get_runner() -> PersistentTaskRunner:
    """获取与当前活跃存储实例绑定的持久化异步任务执行器。"""
    global _runner, _runner_storage_ref
    st = get_storage()
    with _lock:
        if _runner is None or _runner_storage_ref is not st:
            if _runner is not None:
                _runner.stop_background_workers(timeout=1.0)
            _runner = PersistentTaskRunner(st)
            _runner_storage_ref = st
        return _runner


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    enable_worker = os.environ.get("CONTRACT_KB_ENABLE_JOB_WORKER", "0") == "1"
    if enable_worker:
        get_runner().start_background_workers()
    try:
        yield
    finally:
        if _runner is not None:
            _runner.stop_background_workers(timeout=2.0)


app = FastAPI(
    title="contract-kb",
    version="1.0.0",
    description="AI 合同智能审查与模板知识库企业级 API（支持 /api/v1/* 与 /api/* 渐进兼容）",
    lifespan=_lifespan,
    responses={
        400: {"model": ErrorEnvelope},
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
        413: {"model": ErrorEnvelope},
        422: {"model": ErrorEnvelope},
        500: {"model": ErrorEnvelope},
    },
)
install_observability_and_error_handlers(app)


@app.middleware("http")
async def capture_request_body_middleware(request: Request, call_next):
    MAX_BODY_SIZE = 10 * 1024 * 1024  # 10MB 有界读取
    body = await request.body()
    if len(body) > MAX_BODY_SIZE:
        return JSONResponse(
            status_code=413,
            content={"error": "file_too_large", "detail": "Request Entity Too Large"},
        )
    response = await call_next(request)
    return response


@app.exception_handler(IdempotencyConflictError)
def idempotency_conflict_handler(request: Request, exc: IdempotencyConflictError):
    return JSONResponse(
        status_code=409,
        content={"error": str(exc), "detail": str(exc)},
    )


def _read_upload_bounded(upload: UploadFile, max_bytes: int) -> bytes:
    """分块流式读取上传文件，超限时立即中断并返回 413（防止内存耗尽）。"""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = upload.file.read(UPLOAD_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(413, "file too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _validate_and_stage_docx(
    upload: UploadFile, prefix: str = ""
) -> tuple[Path, bytes]:
    safe_name = Path(upload.filename or "upload.docx").name
    suffix = Path(safe_name).suffix.lower()
    if suffix != ".docx":
        raise HTTPException(400, "only .docx accepted")
    data = _read_upload_bounded(upload, config.MAX_DOCX_BYTES)
    config.ensure_dirs()
    tmp = config.DATA_DIR / "upload" / f"{prefix}{uuid.uuid4().hex[:12]}{suffix}"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(data)
    return tmp, data


def _principal(request: Request) -> Principal:
    if _auth.storage is None:
        try:
            _auth.storage = get_storage()
        except Exception:
            pass
    p = _auth.authenticate(request)
    authorize(p, f"{request.method} {request.url.path}")
    return p


def _owned_task(st: Any, task_id: str, p: Principal) -> dict[str, Any]:
    """强制租户 + 资源所有权检查：
    - 跨租户或不存在 → 404（不泄露资源存在性）；
    - 同租户但非创建者且非 admin → 403（资源所有权校验）。"""
    t = st.get_task(task_id)
    if not t or t.get("tenant_id", "default") != p.tenant_id:
        raise HTTPException(404, "task not found")
    owner = t.get("created_by")
    if owner and owner != p.user_id and not p.has_role(ROLE_ADMIN):
        raise HTTPException(403, "资源所有权校验失败：仅任务创建者或 admin 可访问")
    return t


def _owned_job(st: Any, job_id: str, p: Principal) -> dict[str, Any]:
    job = st.get_async_job(job_id, tenant_id=p.tenant_id)
    if not job:
        raise HTTPException(404, "job not found")
    owner = job.get("created_by")
    if owner and owner != p.user_id and not p.has_role(ROLE_ADMIN):
        raise HTTPException(403, "资源所有权校验失败：仅任务创建者或 admin 可访问")
    return job


# ---------------- 存活与就绪探针（Liveness / Readiness） ----------------


def _check_readiness() -> ReadinessResponse:
    t0 = time.perf_counter()
    try:
        st = get_storage()
        st.list_tasks(tenant_id="__readyz__", limit=1, offset=0)
    except Exception as exc:
        raise HTTPException(503, "storage backend unavailable") from exc
    latency_ms = round((time.perf_counter() - t0) * 1000.0, 3)
    GLOBAL_METRICS.record_db_query(latency_ms)
    return ReadinessResponse(status="ready", db_ready=True, db_latency_ms=latency_ms)


@app.get("/healthz", response_model=HealthResponse, tags=["observability"])
@app.get("/api/healthz", response_model=HealthResponse, tags=["observability"])
@app.get("/api/v1/healthz", response_model=HealthResponse, tags=["observability"])
def healthz() -> HealthResponse:
    return HealthResponse(status="ok", service="contract-kb", version=app.version)


@app.get("/readyz", response_model=ReadinessResponse, tags=["observability"])
@app.get("/api/readyz", response_model=ReadinessResponse, tags=["observability"])
@app.get("/api/v1/readyz", response_model=ReadinessResponse, tags=["observability"])
def readyz() -> ReadinessResponse:
    return _check_readiness()


# ---------------- 共享业务路由（同时挂载至 /api/v1 与 /api） ----------------

shared_router = APIRouter()


@shared_router.get("/metrics", tags=["observability"])
def get_metrics(request: Request, fmt: str = Query(default="json", alias="format")):
    p = _principal(request)
    if not p.has_role(ROLE_ADMIN):
        raise HTTPException(403, "指标查询需要 admin")
    if fmt == "prometheus":
        return PlainTextResponse(GLOBAL_METRICS.render_prometheus())
    return GLOBAL_METRICS.snapshot()


@shared_router.post("/contract-kb/import", tags=["knowledge-base"])
def import_templates(request: Request):
    p = _principal(request)
    st = get_storage()
    from .ingest import scan_and_import

    result = scan_and_import(config.TEMPLATE_DIR)
    raw = result.pop("_raw")
    inserted, skipped = 0, 0
    for d in raw:
        _, is_new = st.upsert_document(d.to_dict(), tenant=p.tenant_id)
        inserted += 1 if is_new else 0
        skipped += 0 if is_new else 1
    st.log_audit(
        p.user_id,
        "import",
        config.TEMPLATE_DIR.name,
        f"inserted={inserted} skipped={skipped}",
    )
    return {
        "scanned": result["file_count"],
        "inserted": inserted,
        "skipped_idempotent": skipped,
        "failures": result["failures"],
    }


@app.get("/api/contract-kb/documents", tags=["knowledge-base-legacy"])
def list_documents_legacy(request: Request):
    """兼容期路由：保持返回裸数组，避免破坏存量调用方。"""
    p = _principal(request)
    return get_storage().list_documents(tenant_id=p.tenant_id)


@app.get(
    "/api/v1/contract-kb/documents",
    response_model=PaginatedResponse,
    tags=["knowledge-base"],
)
def list_documents_v1(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """v1 标准分页文档列表。"""
    p = _principal(request)
    all_docs = get_storage().list_documents(tenant_id=p.tenant_id)
    total = len(all_docs)
    return PaginatedResponse(
        items=all_docs[offset : offset + limit],
        total=total,
        limit=limit,
        offset=offset,
    )


@shared_router.get("/contract-kb/documents/{doc_id}", tags=["knowledge-base"])
def get_document(doc_id: str, request: Request):
    p = _principal(request)
    st = get_storage()
    doc = st.get_document(doc_id, tenant_id=p.tenant_id)
    if not doc:
        raise HTTPException(404, "document not found")
    raw_clauses = st.get_document_clauses(doc_id, tenant_id=p.tenant_id)
    clauses = [
        {
            "clause_id": r["clause_id"],
            "clause_number": r.get("clause_number"),
            "clause_category": r.get("clause_category"),
            "clause_subcategory": r.get("clause_subcategory"),
            "original_text": r["original_text"],
            "normalized_summary": r.get("normalized_summary"),
            "approval_status": r.get("approval_status"),
        }
        for r in raw_clauses
    ]
    return {"document": doc, "clauses": clauses}


@shared_router.get("/contract-kb/taxonomy", tags=["knowledge-base"])
def taxonomy(request: Request):
    _principal(request)
    p = config.DATA_DIR / "taxonomy.json"
    if not p.exists():
        raise HTTPException(404, "taxonomy not generated; run import first")
    return json.loads(p.read_text(encoding="utf-8"))


class KnowledgeApprovalReq(BaseModel):
    approval_reason: str = ""
    clause_ids: list[str] | None = None
    expected_revision: int | None = None


@shared_router.post("/contract-kb/search", tags=["knowledge-base"])
def search(req: SearchReq, request: Request):
    p = _principal(request)
    enforce_input_limits(query=req.query + " " + (req.keyword or ""))
    from .modules.retrieval.service import GovernedRetrievalService

    retrieval_svc = GovernedRetrievalService(get_storage())
    hits = retrieval_svc.search_user_visible_knowledge(
        req.query,
        top_k=req.top_k,
        business_category=req.business_category,
        contract_subtype=req.contract_subtype,
        clause_number=req.clause_number,
        keyword=req.keyword,
        rerank=req.rerank,
        tenant_id=p.tenant_id,
        include_unapproved=False,
    )
    GLOBAL_METRICS.record_rag_query(hit_count=len(hits))
    return {"query": req.query, "count": len(hits), "results": hits}


@shared_router.post(
    "/contract-kb/documents/{document_id}/approve", tags=["knowledge-base"]
)
def approve_document_knowledge(
    document_id: str, req: KnowledgeApprovalReq, request: Request
):
    p = _principal(request)
    if not (p.has_role(ROLE_ADMIN) or p.has_role(ROLE_REVIEWER)):
        raise HTTPException(status_code=403, detail="仅法务或管理员有权审批知识版本")
    storage = get_storage()
    try:
        from .storage_errors import OptimisticLockConflictError
        res = storage.approve_knowledge_version(
            tenant_id=p.tenant_id,
            document_id=document_id,
            approver_id=p.user_id,
            approval_reason=req.approval_reason,
            clause_ids=req.clause_ids,
            expected_revision=req.expected_revision,
        )
        return res
    except OptimisticLockConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@shared_router.post(
    "/contract-kb/documents/{document_id}/revoke", tags=["knowledge-base"]
)
def revoke_document_knowledge(
    document_id: str, req: KnowledgeApprovalReq, request: Request
):
    p = _principal(request)
    if not (p.has_role(ROLE_ADMIN) or p.has_role(ROLE_REVIEWER)):
        raise HTTPException(status_code=403, detail="仅法务或管理员有权撤销知识版本")
    storage = get_storage()
    try:
        from .storage_errors import OptimisticLockConflictError
        res = storage.revoke_knowledge_version(
            tenant_id=p.tenant_id,
            document_id=document_id,
            revoker_id=p.user_id,
            revoke_reason=req.approval_reason,
            clause_ids=req.clause_ids,
            expected_revision=req.expected_revision,
        )
        return res
    except OptimisticLockConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@shared_router.post("/contract-kb/compare", tags=["knowledge-base"])
def compare(req: CompareReq, request: Request):
    """合同文本比较：字符串相似度仅作检索/差异提示，不作“语义等价”断言。"""
    _principal(request)
    enforce_input_limits(compare_texts=req.clause_texts)
    import difflib

    from .clause_diff import diff_clauses

    a, b = req.clause_texts
    identical = a.strip() == b.strip()
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    deltas = [d.to_dict() for d in diff_clauses(a, b)]
    rights_affecting = {
        "NEGATION_CHANGE",
        "NUMBER_CHANGE",
        "DEADLINE_CHANGE",
        "PARTY_CHANGE",
        "OBLIGATION_CHANGE",
    }
    critical = [d for d in deltas if d["delta_type"] in rights_affecting]
    if identical:
        diff_level = "完全相同"
    elif critical:
        diff_level = (
            "关键参数不同"
            if all(
                d["delta_type"] in ("NUMBER_CHANGE", "DEADLINE_CHANGE")
                for d in critical
            )
            else "实质差异"
        )
    else:
        diff_level = "文本差异（未判定语义等价）"
    return {
        "similarity": round(ratio, 4),
        "similarity_role": "仅作检索/差异提示，不构成语义等价判断",
        "identical": identical,
        "diff_level": diff_level,
        "structured_deltas": deltas,
        "specialized_checks": sorted({d["delta_type"] for d in critical}),
        "affects_rights": bool(critical),
        "legal_effect_note": "涉及否定词/金额/期限/责任主体/义务强度的变化已经专门检查；"
        "是否构成法律上的实质变更需人工/法务确认",
    }


@shared_router.get("/contract-kb/rules", tags=["rules"])
def list_rules(request: Request, status: str | None = None):
    p = _principal(request)
    return get_storage().get_rules(status=status, tenant_id=p.tenant_id)


@shared_router.post("/contract-kb/rules", tags=["rules"])
def create_rule(rule: RuleIn, request: Request):
    p = _principal(request)
    if not p.has_role(ROLE_ADMIN):
        raise HTTPException(403, "规则创建需要 admin")
    st = get_storage()
    if rule.rule_id in {r["rule_id"] for r in st.get_rules(tenant_id=p.tenant_id)}:
        raise HTTPException(409, "rule_id exists; create new version instead")
    ver = st.save_rule(
        {
            **rule.model_dump(),
            "status": "DRAFT",
            "approved_by": None,
            "effective_at": None,
            "version": 1,
            "tenant_id": p.tenant_id,
        },
        tenant_id=p.tenant_id,
    )
    st.log_audit(p.user_id, "create_rule", rule.rule_id, f"{rule.rule_type} v{ver}")
    return {"rule_id": rule.rule_id, "version": ver, "status": "DRAFT"}


@shared_router.post("/contract-kb/rules/{rule_id}/approve", tags=["rules"])
def approve_rule(rule_id: str, request: Request):
    """审批身份来自认证层（p.user_id），不接受客户端传 approver。"""
    p = _principal(request)
    if not p.has_role(ROLE_ADMIN):
        raise HTTPException(403, "规则审批需要 admin")
    st = get_storage()
    if not st.approve_rule(rule_id, p.user_id, version=None, tenant_id=p.tenant_id):
        raise HTTPException(409, "rule not found or not in DRAFT")
    return {"rule_id": rule_id, "status": "APPROVED", "approved_by": p.user_id}


# ---------------- 公司格式标准（可配置 / 可审批 / 可版本化） ----------------


@shared_router.get("/contract-kb/format-standards", tags=["format-standards"])
def list_format_standards(request: Request):
    p = _principal(request)
    st = get_storage()
    active = st.get_active_format_standard(tenant_id=p.tenant_id)
    latest = st.get_format_standard("company_format", tenant_id=p.tenant_id)
    return {
        "active": active,
        "latest": latest,
        "note": "仅 ACTIVE（已审批）标准可用于产生正式企业违规结论；"
        "未审批标准只作差异提示",
    }


@shared_router.post("/contract-kb/format-standards", tags=["format-standards"])
def create_format_standard(body: FormatStandardIn, request: Request):
    p = _principal(request)
    if not p.has_role(ROLE_ADMIN):
        raise HTTPException(403, "格式标准创建需要 admin")
    st = get_storage()
    rec = st.save_format_standard(
        body.standard_id,
        body.params,
        p.user_id,
        note=body.note,
        tenant_id=p.tenant_id,
    )
    st.log_audit(
        p.user_id,
        "create_format_standard",
        body.standard_id,
        f"version={rec['version']}",
    )
    return {
        "standard_id": body.standard_id,
        "version": rec["version"],
        "status": "DRAFT",
    }


@shared_router.post(
    "/contract-kb/format-standards/{standard_id}/approve", tags=["format-standards"]
)
def approve_format_standard(
    standard_id: str, body: FormatStandardApproveIn, request: Request
):
    p = _principal(request)
    if not p.has_role(ROLE_ADMIN):
        raise HTTPException(403, "格式标准审批需要 admin")
    st = get_storage()
    if not st.approve_format_standard(
        standard_id, body.version, p.user_id, tenant_id=p.tenant_id
    ):
        raise HTTPException(409, "standard version not found or not in DRAFT")
    return {
        "standard_id": standard_id,
        "version": body.version,
        "status": "ACTIVE",
        "approved_by": p.user_id,
    }


# ---------------- 合同审核与阶段治理 ----------------


@shared_router.post("/contract-review/tasks", tags=["contract-review"])
def create_review_task(
    request: Request,
    file: UploadFile = File(...),
    mode: str = Form(default=""),
    custom: str = Form(default=""),
):
    p = _principal(request)
    if not p.has_role(ROLE_REVIEWER):
        raise HTTPException(403, "审核需要 reviewer")
    resolved_mode = mode or request.query_params.get("mode") or "STANDARD"
    resolved_custom = custom or request.query_params.get("custom") or ""
    enforce_input_limits(custom=resolved_custom)
    idem_key = request.headers.get("X-Idempotency-Key", "").strip() or None
    tmp, data = _validate_and_stage_docx(file, prefix="")
    retention = int(
        os.environ.get(
            "CONTRACT_KB_UPLOAD_RETENTION_SECONDS",
            str(config.UPLOAD_RETENTION_SECONDS),
        )
    )
    try:
        st = get_storage()
        engine = ReviewEngine(st)
        res = engine.review_result(
            tmp,
            mode=resolved_mode,
            custom_requirements=[
                c.strip() for c in resolved_custom.split(";") if c.strip()
            ],
            tenant_id=p.tenant_id,
            created_by=p.user_id,
            idempotency_key=idem_key,
        )
        GLOBAL_METRICS.record_review(
            status=res.status, stages=res.stages, issues=res.issues
        )
        st.log_audit(
            p.user_id,
            "review_task",
            res.task_id,
            f"mode={resolved_mode} bytes={len(data)}",
        )
        return res.to_dict()
    finally:
        if retention <= 0 and tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


@shared_router.get(
    "/contract-review/tasks",
    response_model=PaginatedResponse,
    tags=["contract-review"],
)
def list_review_tasks(
    request: Request,
    status: str | None = None,
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    p = _principal(request)
    created_by = None if p.has_role(ROLE_ADMIN) else p.user_id
    items, total = get_storage().list_tasks(
        tenant_id=p.tenant_id,
        status=status,
        limit=limit,
        offset=offset,
        created_by=created_by,
    )
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@shared_router.get("/contract-review/tasks/{task_id}", tags=["contract-review"])
def get_task(task_id: str, request: Request):
    p = _principal(request)
    return _owned_task(get_storage(), task_id, p)


@shared_router.get("/contract-review/tasks/{task_id}/issues", tags=["contract-review"])
def get_issues(task_id: str, request: Request):
    p = _principal(request)
    st = get_storage()
    _owned_task(st, task_id, p)
    return st.get_issues(task_id)


@shared_router.get("/contract-review/tasks/{task_id}/stages", tags=["contract-review"])
def get_task_stages(task_id: str, request: Request):
    p = _principal(request)
    st = get_storage()
    _owned_task(st, task_id, p)
    return {"task_id": task_id, "stages": st.get_stage_states(task_id)}


@shared_router.get(
    "/contract-review/tasks/{task_id}/overrides", tags=["contract-review"]
)
def get_task_overrides(task_id: str, request: Request):
    p = _principal(request)
    st = get_storage()
    _owned_task(st, task_id, p)
    return {"task_id": task_id, "overrides": st.list_human_overrides(task_id)}


@shared_router.post("/contract-review/tasks/{task_id}/cancel", tags=["contract-review"])
def cancel_review_task(task_id: str, request: Request):
    p = _principal(request)
    if not p.has_role(ROLE_REVIEWER):
        raise HTTPException(403, "取消任务需要 reviewer")
    st = get_storage()
    _owned_task(st, task_id, p)
    ok = st.request_cancel_task(task_id, tenant_id=p.tenant_id)
    st.log_audit(p.user_id, "cancel_review_task", task_id, f"ok={ok}")
    return {"task_id": task_id, "cancel_requested": ok}


@shared_router.post(
    "/contract-review/issues/{issue_id}/feedback", tags=["contract-review"]
)
def issue_feedback(issue_id: str, fb: FeedbackIn, request: Request):
    """带不可篡改审计的人工复核覆盖：身份来自认证层，写入 human_overrides 与 audit_log。"""
    p = _principal(request)
    if not p.has_role(ROLE_REVIEWER):
        raise HTTPException(403, "反馈需要 reviewer")
    st = get_storage()
    issue = st.get_issue(issue_id)
    if not issue:
        raise HTTPException(404, "issue not found")
    _owned_task(st, issue["task_id"], p)
    try:
        return _override_service.apply_override(
            st,
            issue_id=issue_id,
            action=fb.action,
            actor=p.user_id,
            reason=fb.note,
            tenant_id=p.tenant_id,
            expected_state=fb.expected_state,
        )
    except HumanOverrideConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, "invalid action or issue not found") from exc


# ---------------- 标准合同实体（Canonical Contracts） ----------------


@shared_router.get(
    "/canonical-contracts",
    response_model=PaginatedResponse,
    tags=["canonical-contracts"],
)
def list_canonical_contracts(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    p = _principal(request)
    items, total = get_storage().list_canonical_contracts(
        tenant_id=p.tenant_id, limit=limit, offset=offset
    )
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@shared_router.get("/canonical-contracts/{contract_id}", tags=["canonical-contracts"])
def get_canonical_contract(contract_id: str, request: Request):
    p = _principal(request)
    rec = get_storage().get_canonical_contract(contract_id, tenant_id=p.tenant_id)
    if not rec:
        raise HTTPException(404, "canonical contract not found")
    return rec


# ---------------- 异步任务执行器（Async Jobs） ----------------


@shared_router.post("/jobs/review", status_code=202, tags=["async-jobs"])
def enqueue_review_job(
    request: Request,
    file: UploadFile = File(...),
    mode: str = Form(default=""),
    custom: str = Form(default=""),
):
    p = _principal(request)
    if not p.has_role(ROLE_REVIEWER):
        raise HTTPException(403, "提交异步审核任务需要 reviewer")
    resolved_mode = mode or request.query_params.get("mode") or "STANDARD"
    resolved_custom = custom or request.query_params.get("custom") or ""
    enforce_input_limits(custom=resolved_custom)
    idem_key = request.headers.get("X-Idempotency-Key", "").strip() or None
    tmp, data = _validate_and_stage_docx(file, prefix="async_")
    job, created = get_runner().submit(
        "CONTRACT_REVIEW",
        {
            "docx_path": str(tmp),
            "mode": resolved_mode,
            "custom_requirements": [
                c.strip() for c in resolved_custom.split(";") if c.strip()
            ],
            "idempotency_key": idem_key,
        },
        tenant_id=p.tenant_id,
        created_by=p.user_id,
        idempotency_key=idem_key,
        temp_file_path=tmp,
    )
    get_storage().log_audit(
        p.user_id,
        "enqueue_review_job",
        job["job_id"],
        f"created={created} mode={resolved_mode} bytes={len(data)}",
    )
    return {"job": job, "created": created}


@shared_router.get("/jobs", response_model=PaginatedResponse, tags=["async-jobs"])
def list_jobs(
    request: Request,
    status: str | None = None,
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    p = _principal(request)
    created_by = None if p.has_role(ROLE_ADMIN) else p.user_id
    items, total = get_storage().list_async_jobs(
        tenant_id=p.tenant_id,
        status=status,
        limit=limit,
        offset=offset,
        created_by=created_by,
    )
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@shared_router.get("/jobs/{job_id}", tags=["async-jobs"])
def get_job(job_id: str, request: Request):
    p = _principal(request)
    return _owned_job(get_storage(), job_id, p)


@shared_router.post("/jobs/{job_id}/cancel", tags=["async-jobs"])
def cancel_job(job_id: str, request: Request):
    p = _principal(request)
    if not p.has_role(ROLE_REVIEWER):
        raise HTTPException(403, "取消任务需要 reviewer")
    st = get_storage()
    _owned_job(st, job_id, p)
    ok = get_runner().cancel(job_id, tenant_id=p.tenant_id)
    st.log_audit(p.user_id, "cancel_async_job", job_id, f"ok={ok}")
    return {"job_id": job_id, "cancelled": ok}


@shared_router.post("/jobs/worker/drain", tags=["async-jobs"])
def drain_jobs(request: Request, max_jobs: int = Query(default=10, ge=1, le=100)):
    """管理员受控触发内嵌异步任务消费（便于运维巡检、批处理与无守护进程环境）。"""
    p = _principal(request)
    if not p.has_role(ROLE_ADMIN):
        raise HTTPException(403, "驱动工作队列需要 admin")
    processed = get_runner().drain(max_jobs=max_jobs)
    return {
        "processed_count": len(processed),
        "job_ids": [j["job_id"] for j in processed],
    }


app.include_router(shared_router, prefix="/api/v1")
app.include_router(shared_router, prefix="/api")

__all__ = [
    "CompareReq",
    "ErrorEnvelope",
    "FeedbackIn",
    "FormatStandardApproveIn",
    "FormatStandardIn",
    "HealthResponse",
    "PaginatedResponse",
    "ReadinessResponse",
    "ReviewTaskIn",
    "RuleIn",
    "SearchReq",
    "app",
    "get_runner",
    "get_storage",
]
