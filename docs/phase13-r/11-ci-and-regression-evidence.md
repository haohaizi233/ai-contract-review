# Phase 13-R9: CI 修复与全量回归证据 (`11-ci-and-regression-evidence.md`)

## 1. P0-E (`test-synthetic`) 失败根因与修复

- **前次失败現象**：
  GitHub Actions 运行 `42649085523` 中，`test-synthetic` 作业设置 `CONTRACT_KB_DB_PATH=test_nonexistent.db` 运行 `pytest tests/test_full_contract_diff.py`。
  在 `89c8c1a` 中抛出 `ValueError: 未能在标准库中找到业务子类 [PATENT_ANNUAL_FEE] 的基准模板`；而在 `85c77d8` 中使用了 `pytest.skip` 掩盖错误（违反第 3.2 节禁止无理由 skip 的规定）。
- **真正工程级修复**：
  1. 在 `tests/test_full_contract_diff.py` 中移除 `pytest.skip`，显式注入 `golden_samples`、`corpus_dir` 与 `storage` fixture，确保在无本地真实模板库的 CI 合成环境下，已入库的合成母本通过 `storage` 正常提供基准条款。
  2. 在 `contract_kb/full_diff.py` 中增加 `tests/fixtures/synth_corpus` 路径回退解析，确保 `storage` 中记录的合成母本文件名能够定位到物理 `.docx` 文件以提取 `ContractSourceNode` 表格与段落结构。

## 2. 本地模拟 CI `test-synthetic` 验证

执行命令：
```powershell
$env:CONTRACT_KB_DB_PATH="test_nonexistent.db"; python -m pytest tests/test_full_contract_diff.py -v
```
执行结果：
- `tests/test_full_contract_diff.py::test_full_contract_diff_on_sample PASSED`
- `tests/test_full_contract_diff.py::test_full_contract_diff_100_percent_match PASSED`
- **2 passed, 0 skipped, 0 failed**。

## 3. 全量代码静态检查与回归测试矩阵

| 门禁项 | 执行命令 | 实际结果 |
| :--- | :--- | :--- |
| **Ruff Lint** | `ruff check . --select=E,F,W --ignore=E501` | `All checks passed!` (Exit 0) |
| **Ruff Format** | `ruff format --check .` | `377 files already formatted` (Exit 0) |
| **Mypy Type Check** | `mypy contract_kb --ignore-missing-imports --no-error-summary` | `0 errors` (Exit 0) |
| **Phase 13-R 新增与核心专项测试** | `python -m pytest tests/test_full_contract_diff.py tests/contract/ tests/integration/ tests/security/ tests/e2e/ -v` | **All passed, 0 failed** |
| **全仓库测试套件回归** | `python -m pytest tests tools/wecom_wedrive_probe/tests -q` | **552 passed, 23 skipped (环境可选依赖如 PostgreSQL/OCR), 0 failed** |
