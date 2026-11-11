# Phase 16-S-R2: 07 - 对抗性与变异测试套件报告 (Adversarial & Mutation Suite)

## 一、对抗性测试设计目标

针对 Phase 16-S-R2 确立的 6 项安全治理原则，设计并实现了包含 13 项极具针对性的边界对抗与攻击性测试用例，集中于 `tests/security/test_phase16_r2_adversarial_suite.py` 中。

目标是证明：**在任何恶意篡改、出处伪造、越权审批、竞争失效和系统异常注入场景下，系统均坚定不移地以 Fail-Closed 阻断，绝无静默放行或信任提权**。

---

## 二、13 项对抗性测试用例执行矩阵

| 用例编号 | 用例函数名 | 攻击 / 异常注入场景 | 预期安全阻断防线 | 执行结果 |
|---|---|---|---|---|
| **ADV-01** | `test_adv_01_missing_source_doc_fail_closed` | 伪造不存在的集团红线文档 ID | `CitationVerifier` 判定 `MISSING_SOURCE`，`verified=False` | **PASS** |
| **ADV-02** | `test_adv_02_missing_source_clause_fail_closed` | 伪造不存在的底层条款 ID | `CitationVerifier` 判定 `CLAUSE_NOT_FOUND`，`verified=False` | **PASS** |
| **ADV-03** | `test_adv_03_db_exception_on_clause_fail_closed` | 查验底层条款时人为触发 `sqlite3.DatabaseError` | 严禁吞并异常，Fail-Closed 判定 `SOURCE_UNAVAILABLE` | **PASS** |
| **ADV-04** | `test_adv_04_unregistered_statute_unsupported_provenance` | 仅提供口头法律条文名称（如“民法典第585条”）未提供受控登记凭据 | 严格识别为 `UNSUPPORTED_PROVENANCE`，拒绝放行 | **PASS** |
| **ADV-05** | `test_adv_05_unverified_rule_degraded_in_prompt` | 未通过来源核验的规则构建 Agent 提示词上下文 | 自动降级至 `【非权威参考资料】`，绝不得冠以权威标签 | **PASS** |
| **ADV-06** | `test_adv_06_model_citing_unverified_rule_triggers_human_review` | 大模型推理结论违规引用未核验规则 | 逆向证据检查网关拦截，触发 `NEEDS_HUMAN_REVIEW` 并禁止写缓存 | **PASS** |
| **ADV-07** | `test_adv_07_anti_self_approval_strict_enforcement` | 提交人持有合法审批角色，试图对自己的反馈执行自批 | 严格抛出 `PermissionError`，防自审机制生效阻断 | **PASS** |
| **ADV-08** | `test_adv_08_cross_tenant_approval_strict_blocking` | Tenant Alpha 管理员试图审批 Tenant Beta 的反馈数据 | 严格抛出 `PermissionError`，租户强隔离阻断 | **PASS** |
| **ADV-09** | `test_adv_09_empty_evidence_cache_invalidated_by_new_approved_rule` | 命中旧的空证据缓存后，知识库中新审批了匹配规则 | 缓存立即强制失效，Agent 重新研判并吸收最新规则 | **PASS** |
| **ADV-10** | `test_adv_10_cache_invalidation_db_exception_fails_closed` | 核查空证据缓存有效性时底层数据库抛出异常 | Fail-Closed 触发，拒绝放行旧缓存，强制重算 | **PASS** |
| **ADV-11** | `test_adv_11_worker_rejects_stripping_storage_context` | Worker 遇到 Agent 接口抛出 `TypeError` | 严禁剥离 `storage` 重试，安全熔断为 `AGENT_INTERFACE_MISMATCH` | **PASS** |
| **ADV-12** | `test_adv_12_clause_hash_tamper_fails_closed` | 底层条款正文内容被外部篡改，哈希不符 | `CitationVerifier` 判定 `HASH_MISMATCH`，彻底阻断 | **PASS** |
| **ADV-13** | `test_adv_13_unparseable_expiry_date_fails_closed` | 规则的有效期字段被注入非法格式垃圾字符串 | Fail-Closed 阻断，判定为 `INVALID_VALIDITY_DATE` | **PASS** |

---

## 三、对抗性执行命令与验证证据

```bash
python -m pytest tests/security/test_phase16_r2_adversarial_suite.py -v
```

**运行结果**：
```text
============================== 13 passed in 0.78s ==============================
```
全部 13 项对抗性攻击与异常注入用例 100% 通过，无一遗漏。
