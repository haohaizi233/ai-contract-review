# -*- coding: utf-8 -*-
"""Phase 16-S-R1: Web 后台 Worker 真实 Storage 注入与多租户知识隔离集成测试。"""

from __future__ import annotations

import time

from contract_kb.application.agents.review_sessions import AgentReviewManager
from contract_kb.infrastructure.persistence.sqlite_storage import create_storage


def test_worker_receives_injected_storage_and_executes_review(tmp_path):
    """验证后台 Worker 真实获得显式注入的 Storage 并执行审查，杜绝伪 Mock 测试。"""
    db_file = tmp_path / "worker_test.db"
    real_storage = create_storage(db_path=db_file)

    # 在 tenant_alpha 写入已审批标准规则
    rule_id = f"RULE_WORKER_TEST_{time.time_ns()}"
    real_storage.save_rule(
        {
            "rule_id": rule_id,
            "version": 1,
            "name": "违约金封顶与追偿",
            "rule_type": "standard",
            "execution_mode": "SYNC",
            "severity": "HIGH",
            "priority": 100,
            "contract_types": ["*"],
            "parameters": {"clause_category": "LIABILITY"},
            "source_clauses": [{"document_id": "std_doc", "clause_id": "c1"}],
            "status": "APPROVED",
            "approved_by": "admin",
            "tenant_id": "tenant_alpha",
        },
        tenant_id="tenant_alpha",
    )

    mgr = AgentReviewManager()
    mgr.set_storage_provider(real_storage)

    session_id = f"sess_worker_int_{time.time_ns()}"
    diff_data = {
        "annotations": [
            {
                "id": "ann-worker-01",
                "clause_category": "LIABILITY",
                "delta_type": "TEXT_DELTA",
                "standard_span": "甲方迟延履行违约金按日万分之五计收",
                "reviewed_span": "甲方迟延履行无需支付任何违约赔偿",
                "clause_title": "违约赔偿责任",
            }
        ]
    }

    session = mgr.start_document_review(
        diff_data=diff_data,
        session_id=session_id,
        tenant_id="tenant_alpha",
    )

    # 验证 Session 确实持有了注入的 Storage
    assert session.get_storage() is real_storage

    # 等待真实后台 Worker 执行完成
    max_wait = 10.0
    start_t = time.time()
    completed = False
    ann_result = None
    while time.time() - start_t < max_wait:
        snap = session.get_snapshot()
        anns = snap.get("annotations", [])
        for a in anns:
            if a.get("id") == "ann-worker-01" and a.get("_aiDone"):
                completed = True
                ann_result = a
                break
        if completed:
            break
        time.sleep(0.2)

    assert completed is True, "Worker 未能在超时内完成条款审查"
    assert ann_result is not None
    assert "review_status" in ann_result
    assert "ai_analysis" in ann_result
    assert "evidence_references" in ann_result


def test_worker_tenant_isolation_rules_not_leaked(tmp_path):
    """跨租户规则隔离：tenant_beta 的后台 Worker 绝不使用 tenant_alpha 的规则。"""
    db_file = tmp_path / "tenant_iso.db"
    real_storage = create_storage(db_path=db_file)

    # 仅向 tenant_alpha 录入特有规则
    real_storage.save_rule(
        {
            "rule_id": "RULE_ALPHA_EXCLUSIVE",
            "version": 1,
            "name": "Alpha专有仲裁条款",
            "rule_type": "standard",
            "execution_mode": "SYNC",
            "severity": "HIGH",
            "priority": 100,
            "contract_types": ["*"],
            "parameters": {"clause_category": "DISPUTE_RESOLUTION"},
            "source_clauses": [{"document_id": "alpha_doc", "clause_id": "c1"}],
            "status": "APPROVED",
            "approved_by": "admin",
            "tenant_id": "tenant_alpha",
        },
        tenant_id="tenant_alpha",
    )

    mgr = AgentReviewManager()
    mgr.set_storage_provider(real_storage)

    session_id = f"sess_beta_{time.time_ns()}"
    diff_data = {
        "annotations": [
            {
                "id": "ann-beta-01",
                "clause_category": "DISPUTE_RESOLUTION",
                "delta_type": "TEXT_DELTA",
                "standard_span": "提交SCIA仲裁",
                "reviewed_span": "提交对方所在地法院诉讼",
                "clause_title": "争议解决",
            }
        ]
    }

    # 以 tenant_beta 身份发起审核
    session = mgr.start_document_review(
        diff_data=diff_data,
        session_id=session_id,
        tenant_id="tenant_beta",
    )

    # 等待 Worker 执行
    max_wait = 10.0
    start_t = time.time()
    ann_result = None
    while time.time() - start_t < max_wait:
        snap = session.get_snapshot()
        for a in snap.get("annotations", []):
            if a.get("id") == "ann-beta-01" and a.get("_aiDone"):
                ann_result = a
                break
        if ann_result:
            break
        time.sleep(0.2)

    assert ann_result is not None
    # 验证证据引用中绝对不包含 tenant_alpha 的专有规则
    refs = ann_result.get("evidence_references", [])
    for ref in refs:
        assert ref.get("rule_id") != "RULE_ALPHA_EXCLUSIVE"
        assert ref.get("tenant_id") != "tenant_alpha"
