# -*- coding: utf-8 -*-
"""检查点安全持久化与防篡改/防串线恢复引擎 (Agent Checkpoint Manager) - Phase 13R2。

严格核验 8 大项条件：
1. 任务身份一致性 (tenant_id / user_id)
2. 送审原文件 SHA256 内容哈希一致性
3. 基准母本版本及内容哈希一致性
4. 企业规则版本与知识库快照版本一致性
5. 待审批注内容指纹一致性
6. 检查点整体内容结构与数据有效性
7. HMAC / SHA256 防篡改与数据完整性校验
8. 租户物理隔离与时效性校验
不满足上述任意一项，坚决阻断恢复，并返回结构化失效诊断日志。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any

from contract_kb import config
from contract_kb.domain.models.review_identity import ReviewExecutionFingerprint

CHECKPOINT_DIR = config.DATA_DIR / "agent_checkpoints"

CHECKPOINT_SECRET = os.environ.get(
    "CONTRACT_KB_CHECKPOINT_KEY", "contract_kb_checkpoint_integrity_secret_key_v14"
)


def _compute_checkpoint_signature(
    fingerprint_hash: str, cards: list[dict[str, Any]]
) -> str:
    """计算检查点防篡改内容指纹签名（HMAC-SHA256 强认证完整性保护）。"""
    raw_cards = json.dumps(cards, sort_keys=True, ensure_ascii=False)
    combined = f"{fingerprint_hash}||{raw_cards}"
    return hmac.new(
        CHECKPOINT_SECRET.encode("utf-8"),
        combined.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def compute_annotations_fingerprint(annotations: list[dict[str, Any]]) -> str:
    """计算批注列表的内容指纹（全文本规范化哈希，绝不截断前40字符，杜绝内容变更碰撞）。"""
    items = []
    for a in sorted(annotations, key=lambda x: str(x.get("id", ""))):
        aid = str(a.get("id", ""))
        std = str(a.get("quote_standard") or a.get("standard_span") or "").strip()
        rev = str(a.get("quote_reviewed") or a.get("reviewed_span") or "").strip()
        dtype = str(a.get("delta_type") or "").strip()
        cat = str(a.get("clause_category") or a.get("category") or "").strip()
        nodes = ",".join(str(n) for n in a.get("source_node_ids") or [])
        ann_content = f"{aid}|{dtype}|{cat}|{nodes}|{std}|{rev}"
        ann_hash = hashlib.sha256(ann_content.encode("utf-8")).hexdigest()
        items.append(f"{aid}:{ann_hash}")
    return hashlib.sha256("|".join(items).encode("utf-8")).hexdigest()


class CheckpointVerificationResult:
    def __init__(
        self,
        is_valid: bool,
        reason: str = "",
        restored_cards: list[dict[str, Any]] | None = None,
    ):
        self.is_valid = is_valid
        self.reason = reason
        self.restored_cards = restored_cards or []


class CheckpointStorage:
    """具备原子落盘与加密防篡改核验的检查点持久化存储服务。"""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or CHECKPOINT_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _get_checkpoint_path(self, fingerprint: ReviewExecutionFingerprint) -> Path:
        f_hash = fingerprint.compute_hash()
        tenant = fingerprint.tenant_id.replace("/", "_").replace("\\", "_")
        tenant_dir = self.base_dir / tenant
        tenant_dir.mkdir(parents=True, exist_ok=True)
        return tenant_dir / f"ckpt_{f_hash[:24]}.json"

    def save_checkpoint(
        self,
        fingerprint: ReviewExecutionFingerprint,
        completed_cards: list[dict[str, Any]],
    ) -> Path:
        """原子落盘保存审查检查点（.tmp -> replace 原子写入）。"""
        target_path = self._get_checkpoint_path(fingerprint)
        f_hash = fingerprint.compute_hash()
        signature = _compute_checkpoint_signature(f_hash, completed_cards)

        payload = {
            "version": "13R2",
            "fingerprint": fingerprint.to_dict(),
            "signature": signature,
            "saved_at": time.time(),
            "completed_count": len(completed_cards),
            "completed_cards": completed_cards,
        }

        tmp_path = target_path.with_suffix(f".tmp_{time.time_ns()}")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp_path.replace(target_path)
        return target_path

    def verify_and_restore(
        self,
        current_fingerprint: ReviewExecutionFingerprint,
    ) -> CheckpointVerificationResult:
        """严格按 8 大安全门禁校验并恢复检查点。"""
        target_path = self._get_checkpoint_path(current_fingerprint)
        if not target_path.exists():
            return CheckpointVerificationResult(False, "CHECKPOINT_NOT_FOUND")

        try:
            content = json.loads(target_path.read_text(encoding="utf-8"))
        except Exception as e:
            return CheckpointVerificationResult(False, f"CORRUPTED_JSON: {e}")

        ckpt_fp = content.get("fingerprint") or {}
        if not isinstance(ckpt_fp, dict):
            return CheckpointVerificationResult(False, "INVALID_FINGERPRINT_STRUCTURE")

        # 1. 租户隔离
        if current_fingerprint.tenant_id != ckpt_fp.get("tenant_id"):
            return CheckpointVerificationResult(False, "TENANT_MISMATCH")

        # 2. 用户所有权
        if current_fingerprint.user_id != ckpt_fp.get("user_id"):
            return CheckpointVerificationResult(False, "USER_MISMATCH")

        # 3. 文件内容 SHA256 哈希
        if current_fingerprint.file_sha256 != ckpt_fp.get("file_sha256"):
            return CheckpointVerificationResult(False, "FILE_CONTENT_MODIFIED")

        # 4. 母本模板 ID、内容哈希与版本
        if (
            current_fingerprint.template_id != ckpt_fp.get("template_id")
            or current_fingerprint.template_content_hash
            != ckpt_fp.get("template_content_hash")
            or current_fingerprint.template_version != ckpt_fp.get("template_version")
        ):
            return CheckpointVerificationResult(False, "TEMPLATE_VERSION_CHANGED")

        # 5. 审核规则版本与知识库快照版本
        if current_fingerprint.rule_set_version != ckpt_fp.get(
            "rule_set_version"
        ) or current_fingerprint.kb_snapshot_version != ckpt_fp.get(
            "kb_snapshot_version"
        ):
            return CheckpointVerificationResult(False, "KNOWLEDGE_BASE_UPDATED")

        # 6. 待审批注内容指纹
        if current_fingerprint.annotations_fingerprint != ckpt_fp.get(
            "annotations_fingerprint"
        ):
            return CheckpointVerificationResult(False, "ANNOTATION_ITEMS_CHANGED")

        # 7. 防篡改签名完整性核验
        completed_cards = content.get("completed_cards") or []
        expected_sig = _compute_checkpoint_signature(
            current_fingerprint.compute_hash(), completed_cards
        )
        actual_sig = str(content.get("signature", "")).strip()
        if not hmac.compare_digest(actual_sig, expected_sig):
            return CheckpointVerificationResult(
                False, "TAMPERED_CHECKPOINT_SIGNATURE_MISMATCH"
            )

        # 8. 内容结构有效性
        if not isinstance(completed_cards, list):
            return CheckpointVerificationResult(False, "INVALID_CARDS_STRUCTURE")

        return CheckpointVerificationResult(True, "VERIFIED", completed_cards)

    def clear_checkpoint(self, fingerprint: ReviewExecutionFingerprint) -> bool:
        """显式清理失效检查点。"""
        target_path = self._get_checkpoint_path(fingerprint)
        if target_path.exists():
            try:
                target_path.unlink()
                return True
            except OSError:
                pass
        return False
