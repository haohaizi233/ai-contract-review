# API 文档（contract-kb）

Base URL：`http://127.0.0.1:8000`（生产部署前需接入企业认证与租户鉴权，当前为单租户实现）

## 知识库

### POST /api/contract-kb/import
扫描模板目录并导入（幂等：同 SHA-256 跳过）。
响应：`{"scanned":26,"inserted":26,"skipped_idempotent":0,"failures":[]}`

### GET /api/contract-kb/documents
文档清单（含哈希/分类/状态/导入时间）。

### GET /api/contract-kb/documents/{id}
文档详情 + 全部条款（编号/类别/原文/归一化摘要/审批状态）。

### GET /api/contract-kb/taxonomy
分类体系（26 模板的 L1/L2 + 证据 + 置信度 + 冲突 + 哈希 + 版本）。

### POST /api/contract-kb/search
混合检索（FTS5 + 向量 + RRF + Rerank）。
```json
{
  "query": "逾期付款违约金",
  "top_k": 8,
  "business_category": "PATENT",
  "contract_subtype": "PATENT_ANNUAL_FEE",
  "clause_number": "6.3",
  "keyword": null,
  "rerank": true
}
```
响应含 `chunk_id/clause_id/document_id/text/clause_number/channels/rrf_score/rerank_score`（引用可回查原文）。

### POST /api/contract-kb/compare
两段条款文本比较：`{"similarity":0.93,"identical":false,"diff_level":"语义等价"}`

## 规则

### GET /api/contract-kb/rules?status=DRAFT
规则列表（含 source_clauses 溯源、external_data_dependency）。

### POST /api/contract-kb/rules
新建规则（默认 DRAFT，不可直接发布为强制规则）。

### POST /api/contract-kb/rules/{id}/approve?approver=legal
法务审批发布（写审计日志；仅 DRAFT 可审批）。

## 合同审核

### POST /api/contract-review/tasks
multipart 上传 `.docx` + `mode`（STANDARD / CUSTOM / COMBINED）+ `custom`（分号分隔用户要求）。
响应为完整审核结果：业务类型识别、匹配模板、问题列表（含 source_reference 原文定位）、RAG 上下文、check_status（CHECKED / INSUFFICIENT_EVIDENCE / NEEDS_MANUAL_REVIEW / NOT_CHECKED）。
> CUSTOM/COMBINED 模式下用户要求不覆盖核心规则（金额一致性、必备条款等）。

### GET /api/contract-review/tasks/{id}
任务状态与元数据。

### GET /api/contract-review/tasks/{id}/issues
问题列表（Pydantic 结构，含 severity/category/expected/actual/source_reference/suggestion/requires_human_review）。

### POST /api/contract-review/issues/{id}/feedback
人工反馈：`{"action":"ACCEPT|IGNORE|EDIT|NOTE|FALSE_POSITIVE|FALSE_NEGATIVE","actor":"...","note":"..."}`
仅记录反馈状态并写审计；不自动修改企业正式规则。

## 错误约定

| 状态码 | 含义 |
|---|---|
| 400 | 非 .docx / 非法反馈动作 |
| 404 | 文档/任务/规则不存在 |
| 409 | rule_id 冲突或规则非 DRAFT |
| 413 | 文件超 20MB |
