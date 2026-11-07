# -*- coding: utf-8 -*-
"""P0-04 失败复现与安全测试：流水线阶段证据核验 Fail-Closed 与 TOCTOU 撤销防御。

验证：
1. Stage 9 与 Stage 10 在构造证据与上下文时，严禁主动填充 "APPROVED" 或 "TEMPLATE" 缺省信任字段；
2. Stage 11 (VerifyEvidenceStage) 必须对所有强制性 Finding 所依赖的标准知识进行真库回查；
3. TOCTOU 防御：若标准条款在审核期间被法务撤销（REVOKED），Stage 11 复核必须拒绝放行，
   自动降级为 INSUFFICIENT_EVIDENCE 并置 requires_human_review=True。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
import pytest

from contract_kb.storage import Storage
from contract_kb.application.pipeline.runner import PipelineContext
from contract_kb.application.pipeline.stages import (
    RetrieveEvidenceStage,
    VerifyEvidenceStage,
)
from contract_kb.domain.models.review import Finding, CheckStatus


def _setup_pipeline_db(tmpdir: Path) -> tuple[Storage, str]:
    st = Storage(tmpdir / "pipeline_sec.db")
    doc = {
        "template_id": "pip_std_001",
        "source_file": "pip_std.docx",
        "sha256": "hash_pip_001_1234567890abcdef",
        "template_version": "v1.0",
        "category": "TECH",
        "subtype": "TECH_SERVICE",
        "document_type": "contract_template",
        "status": "ACTIVE",
        "clauses": [
            {
                "clause_id": "pip_clause_001",
                "clause_number": "第五条",
                "clause_category": "LIABILITY",
                "original_text": "标准违约条款：逾期支付违约金按日万分之五计算。",
            }
        ],
    }
    did, _ = st.upsert_document(doc, tenant="tenant_pip")
    st.approve_knowledge_version(
        tenant_id="tenant_pip",
        document_id=did,
        approver_id="legal_lead",
        approval_reason="已通过合规审查",
    )
    return st, did


def test_stage9_does_not_forge_approved_status():
    """验证 Stage 9 构建 rag_context 时，若召回项缺少 approved_status，绝不伪造成 APPROVED。"""
    st, did = _setup_pipeline_db(Path(tempfile.mkdtemp()))
    try:
        ctx = PipelineContext(
            docx_path=Path("mock.docx"),
            task_id="task_st9_test",
            category="TECH",
            subtype="TECH_SERVICE",
            scope_tenant="tenant_pip",
        )
        ctx.clauses = [
            {
                "clause_id": "rev_c1",
                "source_node_ids": ["n1"],
                "clause_category": "LIABILITY",
                "original_text": "逾期付款应支付违约金。",
            }
        ]
        ctx.valid_node_ids = {"n1"}

        # 模拟一个缺少 approved_status 的分块
        raw_bundle = {
            "target_clause_id": "rev_c1",
            "authoritative_clauses": [
                {
                    "clause_id": "unverified_c1",
                    "text": "某未审批条款",
                    # 故意不提供 approved_status 和 knowledge_type
                }
            ],
        }
        ctx.evidence_bundles = {"rev_c1": raw_bundle}

        stage = RetrieveEvidenceStage(st)
        stage.execute(ctx)

        # 检查 ctx.rag_context
        for item in ctx.rag_context:
            if item.get("clause_id") == "unverified_c1":
                # 核心断言：绝不能被默认补全为 APPROVED！
                assert item.get("approved_status") != "APPROVED"
    finally:
        st.close()


def test_stage11_toctou_revocation_downgrades_mandatory_finding():
    """验证 TOCTOU 场景：审核中途标准依据被法务撤销，Stage 11 回查真库阻断强制判定。"""
    st, did = _setup_pipeline_db(Path(tempfile.mkdtemp()))
    try:
        ctx = PipelineContext(
            docx_path=Path("mock.docx"),
            task_id="task_toctou_test",
            category="TECH",
            subtype="TECH_SERVICE",
            scope_tenant="tenant_pip",
            storage_backend=st,
        )
        ctx.clauses = [
            {
                "clause_id": "rev_c1",
                "clause_number": "第五条",
                "source_node_ids": ["node_1"],
                "original_text": "乙方应按每日千分之一支付违约金。",
            }
        ]
        ctx.valid_node_ids = {"node_1"}

        # 构造一条声称有权威知识依据的强制性 Finding
        kref = {
            "tenant_id": "tenant_pip",
            "document_id": did,
            "clause_id": "pip_clause_001",
            "source_hash": "hash_pip_001_1234567890abcdef",
            "approved_status": "APPROVED",  # 之前已审批
            "knowledge_type": "TEMPLATE",
        }
        f = Finding(
            issue_id="issue_1",
            rule_id="RULE_LIABILITY_RATE",
            severity="HIGH",
            category="COMPLIANCE",
            title="违约金比例偏高",
            description="违约金偏离企业标准",
            execution_mode="DETERMINISTIC",
            knowledge_references=[kref],
        )
        ctx.findings = [f]

        # 关键动作：法务中途撤销了该标准条款
        st.revoke_knowledge_version(
            tenant_id="tenant_pip",
            document_id=did,
            revoker_id="legal_lead",
            revoke_reason="紧急撤回瑕疵标准",
        )

        stage = VerifyEvidenceStage(storage=st)
        stage.execute(ctx)

        # 核心断言：Stage 11 回查数据库后，检测到依据已失效，必须降级并要求人工复核
        assert f.check_status == CheckStatus.INSUFFICIENT_EVIDENCE.value
        assert f.requires_human_review is True
        assert f.citation_status == "CITATION_UNVERIFIED"
        assert "撤销" in f.description or "revoked" in f.description.lower() or "失效" in f.description
    finally:
        st.close()
