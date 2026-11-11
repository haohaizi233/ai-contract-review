# Phase 16-S-R2: 05 - Agent 执行上下文与 Worker 安全注入报告 (Execution Context)

## 一、整改背景与原缺陷分析

在第一轮审计与历史代码中，后台 Worker 与 Agent 执行上下文交互存在极度危险的降级重试逻辑：
- **不安全参数剥离 (Unsafe Parameter Stripping)**：在 `contract_kb/application/agents/review_sessions.py` 的 Worker 执行循环中，当调用 Agent 的 `review_diff` 或 `review_clause` 抛出 `TypeError`（例如接口版本不一致或方法签名变动）时，历史逻辑为了“保证流程不崩”，竟然通过 `review_kwargs.pop("storage", None)` 和 `review_kwargs.pop("tenant_id", None)` 将核心安全上下文剥离，随后不带任何受控存储和租户上下文地重试调用 Agent！
- **后果**：Agent 失去 Storage 注入后，无法连接受控知识库，可能退化为使用默认无限制的静态兜底规则，或者引发严重的租户上下文丢失与安全边界穿透。

---

## 二、不可降级的安全上下文契约 (Non-Degradable Security Context)

确立以下铁律：
1. **安全参数不可剥离**：`storage` 与 `tenant_id` 是可信 Agent 运行的法定边界凭证，任何调用失败均不得通过削减安全参数来实现降级。
2. **Fail-Closed 异常收口**：若 Agent 接口签名与 Worker 注入参数不匹配，系统直接认定为致命配置/接口错误，立即触发高精度降级熔断 `AGENT_INTERFACE_MISMATCH`，标明法务审查未完成并提示人工介入，严禁伪装完成。

---

## 三、关键代码修改对照

在 `contract_kb/application/agents/review_sessions.py` 中：
```python
except TypeError as te:
    logger.error(
        f"Worker 调用 Agent 接口参数不匹配 (FAIL-CLOSED): {te}。"
        "严禁通过剔除 storage/tenant_id 安全参数降级重试！"
    )
    # 彻底杜绝 review_kwargs.pop("storage") 和 review_kwargs.pop("tenant_id")
    # 触发安全回退标明接口不匹配，严禁无受控上下文重试
    return self.build_high_precision_fallback(
        ann=ann,
        failure_code="AGENT_INTERFACE_MISMATCH",
        detail=f"Agent 签名不兼容安全上下文注入: {te}",
    )
```

---

## 四、验证结论

通过 `test_worker_must_not_retry_without_security_context` 与对抗用例验证：
1. 当底层 Agent 签名抛出 `TypeError` 时，审查会话不再执行任何剔除 `storage` 或 `tenant_id` 的剥离重试。
2. 任务安全熔断为 `AGENT_INTERFACE_MISMATCH`，确保在任何异常情况下，租户强隔离与受控 Storage 依赖注入始终坚固不可破。
