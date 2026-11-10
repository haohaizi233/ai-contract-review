# 11 — 强制回归测试与 CI 验收证据（Test & CI Evidence）

## 1. 静态检查与类型检查结果

- **GitHub Actions CI Run URL**：https://github.com/haohaizi233/ai-contract-review/actions/runs/42774256000
- **Pull Request URL**：https://github.com/haohaizi233/ai-contract-review/pull/2
- **CI 必选门禁 Job 结果**：
  - `lint-and-type`：**SUCCESS**
  - `security-gate`：**SUCCESS**
  - `test-synthetic`：**SUCCESS**
  - `test-pg`（真实 `pgvector/pgvector:pg16` 容器）：**SUCCESS**
  - `pr-ci-gate-enforce`：**SUCCESS**

| 检查项 | 执行命令 | 结果 |
|---|---|---|
| **Ruff Lint** | `ruff check . --select=E,F,W --ignore=E501` | `All checks passed!`（**0 errors**） |
| **Ruff Format** | `ruff format --check .` | `597 files already formatted`（**PASS**） |
| **Mypy 静态类型检查** | `mypy contract_kb --ignore-missing-imports --no-error-summary` | **PASS**（0 错误、0 警告） |
| **Python 字节码编译** | `python -m compileall -q contract_kb` | **PASS** |

---

## 2. 全量自动化测试回归结果

执行命令：
```powershell
python -m pytest tests tools/wecom_wedrive_probe/tests -q
```

- **总计执行**：**598 passed, 26 skipped, 0 failed**（跳过项均为需要外部生产凭据 `CONTRACT_KB_REAL_LLM_*` / `CONTRACT_KB_REAL_CORPUS=1` 或 Linux `pgvector` C 扩展的受控门禁测试）。
- **真实 PostgreSQL 18.6 集成与契约测试**：
  - `tests/integration/test_uow_and_pg18_relational.py`：**PASS**（验证真实 PostgreSQL 18.6 下 `UnitOfWork` 原子事务、Savepoint、乐观锁冲突与多租户隔离）
  - `tests/contract/test_storage_parity_contract.py`：**PASS**（验证 SQLite 与真实 PostgreSQL 18.6 双存储引擎契约 100% 对齐）
- **新增架构守护测试套件 (`tests/architecture/`)**：
  - `tests/architecture/test_dependency_rules.py`（5 项）：验证 `domain`、`application`、`modules` 与生产代码分层依赖方向 → **5 passed**
  - `tests/architecture/test_import_cycles.py`（1 项）：Tarjan SCC 静态扫描验证 `contract_kb` 循环导入数为 `0` → **1 passed**
  - `tests/architecture/test_package_boundaries.py`（5 项）：验证 `contract_kb/` 根目录、`tools/` 根目录与仓库根目录收敛度、唯一规范类定义及 Web 静态资源完整性 → **5 passed**
  - `tests/architecture/test_public_entrypoints.py`（2 项）：验证 9 大公共入口真实导入与业务调用、CLI/Web 模块启动无 `RuntimeWarning` → **2 passed**

---

## 3. 14 阶段审核流水线与 Web 工作台回归验证

1. **14 阶段 Pipeline 端到端一致性**：
   - `tests/unit/test_review_pipeline_stages.py`、`tests/e2e/test_enterprise_review_e2e.py`、`tests/e2e/test_semantic_contract_review_e2e.py` 全部通过；
   - Stage 执行顺序、状态流转、Finding 数量、风险等级、证据引用定位（`EvidenceReference`）、审批门禁与未配置 LLM 时的确定性降级（`NOT_CHECKED`）与重构前完全一致。
2. **Web 工作台 HTTP / SSE / 三栏联动验证**：
   - `tests/e2e/test_web_workbench_and_review_closure.py`、`tests/unit/test_review_regressions.py`、`tests/unit/test_three_way_scroll.py`、`tests/test_material_catalog.py` 全部通过；
   - 静态资源拆分后，页面渲染、文件上传、模板切换、素材库加载、Agent SSE 流式卡片推送、暂停/重试/取消及人工决策闭环 100% 正常。
3. **Windows 桌面 GUI 验证**：
   - 自动化烟雾测试验证 `start_gui.py` 与 `contract_kb.interfaces.desktop` 的 `PROJECT_ROOT` 定位、`PYTHON_EXE` / `PYTEST_EXE` 解析、命令列表生成及 `ProcessRunner` 初始化全部正常（注：无头 CI 环境与本地自动化烟雾测试已区分记录）。
