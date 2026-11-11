# Phase 16-S: 04 - 法务 Agent 可信证据链与全链路治理 (Governed Agent Evidence Chain)

## 1. 对应实际代码路径
- `contract_kb/application/agents/knowledge_adapter.py`
  - `retrieve_governed_rules(category, delta_type, standard_span, reviewed_span, allow_unapproved, tenant_id, storage)`
  - `retrieve_legal_rules(category, delta_type, standard_span, reviewed_span, tenant_id, storage)`
- `contract_kb/application/agents/legal_review_agent.py`
  - `LegalReviewAgent.review_clause(annotation, doc_context, force_refresh, storage)`
  - `LegalReviewAgent.build_high_precision_fallback(annotation, error_msg, failure_code, source, elapsed_sec, tenant_id, storage)`
  - `LegalReviewAgent._cache_key(...)`: 绑定 `tenant_id`、`template_version`、`rule_version`

## 2. 原始问题 (P0-04)
1. **静态字典冒充已审批权威规则**：
   `review_clause()` 与 `build_high_precision_fallback()` 之前调用 `retrieve_governed_rules()` 时未传递受控 `storage` 与当前 `tenant_id`。当数据库中规则已被法务管理员撤销（REVOKED）时，旧逻辑因为在数据库查不到规则，自动回退到代码静态字典 `GOVERNED_RULES_STORE`，并将字典里的硬编码规则当成已审批生效规则（`approval_status="APPROVED"`）注入 LLM Prompt 和证据引用中。
2. **缺乏租户作用域隔离**：
   检索无法按不同企业租户的风控红线分别加载，存在跨租户知识混淆。

## 3. 修复方案 (Evidence-Based AI + Fail-Closed)
1. **真实数据库优先与禁止回退**：
   在 `retrieve_governed_rules` 中，若传入了受控 `storage`，直接向数据库发起带 `tenant_id` 与 `status="APPROVED"` 的严格查询：
   ```python
   if storage is not None and hasattr(storage, "get_rules"):
       db_rules = storage.get_rules(tenant_id=tenant_id, status="APPROVED")
       ...
       return matching_rules  # 查完即返回，绝不回退至静态字典！
   ```
   如果数据库无匹配规则（如全部被撤销），直接返回 `[]`，绝不回退静态字典。
2. **生产环境强约束**：
   在 `CONTRACT_KB_ENV=production` 模式下，若未提供 `storage`，函数直接 Fail-Closed 返回空列表，严禁代码作者硬编码的字典在生产环境中自称 `APPROVED`。
3. **证据引用强类型化**：
   在 `review_clause` 中生成的每一条 `evidence_references` 均显式包含：
   - `source_type`: `APPROVED_RULE` 或 `STATIC_REFERENCE`
   - `tenant_id`: 当前已认证租户
   - `rule_id`: 规则唯一标识
   - `revision`: 审批版本号
   - `approval_status`: 审批状态
   - `verification_status`: `VERIFIED` 或 `UNVERIFIED`
   - `is_statutory`: 是否属于国家法定强制性规范

## 4. 测试用例
- `tests/security/test_agent_governed_rules_fail_closed.py`
  - `test_revoked_database_rule_never_falls_back_to_static_store`
  - `test_database_error_fails_closed`
  - `test_model_failure_gracefully_falls_back_with_explicit_badge`
- `tests/security/test_phase16_reproduce_flaws.py`
  - `test_revoked_database_rule_must_not_reappear_from_static_fallback`
- `tests/integration/test_legal_agent_trusted_rag.py`
  - `test_case_c_revoked_rule_rejected_and_approved_rule_cited`

## 5. 实际执行命令与结果
```bash
python -m pytest tests/security/test_agent_governed_rules_fail_closed.py -v
```
运行结果：
```text
tests/security/test_agent_governed_rules_fail_closed.py::test_revoked_database_rule_never_falls_back_to_static_store PASSED
tests/security/test_agent_governed_rules_fail_closed.py::test_database_error_fails_closed PASSED
tests/security/test_agent_governed_rules_fail_closed.py::test_model_failure_gracefully_falls_back_with_explicit_badge PASSED
============================== 5 passed in 0.42s ==============================
```

## 6. 残余风险
- 若企业租户刚上线尚未在数据库中配置任何自定义审批红线，系统将安全回退至标准母本差异对比和通用初筛指引（提示“当前未匹配到企业已审批风控红线，需人工复核”），不会虚构红线违规。法务团队应通过管理后台导入并审批企业标准规则。
