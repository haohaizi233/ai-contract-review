# -*- coding: utf-8 -*-
"""Phase 14-R G6/DEFECT-P1-01: 人工审核与反馈数据闭环接口与签名一致性测试。"""

from __future__ import annotations


from contract_kb.domain.models.review_identity import HumanReviewStatus
from contract_kb.legal_agent_kb import record_user_feedback


def test_record_user_feedback_signature_compatibility(tmp_path, monkeypatch):
    """测试修复后：支持以单个 dict 或多参数调用 record_user_feedback，成功记录且无崩溃。"""
    monkeypatch.setattr(
        "contract_kb.legal_agent_kb.FEEDBACK_BASE_DIR", tmp_path / "feedback"
    )

    data = {
        "task_id": "task_123",
        "file_sha256": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        "annotation_id": "ann_1",
        "clause_category": "LIABILITY",
        "delta_type": "NUMBER_CHANGE",
        "quote_standard": "标准条款 联系人电话: 13812345678",
        "quote_reviewed": "送审条款",
        "ai_analysis": "AI分析",
        "is_approved": True,
        "tenant_id": "tenant_qa",
    }

    # 1. 单参数 dict 调用（Web API 原调用方式）
    out_path = record_user_feedback(data)
    assert out_path.exists()
    content = out_path.read_text(encoding="utf-8")
    assert "task_123" in content
    # 敏感信息被脱敏
    assert "[手机号脱敏]" in content
    assert "13812345678" not in content

    # 2. 多参数调用兼容
    out_path_2 = record_user_feedback(
        task_id="task_456",
        file_sha256="1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        annotation_id="ann_2",
        sample_data={"clause_category": "TERM", "ai_analysis": "常规分析"},
        is_approved=False,
    )
    assert out_path_2.exists()


def test_reproduce_defect_human_review_status_illegal_transition():
    """测试状态机非法倒流防护：HUMAN_ACCEPTED 不得直接非法跳变到 PENDING 或 AI_REVIEWING。"""
    accepted = HumanReviewStatus.HUMAN_ACCEPTED
    assert not accepted.can_transition_to(HumanReviewStatus.PENDING)
    assert not accepted.can_transition_to(HumanReviewStatus.AI_REVIEWING)
