# -*- coding: utf-8 -*-
"""集成测试：流水线最终提交保护与超时/取消终态判定（T07 / F04）。

验证反例修复：
1. 即使前序阶段或第 14 阶段执行完毕，但在最终提交前已超过全局 deadline，
   系统必须判定为 TIMED_OUT，绝不能错误持久化或返回 COMPLETED；
2. 终态为 TIMED_OUT 时，幂等记录绝不能被标记为 COMPLETED；
3. 执行期间收到 cancel 请求后，最终提交前必须被拦截并判定为 CANCELLED。
"""

from __future__ import annotations

from pathlib import Path

from contract_kb.application.pipeline.runner import PipelineRunner
from contract_kb.storage import Storage


def _create_minimal_valid_docx(path: Path) -> Path:
    from docx import Document

    doc = Document()
    doc.add_heading("技术服务合同", level=1)
    doc.add_paragraph("甲方：上海某某科技有限公司")
    doc.add_paragraph("乙方：北京某某信息系统有限公司")
    doc.add_paragraph("第一条 合同金额：人民币 100,000 元整。")
    doc.add_paragraph("第二条 付款期限：合同签订后 30 日内付款。")
    doc.save(str(path))
    return path


def test_final_stage_timeout_prevents_completed_status(tmp_path: Path):
    """即使阶段跑完，若总耗时超过 deadline，最终状态必须为 TIMED_OUT 而非 COMPLETED。"""
    st = Storage(tmp_path / "timeout_guard.sqlite")
    docx_file = _create_minimal_valid_docx(tmp_path / "contract_timeout.docx")

    runner = PipelineRunner(st)

    # 设置极短超时时间 0.001 秒，强制超时
    res = runner.run(
        docx_file,
        mode="STANDARD",
        tenant_id="t1",
        created_by="alice",
        timeout_seconds=0.001,
        idempotency_key="idem_timeout_test_1",
    )

    assert res.status == "TIMED_OUT", f"期望 TIMED_OUT，但实际为 {res.status}"
    assert (
        "timeout" in str(res.reason or "").lower()
        or "deadline" in str(res.reason or "").lower()
    )

    # 验证数据库中持久化的最终状态
    task = st.get_task(res.task_id)
    assert task is not None
    assert task["status"] == "TIMED_OUT"

    # 验证 request_idempotency 表中绝不能是 COMPLETED
    idem_row = st.conn.execute(
        "SELECT status FROM request_idempotency WHERE resource_id = ?",
        (res.task_id,),
    ).fetchone()
    if idem_row:
        assert idem_row["status"] != "COMPLETED", "超时任务绝不能将幂等键写为 COMPLETED"
    st.close()


def test_cancel_before_finalization_prevents_completed(tmp_path: Path):
    """验证在最终提交前若被取消，状态判定为 CANCELLED。"""
    st = Storage(tmp_path / "cancel_guard.sqlite")
    docx_file = _create_minimal_valid_docx(tmp_path / "contract_cancel.docx")

    runner = PipelineRunner(st)

    res = runner.run(
        docx_file,
        mode="STANDARD",
        tenant_id="t1",
        created_by="bob",
        cancel_requested=True,
        idempotency_key="idem_cancel_test_1",
    )

    assert res.status == "CANCELLED"
    task = st.get_task(res.task_id)
    assert task is not None
    assert task["status"] == "CANCELLED"
    st.close()
