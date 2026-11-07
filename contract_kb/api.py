# -*- coding: utf-8 -*-
"""FastAPI 服务（企业级加固版）：鉴权/授权/租户过滤/流式限额/仓储端口/审计。

- 身份来自受信任认证层（auth.TrustedHeaderAuth），不接受客户端自报 actor/approver；
- RBAC：viewer 只读 / reviewer 审核+反馈 / admin 规则+导入；
- tenant_id 强制过滤（文档、检索、规则、格式标准、审核任务与人工反馈全链路）；
- 通过 `storage.create_storage()` 统一存储工厂注入 SQLite 或 PostgreSQL + pgvector；
- 上传文件采用分块流式读取限流并在审核完成后按保留策略自动清理临时文件。
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from . import config
from .auth import (
    ROLE_ADMIN,
    ROLE_REVIEWER,
    Principal,
    TrustedHeaderAuth,
    authorize,
    enforce_input_limits,
)
from .review_engine import ReviewEngine
from .storage import Storage, create_storage

app = FastAPI(title="contract-kb", version="0.3.0")
_lock = threading.Lock()
_storage: Any = None
_auth = TrustedHeaderAuth()

UPLOAD_CHUNK_SIZE = 64 * 1024


def get_storage() -> Storage:
    global _storage
    with _lock:
        if _storage is None:
            _storage = create_storage()
        return _storage


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


class SearchReq(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=8, ge=1, le=50)
    business_category: str | None = None
    contract_subtype: str | None = None
    clause_number: str | None = None
    keyword: str | None = Field(default=None)
    rerank: bool = True


class CompareReq(BaseModel):
    clause_texts: list[str] = Field(min_length=2, max_length=2)


class RuleIn(BaseModel):
    rule_id: str = Field(min_length=3, max_length=80)
    name: str = Field(max_length=200)
    rule_type: str
    execution_mode: str
    severity: str
    contract_types: list[str] = ["*"]
    parameters: dict = {}
    source_clauses: list = []


class ReviewTaskIn(BaseModel):
    mode: str = "STANDARD"
    custom_requirements: list[str] = []


class FeedbackIn(BaseModel):
    action: str
    note: str = Field(default="", max_length=2000)


class FormatStandardIn(BaseModel):
    standard_id: str = Field(default="company_format", min_length=3, max_length=80)
    params: dict
    note: str = Field(default="", max_length=500)


class FormatStandardApproveIn(BaseModel):
    version: int = Field(ge=1)


def _principal(request: Request) -> Principal:
    p = _auth.authenticate(request)
    authorize(p, f"{request.method} {request.url.path}")
    return p


def _owned_task(st: Any, task_id: str, p: Principal) -> dict:
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


@app.exception_handler(HTTPException)
async def _http_exc(request: Request, exc: HTTPException):
    from fastapi.responses import JSONResponse

    req_id = (
        request.headers.get("X-Request-ID")
        or request.headers.get("X-Request-Id")
        or getattr(request.state, "request_id", "")
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail,
            "request_id": req_id,
        },
    )


# ---------------- 知识库 ----------------


@app.post("/api/contract-kb/import")
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


@app.get("/api/contract-kb/documents")
def list_documents(request: Request):
    p = _principal(request)
    return get_storage().list_documents(tenant_id=p.tenant_id)


@app.get("/api/contract-kb/documents/{doc_id}")
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


@app.get("/api/contract-kb/taxonomy")
def taxonomy(request: Request):
    _principal(request)
    p = config.DATA_DIR / "taxonomy.json"
    if not p.exists():
        raise HTTPException(404, "taxonomy not generated; run import first")
    return json.loads(p.read_text(encoding="utf-8"))


@app.post("/api/contract-kb/search")
def search(req: SearchReq, request: Request):
    p = _principal(request)
    enforce_input_limits(query=req.query + " " + (req.keyword or ""))
    hits = get_storage().search(
        req.query,
        top_k=req.top_k,
        business_category=req.business_category,
        contract_subtype=req.contract_subtype,
        clause_number=req.clause_number,
        keyword=req.keyword,
        rerank=req.rerank,
        tenant_id=p.tenant_id,
    )
    return {"query": req.query, "count": len(hits), "results": hits}


@app.post("/api/contract-kb/compare")
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


@app.get("/api/contract-kb/rules")
def list_rules(request: Request, status: str | None = None):
    p = _principal(request)
    return get_storage().get_rules(status=status, tenant_id=p.tenant_id)


@app.post("/api/contract-kb/rules")
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


@app.post("/api/contract-kb/rules/{rule_id}/approve")
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


@app.get("/api/contract-kb/format-standards")
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


@app.post("/api/contract-kb/format-standards")
def create_format_standard(body: FormatStandardIn, request: Request):
    """创建格式标准新版本（DRAFT）。版本化留痕：不覆盖历史版本。"""
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


@app.post("/api/contract-kb/format-standards/{standard_id}/approve")
def approve_format_standard(
    standard_id: str, body: FormatStandardApproveIn, request: Request
):
    """审批格式标准版本：审批身份来自认证层；仅 DRAFT 可审批。"""
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


# ---------------- 合同审核 ----------------


@app.post("/api/contract-review/tasks")
def create_review_task(
    request: Request,
    file: UploadFile = File(...),
    mode: str = "STANDARD",
    custom: str = "",
):
    p = _principal(request)
    if not p.has_role(ROLE_REVIEWER):
        raise HTTPException(403, "审核需要 reviewer")
    enforce_input_limits(custom=custom)
    safe_name = Path(file.filename or "upload.docx").name
    suffix = Path(safe_name).suffix.lower()
    if suffix != ".docx":
        raise HTTPException(400, "only .docx accepted")
    data = _read_upload_bounded(file, config.MAX_DOCX_BYTES)
    config.ensure_dirs()
    tmp = config.DATA_DIR / "upload" / f"{uuid.uuid4().hex[:12]}{suffix}"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    retention = int(
        os.environ.get(
            "CONTRACT_KB_UPLOAD_RETENTION_SECONDS",
            str(config.UPLOAD_RETENTION_SECONDS),
        )
    )
    try:
        tmp.write_bytes(data)
        engine = ReviewEngine(get_storage())
        result = engine.review(
            tmp,
            mode=mode,
            custom_requirements=[c.strip() for c in custom.split(";") if c.strip()],
            tenant_id=p.tenant_id,
            created_by=p.user_id,
        )
        get_storage().log_audit(
            p.user_id,
            "review_task",
            result.get("task_id", ""),
            f"mode={mode} bytes={len(data)}",
        )
        return result
    finally:
        if retention <= 0 and tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


@app.get("/api/contract-review/tasks/{task_id}")
def get_task(task_id: str, request: Request):
    p = _principal(request)
    return _owned_task(get_storage(), task_id, p)


@app.get("/api/contract-review/tasks/{task_id}/issues")
def get_issues(task_id: str, request: Request):
    p = _principal(request)
    _owned_task(get_storage(), task_id, p)
    return get_storage().get_issues(task_id)


@app.post("/api/contract-review/issues/{issue_id}/feedback")
def issue_feedback(issue_id: str, fb: FeedbackIn, request: Request):
    """反馈人身份来自认证层（不接受客户端 actor 字段）；强制租户 + 资源所有权检查。"""
    p = _principal(request)
    if not p.has_role(ROLE_REVIEWER):
        raise HTTPException(403, "反馈需要 reviewer")
    st = get_storage()
    issue = st.get_issue(issue_id)
    if not issue:
        raise HTTPException(404, "issue not found")
    _owned_task(st, issue["task_id"], p)
    if not st.feedback_issue(issue_id, fb.action, p.user_id, fb.note):
        raise HTTPException(400, "invalid action or issue not found")
    return {"issue_id": issue_id, "human_state": fb.action, "actor": p.user_id}
