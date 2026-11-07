# -*- coding: utf-8 -*-
"""集成测试：执行截止时间 (Deadline) 与预算预留 (Budget) 在 LLM 与流水线中的真实执行约束 (R05).

验证：
1. 剩余截止时间动态透传给 Provider，截止时间到达/负值时立即快速失败，禁止发起网络连接。
2. 调用前严格通过 BudgetLedgerService 预留额度，若预算耗尽，外部网络调用被绝对拦截（调用次数为0），
   并生成明确的 BUDGET_EXCEEDED / NOT_CHECKED finding。
3. 成功调用精准核销 TokenUsage；超时调用转入 UNCERTAIN 防重入挂账。
"""

from __future__ import annotations

import http.server
import threading
import time
from decimal import Decimal
from pathlib import Path

import pytest

from contract_kb.application.pipeline.deadline import ExecutionDeadline
from contract_kb.application.pipeline.runner import ReviewPipeline
from contract_kb.application.pipeline.stages import LLMSemanticReviewStage
from contract_kb.application.workflow.budget import (
    BudgetLedgerService,
    ReservationStatus,
)
from contract_kb.domain.models.review import (
    CheckStatus,
    FindingSource,
    PipelineContext,
)
from contract_kb.llm_review import (
    MockLLMProvider,
    OpenAICompatProvider,
    LLMSemanticReviewer,
)
from contract_kb.storage import Storage


class _SlowHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        # 故意休眠 3 秒，模拟慢请求
        time.sleep(3.0)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            b'{"choices":[{"message":{"content":"{\\"findings\\":[],\\"custom_requirement_results\\":[]}"}}]}'
        )

    def log_message(self, format, *args):
        pass  # 静默测试输出


@pytest.fixture(scope="module")
def slow_http_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _SlowHandler)
    port = server.server_port
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def test_openai_compat_provider_deadline_fast_timeout(slow_http_server):
    """验证超时时间小于请求时延时，urllib 必须在指定超时时间内快速超时，不得阻塞默认的 60 秒。"""
    provider = OpenAICompatProvider(
        base_url=slow_http_server,
        api_key="sk-test-token",
        model="gpt-test",
    )

    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        # 传递 0.3s 超时（远小于服务端的 3.0s 延迟）
        provider.complete(system="sys", user="usr", timeout=0.3)
    elapsed = time.monotonic() - t0

    # 必须在 1.5s 内返回（通常 ~0.3-0.5s），证明 timeout 生效
    assert elapsed < 1.5, f"Timeout 未及时生效，耗时: {elapsed:.3f}s"


def test_openai_compat_provider_zero_or_negative_timeout_skips_network():
    """验证 timeout <= 0 时立即抛错，完全不发起网络连接。"""
    # 假地址，如果发起了网络请求会抛 ConnectionRefusedError 或 DNS 错误
    provider = OpenAICompatProvider(
        base_url="http://127.0.0.1:59999",
        api_key="sk-test",
        model="gpt-test",
    )

    t0 = time.monotonic()
    with pytest.raises(TimeoutError) as exc_info:
        provider.complete(system="sys", user="usr", timeout=-0.1)
    elapsed = time.monotonic() - t0

    assert elapsed < 0.1
    assert "截止时间" in str(exc_info.value) or "timeout" in str(exc_info.value).lower()


def test_llm_stage_pre_invocation_budget_exhaustion_blocks_call(tmp_path: Path):
    """验证预算耗尽时，LLM 调用前被阻断，Provider.complete 次数为 0，产出 NOT_CHECKED finding。"""
    storage = Storage(":memory:")
    mock_provider = MockLLMProvider()

    # 仅允许 100 token，而审核通常预估需要 > 500 token
    tiny_budget = BudgetLedgerService(
        max_tokens=100,
        max_cost_usd=Decimal("0.0001"),
    )

    stage = LLMSemanticReviewStage(
        llm_provider=mock_provider,
        budget_service=tiny_budget,
    )

    ctx = PipelineContext(
        docx_path=tmp_path / "test.docx",
        clauses=[{"clause_id": "c1", "original_text": "甲方支付服务费 10 万元整。"}],
        rag_context=[],
        mode="STANDARD",
    )

    result = stage.execute(ctx)

    # 关键断言：
    # 1. 外部 Provider 绝对没有被调用
    assert len(mock_provider.calls) == 0, "预算耗尽时禁止调用 LLM Provider"

    # 2. 状态标记为 NOT_CHECKED / failure=BUDGET_EXCEEDED
    assert result["llm_status"] == "NOT_CHECKED"
    assert result.get("failure") == "BUDGET_EXCEEDED"

    # 3. 产出明确的 BUDGET_EXCEEDED 审计 finding
    budget_findings = [
        f for f in ctx.findings if f.category == "BUDGET_EXCEEDED"
    ]
    assert len(budget_findings) >= 1
    assert budget_findings[0].check_status == CheckStatus.NOT_CHECKED.value
    assert budget_findings[0].finding_source == FindingSource.LLM_SEMANTIC.value
    assert budget_findings[0].requires_human_review is True


def test_llm_stage_budget_reservation_settlement_on_success(tmp_path: Path):
    """验证正常调用成功后，精准结算 TokenUsage，结余退回可用池。"""
    canned_response = {
        "result": {
            "findings": [
                {
                    "finding_id": "f1",
                    "category": "PAYMENT",
                    "severity": "LOW",
                    "title": "付款条款合规",
                    "description": "无明显违约风险",
                    "original_text": "甲方支付服务费 10 万元整。",
                    "suggestion": "",
                    "requires_human_review": False,
                }
            ],
            "custom_requirement_results": [],
        },
        "usage": {
            "prompt_tokens": 150,
            "completion_tokens": 50,
            "tokens": 200,
        },
    }
    mock_provider = MockLLMProvider(canned=canned_response)

    budget_service = BudgetLedgerService(
        max_tokens=5000,
        max_cost_usd=Decimal("1.0000"),
        price_per_1k_tokens_usd=Decimal("0.0020"),
    )

    stage = LLMSemanticReviewStage(
        llm_provider=mock_provider,
        budget_service=budget_service,
    )

    ctx = PipelineContext(
        docx_path=tmp_path / "test.docx",
        clauses=[{"clause_id": "c1", "original_text": "甲方支付服务费 10 万元整。"}],
        rag_context=[],
        mode="STANDARD",
    )

    stage.execute(ctx)

    # Provider 正常调用 1 次
    assert len(mock_provider.calls) == 1

    # 验证账本结算：实际消耗 200 tokens (150+50)
    assert budget_service._consumed_tokens == 200
    assert budget_service._reserved_tokens == 0
    assert budget_service.get_available_tokens() == 5000 - 200

    # 验证预留单据状态变为 SETTLED
    res_list = list(budget_service._reservations.values())
    assert len(res_list) == 1
    assert res_list[0].status == ReservationStatus.SETTLED


def test_llm_stage_timeout_marks_budget_uncertain(tmp_path: Path):
    """验证 LLM 发生超时时，预留凭据转入 UNCERTAIN，防止挂账被静默释放并透支。"""
    mock_provider = MockLLMProvider(canned={"mode": "timeout"})

    budget_service = BudgetLedgerService(
        max_tokens=5000,
        max_cost_usd=Decimal("1.0000"),
    )

    stage = LLMSemanticReviewStage(
        llm_provider=mock_provider,
        budget_service=budget_service,
    )

    ctx = PipelineContext(
        docx_path=tmp_path / "test.docx",
        clauses=[{"clause_id": "c1", "original_text": "甲方支付服务费 10 万元整。"}],
        rag_context=[],
        mode="STANDARD",
    )

    result = stage.execute(ctx)
    assert result["llm_status"] == "FAILED"
    assert result.get("failure") == "TIMEOUT"

    # 验证预留状态转为 UNCERTAIN
    res_list = list(budget_service._reservations.values())
    assert len(res_list) == 1
    assert res_list[0].status == ReservationStatus.UNCERTAIN
    assert budget_service._uncertain_tokens > 0
    assert budget_service._reserved_tokens == 0


def test_pipeline_e2e_budget_exhaustion_interception(tmp_path: Path):
    """端到端验证：ReviewPipeline 在配置微小预算时，LLM 阶段拦截网络调用并输出 AUDIT finding。"""
    from docx import Document

    docx_file = tmp_path / "contract.docx"
    doc = Document()
    doc.add_heading("技术开发合作协议", 0)
    doc.add_paragraph("第一条 费用及支付方式")
    doc.add_paragraph("甲方应于合同签订之日起 5 日内向乙方支付技术服务费人民币 100,000 元整。")
    doc.save(docx_file)

    storage = Storage(":memory:")
    mock_provider = MockLLMProvider()
    tiny_budget = BudgetLedgerService(
        max_tokens=50,
        max_cost_usd=Decimal("0.00005"),
    )

    pipeline = ReviewPipeline(
        storage=storage,
        llm_provider=mock_provider,
        budget_service=tiny_budget,
    )

    result = pipeline.run(
        docx_path=str(docx_file),
        mode="STANDARD",
        tenant_id="test_tenant",
    )

    assert result.status == "COMPLETED"
    assert len(mock_provider.calls) == 0, "Pipeline 执行中预算不足禁止调用 LLM"

    # 验证最终落库的审核记录中包含 BUDGET_EXCEEDED finding
    task = storage.get_task(result.task_id)
    assert task is not None
    findings = storage.get_issues(result.task_id)
    budget_issues = [f for f in findings if f.get("category") == "BUDGET_EXCEEDED"]
    assert len(budget_issues) >= 1
    assert budget_issues[0]["check_status"] in (
        CheckStatus.NOT_CHECKED.value,
        CheckStatus.INSUFFICIENT_EVIDENCE.value,
    )

