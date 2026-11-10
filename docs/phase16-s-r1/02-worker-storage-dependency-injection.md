# Phase 16-S-R1: 02 - 法务 Agent 后台 Worker 存储显式依赖注入改造

## 一、架构缺陷诊断与背景 (P0-R1-01)

在上一轮 Phase 16-S 交付中，独立安全审计发现：
Web 异步审阅管理器 `AgentReviewManager` 与 `AgentReviewSession` 的后台工作线程 `_background_worker_loop()` 调用 `agent.review_clause()` 时，未注入受信任知识库存储实例 `storage`。
同时，上一轮测试采用在测试用例中直接调用底层 `agent.review_clause(..., storage=mock_storage)` 的方式绕过真实 Web Worker 调用链，造成严重的“测试与生产运行路径脱节”假阳性问题。

此外，存在身份绕过隐患：通过检查 `user_id == "system_admin"` 字符串对租户隔离进行特权绕过，违反了“可信身份与服务端强鉴权”原则。

---

## 二、目标架构与显式依赖注入链路

本次整改建立了自顶向下的受信任存储依赖注入链：

```text
Application Bootstrap (app.py / create_web_server)
             ↓ [显式注入 Storage 实例/工厂]
     AgentReviewManager (set_storage_provider / get_storage)
             ↓ [创建会话时显式绑定 storage]
     AgentReviewSession (_storage 属性，非序列化持有)
             ↓ [后台 Worker 循环获取 storage 与 tenant_id]
     _background_worker_loop
             ↓ [调用审查方法，注入 storage=storage, tenant_id=self.tenant_id]
     LegalReviewAgent.review_clause
             ↓ [使用受信任知识库回查 APPROVED 生效规则]
     Governed Knowledge Repository (SQLite / PostgreSQL)
```

### 核心设计原则

1. **显式依赖注入 (Explicit Dependency Injection)**：
   - `AgentReviewManager.__init__(storage_provider=None)`
   - 提供 `set_storage_provider(provider)` 与 `get_storage(tenant_id=None)`。
   - `AgentReviewSession` 在构造与 `start_document_review` 时显式绑定 `_storage`。
2. **存储生命周期隔离 (Non-Serialized Storage)**：
   - 数据库连接与 Storage 实例绝对不放入 `self.doc_context`、断点 Checkpoint、JSON 响应或 SSE 广播载荷，防止非序列化对象破坏前后端通讯与任务状态机。
3. **消除字符串身份绕过**：
   - 彻底废除 `user_id == "system_admin"` 字符串硬编码绕过，改用严格类型提示的 `is_admin: bool = False`，必须由企业 HTTP 验签层通过 HMAC 与角色提取（`principal.has_role("admin")`）显式赋予。
4. **生产环境缺失 Storage 门禁 (Fail-Closed)**：
   - 当 `CONTRACT_KB_ENV=production` 且 `storage is None` 时，`LegalReviewAgent` 拒绝使用任何未经权威校验的静态规则，直接触发 `KNOWLEDGE_UNAVAILABLE` 并降级为待人工复核。

---

## 三、代码改动定位

| 文件路径 | 关键改动说明 |
|---|---|
| `contract_kb/application/agents/review_sessions.py` | 增加 `_storage_provider` 注入，`AgentReviewSession` 持有 `_storage`；在 `_background_worker_loop` 中提取 `storage = self.get_storage()` 并注入 `agent.review_clause`；替换 `system_admin` 字符串绕过为 `is_admin: bool`；完善 `start_document_review` 返回 Session 实例。 |
| `contract_kb/interfaces/web/app.py` | `DiffVisualizerHandler` 增加 `set_storage_provider` / `get_storage`，在服务启动 `create_web_server` 与 `main()` 引导期统一注入 `create_storage()` 到管理器与 Web 处理器。 |
| `contract_kb/interfaces/web/routes/reviews.py` | 传递 `storage=handler.get_storage()` 与 `is_admin=principal.has_role("admin")` 至审查管理器。 |
| `contract_kb/interfaces/web/routes/agents.py` | 重试与跳过路由透传 `is_admin` 与 `storage`。 |

---

## 四、真实业务调用链集成测试验证

在 `tests/integration/test_phase16_r1_worker_storage_injection.py` 中建立完整真实集成测试：

1. **`test_worker_receives_injected_storage_and_executes_review`**：
   - 创建真实 SQLite 数据库；
   - 录入真实已审批规则；
   - 调用 `AgentReviewManager.start_document_review()` 启动后台真实工作线程；
   - 轮询等待 Worker 处理，断言 Worker 确实获得注入的 `Storage` 实例，完成条款审查并写入 `evidence_references`。
2. **`test_worker_tenant_isolation_rules_not_leaked`**：
   - 在 `tenant_alpha` 中写入专有仲裁规则；
   - 以 `tenant_beta` 身份发起审查；
   - 验证 Worker 执行审查时严格按 `tenant_beta` 隔离，绝不泄漏 `tenant_alpha` 规则。
