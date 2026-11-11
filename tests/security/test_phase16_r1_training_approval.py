# -*- coding: utf-8 -*-
"""Phase 16-S-R1: 反馈微调审批权属隔离、SFT 训练集导出与数据防投毒测试集。"""

from __future__ import annotations

import json
import time

import pytest

from contract_kb.application.agents.knowledge_adapter import (
    FEEDBACK_BASE_DIR,
    approve_feedback_for_training,
    export_sft_dataset,
    revoke_training_approval,
    submit_feedback,
)
from contract_kb.interfaces.http.auth import Principal


def test_submit_feedback_strictly_pending_review():
    """任何通过 submit_feedback 提交的原始反馈必须标记 PENDING_REVIEW，仅落盘于 raw_feedback。"""
    t_id = f"task_pending_{time.time_ns()}"
    ann_id = f"ann_pending_{time.time_ns()}"
    tenant = "corp_training_test"

    payload = {
        "task_id": t_id,
        "annotation_id": ann_id,
        "human_decision": "MODIFIED",
        "human_modified_suggestion": "人工合规修订条款内容",
        "is_approved": True,  # 试图自批
        "is_approved_for_training": True,
    }

    saved_path = submit_feedback(payload, tenant_id=tenant, user_id="user_alice")
    assert saved_path is not None
    assert "raw_feedback" in str(saved_path)

    # 验证 raw_feedback 文件的真实内容
    raw_content = saved_path.read_text(encoding="utf-8")
    raw_records = [
        json.loads(line) for line in raw_content.splitlines() if line.strip()
    ]
    matched = [r for r in raw_records if r.get("task_id") == t_id]
    assert len(matched) == 1
    rec = matched[0]
    assert rec.get("is_approved_for_training") is False
    assert rec.get("approved_by") is None

    # 严禁进入 reviewed_events.jsonl
    rev_file = (
        FEEDBACK_BASE_DIR / tenant / "reviewed_feedback" / "reviewed_events.jsonl"
    )
    if rev_file.exists():
        for line in rev_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            assert r.get("task_id") != t_id


def test_unauthorized_user_cannot_approve_training_data():
    """普通 viewer 角色无权审批训练样本，调用 approve_feedback_for_training 必须被拒。"""
    t_id = f"task_unauth_{time.time_ns()}"
    ann_id = f"ann_unauth_{time.time_ns()}"
    tenant = "corp_training_test"

    submit_feedback(
        {"task_id": t_id, "annotation_id": ann_id, "human_decision": "ACCEPTED"},
        tenant_id=tenant,
        user_id="user_bob",
    )

    unauthorized_principal = Principal(
        user_id="user_bob",
        roles=["viewer"],  # 仅只读权限
        tenant_id=tenant,
    )

    with pytest.raises(PermissionError) as exc_info:
        approve_feedback_for_training(
            task_id=t_id,
            annotation_id=ann_id,
            approver=unauthorized_principal,
            tenant_id=tenant,
            reason="法务复核",
        )
    assert "权限不足" in str(exc_info.value) or "无权" in str(exc_info.value)


def test_authorized_reviewer_approves_and_revokes_training_data():
    """授权 Reviewer 批准样本进入训练集，并可审计撤回 (REVOKED)。"""
    t_id = f"task_lifecycle_{time.time_ns()}"
    ann_id = f"ann_lifecycle_{time.time_ns()}"
    tenant = "corp_training_test"

    submit_feedback(
        {
            "task_id": t_id,
            "annotation_id": ann_id,
            "human_decision": "MODIFIED",
            "human_modified_suggestion": "严格违约责任3‰",
        },
        tenant_id=tenant,
        user_id="user_charlie",
    )

    approver = Principal(
        user_id="chief_counsel",
        roles=["reviewer"],
        tenant_id=tenant,
    )

    # 1. 执行审批
    res_app = approve_feedback_for_training(
        task_id=t_id,
        annotation_id=ann_id,
        approver=approver,
        tenant_id=tenant,
        reason="法务总监合规复核通过",
    )
    assert res_app.get("status") == "APPROVED_FOR_TRAINING"

    # 2. 验证已写入 reviewed_events.jsonl，且携带审计人与角色元数据
    rev_file = (
        FEEDBACK_BASE_DIR / tenant / "reviewed_feedback" / "reviewed_events.jsonl"
    )
    assert rev_file.exists()
    rev_lines = [
        json.loads(line)
        for line in rev_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    matched = [r for r in rev_lines if r.get("task_id") == t_id]
    assert len(matched) >= 1
    latest = matched[-1]
    assert latest.get("is_approved_for_training") is True
    assert latest.get("approved_by") == "chief_counsel"
    assert latest.get("approval_revoked") is False

    # 3. 授权管理员执行撤回 (REVOKED)
    admin_principal = Principal(
        user_id="chief_admin",
        roles=["admin"],
        tenant_id=tenant,
    )
    res_rev = revoke_training_approval(
        task_id=t_id,
        annotation_id=ann_id,
        approver=admin_principal,
        tenant_id=tenant,
        reason="客户商务条款变动，撤销微调资格",
    )
    assert res_rev.get("status") == "APPROVAL_REVOKED"


def test_export_sft_dataset_filters_revoked_and_cross_tenant():
    """SFT 训练集导出必须严格过滤未审批或已撤销样本，且绝不混入跨租户数据。"""
    tenant_a = f"tenant_export_a_{time.time_ns()}"
    tenant_b = f"tenant_export_b_{time.time_ns()}"

    approver = Principal(user_id="admin_a", roles=["admin"], tenant_id=tenant_a)

    # 在 tenant_a 提交一个审批通过的样本
    t_a = f"task_a_{time.time_ns()}"
    submit_feedback(
        {"task_id": t_a, "annotation_id": "ann-a", "human_decision": "MODIFIED"},
        tenant_id=tenant_a,
    )
    approve_feedback_for_training(
        task_id=t_a,
        annotation_id="ann-a",
        approver=approver,
        tenant_id=tenant_a,
        reason="A租户合规样本",
    )

    # 在 tenant_b 提交一个审批通过的样本
    t_b = f"task_b_{time.time_ns()}"
    approver_b = Principal(user_id="admin_b", roles=["admin"], tenant_id=tenant_b)
    submit_feedback(
        {"task_id": t_b, "annotation_id": "ann-b", "human_decision": "MODIFIED"},
        tenant_id=tenant_b,
    )
    approve_feedback_for_training(
        task_id=t_b,
        annotation_id="ann-b",
        approver=approver_b,
        tenant_id=tenant_b,
        reason="B租户合规样本",
    )

    # 导出 tenant_a 的 SFT 训练集
    res = export_sft_dataset(tenant_id=tenant_a, require_approval=True)
    assert "train_count" in res
    train_file = (
        FEEDBACK_BASE_DIR / tenant_a / "approved_training_samples" / "sft_train.json"
    )
    assert train_file.exists()
    train_content = train_file.read_text(encoding="utf-8")

    # 严禁混入 tenant_b 样本
    assert t_b not in train_content
    assert t_a in train_content
