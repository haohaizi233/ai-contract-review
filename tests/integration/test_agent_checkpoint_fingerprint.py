# -*- coding: utf-8 -*-
"""Phase 13R2: Agent 检查点 8 维原子指纹防篡改与失效自愈测试集。"""

import json
import tempfile
from pathlib import Path

from contract_kb.infrastructure.checkpoints.storage import (
    CheckpointStorage,
    compute_annotations_fingerprint,
)
from contract_kb.domain.models.review_identity import ReviewExecutionFingerprint


def _make_sample_fingerprint(
    tenant_id: str = "tenant_test",
    user_id: str = "user_01",
    file_sha256: str = "hash_file_v1",
    template_content_hash: str = "hash_tpl_v1",
    template_version: str = "v1",
    rule_set_version: str = "13R",
    kb_snapshot_version: str = "13R",
    annotations_fingerprint: str = "hash_ann_v1",
) -> ReviewExecutionFingerprint:
    return ReviewExecutionFingerprint(
        tenant_id=tenant_id,
        user_id=user_id,
        file_sha256=file_sha256,
        template_id="tpl_annual_fee",
        template_content_hash=template_content_hash,
        template_version=template_version,
        rule_set_version=rule_set_version,
        kb_snapshot_version=kb_snapshot_version,
        agent_prompt_version="13R",
        model_config_version="qwen-max",
        diff_engine_version="13R1",
        annotations_fingerprint=annotations_fingerprint,
    )


def test_checkpoint_save_and_restore_success() -> None:
    """验证正常保存与还原检查点 8 大门禁全部通过。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage = CheckpointStorage(base_dir=Path(tmp_dir))
        fp = _make_sample_fingerprint()

        cards = [
            {
                "id": "ann-1",
                "risk_level": "HIGH",
                "ai_analysis": "逾期罚息上调",
                "action_advice": "纠正",
            },
            {
                "id": "ann-2",
                "risk_level": "LOW",
                "ai_analysis": "措辞调整",
                "action_advice": "确认",
            },
        ]

        storage.save_checkpoint(fp, cards)

        result = storage.verify_and_restore(fp)
        assert result.is_valid is True
        assert result.reason == "VERIFIED"
        assert len(result.restored_cards) == 2
        assert result.restored_cards[0]["id"] == "ann-1"


def test_checkpoint_file_content_modified_rejected() -> None:
    """验证送审原文件内容修改（SHA256 变化）时检查点拒绝恢复。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage = CheckpointStorage(base_dir=Path(tmp_dir))
        fp_original = _make_sample_fingerprint(file_sha256="original_file_hash")
        storage.save_checkpoint(fp_original, [{"id": "ann-1"}])

        # 用户重新上传了经过修改的合同文件
        fp_modified = _make_sample_fingerprint(file_sha256="modified_file_hash")

        result = storage.verify_and_restore(fp_modified)
        assert result.is_valid is False
        assert result.reason in ("CHECKPOINT_NOT_FOUND", "FILE_CONTENT_MODIFIED")


def test_checkpoint_template_changed_rejected() -> None:
    """验证法务更新母本模板版本或内容时，旧审查检查点被拒绝。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage = CheckpointStorage(base_dir=Path(tmp_dir))
        fp_v1 = _make_sample_fingerprint(
            template_content_hash="tpl_v1_hash", template_version="v1"
        )
        storage.save_checkpoint(fp_v1, [{"id": "ann-1"}])

        # 母本升级到 v2
        fp_v2 = _make_sample_fingerprint(
            template_content_hash="tpl_v2_hash", template_version="v2"
        )

        result = storage.verify_and_restore(fp_v2)
        assert result.is_valid is False
        assert result.reason in ("CHECKPOINT_NOT_FOUND", "TEMPLATE_VERSION_CHANGED")


def test_checkpoint_signature_tampering_detected() -> None:
    """验证直接篡改检查点文件内容（伪造审查通过）被 HMAC/SHA256 签名校验阻断。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage = CheckpointStorage(base_dir=Path(tmp_dir))
        fp = _make_sample_fingerprint()
        cards = [{"id": "ann-1", "risk_level": "HIGH", "action_advice": "要求撤销"}]
        ckpt_path = storage.save_checkpoint(fp, cards)

        # 模拟恶意篡改：将 HIGH 风险篡改成 LOW，并不更新签名
        raw_data = json.loads(ckpt_path.read_text(encoding="utf-8"))
        raw_data["completed_cards"][0]["risk_level"] = "LOW"
        raw_data["completed_cards"][0]["action_advice"] = "放行（伪造）"
        ckpt_path.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

        result = storage.verify_and_restore(fp)
        assert result.is_valid is False
        assert result.reason == "TAMPERED_CHECKPOINT_SIGNATURE_MISMATCH"


def test_annotations_fingerprint_computation() -> None:
    """验证批注内容变化会导致不同的 annotations_fingerprint。"""
    anns_1 = [
        {
            "id": "ann-1",
            "delta_type": "NUMBER_CHANGE",
            "quote_standard": "100",
            "quote_reviewed": "200",
        }
    ]
    anns_2 = [
        {
            "id": "ann-1",
            "delta_type": "NUMBER_CHANGE",
            "quote_standard": "100",
            "quote_reviewed": "999",
        }
    ]

    fp1 = compute_annotations_fingerprint(anns_1)
    fp2 = compute_annotations_fingerprint(anns_2)

    assert fp1 != fp2
    assert len(fp1) == 64
