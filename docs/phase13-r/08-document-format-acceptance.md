# Phase 13-R6: 文档格式解析真实性与边界治理报告 (`08-document-format-acceptance.md`)

## 1. 格式支持矩阵与真实能力核验 (P1-B)

针对 `contract_kb/document_parser.py`、`contract_kb/clause_extractor.py` 与 `contract_kb/ingest.py`，我们对企业合同常见输入格式进行了逐项核对与加固：

| 文档格式 | 解析能力状态 | 处理策略与失败边界 |
| :--- | :--- | :--- |
| `.docx` (标准 Word) | **完整支持 (`SUPPORTED`)** | 解析段落、多表格、合并单元格 (`gridSpan`)、修订痕迹 (`w:ins`/`w:del`) 及批注 (`comments.xml`)，映射为 `ContractSourceNode`。 |
| `.pdf` (原生文本型 PDF) | **支持 (`SUPPORTED`)** | 基于 `pypdf` / `pdfplumber` 提取每页文本块，保留页码与节点哈希。 |
| `.pdf` (扫描件 / 图片型 PDF) | **显式阻断 (`OCR_REQUIRED`)** | 当页面无可提取文本流时，生成 `node_type="scanned_placeholder"` 并在 `warnings` 中显式标记 `OCR_REQUIRED: 第 N 页未检测到文本流，疑似扫描件...`。 |
| `.doc` (旧版二进制 Word) | **受控转换 (`CONVERSION_REQUIRED`)** | 依赖本地 LibreOffice (`soffice`) 或 Word COM 组件先转换为 `.docx`；若运行环境未安装转换器，明确报错拒绝静默吞错。 |

## 2. 扫描件 PDF 防伪造合规结论机制 (Fail-Closed)

1. **`contract_kb/document_parser.py`**：
   - 当 PDF 页面提取文本为空时，不再将 `[第 N 页为扫描图片或无文本流，需 OCR 引擎支持]` 伪装成普通正文段落（`paragraph`），而是标记为 `node_type="scanned_placeholder"` 并追加 `OCR_REQUIRED` 告警。
2. **`contract_kb/clause_extractor.py`**：
   - `extract_clauses()` 显式过滤 `b.node_type == "scanned_placeholder"`，禁止将占位提示提取为合成条款或参与合规比对，防止对空白扫描件输出“无差异/合规”假阳性结论。
3. **`contract_kb/ingest.py`**：
   - 清理了文件末尾重复拼接的死代码，确保入库流水线单一可信。

## 3. 验证测试证据

- `tests/e2e/test_contract_diff_source_fidelity.py::test_case_j_scanned_pdf_placeholder_not_extracted_as_clause`：验证扫描件占位节点被 `extract_clauses` 100% 拦截，提取条款数为 0。
