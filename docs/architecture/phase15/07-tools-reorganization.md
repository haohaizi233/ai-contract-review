# 07 — `tools/` 与 `scripts/` 目录深度整理（Tools & Scripts Reorganization）

## 1. 整理前后对比

- **整理前**：`tools/` 根目录平铺 **42 个 `.py` 文件**，混杂了 4,603 行的 Web 工作台应用、被生产代码依赖的 `material_catalog.py`、数据库探针、企微微盘同步 CLI、Tender 导出工具及历史数据合并修复脚本；仓库根目录另有 `build_doc.py`。
- **整理后**：
  1. 属于正式业务能力的 `material_catalog.py` 抽入 `contract_kb/modules/templates/material_catalog.py`；
  2. 属于正式 Web 界面的 `contract_diff_web.py` 拆解入 `contract_kb/interfaces/web/`；
  3. 其余脚本按职责归入 `tools/` 下的 **6 个分类子包**；
  4. `tools/` 根目录仅保留 **8 个轻量兼容入口脚本**（每个 ≤ 75 行），确保所有外部 CLI 调用、CI 与批处理脚本 100% 兼容。

---

## 2. `tools/` 子包分类清单

| 子包路径 | 职责定位 | 包含脚本 |
|---|---|---|
| `tools/diagnostics/` | 数据库表结构、枚举、字段注释、OA 流程与微盘探针诊断 | `check_db_tables.py`, `diagnose_wedrive.py`, `dump_all_col_comments.py`, `dump_all_db_templates.py`, `dump_db_enums.py`, `dump_dict_tables.py`, `dump_sample.py`, `inspect_matter.py`, `query_e9_workflows.py`, `run_phase3_acceptance_test.py` |
| `tools/templates/` | 合同模板扫描分析、五级分类规则生成、OSS 标准模板下载 | `analyze_templates.py`, `build_template_5th_layer_rules.py`, `download_db_templates.py`, `list_ref_files.py`, `match_templates.py`, `material_catalog.py`, `read_rules_docx.py`, `read_sample_templates.py`, `template_5th_layer_data.py` |
| `tools/integrations/wecom/` | 企业微信与微盘增量 Diff 同步、软删除防抖与 CLI 同步 | `sync_wecom_cli.py`, `sync_wecom_wedrive.py` |
| `tools/integrations/tender/` | 新合同系统 (`tender_*`) 全量同步、案卷三件套导出、质量校验与进度 Web | `tender_attach_export.py`, `tender_case_export.py`, `tender_case_quality.py`, `tender_case_web.py`, `tender_fullsync.py`, `tender_sync_web.py`, `tender_unify_merge.py` |
| `tools/data_ops/` | 泛微 E9 与新库交叉核验、联合合并、图片资产恢复、断链复验 | `contract_merge_repair.py`, `cross_verify_and_merge.py`, `live_db_merge.py`, `recover_e9_ledger_assets.py`, `reorganize_cases_by_category.py`, `validate_contract_merge_repair.py` |
| `tools/reports/` | 五级知识图谱 HTML、Excel 映射表、幕布导出、台账与文档生成 | `build_doc.py`, `build_knowledge_graph_html.py`, `compile_b64_to_png.py`, `export_contract_mappings_excel.py`, `export_merge_supplement.py`, `export_mubu_files.py`, `export_prefixes.py`, `generate_classification_ledger.py`, `sample_prefixes.py` |
| `tools/maintenance/` | 单份合同命令行微观条款 Diff 比对与法务批注报告 | `diff_contract.py` |

---

## 3. `tools/` 根目录保留的 8 个薄兼容包装器

| 兼容入口路径 | 行数 | 转发目标模块 | 保留原因 |
|---|---:|---|---|
| `tools/contract_diff_web.py` | 73 | `contract_kb.interfaces.web` | `start_gui.py`、E2E 测试与用户常用启动命令 `python tools/contract_diff_web.py --port 8089` |
| `tools/diff_contract.py` | 26 | `tools.maintenance.diff_contract` | 桌面 GUI 单合同比对按钮与命令行习惯入口 |
| `tools/sync_wecom_cli.py` | 20 | `tools.integrations.wecom.sync_wecom_cli` | 企微 CLI 同步入口与 `tests/test_wedrive_cli_sync.py` |
| `tools/sync_wecom_wedrive.py` | 20 | `tools.integrations.wecom.sync_wecom_wedrive` | 桌面 GUI 微盘增量同步按钮与 `tests/test_wedrive_rag_sync_e2e.py` |
| `tools/tender_case_export.py` | 20 | `tools.integrations.tender.tender_case_export` | `tests/contract/test_legacy_cli_compatibility.py` CLI 契约测试入口 |
| `tools/cross_verify_and_merge.py` | 20 | `tools.data_ops.cross_verify_and_merge` | `tests/contract/test_legacy_cli_compatibility.py` CLI 契约测试入口 |
| `tools/live_db_merge.py` | 20 | `tools.data_ops.live_db_merge` | `tests/contract/test_legacy_cli_compatibility.py` CLI 契约测试入口 |
| `tools/material_catalog.py` | 31 | `contract_kb.modules.templates.material_catalog` | 历史脚本与测试导入兼容 |
