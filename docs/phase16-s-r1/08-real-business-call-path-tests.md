# Phase 16-S-R1: 08 - 真实业务调用链路测试与假阳性消除分析

## 一、测试哲学与核心原则

在上一轮 Phase 16-S 中，诸多“看似通过”的测试实际上存在致命的假阳性与脱节问题。
本次 Phase 16-S-R1 严格贯彻 **“Test the Actual Call Path + Fail-Closed by Default”** 核心准则：

1. **拒绝过度 Mock 底层 Agent**：禁止直接调用 `LegalReviewAgent.review_clause()` 并注入 `MagicMock` 就宣称全链路通过；必须从 Web HTTP 或 `AgentReviewManager.start_document_review()` 入口发起，由真实工作线程执行。
2. **消灭虚假断言**：禁止使用不相关的字段断言测试结论（如原自审批测试断言未写入的 `doc_id`）；必须逐行解析真实 JSON/JSONL 数据结构并核验实际主键。
3. **覆盖真实数据库实现**：核心集成测试全部使用真实 SQLite 数据库（磁盘或 `:memory:`），验证真实 SQL 表结构、触发器与并发事务行为。

---

## 二、新旧测试对比表

| 测试场景 | 上一轮存在缺陷的测试方式 | 本次 R1 真实业务链路测试方案 | 闭环效果 |
|---|---|---|---|
| **Worker 存储注入 (P0-R1-01)** | 直接调用 `agent.review_clause(..., storage=mock_storage)`，未走 Worker 线程 | 调用 `AgentReviewManager.start_document_review()`，真实线程启动并提取注入的 Storage (`tests/integration/test_phase16_r1_worker_storage_injection.py`) | 证实 Worker 真正持有并使用了受信任 Storage |
| **规则真实核验 (P0-R1-02)** | 仅断言 `approval_status == APPROVED` 即期望 `VERIFIED` | 针对无来源、跨租户、已废止、已过期的规则分别测试，核验返回 `INSUFFICIENT_PROVENANCE` 等非 VERIFIED 状态 (`tests/security/test_phase16_r1_citation_verification.py`) | 杜绝空头审批规则冒充真实凭证 |
| **缓存失效与 Fail-Closed (P0-R1-03/04)** | 数据库异常被 `except Exception: pass` 吞掉，测试未覆盖异常场景 | 注入模拟数据库连接池中断与读写异常，断言缓存立即作废 (`from_cache=False`)，TOCTOU 降级为 `NEEDS_HUMAN_REVIEW` 且不入缓存 | 杜绝故障时放行陈旧脏结果 |
| **训练数据自审批 (P1-R1-05/06)** | 断言 `assert fake_doc not in content`，而模型根本不存 `doc_id`，导致假阳性通过 | 逐行反序列化 `reviewed_events.jsonl`，比对真实 `task_id` 和 `annotation_id`；并新增变异测试验证阻断生效 (`tests/security/test_feedback_approval_authorization.py`) | 杜绝客户端自批数据进入训练集 |
| **生产持久化 Nonce (P1-R1-07)** | 依赖 `sys.modules.get("contract_kb.api")`，在独立运行测试中未覆盖生产模式 | 在移除 `contract_kb.api` 且配置 `CONTRACT_KB_ENV=production` 下执行，测试跨实例重启防重放与 DB 故障 Fail-Closed (`tests/security/test_phase16_r1_nonce_persistence.py`) | 验证独立 Web 服务生产级健壮性 |
| **未认证首页防泄露 (P1-R1-08)** | 仅断言活动合同置空，未核查静态模板与素材库案例 | 真实发起 HTTP `GET /`，断言 `initial_cases` 与 `allTemplates` 均为空 (`tests/security/test_phase16_r1_homepage_data_leak.py`) | 杜绝商业机密与敏感元数据泄露 |

---

## 三、真实端到端 (E2E) 调用链贯通验证

在 `tests/e2e/test_phase16_r1_full_authenticated_workflow.py` 中验证了完整的生产级业务调用链：

```text
企业客户端 (HMAC-SHA256 签名 + 时间戳 + Nonce)
       ↓
HTTP 验签中间件 (authenticate_web_request + Storage Nonce 消费)
       ↓
创建/启动审阅任务 (AgentReviewManager + Storage 注入)
       ↓
后台异步执行线程 (Worker 提取 Storage，执行 CitationVerifier 真实核验)
       ↓
任务状态轮询 (/api/diff/agent_status，持续 HMAC 防重放校验)
       ↓
审查结果完成 (completed=1, evidence_references 完备，数据租户完全隔离)
```
全流程耗时 1.55s，无任何 Mock 注入底层 Agent，全面验证真实业务链路。
