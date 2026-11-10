# Phase 14-R: Web 工作台信任边界与身份认证规范 (01-trust-boundary-and-authentication)

## 一、架构背景与整改目标

在 Phase 14 之前，系统存在两条不同安全标准的审核链路：
1. **FastAPI 主审核 API**：通过受信任网关注入的 `X-User`、`X-Roles`、`X-Tenant` 及规范化 HMAC-SHA256 V2 签名与防重放时间戳/Nonce 进行强鉴权。
2. **Web Diff 工作台** (`tools/contract_diff_web.py`)：历史实现中 `_get_request_identity()` 允许从客户端请求的 Query 参数或 Header 中无签名单方面声明 `tenant_id` 与 `user_id`，且 CORS 策略默认使用 `Access-Control-Allow-Origin: *`，导致存在跨站请求伪造、身份冒领与多租户越权读取风险。

本次整改彻底消除了双链路安全标准不一致问题，建立起清晰的双模式认证与租户沙箱隔离机制。

---

## 二、双运行模式架构设计

### 2.1 模式 A：单机本地开发模式 (LOCAL_DEV)

- **适用场景**：开发者在本地单机运行 `python tools/contract_diff_web.py` 进行独立调试与比对。
- **运行特征**：
  - 默认监听 `127.0.0.1` 本地回环接口；
  - 严格限制请求者 Host 头必须属于回环白名单（`127.0.0.1`, `localhost`, `::1` 等）；
  - **租户范围锁定**：未认证状态下，系统严格锁定调用者租户为 `default`，用户标识固定为 `local_dev_user`；
  - **防越权拦截**：任何试图通过 Query (`?tenant_id=victim_corp`) 或 Header (`X-Tenant-ID: victim_corp`) 越权指定非 default 租户的未认证请求，一律直接阻断并返回 `HTTP 403 Forbidden`；
  - **跨站写保护 (Anti-CSRF)**：所有变更性请求 (`POST`, `PUT`, `DELETE`) 校验 `Origin` / `Referer`，非本地来源的跨站写操作一律拦截。

### 2.2 模式 B：企业多用户模式 (ENTERPRISE)

- **适用场景**：部署在企业私有云、生产 Kubernetes 或反向代理网关后。
- **触发条件**：环境变量 `DIFF_WEB_MODE=ENTERPRISE` 或配置了共享密钥 `CONTRACT_KB_AUTH_SHARED_KEY`。
- **运行特征**：
  - 必须由受信任企业认证网关注入 `X-User`, `X-Roles`, `X-Tenant`, `X-Auth-Signature`, `X-Auth-Timestamp`, `X-Auth-Nonce`；
  - 统一调用 `compute_auth_signature_v2` 对 `METHOD\nPATH\nQUERY\nUSER\nROLES\nTENANT\nTIMESTAMP\nNONCE\nBODY_SHA256` 进行全载荷规范化校验；
  - 任何缺失签名、时间戳超出倾斜窗口或签名不匹配的请求，均直接返回 `HTTP 403 Forbidden`。

---

## 三、物理存储租户沙箱隔离

为杜绝不同租户或特殊字符导致的目录合流碰撞，会话存储目录结构升级为两级物理隔离：

```text
data/web_sessions/
  ├── <clean_tenant_id>/
  │     ├── <clean_session_id_with_hash>/
  │     │     ├── contract.docx
  │     │     ├── meta.json
  │     │     └── checkpoint.json
```

- **防路径穿越**：通过 `resolve().is_relative_to(SESSION_CACHE_DIR)` 进行物理基准目录越界检测；
- **防字符折叠碰撞**：对含有特殊符号的会话标识追加原字符串 SHA-256 截断尾缀，确保 `sess/1` 与 `sess@1` 不再合流；
- **元数据校验**：每个会话的 `meta.json` 强绑定 `tenant_id`，跨租户读取直接拒绝。

---

## 四、自动化回归验证证据

- 测试文件：`tests/security/test_web_identity_authorization.py`
  - `test_session_dir_collision_and_tenant_isolation_verified`：验证特殊字符去折叠与跨租户目录物理隔离；
  - `test_spoofed_tenant_in_query_or_header_rejected`：验证单机本地模式下越权自报外部租户被 403 阻断；
  - `test_legitimate_local_dev_request_allowed`：验证单机本地合法请求正常放行；
  - `test_csrf_origin_rejected_on_post`：验证恶意外部 Origin 写操作被 403 拦截；
  - `test_enterprise_mode_signature_verification`：验证企业多用户模式下缺失签名 403 阻断与合法 HMAC-SHA256 签名正常放行。
