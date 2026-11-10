# Phase 13-R5: Web 审核工作台多会话隔离与并发治理报告 (`07-web-session-isolation.md`)

## 1. 缺陷根因 (P0-D)

改造前，`tools/contract_diff_web.py` 与 `contract_kb/agent_queue_manager.py` 存在以下并发串线隐患：
1. 上传文件统一覆盖写入单一路径 `_UPLOAD_CACHE_DIR / "current_active_review.docx"`，导致多浏览器会话连续上传不同合同时发生文件覆盖。
2. 全局单例 `_CURRENT_DOCX_PATH`、`_CURRENT_TEMPLATE` 与单会话队列导致用户 A 的取消操作或 SSE 订阅直接影响用户 B。
3. Worker 线程在超时跳过（Skip）或重试后缺乏世代号（Generation）隔离，旧线程返回后可能覆盖当前新会话状态。

## 2. 工程级重构方案

### 2.1 会话级物理目录隔离 (`tools/contract_diff_web.py`)
- 引入 `SESSION_CACHE_DIR = _UPLOAD_CACHE_DIR / "sessions"`。
- 每次请求通过 `_extract_session_identity()` 提取 `(session_id, tenant_id, user_id)`，使用 `_sanitize_session_id()` 防止路径穿越。
- 上传文件限制在 `MAX_UPLOAD_BYTES = 25 * 1024 * 1024` (25MB)，并独立保存在 `SESSION_CACHE_DIR / safe_sid / "contract.docx"`。

### 2.2 多会话调度器 (`AgentReviewManager` & `AgentReviewSession`)
- `AgentReviewManager` 维护 `dict[str, AgentReviewSession]`，每个 `session_id` 拥有独立的：
  - `annotations` 列表与 `_work_queue`
  - `_stop_event` 与 `_worker_generation` 世代计数器
  - `_sse_subscribers` 独立广播队列
- **跨租户防护**：当不同 `tenant_id` 尝试访问已有 `session_id` 时，立即抛出 `PermissionError`。

### 2.3 Worker 世代锁与防重复提交 (`contract_kb/agent_queue_manager.py`)
- 每次 `init_or_recover_session` 或 `cancel_session` 均递增 `self._worker_generation += 1`。
- Worker 线程在写回结果前校验 `self._worker_generation == my_generation` 且目标条款尚未被 Watchdog/Skip 标记 `_aiDone = True`，彻底消除僵尸线程写回覆盖。

### 2.4 审查覆盖率指标 (`get_snapshot`)
- 实时统计并返回：`total_clauses`、`reviewed_clauses`、`evidence_verified_clauses`、`unreviewed_clauses`、`budget_exhausted`、`coverage_ratio`。

## 3. 验证测试证据

- `tests/integration/test_agent_session_isolation.py`（3 项测试全部通过）
- `tests/integration/test_agent_worker_recovery.py`（3 项测试全部通过）
- `tests/security/test_agent_cross_tenant_access.py`（2 项测试全部通过）
