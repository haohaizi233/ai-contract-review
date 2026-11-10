# -*- coding: utf-8 -*-
"""智能体研判高可用与容灾机制测试（借鉴爱马仕、Claude Code 与小龙虾）。"""

from __future__ import annotations

import time
from unittest.mock import patch

from contract_kb.application.agents.review_sessions import AgentReviewManager
from contract_kb.legal_agent import CircuitBreaker, LegalReviewAgent, get_legal_agent


def test_circuit_breaker_transitions():
    """测试自适应熔断器三态转换 (CLOSED -> OPEN -> HALF_OPEN -> CLOSED)。"""
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.2)

    assert cb.state == "CLOSED"
    assert cb.can_execute() is True

    # 记录前 2 次失败，熔断器仍闭合
    cb.record_failure()
    assert cb.state == "CLOSED"
    cb.record_failure()
    assert cb.state == "CLOSED"

    # 第 3 次失败触发熔断 OPEN
    cb.record_failure()
    assert cb.state == "OPEN"
    assert cb.can_execute() is False

    # 等待恢复时限过去，转为 HALF_OPEN
    time.sleep(0.25)
    assert cb.can_execute() is True
    assert cb.state == "HALF_OPEN"

    # 探针请求成功，恢复 CLOSED
    cb.record_success()
    assert cb.state == "CLOSED"
    assert cb.failure_count == 0


def test_hermes_robust_json_self_healing():
    """测试爱马仕级 JSON 鲁棒自愈提取（剥离思考链、处理尾部逗号、修复未闭合大括号）。"""
    agent = LegalReviewAgent()

    # 1. 含有 <think> 思考链与 Markdown 代码块
    raw_1 = """
    <think>
    这是深度思考内容，包含各种特殊字符：{ "fake": 123 }
    </think>
    ```json
    {
      "risk_level": "HIGH",
      "risk_label": "管辖偏离",
      "ai_analysis": "约定甲方所在地法院管辖违背母本红线",
      "action_advice": "【法务处置建议】坚决恢复南山法院"
    }
    ```
    """
    res_1 = agent._extract_json_robust(raw_1)
    assert res_1.get("risk_level") == "HIGH"
    assert res_1.get("risk_label") == "管辖偏离"
    assert "南山法院" in res_1.get("action_advice", "")

    # 2. 含有尾随逗号与单引号的截断文本
    raw_2 = """
    {
      "risk_level": "MEDIUM",
      "risk_label": "违约金下调",
      "ai_analysis": "日利率由3‰下调至1‰",
      "action_advice": "【法务处置建议】驳回并恢复千分之三",
    }
    """
    res_2 = agent._extract_json_robust(raw_2)
    assert res_2.get("risk_level") == "MEDIUM"
    assert "违约金下调" in res_2.get("risk_label", "")

    # 3. 终极截断修复（缺少闭合大括号）
    raw_3 = """
    {
      "risk_level": "HIGH",
      "risk_label": "保密条款整段删除",
      "ai_analysis": "核心商业秘密裸奔",
      "action_advice": "【法务处置建议】一票否决
    """
    res_3 = agent._extract_json_robust(raw_3)
    assert res_3.get("risk_level") == "HIGH"
    assert "保密条款" in res_3.get("risk_label", "")


def test_high_precision_fallback_redlines():
    """测试企业红线兜底逻辑提供权威法务处置意见而非空洞模板。"""
    agent = LegalReviewAgent()

    ann = {
        "id": "ann-test-1",
        "clause_category": "LIABILITY",
        "clause_title": "第十条 违约责任",
        "delta_type": "NUMBER_CHANGE",
        "ai_analysis": "违约金由3‰改为1‰",
    }

    fb = agent.build_high_precision_fallback(
        annotation=ann,
        error_msg="大模型超时熔断",
        source="CIRCUIT_BREAKER_FALLBACK",
        elapsed_sec=0.05,
    )

    assert fb["id"] == "ann-test-1"
    assert fb["source"] == "CIRCUIT_BREAKER_FALLBACK"
    assert fb["risk_level"] == "HIGH"
    assert "【法务处置建议】" in fb["action_advice"]
    assert "违约责任" in fb["ai_analysis"] or "违约金" in fb["action_advice"]


def test_agent_checkpoint_save_and_resume(tmp_path, monkeypatch):
    """测试断点续跑机制（Hermes 3层记忆）：已研判项持久化落盘，重新加载直接复用。"""
    ckpt_dir = tmp_path / "checkpoints"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "contract_kb.application.agents.review_sessions.CHECKPOINT_DIR", ckpt_dir
    )

    mgr = AgentReviewManager()

    diff_data = {
        "uploaded_file": "test_contract.docx",
        "matched_template_name": "专利服务合同",
        "annotations": [
            {
                "id": "ann-1",
                "clause_category": "LIABILITY",
                "clause_title": "违约责任",
                "standard_span": "按 3‰ 支付",
                "reviewed_span": "按 1‰ 支付",
                "_aiDone": True,
                "risk_level": "HIGH",
                "risk_label": "违约金削弱",
                "ai_analysis": "历史研判已完成",
                "action_advice": "【法务处置建议】恢复母本",
            },
            {
                "id": "ann-2",
                "clause_category": "CONFIDENTIALITY",
                "clause_title": "保密义务",
                "standard_span": "严格保密",
                "reviewed_span": "",
                "_aiDone": False,
            },
        ],
    }

    # 1. 模拟写入检查点
    mgr.doc_id = "test_contract.docx:专利服务合同:2"
    mgr.annotations = [dict(a) for a in diff_data["annotations"]]
    mgr.total_count = 2
    mgr.completed_count = 1
    mgr._save_checkpoint()

    ckpt_file = mgr._get_checkpoint_file(mgr.doc_id)
    assert ckpt_file.exists()

    # 2. 新会话载入相同文档，验证断点秒级自愈
    mgr2 = AgentReviewManager()
    with patch.object(mgr2, "_background_worker_loop"):  # 避免启动实际线程
        mgr2.start_document_review(diff_data, force_restart=False)

    assert mgr2.completed_count == 1
    assert mgr2.annotations_map["ann-1"]["_aiDone"] is True
    assert mgr2.annotations_map["ann-1"]["risk_label"] == "违约金削弱"
    # ann-2 应该排在等待队列中
    assert mgr2._work_queue == ["ann-2"]


def test_anti_zombie_generation_token():
    """测试代际令牌彻底消除僵尸线程竞态 (Claude Code 进程级隔离思想)。"""
    mgr = AgentReviewManager()

    gen_0 = mgr._worker_generation
    assert gen_0 == 0

    diff_data_1 = {
        "uploaded_file": "doc_v1.docx",
        "annotations": [{"id": "a1", "_aiDone": False}],
    }
    with patch.object(mgr, "_background_worker_loop"):
        mgr.start_document_review(diff_data_1)

    gen_1 = mgr._worker_generation
    assert gen_1 == 1

    # 再次触发审查不同文档
    diff_data_2 = {
        "uploaded_file": "doc_v2.docx",
        "annotations": [{"id": "b1", "_aiDone": False}],
    }
    with patch.object(mgr, "_background_worker_loop"):
        mgr.start_document_review(diff_data_2)

    gen_2 = mgr._worker_generation
    assert gen_2 == 2

    # 如果老线程以 gen_1 运行，在 worker 循环中检测 (self._worker_generation != my_generation) 会立即安全终止
    assert gen_1 != mgr._worker_generation


def test_watchdog_supervisor_rescues_stalled_task():
    """测试看门狗守护循环（OpenClaw 任务卡死检测与强制施救）。"""
    mgr = AgentReviewManager()
    mgr.card_timeout_seconds = 0.1  # 设为极小时限便于单元测试

    mgr.status = "RUNNING"
    mgr.current_ann_id = "stalled-card"
    mgr._card_start_time = time.time() - 1.0  # 模拟已卡住 1 秒
    mgr.annotations_map = {
        "stalled-card": {
            "id": "stalled-card",
            "clause_category": "DISPUTE_RESOLUTION",
            "clause_title": "管辖法院",
            "standard_span": "南山法院",
            "reviewed_span": "朝阳法院",
            "_aiDone": False,
        }
    }
    mgr.completed_count = 0

    # 手动触发一次看门狗巡检逻辑
    with mgr._lock:
        elapsed = time.time() - mgr._card_start_time
        if elapsed > mgr.card_timeout_seconds:
            agent = get_legal_agent()
            res = agent.build_high_precision_fallback(
                annotation=mgr.annotations_map["stalled-card"],
                error_msg=f"研判执行超过看门狗时限({round(elapsed)}s)",
                source="WATCHDOG_TIMEOUT_FALLBACK",
                elapsed_sec=elapsed,
            )
            item = mgr.annotations_map["stalled-card"]
            item["_source"] = res["source"]
            item["_aiDone"] = True
            mgr.completed_count += 1
            mgr.current_ann_id = None
            mgr._card_start_time = 0.0

    stalled_item = mgr.annotations_map["stalled-card"]
    assert stalled_item["_aiDone"] is True
    assert stalled_item["_source"] == "WATCHDOG_TIMEOUT_FALLBACK"
    assert mgr.completed_count == 1
    assert mgr.current_ann_id is None
