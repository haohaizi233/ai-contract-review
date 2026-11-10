# Phase 16-S: 02 - Web 企业认证与防重放机制 (Authentication and Replay Defense)

## 1. 对应实际代码路径
- `contract_kb/interfaces/http/auth.py`
  - `AuthenticatedPrincipal`: 认证主体结构体
  - `RequestAuthenticationContext`: 请求认证上下文
  - `InMemoryAndDbNonceStore`: 具备线程安全、TTL 清理与数据库唯一索引联动的 Nonce 去重存储器
  - `authenticate_web_request()`: 统一 Web HTTP 认证核心函数
  - `compute_auth_signature_v2()`: HMAC-SHA256 规范化验签算法
- `contract_kb/interfaces/web/app.py`
  - `DiffVisualizerHandler._get_request_identity()`: 统一认证与 RBAC 权限拦截
  - `DiffVisualizerHandler.get_request_body()`: 单次读取、长度检查与哈希缓存
  - `create_web_server()`: 生产/开发模式监听地址绑定安全门禁
- `contract_kb/interfaces/web/routes/*.py`: 消费 `handler.get_request_body()` 原始字节

## 2. 原始问题 (P0-01 & P0-02)
1. **企业模式无密钥绕过 (P0-01)**：
   当 `DIFF_WEB_MODE=ENTERPRISE` 时，若未设置 `CONTRACT_KB_AUTH_SHARED_KEY`，旧代码执行 `key = os.environ.get("CONTRACT_KB_AUTH_SHARED_KEY", "")`，若 `key` 为空，跳过 HMAC 验签分支，导致任何请求可免密放行。
2. **签名验证缺少时间戳、Nonce 与 Body 校验 (P0-02)**：
   旧 Web Handler 未校验 `X-Auth-Timestamp` 新鲜度窗口（测试使用 2023 年过期时间戳 `1700000000.0` 仍返回 200）；未对 `X-Auth-Nonce` 判重，允许同一合法签名无限次重放；未比对实际读取的正文与 `X-Content-SHA256`。

## 3. 修复方案 (Security by Construction + Fail-Closed)
1. **企业模式缺密钥直接阻断 (Fail-Closed)**：
   在 `authenticate_web_request` 中，若处于企业模式但共享密钥为空或长度过短，无条件抛出 `PermissionError("企业模式未配置合法共享密钥 (CONTRACT_KB_AUTH_SHARED_KEY)，拒绝请求")`。
2. **时间戳容差窗口核验**：
   从服务器可信时钟核查 `abs(now - ts) > CONTRACT_KB_AUTH_MAX_SKEW_SECONDS`（默认 300 秒），任何解析错误、非法数值、未来溢出或超时均抛出 `PermissionError` (403)。
3. **原子 Nonce 去重与防重放**：
   引入 `InMemoryAndDbNonceStore`，在有效 TTL 窗口内记录 `(tenant_id, user_id, nonce)`。相同 Nonce 第二次请求立即拦截。若连接数据库，通过数据库唯一约束实现多实例防重放。
4. **请求正文完整性校验**：
   认证层通过 `get_request_body()` 单次读取请求原始字节，强制校验 `SHA256(raw_bytes) == header["X-Content-SHA256"]`。篡改 Body 或伪造哈希直接 403 阻断。
5. **集中式 RBAC 路由矩阵**：
   映射所有 Web API 到 `ROLE_VIEWER`、`ROLE_REVIEWER`、`ROLE_ADMIN`，在 Handler 入口统一拦截越权调用。

## 4. 测试用例
- `tests/security/test_web_enterprise_auth_fail_closed.py`
  - `test_enterprise_mode_without_secret_fails_closed`
  - `test_nonce_replay_attack_rejected`
  - `test_body_tampering_rejected`
  - `test_timestamp_skew_tolerance_rejected`
  - `test_rbac_viewer_blocked_from_mutating_actions`
- `tests/security/test_web_identity_authorization.py`
  - `test_enterprise_mode_signature_verification`

## 5. 实际执行命令与结果
```bash
python -m pytest tests/security/test_web_enterprise_auth_fail_closed.py -v
```
运行结果：
```text
tests/security/test_web_enterprise_auth_fail_closed.py::test_enterprise_mode_without_secret_fails_closed PASSED
tests/security/test_web_enterprise_auth_fail_closed.py::test_nonce_replay_attack_rejected PASSED
tests/security/test_web_enterprise_auth_fail_closed.py::test_body_tampering_rejected PASSED
tests/security/test_web_enterprise_auth_fail_closed.py::test_timestamp_skew_tolerance_rejected PASSED
tests/security/test_web_enterprise_auth_fail_closed.py::test_rbac_viewer_blocked_from_mutating_actions PASSED
============================== 5 passed in 1.13s ==============================
```

## 6. 残余风险与操作边界
- 单机内存 Nonce 存储依赖服务器内存，服务冷重启后 300s 内的旧 Nonce 在单机模式下若无数据库可能出现重放窗口。生产环境必须配置共享数据库 (`CONTRACT_KB_DB_URL`) 启用持久化 Nonce 表。
- 密钥泄露风险：企业模式依赖 `CONTRACT_KB_AUTH_SHARED_KEY`，运维必须定期通过密钥轮换机制安全轮换。
