# Phase 13-R1：合同原文真实性与来源追溯修复报告 (02-contract-truthfulness)

## 1. 修复背景与不变量约束

前次审计发现 `contract_kb/full_diff.py::_enhance_diff_tables()` 存在严重的硬编码演示数据注入问题：
- 无论送审合同实际签约方是谁，均替换为“深圳市某某科技有限公司”与“深圳市世纪恒程知识产权代理事务所”；
- 无论送审合同实际官费与服务费是多少，均替换为“600 / 150 元”；
- 根据固定块序号（如 `block-8`、`block-9`）猜测表格类型并删除真实差异批注。

**R1 强制验收不变量**：所有正式显示的合同事实必须来自原始合同、已批准标准或明确标记的人工输入，绝不允许凭空生成。

---

## 2. 核心工程改造

### 2.1 建立强类型来源映射模型 (`contract_kb/domain/models/source_node.py`)
新增 `ContractSourceNode` 数据类，所有参评段落、表格及单元格均携带：
- `document_id` / `document_hash` / `document_version`
- `node_id` / `node_type` (`paragraph` | `table` | `table_cell` | `scanned_placeholder`)
- `table_index` / `row_index` / `column_index` / `grid_span` / `v_merge`
- `original_text` / `source_path` / `source_range` / `extraction_method` / `parse_warnings`
- `node_hash`（基于文档哈希、节点 ID 与原文内容生成的确定性 SHA-256 指纹）

### 2.2 彻底清除 `_enhance_diff_tables` 中的演示假数据 (`contract_kb/full_diff.py`)
- 移除全部固定公司名称、固定地址、固定专利号、固定官费/服务费数值及示例批注；
- 接入 `contract_kb/table_aligner.py` 中的 `extract_tables_from_nodes`、`align_contract_tables` 与 `build_table_diff_block`；
- 为每个 `aligned_block` 注入可反查来源的真实性元数据：
  - `standard_source_node_ids`
  - `reviewed_source_node_ids`
  - `alignment_method`
  - `alignment_confidence`
  - `content_integrity_status` (`SOURCE_TRUTH_VERIFIED` | `ALIGNMENT_UNCERTAIN`)
  - `unresolved_mapping_reason`

---

## 3. 对抗验证与验收结果

- **Case A（独特金额保留）**：`tests/contract/test_contract_source_truth.py::test_contract_source_truth_case_a_amounts_preserved`
  - 输入官费 `913` 元、服务费 `287` 元，验证输出完整保留 `913` 与 `287`，绝无 `600` 或 `150` 出现。
- **Case B（真实主体保留）**：`tests/contract/test_contract_source_truth.py::test_contract_source_truth_case_b_parties_preserved`
  - 输入“北京量子跃迁智能科技有限公司”与“上海领航航天科技合伙企业”，验证输出完整保留真实名称，绝无硬编码演示公司名称。
