# 11 - CI Evidence & Quality Gates Report

## 1. 质量门禁执行概况

- **分支**: `fix/phase11-governed-rag-integrity`
- **代码规范检查 (Ruff)**: 100% 通过
- **格式规范检查 (Ruff Format)**: 100% 通过
- **静态类型安全 (Mypy)**: 86 source files, 0 issues
- **自动化测试套件 (Pytest)**: 全部通过，零回归，零静默失败

---

## 2. 专项质量门禁矩阵 (Phase 11 Quality Gates)

| 专项门禁名称 | 覆盖模块与测试集合 | 结果 | 门禁判定 |
|---|---|:---:|:---:|
| **knowledge-governance-gate** | `KnowledgeGovernancePolicy`<br>`test_phase11_reproduce_flaws.py`<br>`test_governance_fail_closed_strict.py`<br>`test_knowledge_governance_fail_closed.py` | PASS | **PASSED** |
| **citation-integrity-gate** | `CitationVerifier`<br>`EvidenceReference`<br>`test_citation_verification.py`<br>`test_phase11_adversarial_suite.py` | PASS | **PASSED** |
| **storage-parity-gate** | SQLite / PostgreSQL 双存储契约<br>`test_storage_parity_contract.py`<br>`test_all_migrations_parse_with_pglast` | PASS | **PASSED** |
| **rag-security-regression** | 审批绕过、跨租户隔离、状态失效、引文伪造、全链路证据权限 | PASS | **PASSED** |

---

## 3. 静态检查指令及日志输出

```powershell
.\.venv\Scripts\ruff.exe check contract_kb tests
# Output: All checks passed!

.\.venv\Scripts\ruff.exe format --check contract_kb tests
# Output: 154 files already formatted

.\.venv\Scripts\mypy.exe contract_kb
# Output: Success: no issues found in 86 source files
```
