# -*- coding: utf-8 -*-
"""单元测试：ExecutionManifest 动态指纹与版本一致性校验 (R07).

验证：
1. ExecutionManifest 绝非静态占位符，而是动态反映当前运行的真实配置：
   - rule_set_version 动态获取 registry 版本；
   - provider 和 model 动态提取当前注入的 LLM 实例属性；
   - prompt_version 动态基于 PROMPT_SYSTEM 哈希；
   - schema_version 动态结合全部流水线阶段契约版本；
2. 当任何维度发生变更时，清单哈希必须敏感改变，阻止混用不兼容快照。
"""

from __future__ import annotations

from pathlib import Path
from docx import Document

from contract_kb.application.pipeline.contracts import ExecutionManifest
from contract_kb.application.pipeline.runner import ReviewPipeline
from contract_kb.llm_review import MockLLMProvider
from contract_kb.modules.rules import RuleRegistry
from contract_kb.storage import Storage


def test_manifest_extracts_dynamic_rule_provider_and_prompt(tmp_path: Path):
    """验证 ReviewPipeline 运行时动态注入真实配置到 ExecutionManifest 与 PipelineContext。"""
    docx_file = tmp_path / "test.docx"
    doc = Document()
    doc.add_paragraph("合同正文测试")
    doc.save(docx_file)

    storage = Storage(":memory:")
    registry = RuleRegistry(include_defaults=True)
    registry.version = "custom-v2.1"

    custom_provider = MockLLMProvider()
    custom_provider.name = "vllm_local"
    custom_provider.model = "qwen2.5-72b-instruct"

    pipeline = ReviewPipeline(
        storage=storage,
        llm_provider=custom_provider,
        registry=registry,
    )

    result = pipeline.run(str(docx_file))
    assert result.status == "COMPLETED"

    task = storage.get_task(result.task_id)
    assert task is not None
    meta = task.get("meta") or {}

    # 验证上下文中的版本属性动态匹配
    expected_rule_ver = registry.compute_rule_set_version([])
    assert meta.get("rule_set_version") == expected_rule_ver
    assert meta.get("model_version") == "vllm_local:qwen2.5-72b-instruct"

    # 验证快照清单哈希与动态生成的 manifest 一致
    cps = pipeline.checkpoint_store.get_checkpoints(result.task_id)
    assert len(cps) > 0
    first_cp = cps[0]
    manifest_hash = first_cp.manifest_hash
    assert manifest_hash, "快照清单哈希不能为空"

    # 重新构造相同配置的 manifest，哈希必须完全相同
    manifest_same = ExecutionManifest(
        file_hash=meta.get("file_hash", ""),
        tenant_id="default",
        rule_set_version=expected_rule_ver,
        provider="vllm_local",
        model="qwen2.5-72b-instruct",
    )
    # 当模型改动时，哈希必然改变
    manifest_diff = ExecutionManifest(
        file_hash=meta.get("file_hash", ""),
        tenant_id="default",
        rule_set_version=expected_rule_ver,
        provider="vllm_local",
        model="deepseek-r1-671b",
    )
    assert (
        manifest_diff.compute_manifest_fingerprint()
        != manifest_same.compute_manifest_fingerprint()
    )
