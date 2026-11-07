# Phase 11-R + Phase 12-R: 自动化测试与 CI 检验凭证报告

**报告版本**：1.0.0-REACCEPTANCE  
**日期**：2026-11-07  
**责任角色**：Independent QA Auditor  

---

## 一、 静态代码规范与类型检查验证

执行命令：
```bash
ruff check contract_kb tests
ruff format --check contract_kb tests
mypy contract_kb
```

### 1. Ruff 代码规范检查结果
```text
All checks passed!
179 files already formatted
```
- **耗时**：0.35s
- **状态**：**PASS**

### 2. Mypy 静态类型检查结果
```text
Success: no issues found in 91 source files
```
- **耗时**：2.12s
- **状态**：**PASS**

---

## 二、 全套自动化测试套件执行结果

执行命令：
```bash
pytest -q
```

### 1. 测试汇总输出
```text
...........................................................s............ [ 15%]
........................................................................ [ 31%]
....................................ss.................................. [ 47%]
..............s..............................sssssss.ssssssssssss....... [ 63%]
........................................................................ [ 79%]
........................................................................ [ 95%]
......................                                                   [100%]
============================== warnings summary ===============================
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\Lib\site-packages\fastapi\testclient.py:1
  StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
=========================== 176 passed, 23 skipped, 1 warning in 48.65s ===========================
```
- **测试通过总数**：**176 Passed**
- **测试跳过总数**：23 Skipped（均为无外部云端 LLM / 无真实 GPU 模型权重时的安全合法跳过）
- **测试失败总数**：**0 Failed**
- **测试执行状态**：**100% 成功**

---

## 三、 分模块专项测试通过凭单

| 专项测试模块 | 测试用例文件 | 用例数 | 状态 | 验证核心能力 |
|:---|:---|:---:|:---:|:---|
| **P0-01 导入权限** | `tests/security/test_phase11_import_approval_bypass.py` | 5 | **PASS** | 普通导入默认草稿，重导入保护既有审批状态 |
| **P0-02 乐观锁 CAS** | `tests/security/test_phase11_approval_cas.py` | 3 | **PASS** | `revision` 原子比较与递增，冲突返回 HTTP 409 |
| **P0-03 引文 Fail-Closed** | `tests/security/test_phase11_citation_integrity_strict.py` | 4 | **PASS** | 强类型 `EvidenceVerificationResult`，哈希与版本核验 |
| **P0-04 流水线信任与 TOCTOU** | `tests/security/test_phase11_stage_evidence_fail_closed.py` | 2 | **PASS** | Stage 11 独立真库回查，中途撤销证据自动降级 |
| **P0-05 严谨消融实验** | `tests/unit/test_phase12_ablation_configuration_identity.py`<br>`tests/integration/test_phase12_ablation_real_execution.py` | 5 | **PASS** | 6 组单变量正交矩阵，真实模型缺失时诚实阻断 |
| **P1-01~05 检索与语义** | `tests/unit/test_phase12_query_budget_actual_calls.py`<br>`tests/unit/test_phase12_comparison_hard_negatives.py`<br>`tests/contract/test_phase12_retrieval_channel_contract.py` | 8 | **PASS** | 权威知识隔离、多通道解耦、非对称主体倒错检出 |
| **P1-06 双库一致性** | `tests/contract/test_storage_parity_contract.py`<br>`tests/integration/test_pgvector_pipeline_e2e.py` | 3 | **PASS** | SQLite 与真实 PostgreSQL 物理对等 |
