# ADR-003：采用受控确定性 8 状态机替代自由自治循环 Agent

- **状态**：已采纳（Accepted）
- **日期**：2026-11-07
- **决策者**：首席软件架构师（Principal Architect）

## 1. 背景与问题

在合同法律审核场景中，待审合同正文天然属于外部不可信输入（对手方起草或修改的 DOCX 文档），且审核结论直接关联企业财务结算、知识产权归属与诉讼管辖红线。若引入开放式自治 Agent（由 LLM 自由决定下一步调用哪个工具、循环多少轮、何时结束）：
1. **提示词注入放大风险**：对手方在合同条款中嵌入诱导指令即可劫持 Agent 执行非预期工具调用或跳过关键合规检查。
2. **成本与延迟失控**：复杂长合同容易触发多轮无效重试风暴，导致 P99 延迟与 Token 账单不可预测。
3. **合规不可审计、不可复现**：同一份合同两次运行可能走出不同工具路径，违反企业内控可复现性要求。

## 2. 决策内容

采用显式状态转移表驱动的 **8 状态受控工作流（[`ControlledReviewAgentWorkflow`](file:///d:/Users/Administrator/Desktop/AI合同/contract_kb/application/workflow/state_machine.py)）** + **受控工具执行器（[`ControlledToolExecutor`](file:///d:/Users/Administrator/Desktop/AI合同/contract_kb/application/workflow/tool_executor.py)）**：
1. 状态转移路径由确定性代码静态锁定（`ALLOWED_TRANSITIONS`），LLM 仅在 `SemanticRiskAnalysis` 状态内作为受限分析组件被调用，无权决定流程走向或修改企业规则表。
2. 每次工具调用强制经过 `ToolPermission` 权限校验、Pydantic 输入/输出校验、单次超时限制与 `WorkflowBudget`（`Decimal` 成本上限 + Token 上限 + 调用次数上限）核验。
3. 当 `SemanticRiskAnalysis` 因未授权、超预算或超时失败时，状态机记录 `ToolExecutionAuditRecord` 并生成诚实的 `NOT_CHECKED` 提示，继续完成确定性规则汇总与报告持久化。

## 3. 后果与收益

- 消除了自治死循环与工具越权攻击面，审计日志可精确追踪每个状态下的工具耗时、输入摘要与 Token 成本。
- 在离线无模型或内网隔离环境下，智能体工作流仍可 100% 稳定交付确定性审核报告。
