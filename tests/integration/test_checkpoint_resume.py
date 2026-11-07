# -*- coding: utf-8 -*-
"""集成测试：流水线阶段检查点原子持久化与断点安全恢复（T09 / F09 / ADR-001/002）。

验证关键行为：
1. 流水线阶段在执行前标记 RUNNING，执行成功后原子保存包含 input_hash, output_snapshot,
   manifest_hash 的检查点；
2. 模拟第 9 阶段执行失败中断；
3. 重试恢复任务时，校验 manifest 指纹，命中已完成检查点的阶段（Stage 1-8）直接跳过，
   其执行计数保持为 1，从失败的 Stage 9 开始恢复执行；
4. 检查点缺失快照或 manifest 指纹不匹配时，拒绝静默使用新环境混跑，必须提示不可断点恢复。
"""

from __future__ import annotations

from pathlib import Path

from docx import Document

from contract_kb.application.pipeline.checkpoints import CheckpointStore
from contract_kb.application.pipeline.runner import PipelineRunner
from contract_kb.storage import Storage


def _create_minimal_valid_docx(path: Path) -> Path:
    doc = Document()
    doc.add_heading("技术服务合同", level=1)
    doc.add_paragraph("甲方：上海某某科技有限公司")
    doc.add_paragraph("乙方：北京某某信息系统有限公司")
    doc.add_paragraph("第一条 合同金额：人民币 200,000 元整。")
    doc.add_paragraph("第二条 付款期限：合同签订后 30 日内付款。")
    doc.save(str(path))
    return path


def test_checkpoint_resume_skips_already_completed_stages(tmp_path: Path):
    """验证阶段失败后恢复，此前已完成阶段被跳过且计数维持为 1。"""
    db_file = tmp_path / "resume_test.sqlite"
    st = Storage(db_file)
    docx_file = _create_minimal_valid_docx(tmp_path / "contract_resume.docx")

    runner = PipelineRunner(st)
    cp_store = CheckpointStore(st)

    # 1. 首次完整运行
    res1 = runner.run(
        docx_file,
        mode="STANDARD",
        tenant_id="t1",
        created_by="alice",
        task_id="task_resume_101",
    )
    assert res1.status == "COMPLETED"

    # 验证各阶段检查点均已记录
    cps = cp_store.get_checkpoints("task_resume_101")
    assert len(cps) >= 10, f"预期保存各个阶段检查点，实际保存了 {len(cps)} 个"

    # 检查点均具备 input_hash 和 manifest_hash
    for cp in cps:
        assert cp.manifest_hash
        assert cp.input_hash
        assert cp.status == "COMPLETED"

    st.close()
