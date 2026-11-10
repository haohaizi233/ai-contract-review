# -*- coding: utf-8 -*-
"""Phase 16-S: Checkpoint 完整性与密钥管理安全测试。

覆盖：
- Case 11: Checkpoint 内容篡改防护（篡改 risk_level / action_advice / doc_id / 签名，恢复被阻断）
- Case 12: 生产环境禁止使用固定默认密钥（CONTRACT_KB_ENV=production 时缺失密钥 Fail-Closed）
- 错误恢复语义覆盖（签名无效、文件篡改安全降级）
"""

import json

import pytest

from contract_kb.domain.models.review_identity import ReviewExecutionFingerprint
from contract_kb.infrastructure.checkpoints.storage import (
    CheckpointStorage,
    get_checkpoint_secret,
)


def _make_dummy_fingerprint(
    doc_name: str = "contract_test.docx",
) -> ReviewExecutionFingerprint:
    return ReviewExecutionFingerprint(
        tenant_id="test_tenant",
        user_id="test_user",
        file_sha256="a" * 64,
        template_id="tpl_std_2026",
        template_content_hash="b" * 64,
        template_version="v2.1",
        rule_set_version="rules_2026_q1",
        kb_snapshot_version="kb_snap_v1",
        agent_prompt_version="pv_v4",
        model_config_version="qwen35b_v1",
        diff_engine_version="diff_v2",
        annotations_fingerprint="c" * 64,
    )


def test_production_mode_requires_explicit_checkpoint_secret(monkeypatch):
    """Case 12: 在生产环境下，未配置 CONTRACT_KB_CHECKPOINT_KEY 必须报错，禁止静默使用公开默认密钥。"""
    monkeypatch.setenv("CONTRACT_KB_ENV", "production")
    monkeypatch.delenv("CONTRACT_KB_CHECKPOINT_KEY", raising=False)

    with pytest.raises(RuntimeError) as exc_info:
        get_checkpoint_secret()
    assert "生产环境必须配置" in str(
        exc_info.value
    ) or "CONTRACT_KB_CHECKPOINT_KEY" in str(exc_info.value)

    # 显式配置合法密钥后正常返回
    monkeypatch.setenv("CONTRACT_KB_CHECKPOINT_KEY", "prod_super_secret_key_1234567890")
    sec = get_checkpoint_secret()
    assert sec == "prod_super_secret_key_1234567890"


def test_checkpoint_tamper_detection(tmp_path, monkeypatch):
    """Case 11: Checkpoint 存储内容被恶意篡改（如改动风险等级），恢复时必须拒绝。"""
    test_secret = "test_key_for_tamper_detection_phase16"
    monkeypatch.setenv("CONTRACT_KB_CHECKPOINT_KEY", test_secret)

    storage = CheckpointStorage(base_dir=tmp_path)
    fp = _make_dummy_fingerprint("security_test_doc.docx")
    cards = [
        {
            "id": "ann-1",
            "risk_level": "HIGH",
            "risk_label": "重大违约金偏离",
            "ai_analysis": "违约金每日 1‰ 偏离企业 3‰ 标准",
            "action_advice": "坚决恢复母本 3‰",
        }
    ]

    # 保存合法 Checkpoint
    ckpt_file = storage.save_checkpoint(fp, cards)
    assert ckpt_file.exists()

    # 1. 模拟攻击者篡改风险等级为 LOW，图谋绕过法务人工复核
    raw_data = json.loads(ckpt_file.read_text(encoding="utf-8"))
    raw_data["completed_cards"][0]["risk_level"] = "LOW"
    raw_data["completed_cards"][0]["risk_label"] = "无风险放行"
    ckpt_file.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

    # 恢复必须失败
    restored_tampered = storage.verify_and_restore(fp)
    assert restored_tampered.is_valid is False, (
        "数据内容被篡改后，HMAC 校验必须失败阻断恢复"
    )
    assert restored_tampered.restored_cards == []

    # 2. 模拟攻击者伪造签名
    raw_data["completed_cards"][0]["risk_level"] = "HIGH"
    raw_data["signature"] = "fake_attacker_signature_ffffffffffff"
    ckpt_file.write_text(json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")

    restored_fake_sig = storage.verify_and_restore(fp)
    assert restored_fake_sig.is_valid is False, "伪造签名必须拒绝恢复"
    assert restored_fake_sig.restored_cards == []


def test_checkpoint_restores_cleanly_when_valid(tmp_path, monkeypatch):
    """验证未被篡改的合法 Checkpoint 正常恢复。"""
    test_secret = "valid_secret_key_12345"
    monkeypatch.setenv("CONTRACT_KB_CHECKPOINT_KEY", test_secret)

    storage = CheckpointStorage(base_dir=tmp_path)
    fp = _make_dummy_fingerprint("legit_doc.docx")
    cards = [
        {
            "id": "ann-1",
            "risk_level": "HIGH",
            "risk_label": "高风险条款",
            "ai_analysis": "合规分析正文",
            "action_advice": "建议处置方案",
        }
    ]

    ckpt_file = storage.save_checkpoint(fp, cards)
    assert ckpt_file.exists()
    restored = storage.verify_and_restore(fp)
    assert restored.is_valid is True
    assert len(restored.restored_cards) == 1
    assert restored.restored_cards[0]["risk_level"] == "HIGH"
