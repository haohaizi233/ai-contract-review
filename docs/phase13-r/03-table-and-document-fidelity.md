# Phase 13-R1：表格对齐重构与文档结构保真报告 (03-table-and-document-fidelity)

## 1. 表格对齐与重建架构 (`contract_kb/table_aligner.py`)

本阶段重构了从 `真实母本 AST + 真实送审 AST → 表格结构对齐 → 单元格级差异 → HTML 呈现` 的全链路能力：

1. **纯净表格提取 (`ExtractedTable` / `extract_tables_from_nodes`)**:
   - 按 `table_index`、`row_index`、`column_index` 与 `grid_span` 完整还原表格拓扑；
   - 自动抽取首行表头语义与全部单元格 `ContractSourceNode`。

2. **多特征表格对齐 (`align_contract_tables`)**:
   - 综合计算表头语义相似度（50%）、列数比例（20%）、全文词汇重合度（30%）与位置加权；
   - 识别 `ALIGNED`（可靠对齐）、`MISSING_IN_REVIEWED`（送审整表删除）、`NEW_IN_REVIEWED`（送审新增表格）；
   - 当两侧表格列数差异过大（`abs(std_cols - rev_cols) > 2` 且置信度 `< 0.6`）时，严格遵循 **Fail-Closed** 原则，输出 `ALIGNMENT_UNCERTAIN`，保留两侧原始表格交由法务人工复核，严禁强行合并导致错行。

3. **跨列合并单元格与高保真 HTML 渲染 (`build_table_diff_block`)**:
   - 完整保留 `grid_span > 1` 对应的 `colspan="{span}"` 属性，并自动跳过被跨列覆盖的后续列位；
   - 单元格内容变动自动生成细粒度法务批注（区分 `NUMBER_CHANGE`、`PARTY_CHANGE`、`TEXT_DELTA`）。

4. **HTML 注入与 XSS 防护**:
   - 所有来自合同的原始文本统一经过 `html.escape(..., quote=True)` 转义；
   - 彻底防御 `<script>`、`<img onerror=...>`、`<svg onload=...>` 等恶意载荷。

---

## 2. 四级保真度边界声明

| 保真层级 | 能力状态 | 说明与验收依据 |
| :--- | :--- | :--- |
| **1. 文本内容保真** | **已达成 (VERIFIED)** | 100% 忠实于源文档段落与单元格文字，零硬编码假数据替换 |
| **2. 表格结构保真** | **已达成 (VERIFIED)** | 保留表格顺序、行列数、表头、跨列合并 (`colspan`) 及多表独立性 |
| **3. 语义样式保真** | **已达成 (VERIFIED)** | 保留条款层级、标红删除线 `<del>`、新增高亮 `<ins>` 与表格语义徽章 |
| **4. Word 页面级视觉保真** | **受限披露 (BOUNDED)** | Web 工作台采用流式双栏 HTML 卡片对照，不等同于 Word 物理分页排版 |

---

## 3. 验证用例

- `tests/integration/test_table_reconstruction_integrity.py`（7 项全通过：15行费用表不丢行、双费用表不合并、主体表与专利表并存、空单元格填值、整表删除、整表新增、`colspan="3"` 合并单元格保留）
- `tests/security/test_diff_html_injection.py`（4 项全通过：XSS 标签与属性转义）
