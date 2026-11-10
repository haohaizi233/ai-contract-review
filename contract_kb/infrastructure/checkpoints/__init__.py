# -*- coding: utf-8 -*-
"""检查点安全持久化与防篡改恢复基础设施（Checkpoints Infrastructure）。"""

from .storage import (
    CHECKPOINT_DIR,
    CheckpointStorage,
    CheckpointVerificationResult,
    compute_annotations_fingerprint,
)

__all__ = [
    "CHECKPOINT_DIR",
    "CheckpointStorage",
    "CheckpointVerificationResult",
    "compute_annotations_fingerprint",
]
