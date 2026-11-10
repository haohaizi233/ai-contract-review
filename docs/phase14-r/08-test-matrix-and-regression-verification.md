# Phase 14-R: 测试矩阵与全量回归验证 (08-test-matrix-and-regression-verification)

## 一、测试工程执行矩阵

本专项整改严格遵循“先审计、再复现、后修复、全量回归”的工程准则。覆盖单元测试、集成测试、契约测试、安全渗透测试与端到端真实测试。

| 测试层级 | 测试套件路径 | 重点覆盖目标 | 执行结果 |
|---|---|---|---|
| **契约测试 (Contract)** | `tests/contract/test_review_fingerprint_real_inputs.py` | 真实 40 字符外篡改防截断指纹、HMAC 防篡改签名 | **PASSED (2/2)** |
| **契约测试 (Contract)** | `tests/contract/test_pg_sqlite_governed_retrieval_parity.py` | PG `LAYER_A_RAW_CLAUSE` 与 SQLite `TEMPLATE` 契约对齐 | **PASSED (1/1)** |
| **契约测试 (Contract)** | `tests/contract/test_rollback_template_chain_validation.py` | 模板版本回滚全链路未审批阻断校验 | **PASSED (7/7)** |
| **安全测试 (Security)** | `tests/security/test_web_identity_authorization.py` | Web 工作台本地开发防越权、企业 HMAC 签名、CSRF 拦截、租户目录隔离 | **PASSED (5/5)** |
| **安全测试 (Security)** | `tests/security/test_web_html_bootstrap_xss.py` | 初始内嵌数据 `_safe_json_embed` 防 XSS 注入 | **PASSED (1/1)** |
| **安全测试 (Security)** | `tests/security/test_phase11_adversarial_suite.py` | 规则废止即刻失效、跨租户同内容隔离、越权召回防御 | **PASSED (18/18)** |
| **安全测试 (Security)** | `tests/security/test_phase11_stage_evidence_fail_closed.py` | 运行时依据撤销动态降级 Fail-Closed 校验 | **PASSED (2/2)** |
| **安全测试 (Security)** | `tests/security/test_stale_evidence_blocking.py` | 过期与未审批引文核验器强阻断 | **PASSED (1/1)** |
| **集成测试 (Integration)** | `tests/integration/test_agent_human_feedback_e2e.py` | 审核反馈单参/多参兼容、脱敏入库、7 态状态机流转防护 | **PASSED (2/2)** |
| **集成测试 (Integration)** | `tests/integration/test_dynamic_retrieval_pipeline.py` | 受控动态意图检索与权威证据束装配 | **PASSED (2/2)** |
| **单元测试 (Unit)** | `tests/unit/test_rag_governance_and_agent.py` | 治理策略过滤、权威标准标注、引文一致性 | **PASSED (7/7)** |
| **端到端测试 (E2E)** | `tests/e2e/test_semantic_contract_review_e2e.py` | 全文 Diff、对齐、审核到报告全流程 | **PASSED (1/1)** |
| **全量自动化套件** | `tests tools/wecom_wedrive_probe/tests` | 仓库内全部测试脚本（跨模块综合） | **PASSED (100% 通过)** |

---

## 二、代码质量与 CI 门禁检查结果

1. **Ruff 语法与代码规范扫描**：
   ```bash
   ruff check . --select=E,F,W --ignore=E501
   # 结果：All checks passed! (0 errors)
   ```
2. **Ruff 代码格式化合规校验**：
   ```bash
   ruff format --check .
   # 结果：400 files already formatted (0 changes needed)
   ```
3. **Mypy 静态类型推断检查**：
   ```bash
   mypy contract_kb --ignore-missing-imports --no-error-summary
   # 结果：Success: no issues found
   ```
4. **CI 结构性安全门禁 (security-gate)**：
   - 绝不提交非受控 `.docx` 文件（经扫描 0 命中）；
   - 零硬编码私钥、内部明文密码或未经脱敏的真实企业敏感标识；
   - 零 `pytest.skip()` 掩盖失败；
   - 零修改断言以伪造通过。
