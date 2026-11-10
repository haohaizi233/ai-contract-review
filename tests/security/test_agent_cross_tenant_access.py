# -*- coding: utf-8 -*-
"""Phase 13R2 & Phase 13R6: 法务 Agent 与工作台跨租户越权访问阻断安全测试。"""

import tempfile
from pathlib import Path
import pytest

from contract_kb.infrastructure.checkpoints.storage import CheckpointStorage
from contract_kb.application.agents.review_sessions import AgentReviewManager
from contract_kb.domain.models.review_identity import ReviewExecutionFingerprint


def test_agent_manager_blocks_cross_tenant_session_access() -> None:
    """验证 AgentReviewManager 强制阻断跨租户越权获取或操作 Session。"""
    manager = AgentReviewManager()

    # 租户 A 创建会话
    manager.get_or_create_session(
        session_id="confidential_session_001",
        tenant_id="tenant_secret_corp",
        user_id="lawyer_alice",
    )

    # 租户 B 试图使用相同的 session_id 访问
    with pytest.raises(PermissionError) as exc_info:
        manager.get_or_create_session(
            session_id="confidential_session_001",
            tenant_id="tenant_competitor_corp",
            user_id="hacker_bob",
        )
    assert "跨租户越权阻断" in str(exc_info.value)

    # 租户 B 试图通过 get_session 读取
    with pytest.raises(PermissionError) as exc_info2:
        manager.get_session(
            session_id="confidential_session_001",
            tenant_id="tenant_competitor_corp",
        )
    assert "跨租户越权阻断" in str(exc_info2.value)


def test_checkpoint_storage_enforces_tenant_boundary() -> None:
    """验证即使指纹哈希完全相同，不同租户的检查点物理存储与恢复门禁坚决隔离。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage = CheckpointStorage(base_dir=Path(tmp_dir))

        fp_tenant_a = ReviewExecutionFingerprint(
            tenant_id="tenant_a",
            user_id="user_a",
            file_sha256="contract_hash_123",
            template_id="tpl_01",
            template_content_hash="tpl_hash_1",
            template_version="v1",
            rule_set_version="13R",
            kb_snapshot_version="13R",
            agent_prompt_version="13R",
            model_config_version="qwen",
            diff_engine_version="13R1",
            annotations_fingerprint="ann_hash_1",
        )

        cards = [
            {"id": "ann-1", "risk_level": "HIGH", "action_advice": "租户A核心机密"}
        ]
        storage.save_checkpoint(fp_tenant_a, cards)

        # 租户 B 构造除 tenant_id 外完全相同的指纹，试图探测恢复
        fp_tenant_b = ReviewExecutionFingerprint(
            tenant_id="tenant_b",
            user_id="user_b",
            file_sha256="contract_hash_123",
            template_id="tpl_01",
            template_content_hash="tpl_hash_1",
            template_version="v1",
            rule_set_version="13R",
            kb_snapshot_version="13R",
            agent_prompt_version="13R",
            model_config_version="qwen",
            diff_engine_version="13R1",
            annotations_fingerprint="ann_hash_1",
        )

        result_b = storage.verify_and_restore(fp_tenant_b)
        assert result_b.is_valid is False
        assert result_b.reason in ("CHECKPOINT_NOT_FOUND", "TENANT_MISMATCH")
        assert len(result_b.restored_cards) == 0
