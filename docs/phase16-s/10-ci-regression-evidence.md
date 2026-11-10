# Phase 16-S: 10 - CI 全量回归与流水线验证证据 (CI Regression Evidence)

## 1. 本地全量工程门禁执行证据

### 1.1 代码格式规范检查 (ruff format)
```bash
ruff format --check .
```
执行结果：
```text
495 files already formatted
Exit Code: 0
```

### 1.2 静态代码分析与 Lint 检查 (ruff check)
```bash
ruff check .
```
执行结果：
```text
All checks passed!
Exit Code: 0
```

### 1.3 静态类型检查 (mypy)
```bash
mypy contract_kb --ignore-missing-imports --no-error-summary
```
执行结果：
```text
Success: no issues found in 118 source files
Exit Code: 0
```

### 1.4 全仓单元与集成测试套件回归
```bash
python -m pytest tests tools/wecom_wedrive_probe/tests -q
```
执行结果：
```text
638 passed, 35 skipped, 1 warning in 83.45s
Exit Code: 0
```

## 2. Phase 16-S 新增测试套件执行证据
```bash
python -m pytest \
  tests/security/test_phase16_reproduce_flaws.py \
  tests/security/test_web_enterprise_auth_fail_closed.py \
  tests/security/test_sse_user_ownership.py \
  tests/security/test_agent_governed_rules_fail_closed.py \
  tests/security/test_feedback_approval_authorization.py \
  tests/security/test_checkpoint_key_management.py \
  tests/integration/test_phase16_trusted_agent_end_to_end.py \
  tests/e2e/test_phase16_authenticated_review_workflow.py -v
```
执行结果：
```text
collected 31 items
tests/security/test_phase16_reproduce_flaws.py ..........                [ 32%]
tests/security/test_web_enterprise_auth_fail_closed.py .....             [ 48%]
tests/security/test_sse_user_ownership.py ...                            [ 58%]
tests/security/test_agent_governed_rules_fail_closed.py .....            [ 74%]
tests/security/test_feedback_approval_authorization.py ...               [ 83%]
tests/security/test_checkpoint_key_management.py ...                     [ 93%]
tests/integration/test_phase16_trusted_agent_end_to_end.py .             [ 96%]
tests/e2e/test_phase16_authenticated_review_workflow.py .                [100%]
============================== 31 passed in 3.52s ==============================
Exit Code: 0
```

## 3. GitHub Actions 远端 CI 真实运行证明
- **仓库地址**：https://github.com/haohaizi233/ai-contract-review
- **当前工作分支**：`fix/phase16-trust-boundary-security`
- **基线提交**：`3e015d2402f7432918ca956ee60842a6657292e7`
- **交付提交 SHA**：`3ced979e9287b60a8abb4663dbe3aaec07f901e0`
- **Pull Request 链接**：https://github.com/haohaizi233/ai-contract-review/pull/4
- **GitHub Actions 运行 URL**：https://github.com/haohaizi233/ai-contract-review/actions/runs/42804933013
- **CI 执行结论**：`completed | success` (所有必需 Check 全绿通过)

### 流水线 Job 执行明细：
| Job 名称 | 运行状态 | 判定结论 | 耗时与说明 |
|---|---|---|---|
| `lint-and-type` | completed | **success** | Ruff 语法/规范检查与 Mypy 类型检查通过 |
| `test-synthetic` | completed | **success** | 运行全量单元与合成测试套件 |
| `security-gate` | completed | **success** | 运行全量安全红线与凭据特征静态扫描 |
| `test-pg` | completed | **success** | PostgreSQL 容器化数据库兼容性测试通过 |
| `real-corpus-regression` | completed | **skipped** | 受控真实语料回归（需授权凭据标记，符合预期） |
| `pr-ci-gate-enforce` | completed | **success** | PR 合并门禁终审通过 |
