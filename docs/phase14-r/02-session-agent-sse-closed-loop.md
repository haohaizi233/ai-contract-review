# Phase 14-R: 上传、SSE 与法务 Agent 会话闭环体系 (02-session-agent-sse-closed-loop)

## 一、历史缺陷与失联场景 (DEFECT-P0-02)

在历史实现中，Web 工作台存在严重的“会话脱节”隐患：
1. **上传生成全新会话，SSE 仍停留在默认会话**：用户上传新合同后，后台 `/api/diff/upload` 生成了随机 `sess_<sha>` 会话，但前端 `initAgentSSE` 未强制重建长连接，导致 EventSource 仍停留在 `default_session`；
2. **控制操作未传递 Session ID**：用户点击“暂停研判”、“优先处理”、“重审单卡”或“确认研判”等按钮时，前端 `fetch('/api/diff/toggle_pause')` 均未携带当前合同的 `session_id`，导致所有控制信号错发给了 `default_session`，用户实际上传的合同任务处于无人可控的失联状态；
3. **多标签页交叉干扰**：当不同标签页打开不同合同或不同租户数据时，由于没有强类型上下文封装，操作容易发生串线。

---

## 二、整改方案与核心设计

### 2.1 强类型审查会话上下文 (`ReviewSessionContext`)

在领域层 `contract_kb/domain/models/review_identity.py` 中规范化定义了 `ReviewSessionContext`：
- `session_id`：全局唯一会话标识；
- `tenant_id`：受信任租户标识；
- `user_id`：发起用户标识；
- `doc_name`：送审合同规范名称；
- `file_sha256`：合同原件全量 SHA-256 哈希；
- `template_id` / `template_version`：基准母本标识及审批版本；
- `created_at` / `expires_at`：生命周期控制。

所有上传、比对计算、Agent 管理与检查点操作均必须携带此完整上下文。

### 2.2 前端统一通信客户端 (`ReviewApiClient`)

在 Web 前端页面 (`tools/contract_diff_web.py` 内嵌 HTML/JS) 统一构建了 `ReviewApiClient` 模块：

```javascript
const ReviewApiClient = {
  sessionId: (window.INITIAL_DATA && window.INITIAL_DATA.session_id) || 'default_session',
  tenantId: (window.INITIAL_DATA && window.INITIAL_DATA.tenant_id) || 'default',
  userId: (window.INITIAL_DATA && window.INITIAL_DATA.user_id) || 'local_dev_user',

  setSession(sId, tId, uId) {
    if (sId) this.sessionId = sId;
    if (tId) this.tenantId = tId;
    if (uId) this.userId = uId;
  },

  async request(url, options = {}) { ... }
};
```

并通过底层对全局 `window.fetch` 进行透明拦截与请求头注入：
- 任何发往 `/api/*` 的 HTTP 请求，若未显式指定，自动附带 `X-Session-ID: ReviewApiClient.sessionId` 与 `X-Tenant-ID: ReviewApiClient.tenantId`；
- 在用户上传文档、切换模板或加载实测合同时，服务端在返回的 Diff 结构中明确回传 `session_id`、`tenant_id` 与 `user_id`；
- 前端 `renderFullDiff(data)` 立即同步调用 `ReviewApiClient.setSession(...)`；
- `initAgentSSE(true)` 强行关闭旧连接，携带 `?session_id=<current>&tenant_id=<current>` 重建实时事件流，并在 EventSource 对象上绑定 `_forSessionId`，杜绝孤儿连接与状态失联。

---

## 三、闭环流转全链路验证

```text
[用户选择合同上传]
       ↓ POST /api/diff/upload
[服务端计算 SHA256，分配 sess_<hash>，启动后台 Agent 会话]
       ↓ 返回 { session_id: "sess_123", ... }
[前端 ReviewApiClient.setSession("sess_123")]
       ↓ initAgentSSE(force=true)
[建立 EventSource("/api/diff/agent_stream?session_id=sess_123")]
       ↓ 实时接收 card_start, card_complete, all_complete
[用户点击“暂停研判” / “人工确认”]
       ↓ POST 自动携带 Header X-Session-ID: sess_123
[精确控制 sess_123 队列，无任何状态错位与串线]
```

经全量端到端与自动化回归测试，上传、SSE、流控与反馈操作实现 100% 严格一致闭环。
