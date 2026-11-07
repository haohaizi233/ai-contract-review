# -*- coding: utf-8 -*-
"""阶段强类型契约 (Contracts) 与可复现执行清单 (ExecutionManifest)（T09 / F09 / ADR-001/002）。

实现：
1. 阶段输入/输出运行时强类型 Pydantic 模型，杜绝无格式隐式字典传递；
2. 不可变执行清单 ExecutionManifest 与确定性指纹计算（独立于 Task ID）；
3. 异常边界 StageContractValidationError。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class StageContractValidationError(Exception):
    """阶段输入输出契约校验失败。"""

    pass


class ExecutionManifest(BaseModel):
    """流水线执行环境不可变清单契约（用于重放比对与断点恢复合法性校验）。"""

    file_hash: str
    tenant_id: str = "default"
    rule_set_version: str = "builtin-v1"
    template_version: str = "builtin-v1"
    embedding_version: str = "local_hash_256"
    index_version: str = "idx_v1"
    provider: str = "mock"
    model: str = "mock"
    prompt_version: str = "v1"
    schema_version: str = "v1"
    code_version: str = "head"

    def compute_manifest_fingerprint(self) -> str:
        """计算确定性配置指纹（与 task_id 完全解耦）。"""
        payload = {
            "file_hash": self.file_hash,
            "tenant_id": self.tenant_id,
            "rule_set_version": self.rule_set_version,
            "template_version": self.template_version,
            "embedding_version": self.embedding_version,
            "index_version": self.index_version,
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "code_version": self.code_version,
        }
        canonical_str = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()


# ---------- 阶段具名契约模型 ----------


class ValidateStageInput(BaseModel):
    docx_path: Path
    mode: str = "STANDARD"
    tenant_id: str = "default"


class ValidateStageOutput(BaseModel):
    file_hash: str
    file_size_bytes: int
    is_valid: bool
    details: dict[str, Any] = Field(default_factory=dict)


class ParseStageInput(BaseModel):
    docx_path: Path
    file_hash: str


class ParseStageOutput(BaseModel):
    paragraph_count: int
    table_count: int
    node_count: int
    clauses_count: int


class ClassifyStageInput(BaseModel):
    clause_count: int
    text_sample: str = ""


class ClassifyStageOutput(BaseModel):
    category: str
    subtype: str
    confidence: float
    conflicts: list[str] = Field(default_factory=list)
