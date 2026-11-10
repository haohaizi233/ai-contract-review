# Phase 16-S-R1: 06 - 独立 Web Nonce Store 显式注入与生产环境持久化防重放

## 一、审计缺陷诊断 (P1-R1-07)

在上一轮 Phase 16-S 实现中，防重放 Nonce 存储 `InMemoryAndDbNonceStore` 存在如下隐式加载漏洞：
```python
if st is None:
    api_mod = sys.modules.get("contract_kb.api")
    if api_mod is not None:
        st = getattr(api_mod, "get_storage", lambda: None)()
...
elif os.environ.get("CONTRACT_KB_ENV") == "production":
    raise PermissionError("生产环境下持久化 Nonce 存储不可用")
```
**严重风险**：
1. **模块依赖猜测**：当通过命令行启动独立 Web 审阅服务（`python -m tools.contract_diff_web` 或 `python -m contract_kb.interfaces.web.app`）时，`contract_kb.api` 根本不会被加载到 `sys.modules` 中！
2. **生产崩溃**：在配置 `CONTRACT_KB_ENV=production` 时，由于未显式注入 Storage 且 `contract_kb.api` 为空，所有通过企业 HMAC 验签的合法请求均被抛出 `PermissionError("生产环境下持久化 Nonce 存储不可用")`，服务直接瘫痪。
3. **单机伪持久化**：在非生产模式下，由于找不到数据库，系统静默降级为纯内存 Nonce 存储，服务一旦重启或在多进程部署下，攻击者即可实施跨进程、跨重启重放攻击。

---

## 二、Nonce 存储依赖注入架构

本次整改彻底消除了对 `sys.modules` 的猜测，采用显式接口契约与依赖注入：

```text
                  NonceStoreProtocol (Protocol)
                                ▲
                                │ 实现
                    InMemoryAndDbNonceStore
                                │
       ┌────────────────────────┴────────────────────────┐
       ▼                                                 ▼
[内存层: 原子快速防重放]                     [持久化存储层: 跨进程/跨重启防重放]
  • threading.Lock                             • st.record_and_check_nonce(...)
  • tenant:user:nonce 内存哈希                  • 支持 SQLite 与 PostgreSQL
  • 过期自动清理                                • 数据库故障时 Fail-Closed
```

### 显式注入链路

1. **Protocol 契约**：
   在 `contract_kb/interfaces/http/auth.py` 中定义标准 `NonceStoreProtocol(Protocol)` 与全局存储挂载函数 `set_global_web_nonce_store()` / `get_global_web_nonce_store()`。
2. **Web 请求与 Handler 显式持有**：
   `DiffVisualizerHandler` 增加类属性 `_storage_provider` 与 `set_storage_provider`。每次接收 HTTP 请求时，从自身提取受信任 Storage 并显式透传给 `authenticate_web_request(..., storage=st)`。
3. **服务启动引导显式初始化**：
   `create_web_server(host, port, storage=None)` 接受外部 Storage 注入；在 `app.py` 的 `main()` 入口中，引导期显式调用 `create_storage()` 并注入 Web Handler 与 Agent Review Manager。

---

## 三、代码改动定位

1. **`contract_kb/interfaces/http/auth.py`**：
   - 引入 `@runtime_checkable` 的 `NonceStoreProtocol`；
   - `InMemoryAndDbNonceStore` 支持构造函数注入 `storage` 与 `set_storage()`；
   - 彻底移除 `sys.modules.get("contract_kb.api")` 隐式猜测；
   - 数据库抛出异常时严格 Fail-Closed（抛出明确 `PermissionError`，绝不静默放行）；
   - 生产模式下未注入持久化存储严格抛出异常。
2. **`contract_kb/interfaces/web/app.py`**：
   - `DiffVisualizerHandler` 提供 `set_storage_provider` / `get_storage`；
   - `_get_request_identity` 显式透传 `storage=st`；
   - `create_web_server` 与 `main()` 完成生产级存储绑定。

---

## 四、安全测试验证

在 `tests/security/test_phase16_r1_nonce_persistence.py` 中验证：
- `test_nonce_store_production_without_implicit_sys_modules`: 在 `sys.modules` 移除 `contract_kb.api` 且配置 `CONTRACT_KB_ENV=production` 的极端条件下，显式注入的 NonceStore 稳定工作；
- `test_nonce_store_in_memory_replay_rejected`: 同一实例在有效期内重放相同 Nonce 被拦截；
- `test_nonce_store_cross_restart_persistence`: 模拟服务完全重启，新实例接入相同 SQLite 数据库，成功阻断前一实例消费过的 Nonce；
- `test_nonce_store_db_failure_fails_closed`: 数据库抛出 I/O 异常时，Fail-Closed 抛出 `PermissionError`；
- `test_nonce_store_production_without_storage_fails_closed`: 生产模式下缺少存储严格拒绝运行。
