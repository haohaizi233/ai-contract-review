# -*- coding: utf-8 -*-
"""测试断点恢复过程中 Finding 完整性、风险不丢失及门禁不降级（R01 整改验证）。"""

from __future__ import annotations

from pathlib import Path
import pytest

from contract_kb.storage import Storage
from contract_kb.application.pipeline.runner import ReviewPipeline
from contract_kb.application.pipeline.stages import ReviewStage
from contract_kb.golden import generate_all


def test_resume_preserves_all_findings_and_gate_status(tmp_path: Path):
    """
    使用金额大小写不一致合同 mut_08_amount_case_mismatch.docx：
    1. 不中断执行作为 baseline 对照；
    2. 在第 9 阶段（RetrieveEvidence）注入故障后中断；
    3. 重新创建执行器，恢复执行同一任务；
    4. 对比 baseline 与 resumed 的 findings（包含 AMOUNT_CASE_CONSISTENCY 与 FORMAT 风险）、gate 及数据库存储。
    """
    samples_dir = tmp_path / "samples"
    generate_all(samples_dir)
    docx_file = samples_dir / "mut_08_amount_case_mismatch.docx"
    assert docx_file.exists()

    db_path = tmp_path / "resume_verify.db"
    st = Storage(db_path)

    # 1. 运行正常 baseline
    pipeline_normal = ReviewPipeline(st)
    normal_res = pipeline_normal.run(docx_file, task_id="task_baseline", tenant_id="tenant_audit")
    assert normal_res.status == "COMPLETED"
    baseline_rule_ids = {issue["rule_id"] for issue in normal_res.issues}
    assert "AMOUNT_CASE_CONSISTENCY" in baseline_rule_ids
    assert normal_res.review_gate == "BLOCKED_BY_HIGH_RISK"
    assert len(normal_res.issues) >= 5

    # 2. 注入故障在 stage 9
    class SyntheticFailStage9(ReviewStage):
        stage_name = "RetrieveEvidence"
        stage_order = 9
        def execute(self, ctx):
            raise RuntimeError("Synthetic interruption at stage 9")

    pipeline_fail = ReviewPipeline(st)
    # 找到 RetrieveEvidence 替换为失败桩
    stage_idx = next(i for i, s in enumerate(pipeline_fail.stages) if s.stage_name == "RetrieveEvidence")
    pipeline_fail.stages[stage_idx] = SyntheticFailStage9()

    with pytest.raises(RuntimeError, match="Synthetic interruption at stage 9"):
        pipeline_fail.run(docx_file, task_id="task_resume_01", tenant_id="tenant_audit")

    # 3. 重新创建干净的执行器并恢复同任务
    pipeline_resumed = ReviewPipeline(st)
    resumed_res = pipeline_resumed.run(docx_file, task_id="task_resume_01", tenant_id="tenant_audit")

    assert resumed_res.status == "COMPLETED"

    # 4. 关键断言：断点恢复绝不丢失关键风险发现与门禁
    resumed_rule_ids = {issue["rule_id"] for issue in resumed_res.issues}
    assert "AMOUNT_CASE_CONSISTENCY" in resumed_rule_ids, (
        f"恢复后丢失金额大小写一致性检查！resumed rules: {resumed_rule_ids}"
    )
    assert resumed_res.review_gate == normal_res.review_gate, (
        f"恢复后审核门禁发生降级！resumed: {resumed_res.review_gate}, expected: {normal_res.review_gate}"
    )
    assert len(resumed_res.issues) == len(normal_res.issues), (
        f"恢复后 Finding 数量减少！resumed: {len(resumed_res.issues)}, expected: {len(normal_res.issues)}"
    )

    # 验证数据库中持久化的 issues
    db_issues = st.get_issues("task_resume_01")
    db_rule_ids = {issue["rule_id"] for issue in db_issues}
    assert "AMOUNT_CASE_CONSISTENCY" in db_rule_ids
    assert len(db_issues) == len(normal_res.issues)

    st.close()


def test_resume_rejects_corrupted_snapshot(tmp_path: Path):
    """验证快照损坏或缺失必要字段时，明确拒绝恢复，不带残缺上下文继续执行。"""
    samples_dir = tmp_path / "samples"
    generate_all(samples_dir)
    docx_file = samples_dir / "mut_08_amount_case_mismatch.docx"

    db_path = tmp_path / "corrupt_test.db"
    st = Storage(db_path)

    class SyntheticFailStage9(ReviewStage):
        stage_name = "RetrieveEvidence"
        stage_order = 9
        def execute(self, ctx):
            raise RuntimeError("Synthetic interruption at stage 9")

    pipeline_fail = ReviewPipeline(st)
    stage_idx = next(i for i, s in enumerate(pipeline_fail.stages) if s.stage_name == "RetrieveEvidence")
    pipeline_fail.stages[stage_idx] = SyntheticFailStage9()

    with pytest.raises(RuntimeError):
        pipeline_fail.run(docx_file, task_id="task_corrupted", tenant_id="tenant_audit")

    # 人为破坏 Stage 8 的 output_snapshot
    with st._lock:
        st.conn.execute(
            "UPDATE execution_checkpoints SET output_snapshot = '{\"corrupted\": true}' WHERE stage_name = 'DeterministicValidation'"
        )
        st._commit()

    pipeline_resumed = ReviewPipeline(st)
    from contract_kb.application.pipeline.checkpoints import CheckpointCorruptedError
    with pytest.raises(CheckpointCorruptedError, match="missing|corrupted|invalid"):
        pipeline_resumed.run(docx_file, task_id="task_corrupted", tenant_id="tenant_audit")

    st.close()


def test_resume_rejects_manifest_mismatch(tmp_path: Path):
    """验证当运行配置/Manifest 指纹变化时，明确拒绝混用旧快照。"""
    samples_dir = tmp_path / "samples"
    generate_all(samples_dir)
    docx_file = samples_dir / "mut_08_amount_case_mismatch.docx"

    db_path = tmp_path / "manifest_test.db"
    st = Storage(db_path)

    class SyntheticFailStage9(ReviewStage):
        stage_name = "RetrieveEvidence"
        stage_order = 9
        def execute(self, ctx):
            raise RuntimeError("Synthetic interruption at stage 9")

    pipeline_fail = ReviewPipeline(st)
    stage_idx = next(i for i, s in enumerate(pipeline_fail.stages) if s.stage_name == "RetrieveEvidence")
    pipeline_fail.stages[stage_idx] = SyntheticFailStage9()

    with pytest.raises(RuntimeError):
        pipeline_fail.run(docx_file, task_id="task_manifest_diff", tenant_id="tenant_audit")

    # 人为修改已存检查点的 manifest_hash
    with st._lock:
        st.conn.execute(
            "UPDATE execution_checkpoints SET manifest_hash = 'different_manifest_hash_123' WHERE task_id = 'task_manifest_diff'"
        )
        st._commit()

    pipeline_resumed = ReviewPipeline(st)
    from contract_kb.application.pipeline.checkpoints import ManifestMismatchError
    with pytest.raises(ManifestMismatchError, match="manifest|mismatch|incompatible"):
        pipeline_resumed.run(docx_file, task_id="task_manifest_diff", tenant_id="tenant_audit")

    st.close()

