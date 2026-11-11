# Phase 16-S-R2: 00 - 审计基线与红态缺陷复现报告

## 一、审计基线环境

- **项目仓库**：`https://github.com/haohaizi233/ai-contract-review`
- **目标 PR**：[PR #4: feat(phase16-s): security trust boundaries, fail-closed auth, and governed agent](https://github.com/haohaizi233/ai-contract-review/pull/4)
- **基线分支**：`fix/phase16-trust-boundary-security`
- **审计起始 Commit SHA**：`0409e81f3549d1d86834ed023c52f95f802a300b`
- **Python 运行时**：Python 3.13.17, Windows 11 AMD64
- **测试框架**：pytest 9.2.0, anyio 4.16.0

---

## 二、8 项关键安全缺陷红态复现矩阵

在修改生产代码前，严格按照 **Test-Driven Security Remediation** 要求，编写了 `tests/security/test_phase16_r2_reproduce_flaws.py`，针对 8 项核心缺陷执行红态验证：

| 编号 | 测试用例名称 | 核心断言与安全预期 | 基线实际运行结果 | 缺陷定位 |
|---|---|---|---|---|
| **R2-FLAW-01** | `test_missing_source_document_cannot_be_verified` | 规则仅有来源文档字符串但文档在存储中不存在，必须拒绝判定为 `VERIFIED` | **FAILED** (`AssertionError: 来源文档不存在却放行了 VERIFIED`) | `citation.py:534` |
| **R2-FLAW-02** | `test_missing_source_clause_cannot_be_verified` | 规则引用 `source_clauses` 但底层 `get_clause()` 返回 `None`，不可放行 `VERIFIED` | **FAILED** (`AssertionError: 底层条款不存在却放行了 VERIFIED`) | `citation.py:501-534` |
| **R2-FLAW-03** | `test_source_clause_query_failure_must_fail_closed` | 查验底层条款遇数据库异常时，严禁静默吞并异常后放行 `VERIFIED` | **FAILED** (`AssertionError: 数据库异常被静默吞掉并放行了 VERIFIED`) | `citation.py:508-509` |
| **R2-FLAW-04** | `test_unsupported_provenance_cannot_be_verified` | 规则仅提供口头法规字符串而未登记权威条文出处，不可判定为 `VERIFIED` | **FAILED** (`AssertionError: 未登记的口头法规字符串被判为 VERIFIED`) | `citation.py:468-498` |
| **R2-FLAW-05** | `test_unverified_rule_not_used_as_authoritative_prompt_context` | 未核验成功的规则在组装提示词时，绝不得标注为 `[企业已审批风控基准·优先适用]` | **FAILED** (`AssertionError: 未验证规则被冠以权威标签传入提示词上下文`) | `knowledge_adapter.py:328-335` |
| **R2-FLAW-06** | `test_legacy_feedback_cannot_approve_by_reviewer_name` | 服务层接口严禁凭 `reviewer_id="legal_admin"` 配合 `is_approved=True` 直接自提自批 | **FAILED** (`AssertionError: 直接通过 reviewer_id='legal_admin' 成功自审批入库`) | `knowledge_adapter.py:474-488` |
| **R2-FLAW-07** | `test_empty_evidence_cache_requires_revalidation` | 知识库更新审批规则后，旧的空证据（无匹配规则）缓存必须失效，不可复用旧缓存 | **FAILED** (`AssertionError: 知识库新增规则后，旧的空证据缓存依然被放行命中`) | `legal_review_agent.py:471-525` |
| **R2-FLAW-08** | `test_worker_must_not_retry_without_security_context` | Worker 遇到 Agent 接口不匹配时，严禁删除 `storage` 与 `tenant_id` 安全参数重试 | **FAILED** (`AssertionError: Worker 中仍存在 review_kwargs.pop('storage') 不安全重试`) | `review_sessions.py:504-512` |

### 复现执行命令与输出：
```bash
python -m pytest tests/security/test_phase16_r2_reproduce_flaws.py -v
```
**统计结果**：`8 failed in 0.55s` (100% 失败红态建立完成)。
