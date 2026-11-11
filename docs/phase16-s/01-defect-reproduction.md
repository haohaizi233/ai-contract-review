# Phase 16-S 缺陷复现与台账 (01-defect-reproduction.md)

## 一、复现测试执行纪律

所有缺陷必须在未修改生产代码前通过独立自动化测试进行失败复现，拿到确凿失败证据（Red State）后再行编码修复。

- **测试文件**: `tests/security/test_phase16_reproduce_flaws.py`
- **执行命令**: `python -m pytest tests/security/test_phase16_reproduce_flaws.py -v`
- **执行时间**: 2026-11-10T18:19:59
- **复现结果**: **10 Failed, 0 Passed (100% 确认缺陷)**

```text
============================= test session starts =============================
platform win32 -- Python 3.13.17, pytest-9.2.0, pluggy-1.7.0
rootdir: D:\Users\Administrator\Desktop\AI合同
configfile: pyproject.toml
collected 10 items

tests\security\test_phase16_reproduce_flaws.py FFFFFFFFFF                [100%]
============================= 10 failed in 1.79s ==============================
```

## 二、缺陷台账与失败证据详解

| 缺陷编号 | 严重度 | 缺陷名称与涉及模块 | 复现测试用例 | 修复前失败证据 (Actual vs Expected) |
|---|---|---|---|---|
| **DEFECT-P0-01** | **P0** | 企业模式未配置共享密钥跳过 HMAC 验签<br>`contract_kb/interfaces/web/app.py` | `test_enterprise_mode_without_secret_must_reject` | `DID NOT RAISE HTTPError`: 请求返回 200放行。当 `DIFF_WEB_MODE=ENTERPRISE` 且 `CONTRACT_KB_AUTH_SHARED_KEY` 未配置时，代码 `if key:` 为假跳过了签名对比。 |
| **DEFECT-P0-02a** | **P0** | Web HMAC 未校验时间戳时效性<br>`contract_kb/interfaces/web/app.py` | `test_expired_auth_timestamp_must_reject` | `DID NOT RAISE HTTPError`: 请求返回 200。使用 2023 年历史时间戳 (`1700000000.0`) 计算的合法签名仍被成功接受，未核验 300s 防重放窗口。 |
| **DEFECT-P0-02b** | **P0** | Web HMAC 未校验 Nonce 防重放<br>`contract_kb/interfaces/web/app.py` | `test_replayed_nonce_must_reject` | `DID NOT RAISE HTTPError`: 相同 Nonce 第二次请求仍然返回 200。服务端未对 Nonce 进行记录和唯一性核验。 |
| **DEFECT-P0-02c** | **P0** | Web HMAC 未核验请求正文原始哈希完整性<br>`contract_kb/interfaces/web/app.py` | `test_request_body_hash_mismatch_must_reject` | `DID NOT RAISE HTTPError`: 请求正文被篡改为攻击载荷，Header 中的 SHA256 与实际正文不符，服务端仍返回 200。 |
| **DEFECT-P0-03** | **P0** | Agent SSE 订阅与会话缺少用户所有权校验<br>`contract_kb/application/agents/review_sessions.py` | `test_cross_user_sse_subscription_must_reject` | `DID NOT RAISE PermissionError`: 同租户 User B 可以直接调用 `subscribe_events` 或 `get_or_create_session` 订阅 User A 的会话；传入 `user_id="anonymous"` 绕过用户所有权。 |
| **DEFECT-P0-04** | **P0** | 法务 Agent 静态规则兜底绕过数据库撤销状态<br>`contract_kb/application/agents/knowledge_adapter.py` | `test_revoked_database_rule_must_not_reappear_from_static_fallback` | `AssertionError: assert 1 == 0`: 数据库中规则被全部撤销（返回空 APPROVED 规则），`retrieve_governed_rules` 却静默回退至静态 `GOVERNED_RULES_STORE`，使得已撤销规则作为 APPROVED 重新出现。 |
| **DEFECT-P1-01** | **P1** | 用户反馈身份字段被伪造且客户端自批准训练样本<br>`contract_kb/interfaces/web/routes/feedback.py` | `test_client_cannot_self_approve_training_sample` | `AssertionError: assert 2 == 1`: 普通反馈接口接收客户端自报的 `is_approved: true` 后，直接将其持久化到了 `reviewed_feedback/reviewed_events.jsonl`，普通用户可直接将样本批准入训练集。 |
| **DEFECT-P1-02** | **P1** | SFT 数据导出接口未认证且未校验角色<br>`contract_kb/interfaces/web/routes/feedback.py` | `test_training_export_requires_authorized_role` | `DID NOT RAISE HTTPError`: 匿名客户端直接 POST `/api/diff/export_training_data` 成功返回 200，并导出了全部数据。 |
| **DEFECT-P1-03** | **P1** | 生产环境下 Checkpoint 仍使用公开固定默认密钥<br>`contract_kb/infrastructure/checkpoints/storage.py` | `test_production_checkpoint_requires_secret` | `DID NOT RAISE RuntimeError`: 生产环境 (`CONTRACT_KB_ENV=production`) 下缺少环境变量密钥时，系统仍使用硬编码的 `"contract_kb_checkpoint_integrity_secret_key_v14"` 成功保存检查点。 |
| **DEFECT-P1-04** | **P1** | 企业模式未认证 GET / 响应泄露活动合同 Diff 数据<br>`contract_kb/interfaces/web/app.py` | `test_unauthenticated_root_page_does_not_leak_active_contract` | `AssertionError: assert 'mut_11' not in html and '违约金' not in html`: 企业模式下未认证访问 GET `/`，返回的 HTML 直接内嵌了全局活动合同中的违约金条款与变动批注。 |
