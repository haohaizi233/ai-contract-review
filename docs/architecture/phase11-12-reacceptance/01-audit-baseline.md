# Phase 11-R + Phase 12-R: 全仓库缺陷基线与影响面审计报告

**报告版本**：1.0.0-REACCEPTANCE  
**审计日期**：2026-11-07  
**审计基线 Commit**：`10d9b0013bb53964b4db22bdde29b51d73826c36`  
**审计分支**：`fix/phase11-12-reacceptance`  
**审计角色**：Principal Software Architect & Independent QA Auditor  

---

## 一、 审计概述与原则

本报告依据《Phase 11-R + Phase 12-R：合同智能审核系统联合整改与独立复验规范》，针对已合入主分支的 Phase 11 与 Phase 12 展开全面独立源码审计。
审计坚决贯彻以下原则：
1. **真实性第一**：禁止以增加未接线抽象层、伪造测试断言或编写夸大文档代替实际修复。
2. **两级验收分离**：严格区分 **L1 工程与安全契约**（代码安全、租户隔离、CAS 并发、Fail-Closed）与 **L2 真实模型与算法效果**（真实 Dense 模型、真实 Cross-Encoder、真实消融对照）。在未部署真实大模型权重或运行环境时，照实标注 `BLOCKED_MODEL_RESOURCE`，绝不用简单散列或词法算法冒充。
3. **零信任与 Fail-Closed**：缺失关键安全元数据（租户、审批状态、来源哈希、版本号）必须拒绝，严禁自动缺省补造。

---

## 二、 缺陷清单与影响面分析矩阵

| 缺陷编号 | 严重级别 | 模块/文件路径 | 核心函数/行号 | 缺陷性质 | 修复批次 |
|:---|:---|:---|:---|:---|:---|
| **P0-01** | CRITICAL | `contract_kb/storage.py`<br>`contract_kb/storage_pg.py` | `upsert_document` (L620-629 / L383) | 普通导入允许外部参数写入 `APPROVED` 绕过正式法务审批 | Batch R1 |
| **P0-02** | CRITICAL | `contract_kb/storage.py`<br>`contract_kb/storage_pg.py`<br>`contract_kb/api.py` | `approve_knowledge_version`<br>`revoke_knowledge_version` (L879 / L591) | 审批与撤销方法形参含 `expected_revision` 但未在 SQL 中执行 CAS 校验，并发覆盖 | Batch R1 |
| **P0-03** | CRITICAL | `contract_kb/modules/retrieval/citation.py` | `verify_retrieval_hits` (L70-134) | CitationVerifier 存在默认放行（`or "ACTIVE"` / `or "TEMPLATE"`），未校验证据哈希与版本 | Batch R2 |
| **P0-04** | CRITICAL | `contract_kb/application/pipeline/stages.py` | Stage 9 (L806, 848)<br>Stage 10 (L1039)<br>Stage 11 (L1308) | 流水线阶段在构建证据字典时主动补充 `APPROVED`、`TEMPLATE` 伪造可信，Stage 11 未回查真库 | Batch R2 |
| **P0-05** | CRITICAL | `contract_kb/ablation_study.py`<br>`docs/architecture/phase12-semantic-rag/` | `evaluate_configuration`<br>`run_full_ablation_study` | 消融实验失真：A/B 同配置，Parent-Child/Cross-Encoder/Comparison 参数未生效，绕过统一 RAG 服务 | Batch R3 |
| **P1-01** | HIGH | `contract_kb/modules/retrieval/governance.py`<br>`contract_kb/modules/retrieval/service.py` | `retrieve_authoritative`<br>`retrieve_for_intent` | 权威知识分类混淆：已审批的 `LEGAL_REFERENCE` 误入权威标准，未能隔离非强制参考源 | Batch R4 |
| **P1-02** | HIGH | `contract_kb/modules/retrieval/service.py`<br>`contract_kb/modules/retrieval/query_planner.py` | `retrieve_contract_evidence_bundles` | 动态 Query 预算与真实 `storage.search()` 次数脱节，重复规划且无实际调用计数与熔断 | Batch R4 |
| **P1-03** | HIGH | `contract_kb/modules/retrieval/query_planner.py` | `expand_intents_with_llm` | Level 2 查询扩展为空实现占位符（恒定返回 `base_intents`），存在虚假能力声明 | Batch R4 |
| **P1-04** | HIGH | `contract_kb/modules/retrieval/service.py` | `retrieve_for_intent` | 多路召回通道未真实分离，各通道重复调用混合搜索，知识库父子条款层级缺失审批约束 | Batch R4 |
| **P1-05** | HIGH | `contract_kb/modules/retrieval/comparison.py` | `compare_clauses`<br>`check_polarity_inversion` | 条款语义比对依赖关键词匹配，缺乏主体、条件及复杂数值/日历口径的抗混淆校验，易产生误报 | Batch R4 |
| **P1-06** | HIGH | `tests/contract/test_storage_parity_contract.py` | `test_storage_parity_contract.py` | 双数据库一致性测试名不符实，缺乏 PostgreSQL 实例对照与 FTS 降级安全验证 | Batch R5 |

---

## 三、 详细缺陷剖析与修复策略

### 1. P0-01: 导入绕过正式审批缺陷
- **当前行为**：
  在 `contract_kb/storage.py` 中：
  ```python
  raw_appr = c.get("approval_status")
  if raw_appr and str(raw_appr).strip().upper() in (
      "PENDING_REVIEW",
      "APPROVED",
      "REJECTED",
      "REVOKED",
  ):
      clause_appr = str(raw_appr).strip().upper()
  ```
  外部数据导入若携带 `approval_status="APPROVED"`，该条款将直接以 `APPROVED` 状态落库入库，跳过所有法务审核流程。在 PostgreSQL 实现中同样存在该漏洞。
- **预期行为**：
  - 数据导入仅产生知识候选，不授予知识权威。
  - 普通导入无论传入何种参数，一律强制写入 `PENDING_REVIEW`。
  - 重复导入（Re-import）不得覆盖或恢复已被撤销（REVOKED）的审批状态。
  - 如需导入历史已审批标准库，必须通过具有安全审计认证的独立管理迁移接口 `authorized_bulk_import_approved()`，并记录审批人及审批时间戳。
- **复现测试**：`tests/security/test_phase11_import_approval_bypass.py`

### 2. P0-02: 审批与撤销缺乏真实 CAS 乐观锁
- **当前行为**：
  `Storage.approve_knowledge_version()` 和 `PgVectorStorage.approve_knowledge_version()` 声明了 `expected_revision: int | None = None`，但内部执行的 SQL 为：
  ```sql
  UPDATE clauses SET approval_status='APPROVED' WHERE document_id=?
  ```
  该 SQL 根本未对 revision 进行条件匹配，也未在修改时递增版本，无法防范并发写冲突或并发撤销覆盖。
- **预期行为**：
  - `documents` 与 `clauses` 引入/维护 `revision`（知识修订版本号）。
  - 审批与撤销执行原子 CAS：
    ```sql
    UPDATE documents SET revision = revision + 1 WHERE document_id = ? AND tenant_id = ? AND revision = ?;
    UPDATE clauses SET approval_status = ?, revision = revision + 1 WHERE document_id = ? AND tenant_id = ?;
    ```
  - 当 `expected_revision` 不匹配时，抛出 `OptimisticLockConflictError`，API 层返回 HTTP 409。
- **复现测试**：`tests/security/test_phase11_approval_cas.py`

### 3. P0-03: CitationVerifier 默认放行与证据哈希脱钩
- **当前行为**：
  `CitationVerifier.verify_retrieval_hits()` 中：
  ```python
  doc_status = str(row_dict.get("doc_status") or "ACTIVE").upper()
  ktype = str(
      row_dict.get("knowledge_type") or h.get("knowledge_type") or "TEMPLATE"
  ).upper()
  ```
  若数据库行或 hit 缺失对应字段，直接补成 `ACTIVE` 和 `TEMPLATE`。此外，未对 `document_id`、`source_hash`、`source_version` 与原始审批凭证进行强一致性校验。
- **预期行为**：
  - 严格 Fail-Closed：缺失 `tenant_id`、`document_id`、`source_hash`、`doc_status` 任一字段均判定校验失败。
  - 构建强类型契约 `EvidenceVerificationResult`，覆盖 `VERIFIED`、`MISSING_SOURCE`、`TENANT_MISMATCH`、`DOCUMENT_MISMATCH`、`HASH_MISMATCH`、`VERSION_MISMATCH`、`APPROVAL_MISSING`、`APPROVAL_REVOKED` 等状态。
  - 基于真实数据库连接核验证据哈希与生效审批单号。
- **复现测试**：`tests/security/test_phase11_citation_integrity_strict.py`

### 4. P0-04: 流水线 Stage 9/10/11 伪造可信凭证
- **当前行为**：
  - Stage 9 (`RetrieveEvidenceStage`)：在构造 `rag_context` 时多次采用 `h.get("knowledge_type") or "TEMPLATE"` 和 `h.get("approved_status") or "APPROVED"`，主动补充可信字段。
  - Stage 10 (`LLMSemanticReviewStage`)：在构造 `comparison_findings` 时同样补全 `top_ref.get("approved_status") or "APPROVED"`。
  - Stage 11 (`VerifyEvidenceStage`)：仅读取 Finding 内部字典字段，未回查存储库权威状态。
- **预期行为**：
  - 证据构造阶段严禁填充伪造的可信属性，缺失属性保留为空或标记 `UNVERIFIED`。
  - Stage 11 针对所有强制性违规 Finding，通过存储层进行独立权威回查（查验租户、文档状态、审批状态与来源哈希），未通过者一律降级为 `INSUFFICIENT_EVIDENCE` 并标记 `requires_human_review=True`。
- **复现测试**：`tests/security/test_phase11_stage_evidence_fail_closed.py`

### 5. P0-05: Phase 12 消融实验失真
- **当前行为**：
  - `contract_kb/ablation_study.py` 中 Config A 与 Config B 使用完全相同的 `LocalHashEmbedding` 存储，未接入真实 Dense Embedding。
  - `use_parent_child` 和 `use_structural_comparison` 形参在 `evaluate_configuration` 中完全未被使用。
  - `use_cross_encoder` 调用 `GovernedRetrievalService.reranker.rerank`，而该服务默认绑定 `LexicalCoverageReranker`，实质上是用词法重排冒充 Cross-Encoder。
  - 绕过 `retrieve_contract_evidence_bundles`，直接调用 `st.search()`。
- **预期行为**：
  - 构建真实的消融执行矩阵 A～F，每组配置必须调用独立技术实现。
  - 建立组件调用跟踪器（Component Call Tracing），验证层级扩展、Cross-Encoder、结构化比对是否真正执行。
  - 当环境中缺少 `sentence-transformers` 或真实模型权重时，显式标记 `NOT_EXECUTED_MODEL_UNAVAILABLE`，禁止使用 LocalHash 模拟并出具虚假的高准确率报告。
- **复现测试**：`tests/unit/test_phase12_ablation_configuration_identity.py` 与 `tests/integration/test_phase12_ablation_real_execution.py`

---

## 四、 整改推进排期与 Commit 规划

* **Batch R1 (P0)**：修复普通导入审批绕过、增加知识版本 CAS 乐观锁及审批审计（`storage.py`, `storage_pg.py`, `api.py`）。
* **Batch R2 (P0)**：重构 CitationVerifier 证据核验与强类型结果契约，清理 Stage 9/10/11 伪造字段，实现 Stage 11 独立真库权威回查（`citation.py`, `stages.py`）。
* **Batch R3 (P0)**：彻底重构 `ablation_study.py`，建立真实 6 组消融矩阵与组件调用跟踪，完善不可用模型时的状态标注（`ablation_study.py`, `retrieval_golden.py`）。
* **Batch R4 (P1)**：完善权威知识与参考资料分类隔离、建立真实检索执行预算管理器、实现多路候选通道分离与困难语义比对（`governance.py`, `service.py`, `query_planner.py`, `comparison.py`）。
* **Batch R5 (P1)**：实施双数据库端到端一致性契约测试、PostgreSQL 无向量降级安全测试，记录真实模型验证与阻塞证据。
* **Batch R6 (验收)**：全量代码格式化（Ruff）、类型检查（Mypy）、全套测试回归，输出最终复验报告。
