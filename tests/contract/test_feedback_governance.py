# -*- coding: utf-8 -*-
"""Phase 13R7: 人工审核闭环状态机、反馈数据治理与脱敏隔离测试集。"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from contract_kb.domain.models.review_identity import HumanReviewStatus
from contract_kb.application.agents.knowledge_adapter import (
    export_sft_dataset,
    record_user_feedback,
    sanitize_sensitive_data,
)


def test_human_review_status_lifecycle_transitions() -> None:
    """验证人工审阅 7 态状态机生命周期流转合法性与非法拦截。"""
    # 1. 验证 7 大状态枚举完备
    assert len(HumanReviewStatus) == 7
    expected_states = {
        "PENDING",
        "AI_REVIEWING",
        "AI_REVIEWED",
        "HUMAN_REVIEW_REQUIRED",
        "HUMAN_ACCEPTED",
        "HUMAN_REJECTED",
        "HUMAN_MODIFIED",
    }
    assert {s.value for s in HumanReviewStatus} == expected_states

    # 2. 合法流转
    assert (
        HumanReviewStatus.PENDING.can_transition_to(HumanReviewStatus.AI_REVIEWING)
        is True
    )
    assert (
        HumanReviewStatus.AI_REVIEWING.can_transition_to(HumanReviewStatus.AI_REVIEWED)
        is True
    )
    assert (
        HumanReviewStatus.AI_REVIEWED.can_transition_to(
            HumanReviewStatus.HUMAN_ACCEPTED
        )
        is True
    )
    assert (
        HumanReviewStatus.AI_REVIEWED.can_transition_to(
            HumanReviewStatus.HUMAN_MODIFIED
        )
        is True
    )
    assert (
        HumanReviewStatus.HUMAN_REVIEW_REQUIRED.can_transition_to(
            HumanReviewStatus.HUMAN_REJECTED
        )
        is True
    )

    # 3. 非法流转（越级逆流）
    assert (
        HumanReviewStatus.AI_REVIEWED.can_transition_to(HumanReviewStatus.PENDING)
        is False
    )
    assert (
        HumanReviewStatus.AI_REVIEWING.can_transition_to(HumanReviewStatus.PENDING)
        is False
    )


def test_pii_sanitization_masks_sensitive_identifiers() -> None:
    """验证个人隐私与商业敏感字段被合规脱敏掩码。"""
    raw_text = (
        "联系人：张三，手机号：13812345678，"
        "身份证号码：110101199003072345，"
        "结算对公银行账号：6222021234567890123。"
    )
    cleaned = sanitize_sensitive_data(raw_text)

    # 敏感真实信息必须被彻底抹除
    assert "13812345678" not in cleaned
    assert "110101199003072345" not in cleaned
    assert "6222021234567890123" not in cleaned

    # 必须出现对应的脱敏标记
    assert "[手机号脱敏]" in cleaned
    assert "[身份证号脱敏]" in cleaned
    assert "[银行卡号脱敏]" in cleaned


def test_feedback_dataset_export_train_eval_isolation() -> None:
    """验证法务人工修改反馈样本经审批后导出训练集与评估保留集（严格物理隔离）。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_base = Path(tmp_dir) / "ai_feedback"
        tmp_base.mkdir(parents=True, exist_ok=True)

        with patch(
            "contract_kb.application.agents.knowledge_adapter.FEEDBACK_BASE_DIR",
            tmp_base,
        ):
            # 记录多条反馈数据（包含不同文档哈希，以触发 holdout 切分）
            for idx in range(10):
                file_hash = f"contract_hash_{idx:03d}"
                record_user_feedback(
                    task_id=f"task_{idx}",
                    file_sha256=file_hash,
                    annotation_id=f"ann-{idx}",
                    sample_data={
                        "clause_category": "LIABILITY",
                        "delta_type": "NUMBER_CHANGE",
                        "quote_standard": f"条款 {idx}: 违约金每日 3‰",
                        "quote_reviewed": f"条款 {idx}: 违约金每日 1‰",
                        "ai_analysis": "违约金下调",
                        "action_advice": "要求恢复",
                        "human_analysis": f"经核查第 {idx} 项商务背景，同意按 LPR 4倍执行",
                        "human_advice": "修改为 LPR 4倍基准",
                    },
                    reviewer_id="lead_counsel",
                    is_approved=True,  # 已经审批入库
                )

            # 导出 SFT 微调数据
            result = export_sft_dataset(require_approval=True)

            assert result["train_count"] > 0
            assert result["eval_count"] > 0
            assert result["train_count"] + result["eval_count"] == 10

            train_path = Path(result["train_file"])
            eval_path = Path(result["eval_file"])

            assert train_path.exists()
            assert eval_path.exists()

            train_data = json.loads(train_path.read_text(encoding="utf-8"))
            eval_data = json.loads(eval_path.read_text(encoding="utf-8"))

            # 验证两者完全独立
            train_inputs = {item["input"] for item in train_data}
            eval_inputs = {item["input"] for item in eval_data}
            assert len(train_data) == result["train_count"]
            assert len(eval_data) == result["eval_count"]
            assert len(train_inputs.intersection(eval_inputs)) == 0, (
                "训练集与评估测试集严禁发生样本重叠"
            )
