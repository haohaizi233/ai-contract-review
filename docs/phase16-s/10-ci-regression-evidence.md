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

## 3. GitHub Actions 远端 CI 计划与对接
仓库地址：https://github.com/haohaizi233/ai-contract-review
当前工作分支：`fix/phase16-trust-boundary-security`
基线提交：`3e015d2402f7432918ca956ee60842a6657292e7`

在推送分支后将触发以下 Actions 工作流：
1. `lint-and-type`: 运行 ruff check, ruff format --check, mypy
2. `test-synthetic`: 运行基础单元与合成测试
3. `test-pg`: 运行 PostgreSQL 兼容与集成测试
4. `security-gate`: 运行包括 Phase 16-S 在内的所有安全门禁测试
5. `pr-ci-gate-enforce`: 校验所有必需 Check 全绿
