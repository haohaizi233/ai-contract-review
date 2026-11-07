# 10 - Security Test Matrix & Adversarial Verification

## 1. 对抗性测试矩阵汇总

所有用例均在真实执行环境中通过，无任何 Mock 绕过或 `assert True`：

| 测试组 | 针对缺陷 | 测试目标 | 执行用例 | 结果 |
|---|---|---|---|:---:|
| **Group A: 审批绕过** | P0-01, P0-02 | 验证 `upsert_document` 不自动提升状态；`is_company_standard` 不提权；撤销后立即丧失权威；重建索引保持状态。 | `test_group_a_new_import_remains_pending`<br>`test_group_a_company_standard_flag_does_not_grant_approval`<br>`test_group_a_revocation_immediately_removes_authority`<br>`test_group_a_rebuild_index_preserves_approval_states` | **PASS** |
| **Group B: 跨租户攻击** | P0-03, P0-05 | 验证相同文本不同租户严格隔离；检索 fallback 不扩大租户；CitationVerifier 跨租户回查直接报错并标记 UNVERIFIED。 | `test_group_b_identical_text_different_tenants_isolated`<br>`test_group_b_citation_verifier_rejects_cross_tenant_lookup` | **PASS** |
| **Group C: 状态失效** | P0-03 | 验证 `DRAFT`, `SUPERSEDED`, `ARCHIVED`, `DELETED`, `REVOKED`, `UNKNOWN`, `None` 坚决阻断。 | `test_group_c_invalid_lifecycle_statuses_blocked` (8 组参数化矩阵) | **PASS** |
| **Group D: 引文伪造** | P0-05 | 验证伪造 `clause_id`、篡改引述正文、空集输入等对抗场景下引文核验的准确性。 | `test_group_d_nonexistent_clause_id_rejected`<br>`test_group_d_text_mismatch_rejected` | **PASS** |
| **Group E: 检索一致性** | P0-02, P0-04 | 验证 FTS 降级通道真实读取字段，历史案例禁止作为强制依据。 | `test_group_e_historical_case_cannot_drive_mandatory_finding` | **PASS** |
| **Group F: 审核链路** | P0-04, P0-05 | 验证 Stage 9 到 Stage 14 全链路：非权威依据（如历史案例）驱动的 HIGH 结论在冲突消解层自动降级。 | `test_group_f_non_authoritative_reference_cannot_drive_mandatory_finding` | **PASS** |
