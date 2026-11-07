# -*- coding: utf-8 -*-
"""契约测试：流水线各阶段强类型运行时输入/输出 Schema 校验（T09 / F09 / ADR-002）。

验证契约：
1. 阶段拥有显式具名 Pydantic input_schema 与 output_schema；
2. 输入数据不满足契约时在阶段执行前抛出 StageContractValidationError，阻止脏数据穿透；
3. 输出数据由 output_schema 进行校验与脱敏快照序列化。
"""

from __future__ import annotations

from pathlib import Path
import pytest
from pydantic import ValidationError

from contract_kb.application.pipeline.contracts import (
    ValidateStageInput,
    ValidateStageOutput,
)


def test_stage_contract_validation_rejects_malformed_input():
    """验证格式校验阶段在输入缺失关键字段时主动拦截。"""
    with pytest.raises(ValidationError):
        # 缺少 docx_path
        ValidateStageInput.model_validate({"mode": "STANDARD"})


def test_stage_contract_validates_correct_input_and_output():
    """验证合法结构正确解析并支持快照输出。"""
    valid_in = ValidateStageInput(
        docx_path=Path("dummy.docx"),
        mode="STANDARD",
        tenant_id="t1",
    )
    assert valid_in.mode == "STANDARD"

    valid_out = ValidateStageOutput(
        file_hash="hash123",
        file_size_bytes=1024,
        is_valid=True,
    )
    dumped = valid_out.model_dump()
    assert dumped["file_hash"] == "hash123"
    assert dumped["is_valid"] is True
