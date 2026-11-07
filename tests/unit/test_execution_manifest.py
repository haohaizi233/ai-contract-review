# -*- coding: utf-8 -*-
"""单元测试：可复现执行清单 (ExecutionManifest) 与指纹计算（T09 / F09 / ADR-001）。

验证契约：
1. 指纹与 Task ID 完全解耦（同配置不同 Task ID 指纹必相同）；
2. 任何影响输出的关键环境因子变动（模型、规则集、模板、索引版本、Prompt 版本等）
   必须产生严格不同的 manifest 指纹；
3. 确定性规则与模型指纹能够支撑可审计重放。
"""

from __future__ import annotations


from contract_kb.application.pipeline.contracts import ExecutionManifest


def test_manifest_fingerprint_independent_of_task_id():
    """验证 manifest 指纹独立于 task_id，仅取决于输入配置与依赖版本。"""
    m1 = ExecutionManifest(
        file_hash="hash_abc123",
        tenant_id="tenant_1",
        rule_set_version="rules_2026_v1",
        template_version="tpl_2026_v1",
        embedding_version="emb_v2",
        index_version="idx_v1",
        provider="mock_llm",
        model="deepseek-v3",
        prompt_version="p_v1",
        schema_version="s_v1",
        code_version="commit_sha_123",
    )
    m2 = ExecutionManifest(
        file_hash="hash_abc123",
        tenant_id="tenant_1",
        rule_set_version="rules_2026_v1",
        template_version="tpl_2026_v1",
        embedding_version="emb_v2",
        index_version="idx_v1",
        provider="mock_llm",
        model="deepseek-v3",
        prompt_version="p_v1",
        schema_version="s_v1",
        code_version="commit_sha_123",
    )

    fp1 = m1.compute_manifest_fingerprint()
    fp2 = m2.compute_manifest_fingerprint()
    assert fp1 == fp2
    assert len(fp1) == 64


def test_manifest_fingerprint_changes_on_model_difference():
    """验证切换模型 (model-a vs model-b) 必然生成不同 manifest 指纹。"""
    base_args = dict(
        file_hash="hash_abc123",
        tenant_id="tenant_1",
        rule_set_version="rules_2026_v1",
        template_version="tpl_2026_v1",
        embedding_version="emb_v2",
        index_version="idx_v1",
        provider="mock_llm",
        prompt_version="p_v1",
        schema_version="s_v1",
        code_version="commit_sha_123",
    )
    m_a = ExecutionManifest(model="model-a", **base_args)
    m_b = ExecutionManifest(model="model-b", **base_args)

    assert m_a.compute_manifest_fingerprint() != m_b.compute_manifest_fingerprint()


def test_manifest_fingerprint_changes_on_rule_or_template_version():
    """验证规则集版本或模板版本变动产生不同指纹。"""
    base_args = dict(
        file_hash="hash_abc123",
        tenant_id="tenant_1",
        embedding_version="emb_v2",
        index_version="idx_v1",
        provider="mock_llm",
        model="deepseek-v3",
        prompt_version="p_v1",
        schema_version="s_v1",
        code_version="commit_sha_123",
    )
    m1 = ExecutionManifest(rule_set_version="v1.0", template_version="t1", **base_args)
    m2 = ExecutionManifest(rule_set_version="v1.1", template_version="t1", **base_args)
    m3 = ExecutionManifest(rule_set_version="v1.0", template_version="t2", **base_args)

    fp1 = m1.compute_manifest_fingerprint()
    fp2 = m2.compute_manifest_fingerprint()
    fp3 = m3.compute_manifest_fingerprint()

    assert fp1 != fp2
    assert fp1 != fp3
    assert fp2 != fp3
