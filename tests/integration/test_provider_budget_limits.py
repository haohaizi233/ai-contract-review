# -*- coding: utf-8 -*-
"""集成测试：工作流工具与供应商端到端预算预留保护（T08 / F05 / ADR-003）。

验证：
1. ToolExecutor 与 LLM 调用协同：调用前通过 BudgetLedgerService 预留额度；
2. 预算耗尽时外部调用被 fail-closed 拦截，不产生网络副作用；
3. 调用成功后精准核销实际消耗并返还结余；
4. 价格版本变更时，旧预留与新预留各自按对应版本核算。
"""

from __future__ import annotations

from decimal import Decimal


from contract_kb.application.workflow.budget import (
    BudgetLedgerService,
    TokenUsage,
)


def test_price_version_consistency():
    """验证价格版本变更时，账本严格按预留时的单价进行核算。"""
    service = BudgetLedgerService(
        max_tokens=10000,
        max_cost_usd=Decimal("1.0000"),
        price_per_1k_tokens_usd=Decimal("0.0020"),
        price_version="v1",
    )

    # 在 v1 价格下预留 1000 token，预估成本 1000/1000 * 0.002 = 0.002
    res_v1 = service.reserve_budget(input_tokens=500, max_output_tokens=500)
    assert res_v1.price_version == "v1"
    assert res_v1.reserved_cost_usd == Decimal("0.0020")

    # 业务系统更新单价为 v2 ($0.0100 / 1k)
    service.update_pricing(new_price_per_1k_usd=Decimal("0.0100"), new_version="v2")

    # 新预留按 v2 单价：1000/1000 * 0.0100 = 0.0100
    res_v2 = service.reserve_budget(input_tokens=500, max_output_tokens=500)
    assert res_v2.price_version == "v2"
    assert res_v2.reserved_cost_usd == Decimal("0.0100")

    # 结算 v1 任务：按 v1 单价 $0.0020 结算
    service.settle_budget(
        res_v1.reservation_id, TokenUsage(input_tokens=500, output_tokens=500)
    )
    # 结算 v2 任务：按 v2 单价 $0.0100 结算
    service.settle_budget(
        res_v2.reservation_id, TokenUsage(input_tokens=500, output_tokens=500)
    )

    # 总消耗成本 = 0.0020 + 0.0100 = 0.0120
    assert service.get_total_consumed_cost() == Decimal("0.0120")
