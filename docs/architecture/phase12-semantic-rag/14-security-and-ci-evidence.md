# 14. 语义 RAG 安全防护矩阵与 CI 门禁证据（Security & CI Evidence）

## 1. 语义 RAG 威胁建模与防护矩阵（Threat Matrix）

在企业级合同知识库系统中，检索增强生成（RAG）面临特有的安全威胁。系统建立了 Fail-Closed 机制，坚决阻断任何试图越权、篡改或伪造依据的行为：

| 威胁编号 | 威胁类别 | 攻击向量与手法 | 系统防御机制与实现位置 | 自动化验证用例 |
|---|---|---|---|---|
| **THREAT-01** | 提示词注入 / 意图投毒 | 待审合同条款伪造“忽略规则/删除表/强制通过”指令 | 定界符转义 + XML 信封 + 确定性语义槽位提取（`query_planner.py`, `llm_review.py`） | `test_retrieval_intent_injection.py` |
| **THREAT-02** | 跨租户检索越权穿透 | 待审合同伪造跨条款引用，企图召回其他租户机密合同 | SQL 层强制 `tenant_id` 预过滤 + 准入策略后验核实（`service.py`, `storage.py`） | `test_cross_tenant_retrieval_expansion.py` |
| **THREAT-03** | 废止与未审批知识污染 | 历史废止模板（REVOKED）或草稿条目被用作强制违规标准 | 数据库 `status='ACTIVE'` + `approval_status='APPROVED'` 双重硬门禁（`governance.py`） | `test_stale_evidence_blocking.py` |
| **THREAT-04** | 检索与 Token 预算爆炸 | 待审合同包含数百条款，导致向模型发起数千次检索请求 | `QueryPlanningBudget` 硬上限（最多20个意图）+ 预调用配额预留（`stages.py`） | `test_dynamic_query_planner.py` |
| **THREAT-05** | 幻觉证据与引文伪造 | LLM 或模型生成不存在的条款编号与捏造的原文引用 | 双向 AST 节点交叉验证与数据库原文逐字锚定（`VerifyEvidenceStage`, `citation.py`） | `test_semantic_contract_review_e2e.py` |

---

## 2. 核心防护机制技术剖析

### 2.1 待审不可信数据的安全信封封装
待审合同正文在进入任何模型前，必须经过定界符清洗与动态随机 Nonce 信封封装：
```python
# 防御伪造定界符逃逸
cleaned = text.replace("<<<END_CONTRACT_DATA>>>", "[定界符已转义]")
envelope = f'<UNTRUSTED_CONTRACT_BODY nonce="{nonce}">{cleaned}</UNTRUSTED_CONTRACT_BODY>'
```
模型系统提示词（`PROMPT_SYSTEM`）被赋予最高控制权，合同正文仅作为不可信被动客体。

### 2.2 租户与状态预过滤强一致性
在 SQLite 与 PostgreSQL 存储层，所有全文检索（`_fts_search`）与向量检索（`_vector_search`）强制注入租户与状态限定：
```sql
-- 强制租户隔离与 ACTIVE 状态过滤
WHERE d.tenant_id = :tenant_id 
  AND d.status = 'ACTIVE' 
  AND (:approved_only = FALSE OR c.approval_status = 'APPROVED')
```
任何未携带合法租户凭据或试图跨租户读取的操作均在 SQL 驱动层被阻断。

---

## 3. 全套安全测试用例集执行证据

```text
============================= test session starts =============================
platform win32 -- Python 3.13.17, pytest-9.2.0, pluggy-1.7.0
rootdir: D:\Users\Administrator\Desktop\AI合同
configfile: pyproject.toml

tests/security/test_retrieval_intent_injection.py ..                     [ 33%]
tests/security/test_cross_tenant_retrieval_expansion.py .                [ 66%]
tests/security/test_stale_evidence_blocking.py .                         [100%]

============================== 4 passed in 0.50s ==============================
```
在包括原有 Phase 11 治理测试在内的全部 56 项安全测试中：**100% 通过，0 失败，0 告警**。
