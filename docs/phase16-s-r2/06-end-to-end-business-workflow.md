# Phase 16-S-R2: 06 - 真实业务调用链路全流程验收报告 (Business Workflow E2E)

## 一、真实业务调用链路概述

为避免过度 Mock 与脱离实际运行环境的假阳性测试，按照 **Test the Actual Call Path** 要求，在 `tests/e2e/test_phase16_r2_full_business_workflow.py` 中构建了跨越 Web API、Worker 调度、知识仓储、法务 Agent、SSE 流式通道与训练集治理的完整闭环业务验收测试。

---

## 二、端到端全链路执行时序 (End-to-End Execution Flow)

```text
1. 企业用户发起认证 HTTP 请求 (HMAC-SHA256 签名 + X-Content-SHA256 载荷校验)
   POST /api/diff/upload
      ↓ (Web 认证网关校验通过，注入 AuthenticatedPrincipal: user_id, tenant_id)
2. 提取母本与送审合同差异 (Diff Engine 计算)
   生成差异点 (e.g. 违约金单方下调，缺少法定保护)
      ↓
3. 会话管理器 (AgentReviewManager) 创建会话并绑定 Storage 与 Tenant
   Session.review_diff(storage=storage, tenant_id=tenant_id)
      ↓
4. 法务 Agent 执行受控审查 (LegalReviewAgent)
   ├─ 检索已审批规则 (retrieve_governed_rules)
   ├─ 来源出处核验 (CitationVerifier) 判定 VERIFIED
   ├─ 注入【企业已审批生效风控基准】权威上下文
   ├─ 模型推理输出法务研判结论
   └─ 逆向证据检查与结论持久化
      ↓
5. Web 客户端通过 SSE 实时接收流式审查结论
   GET /api/diff/agent_stream?session_id=...
      ↓ (客户端消费 SSE 事件流: event: chunk / finding, data: {...})
6. 用户提交人工反馈记录 (User Feedback)
   POST /api/feedback/record
      ↓ (系统强制标记为 PENDING，严禁自批)
7. 防自审拦截验证 (Anti-Self-Approval Enforcement)
   提交人试图调用审批接口 → 严格抛出 PermissionError 阻断！
      ↓
8. 独立法务合规管理员审批 (Dual-Control Approval)
   独立管理员调用审批接口 → 状态跃迁为 APPROVED
      ↓
9. 导出当前租户 SFT 训练集 (SFT Export)
   export_sft_dataset(require_approval=True, tenant_id=tenant_id)
   成功导出已审批样本 (train/eval)
      ↓
10. 跨租户越权阻断 (Cross-Tenant Isolation)
    Tenant B 试图审批或导出 Tenant A 的数据 → 严格阻断！
```

---

## 三、真实调用路径验证矩阵

在 `test_phase16_r2_full_business_workflow.py` 中实现的具体验证指标：

| 阶段 | 接口 / 组件 | 输入特征 | 预期安全与业务表现 | 实测结果 |
|---|---|---|---|---|
| **Auth & Upload** | `POST /api/diff/upload` | 携带有效 HMAC 签名与 Content SHA256 | 返回 HTTP 200，差异点解析成功 | **PASS** |
| **Agent Review** | `LegalReviewAgent.review_clause` | 注入真实 SQLiteStorage 与 Tenant Alpha | 证据链状态标记为 `VERIFIED`，权威依据正确引用 | **PASS** |
| **SSE Stream** | `GET /api/diff/agent_stream` | 真实 Starlette/FastAPI TestClient 发起 GET | 成功接收完整 SSE 事件流（含 finding 详情与完成事件） | **PASS** |
| **Feedback Submit**| `record_user_feedback` | 普通法务初审员提交采纳反馈 | 数据持久化，状态为 `PENDING`，`is_approved=False` | **PASS** |
| **Self-Approval** | `approve_feedback_for_training` | 提交人作为审批人发起自批 | 严格抛出 `PermissionError`，状态保持 `PENDING` | **PASS (阻断)** |
| **Dual Approval** | `approve_feedback_for_training` | 独立合规官 `legal_officer_99` 审批 | 审批成功，状态跃迁为 `APPROVED` | **PASS** |
| **SFT Export** | `export_sft_dataset` | 租户 Alpha 导出已审批数据集 | 成功生成包含合法样本的训练集与评估集 | **PASS** |
| **Cross-Tenant** | `approve_feedback_for_training` | 租户 Beta 试图审批租户 Alpha 样本 | 严格抛出 `PermissionError`，禁止跨租户操作 | **PASS (阻断)** |

---

## 四、验证结论

真实业务调用全链路端到端集成测试全面通过，证实系统在实际 HTTP 接入、多租户上下文隔离、流式结果分发、证据链追溯以及训练数据治理各环节均完全闭环。
