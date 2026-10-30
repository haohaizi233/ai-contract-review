# -*- coding: utf-8 -*-
"""FastAPI 服务：知识库导入/检索/规则审批 + 合同审核任务（异步任务语义用线程池模拟）。"""
from __future__ import annotations

import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel

from . import config
from .storage import Storage
from .review_engine import ReviewEngine

app = FastAPI(title="contract-kb", version="0.1.0")
_lock = threading.Lock()
_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    with _lock:
        if _storage is None:
            config.ensure_dirs()
            _storage = Storage(config.DB_PATH)
        return _storage


class SearchReq(BaseModel):
    query: str
    top_k: int = 8
    business_category: str | None = None
    contract_subtype: str | None = None
    clause_number: str | None = None
    keyword: str | None = None
    rerank: bool = True


class CompareReq(BaseModel):
    clause_texts: list[str]


class RuleIn(BaseModel):
    rule_id: str
    name: str
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
    actor: str = "reviewer"
    note: str = ""


@app.post("/api/contract-kb/import")
def import_templates():
    st = get_storage()
    from .ingest import scan_and_import
    result = scan_and_import(config.TEMPLATE_DIR)
    raw = result.pop("_raw")
    inserted, skipped = 0, 0
    for d in raw:
        _, is_new = st.upsert_document(d.to_dict())
        inserted += 1 if is_new else 0
        skipped += 0 if is_new else 1
    st.log_audit("system", "import", config.TEMPLATE_DIR.name,
                 f"inserted={inserted} skipped={skipped}")
    return {"scanned": result["file_count"], "inserted": inserted,
            "skipped_idempotent": skipped, "failures": result["failures"]}


@app.get("/api/contract-kb/documents")
def list_documents():
    return get_storage().list_documents()


@app.get("/api/contract-kb/documents/{doc_id}")
def get_document(doc_id: str):
    st = get_storage()
    row = st.conn.execute("SELECT * FROM documents WHERE document_id=?", (doc_id,)).fetchone()
    if not row:
        raise HTTPException(404, "document not found")
    clauses = [dict(r) for r in st.conn.execute(
        "SELECT clause_id, clause_number, clause_category, clause_subcategory, original_text, "
        "normalized_summary, approval_status FROM clauses WHERE document_id=?", (doc_id,))]
    return {"document": dict(row), "clauses": clauses}


@app.get("/api/contract-kb/taxonomy")
def taxonomy():
    p = config.DATA_DIR / "taxonomy.json"
    if not p.exists():
        raise HTTPException(404, "taxonomy not generated; run import first")
    import json
    return json.loads(p.read_text(encoding="utf-8"))


@app.post("/api/contract-kb/search")
def search(req: SearchReq):
    hits = get_storage().search(req.query, top_k=req.top_k,
                                business_category=req.business_category,
                                contract_subtype=req.contract_subtype,
                                clause_number=req.clause_number,
                                keyword=req.keyword, rerank=req.rerank)
    return {"query": req.query, "count": len(hits), "results": hits}


@app.post("/api/contract-kb/compare")
def compare(req: CompareReq):
    import difflib
    a, b = (req.clause_texts + ["", ""])[:2]
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    return {"similarity": round(ratio, 4),
            "identical": a.strip() == b.strip(),
            "diff_level": ("完全相同" if a.strip() == b.strip()
                           else "语义等价" if ratio >= 0.9
                           else "关键参数不同" if ratio >= 0.7 else "实质差异")}


@app.get("/api/contract-kb/rules")
def list_rules(status: str | None = None):
    return get_storage().get_rules(status)


@app.post("/api/contract-kb/rules")
def create_rule(rule: RuleIn):
    st = get_storage()
    if rule.rule_id in {r["rule_id"] for r in st.get_rules()}:
        raise HTTPException(409, "rule_id exists; create new version instead")
    st.save_rule({**rule.dict(), "status": "DRAFT", "approved_by": None,
                  "effective_at": None, "version": 1})
    st.log_audit("api", "create_rule", rule.rule_id, rule.rule_type)
    return {"rule_id": rule.rule_id, "status": "DRAFT"}


@app.post("/api/contract-kb/rules/{rule_id}/approve")
def approve_rule(rule_id: str, approver: str = "legal"):
    st = get_storage()
    if not st.approve_rule(rule_id, approver):
        raise HTTPException(409, "rule not found or not in DRAFT")
    return {"rule_id": rule_id, "status": "APPROVED", "approved_by": approver}


@app.post("/api/contract-review/tasks")
def create_review_task(file: UploadFile = File(...), mode: str = "STANDARD",
                       custom: str = ""):
    config.ensure_dirs()
    suffix = Path(file.filename or "upload.docx").suffix.lower()
    if suffix != ".docx":
        raise HTTPException(400, "only .docx accepted")
    data = file.file.read()
    if len(data) > config.MAX_DOCX_BYTES:
        raise HTTPException(413, "file too large")
    tmp = config.DATA_DIR / "upload" / f"{uuid.uuid4().hex[:8]}{suffix}"
    tmp.parent.mkdir(exist_ok=True)
    tmp.write_bytes(data)
    engine = ReviewEngine(get_storage())
    result = engine.review(tmp, mode=mode,
                           custom_requirements=[c for c in custom.split(";") if c.strip()])
    return result


@app.get("/api/contract-review/tasks/{task_id}")
def get_task(task_id: str):
    st = get_storage()
    t = st.get_task(task_id)
    if not t:
        raise HTTPException(404, "task not found")
    return t


@app.get("/api/contract-review/tasks/{task_id}/issues")
def get_issues(task_id: str):
    return get_storage().get_issues(task_id)


@app.post("/api/contract-review/issues/{issue_id}/feedback")
def issue_feedback(issue_id: str, fb: FeedbackIn):
    if not get_storage().feedback_issue(issue_id, fb.action, fb.actor, fb.note):
        raise HTTPException(400, "invalid action or issue not found")
    return {"issue_id": issue_id, "human_state": fb.action}
