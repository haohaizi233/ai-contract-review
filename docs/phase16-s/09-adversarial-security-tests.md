# Phase 16-S: 09 - 对抗性安全测试用例集与执行证明 (Adversarial Security Tests)

## 1. 15 个强制对抗场景全覆盖矩阵

| 场景编号 | 对抗场景名称 | 攻击手法 / 异常输入 | 期望防护结果 | 对应测试文件及用例 | 执行状态 |
|---|---|---|---|---|---|
| **Case 01** | 无密钥企业模式 | `DIFF_WEB_MODE=ENTERPRISE` 未配置共享密钥 | Fail-Closed 拒绝启动或 403 阻断所有请求 | `test_web_enterprise_auth_fail_closed.py::test_enterprise_mode_without_secret_fails_closed` | **PASS** |
| **Case 02** | 旧签名重放攻击 | 拦截合法签名与 Nonce，原样发送第二次请求 | 403 拒绝重放，记录 Nonce 已消费 | `test_web_enterprise_auth_fail_closed.py::test_nonce_replay_attack_rejected` | **PASS** |
| **Case 03** | 篡改 Body 攻击 | 保持原有签名不变，修改 POST JSON 正文 | 403 拒绝，正文哈希不匹配阻断 | `test_web_enterprise_auth_fail_closed.py::test_body_tampering_rejected` | **PASS** |
| **Case 04** | 同租户跨用户 SSE 窃听 | 用户 B 订阅同租户用户 A 的审核 Session ID | 403 拒绝建立 SSE，阻断事件流窃听 | `test_sse_user_ownership.py::test_cross_user_sse_subscription_blocked` | **PASS** |
| **Case 05** | 跨租户/跨用户任务越权控制 | 用户 B 尝试暂停、跳过或取消用户 A 的审查任务 | PermissionError 阻断，状态保持不变 | `test_sse_user_ownership.py::test_cross_tenant_and_cross_user_task_control_blocked` | **PASS** |
| **Case 06** | 数据库已撤销规则回退 | 数据库中规则标记为 REVOKED，静态字典有同名规则 | 返回空列表，严禁回退静态字典伪造 APPROVED | `test_agent_governed_rules_fail_closed.py::test_revoked_database_rule_never_falls_back_to_static_store` | **PASS** |
| **Case 07** | 推理中途撤销 (TOCTOU) | 规则在检索时生效，在 LLM 生成途中被法务撤销 | 触发警报，标记人工复核，阻断写入缓存 | `test_agent_governed_rules_fail_closed.py::test_toctou_in_flight_rule_revocation_triggers_alarm_and_blocks_cache` | **PASS** |
| **Case 08** | 陈旧缓存复用 | 升级模板版本或规则版本后请求相同合同差异 | 缓存 Key 变更，强制重新走受控推理 | `test_agent_governed_rules_fail_closed.py::test_cache_invalidation_on_template_or_rule_version_change` | **PASS** |
| **Case 09** | 客户端自批训练样本 | 普通用户在反馈请求中提交 `is_approved=True` | 仅写入 `raw_feedback/`，绝不进入训练集 | `test_feedback_approval_authorization.py::test_client_cannot_self_approve_training_data` | **PASS** |
| **Case 10** | 跨租户微调集导出 | Reviewer 调用导出，或租户 A 试图导出全部数据 | 403 阻断无权角色，Admin 导出受限本租户 | `test_feedback_approval_authorization.py::test_sft_export_requires_admin_and_tenant_scoped` | **PASS** |
| **Case 11** | Checkpoint 检查点篡改 | 直接修改检查点文件中的风险等级或建议正文 | HMAC 校验失败，恢复被阻断并返回无效 | `test_checkpoint_key_management.py::test_checkpoint_tamper_detection` | **PASS** |
| **Case 12** | 生产环境默认密钥漏洞 | `CONTRACT_KB_ENV=production` 未配置密钥 | 启动或获取密钥时直接报错终止，绝不使用硬编码默认值 | `test_checkpoint_key_management.py::test_production_mode_requires_explicit_checkpoint_secret` | **PASS** |
| **Case 13** | 外部大模型不可用 | 大模型超时、断网或未配置出境授权 | 安全降级为确定性规则初筛，明确标注 AI 未完成 | `test_agent_governed_rules_fail_closed.py::test_model_failure_gracefully_falls_back_with_explicit_badge` | **PASS** |
| **Case 14** | 数据库查询故障 | 数据库连接池耗尽或抛出异常 | 严格 Fail-Closed 返回空，不伪造合法证据 | `test_agent_governed_rules_fail_closed.py::test_database_error_fails_closed` | **PASS** |
| **Case 15** | 并发重复操作稳定性 | 多线程同时发起暂停、取消与反复重连 SSE | 系统线程安全、无死锁、无异常退出 | `test_sse_user_ownership.py::test_concurrent_repeat_actions_stability` | **PASS** |

## 2. 自动化执行命令与结果证明
```bash
python -m pytest \
  tests/security/test_web_enterprise_auth_fail_closed.py \
  tests/security/test_sse_user_ownership.py \
  tests/security/test_agent_governed_rules_fail_closed.py \
  tests/security/test_feedback_approval_authorization.py \
  tests/security/test_checkpoint_key_management.py -v
```
运行输出：
```text
============================= test session starts =============================
platform win32 -- Python 3.13.17, pytest-9.2.0, pluggy-1.7.0
rootdir: D:\Users\Administrator\Desktop\AI合同
configfile: pyproject.toml
plugins: anyio-4.16.0
collected 19 items

tests/security/test_web_enterprise_auth_fail_closed.py .....             [ 26%]
tests/security/test_sse_user_ownership.py ...                            [ 42%]
tests/security/test_agent_governed_rules_fail_closed.py .....            [ 68%]
tests/security/test_feedback_approval_authorization.py ...               [ 84%]
tests/security/test_checkpoint_key_management.py ...                     [100%]

============================== 19 passed in 2.87s ==============================
```
全部 15 项对抗场景均具备独立的自动化单元与集成测试用例，结果 100% 通过。
