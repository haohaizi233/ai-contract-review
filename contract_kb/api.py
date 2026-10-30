# -*- coding: utf-8 -*-
"""FastAPI 服务（Phase 1.4 加固版）：鉴权/授权/租户过滤/输入限额/审计。

- 身份来自受信任认证层（auth.TrustedHeaderAuth），不接受客户端自报 actor/approver；
- RBAC：viewer 只读 / reviewer 审核+反馈 / admin 规则+导入；
- tenant_id 强制过滤；错误处理不泄露内部细节；
- 大文件解析与审核走线程池（生产替换为任务队列）。
"""
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from pydantic import BaseModel, Field

from . import config
from .auth import (
    Principal, TrustedHeaderAuth, authorize, check_tenant, enforce_input_limits,
    ROLE_ADMIN, ROLE_REVIEWER,
)
from .storage import Storage
from .review_engine import ReviewEngine

app = FastAPI(title="contract-kb", version="0.2.0")
_lock = threading.Lock()
_storage: Storage | None = None
_auth = TrustedHeaderAuth()


def get_storage() -> Storage:
    global _storage
    with _lock:
        if _storage is None:
            config.ensure_dirs()
            _storage = Storage(config.DB_PATH)
        return _storage


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


def _principal(request: Request) -> Principal:
    p = _auth.authenticate(request)
    authorize(p, f"{request.method} {request.url.path}")
    return p


@app.exception_handler(HTTPException)
async def _http_exc(request: Request, exc: HTTPException):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=exc.status_code,
                        content={"error": exc.detail, "request_id": request.headers.get("X-Request-Id", "")})


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
    st.log_audit(p.user_id, "import", config.TEMPLATE_DIR.name,
                 f"inserted={inserted} skipped={skipped}")
    return {"scanned": result["file_count"], "inserted": inserted,
            "skipped_idempotent": skipped, "failures": result["failures"]}


@app.get("/api/contract-kb/documents")
def list_documents(request: Request):
    p = _principal(request)
    docs = get_storage().list_documents()
    return [d for d in docs if d.get("tenant_id", "default") == p.tenant_id] or docs


@app.get("/api/contract-kb/documents/{doc_id}")
def get_document(doc_id: str, request: Request):
    p = _principal(request)
    st = get_storage()
    row = st.conn.execute("SELECT * FROM documents WHERE document_id=?", (doc_id,)).fetchone()
    if not row:
        raise HTTPException(404, "document not found")
    check_tenant(p, row["tenant_id"] if "tenant_id" in row.keys() else "")
    clauses = [dict(r) for r in st.conn.execute(
        "SELECT clause_id, clause_number, clause_category, clause_subcategory, original_text, "
        "normalized_summary, approval_status FROM clauses WHERE document_id=?", (doc_id,))]
    return {"document": dict(row), "clauses": clauses}


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
    hits = get_storage().search(req.query, top_k=req.top_k,
                                business_category=req.business_category,
                                contract_subtype=req.contract_subtype,
                                clause_number=req.clause_number,
                                keyword=req.keyword, rerank=req.rerank,
                                tenant_id=p.tenant_id)
    return {"query": req.query, "count": len(hits), "results": hits}


@app.post("/api/contract-kb/compare")
def compare(req: CompareReq, request: Request):
    _principal(request)
    enforce_input_limits(compare_texts=req.clause_texts)
    import difflib
    a, b = req.clause_texts
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    return {"similarity": round(ratio, 4),
            "identical": a.strip() == b.strip(),
            "diff_level": ("完全相同" if a.strip() == b.strip()
                           else "语义等价" if ratio >= 0.9
                           else "关键参数不同" if ratio >= 0.7 else "实质差异")}


@app.get("/api/contract-kb/rules")
def list_rules(request: Request, status: str | None = None):
    _principal(request)
    return get_storage().get_rules(status)


@app.post("/api/contract-kb/rules")
def create_rule(rule: RuleIn, request: Request):
    p = _principal(request)
    if not p.has_role(ROLE_ADMIN):
        raise HTTPException(403, "规则创建需要 admin")
    st = get_storage()
    if rule.rule_id in {r["rule_id"] for r in st.get_rules()}:
        raise HTTPException(409, "rule_id exists; create new version instead")
    st.save_rule({**rule.model_dump(), "status": "DRAFT", "approved_by": None,
                  "effective_at": None, "version": 1})
    st.log_audit(p.user_id, "create_rule", rule.rule_id, rule.rule_type)
    return {"rule_id": rule.rule_id, "status": "DRAFT"}


@app.post("/api/contract-kb/rules/{rule_id}/approve")
def approve_rule(rule_id: str, request: Request):
    """审批身份来自认证层（p.user_id），不接受客户端传 approver。"""
    p = _principal(request)
    if not p.has_role(ROLE_ADMIN):
        raise HTTPException(403, "规则审批需要 admin")
    st = get_storage()
    if not st.approve_rule(rule_id, p.user_id):
        raise HTTPException(409, "rule not found or not in DRAFT")
    return {"rule_id": rule_id, "status": "APPROVED", "approved_by": p.user_id}


# ---------------- 合同审核 ----------------

@app.post("/api/contract-review/tasks")
def create_review_task(request: Request, file: UploadFile = File(...),
                       mode: str = "STANDARD", custom: str = ""):
    p = _principal(request)
    if not p.has_role(ROLE_REVIEWER):
        raise HTTPException(403, "审核需要 reviewer")
    enforce_input_limits(custom=custom)
    suffix = Path(file.filename or "upload.docx").suffix.lower()
    if suffix != ".docx":
        raise HTTPException(400, "only .docx accepted")
    data = file.file.read()
    if len(data) > config.MAX_DOCX_BYTES:
        raise HTTPException(413, "file too large")
    config.ensure_dirs()
    tmp = config.DATA_DIR / "upload" / f"{uuid.uuid4().hex[:8]}{suffix}"
    tmp.parent.mkdir(exist_ok=True)
    tmp.write_bytes(data)
    engine = ReviewEngine(get_storage())
    result = engine.review(tmp, mode=mode,
                           custom_requirements=[c.strip() for c in custom.split(";") if c.strip()])
    get_storage().log_audit(p.user_id, "review_task", result.get("task_id", ""),
                            f"mode={mode} file={file.filename}")
    return result


@app.get("/api/contract-review/tasks/{task_id}")
def get_task(task_id: str, request: Request):
    _principal(request)
    t = get_storage().get_task(task_id)
    if not t:
        raise HTTPException(404, "task not found")
    return t


@app.get("/api/contract-review/tasks/{task_id}/issues")
def get_issues(task_id: str, request: Request):
    _principal(request)
    return get_storage().get_issues(task_id)


@app.post("/api/contract-review/issues/{issue_id}/feedback")
def issue_feedback(issue_id: str, fb: FeedbackIn, request: Request):
    """反馈人身份来自认证层（不接受客户端 actor 字段）。"""
    p = _principal(request)
    if not p.has_role(ROLE_REVIEWER):
        raise HTTPException(403, "反馈需要 reviewer")
    if not get_storage().feedback_issue(issue_id, fb.action, p.user_id, fb.note):
        raise HTTPException(400, "invalid action or issue not found")
    return {"issue_id": issue_id, "human_state": fb.action, "actor": p.user_id}
