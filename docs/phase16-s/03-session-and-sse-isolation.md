# Phase 16-S: 03 - Agent 会话与 SSE 跨用户隔离 (Session and SSE Isolation)

## 1. 对应实际代码路径
- `contract_kb/application/agents/review_sessions.py`
  - `AgentReviewManager.get_or_create_session(session_id, tenant_id, user_id)`: 强校验会话归属 `(tenant_id, user_id)`
  - `AgentReviewManager.get_session(session_id, tenant_id, user_id)`: 移除 `user_id != 'anonymous'` 绕过逻辑，严格所有权检查
  - `AgentReviewManager.subscribe_events(session_id, tenant_id, user_id)`: SSE 订阅强权属门禁
  - `AgentReviewManager.unsubscribe_events(...)`
  - `AgentReviewSession.start_review()`: 将 `tenant_id` 与 `user_id` 注入 `doc_context`
- `contract_kb/interfaces/web/routes/agents.py`
  - `handle_get_agent_stream`: SSE 路由，捕获 `PermissionError` 返回 403
  - `handle_get_agent_status`, `handle_post_prioritize_card`, `handle_post_toggle_pause`, `handle_post_skip_card`, `handle_post_cancel`: 校验当前已认证主体
- `contract_kb/interfaces/web/routes/reviews.py`: 企业模式下阻断回退至全局 `CURRENT_SESSION_FILE`
- `contract_kb/interfaces/web/app.py`: 企业模式无认证会话时，`window.INITIAL_DATA = null`，杜绝首页泄漏前序用户敏感合同

## 2. 原始问题 (P0-03 & P1-04)
1. **同租户跨用户 SSE 订阅越权 (P0-03)**：
   `get_or_create_session()` 仅检查了 `sess.tenant_id == tenant_id`，没有检查 `sess.user_id == user_id`。`subscribe_events()` 调用该方法，导致同租户的 User B 只要知道 User A 的 `session_id`，即可订阅 User A 的合同审核实时事件，造成合同原文与法务修改建议跨用户泄露。
2. **`user_id="anonymous"` 绕过检查**：
   `get_session()` 历史代码中包含 `if user_id != "anonymous": if sess.user_id != user_id: raise PermissionError`，攻击者只要传入 `user_id="anonymous"` 即可绕过所有权检查。
3. **全局文档与首页泄露 (P1-04)**：
   未认证 `GET /` 会把服务器全局最新合同差异嵌入 HTML `window.INITIAL_DATA`，导致新访客直接看到上一位用户的企业合同。

## 3. 修复方案
1. **联合身份强校验 `(tenant_id, user_id, session_id)`**：
   在 `get_or_create_session` 与 `get_session` 中，统一要求：
   ```python
   if sess.tenant_id != tenant_id:
       raise PermissionError("跨租户越权阻断")
   if user_id != "system_admin" and sess.user_id != user_id:
       raise PermissionError("跨用户越权阻断")
   ```
   彻底消除 `user_id == "anonymous"` 绕过路径。
2. **SSE 订阅前置门禁**：
   `subscribe_events` 与 `unsubscribe_events` 均传递显式 `tenant_id` 与 `user_id`。非会话所有者发起订阅请求立即抛出 `PermissionError`，Web 层返回 403 阻断连接建立。
3. **企业模式禁用全局文件回退**：
   在企业模式下，若请求未携带或未匹配到有效 Session，`reviews.py` 绝不回退至 `CURRENT_SESSION_FILE`，而是返回 404/403；`app.py` 生成首页 HTML 时将 `INITIAL_DATA` 置为 `null`。

## 4. 测试用例
- `tests/security/test_sse_user_ownership.py`
  - `test_cross_user_sse_subscription_blocked`
  - `test_cross_tenant_and_cross_user_task_control_blocked`
  - `test_concurrent_repeat_actions_stability`
- `tests/security/test_phase16_reproduce_flaws.py`
  - `test_cross_user_sse_subscription_must_reject`
  - `test_web_workbench_does_not_leak_initial_contract_in_enterprise_mode`

## 5. 实际执行命令与结果
```bash
python -m pytest tests/security/test_sse_user_ownership.py -v
```
运行结果：
```text
tests/security/test_sse_user_ownership.py::test_cross_user_sse_subscription_blocked PASSED
tests/security/test_sse_user_ownership.py::test_cross_tenant_and_cross_user_task_control_blocked PASSED
tests/security/test_sse_user_ownership.py::test_concurrent_repeat_actions_stability PASSED
============================== 3 passed in 0.58s ==============================
```

## 6. 残余风险
- 若企业需要跨部门法务人员协助审查同一份合同，目前除 `system_admin` 外同租户其他员工无法协作。未来可在会话模型中增加 `authorized_collaborators: list[str]` 授权白名单机制。
