# Phase 11-R + Phase 12-R: 核心缺陷可复现反例与证据链记录

**报告版本**：1.0.0-REACCEPTANCE  
**日期**：2026-11-07  
**责任角色**：Principal Software Architect & Independent QA Auditor  

---

## 一、 缺陷强制失败复现总则

依据验收准则，所有缺陷必须在整改代码生效前编写**不可绕过的强制失败反例（Fail Reproduction Test）**。
严禁通过弱化断言、跳过关键检查或伪造桩数据使旧代码“假通过”。

---

## 二、 核心缺陷反例与报错回溯

### 1. P0-01: 导入绕过正式审批漏洞
- **反例用例**：
  ```python
  doc = create_template()
  doc["clauses"][0]["approval_status"] = "APPROVED"
  st.upsert_document(doc, tenant="tenant_a")
  clause = st.get_clause("clause_1", tenant_id="tenant_a")
  assert clause["approval_status"] != "APPROVED"
  ```
- **旧代码复现报错**：
  ```text
  AssertionError: assert 'APPROVED' != 'APPROVED'
  + where 'APPROVED' = {'clause_id': 'clause_1', 'approval_status': 'APPROVED'}['approval_status']
  ```
- **结论**：证实旧代码直接允许外部输入将草稿直接提权为法定已审批标准，攻击面真实成立。

---

### 2. P0-02: 审批与撤销缺乏乐观锁 CAS 并发保护
- **反例用例**：
  ```python
  # 携带陈旧的 expected_revision 发起审批
  st.approve_knowledge_version(
      tenant_id="tenant_a",
      document_id=did,
      approver_id="admin_1",
      expected_revision=999,
  )
  ```
- **旧代码复现表现**：
  旧代码形参虽然接收 `expected_revision`，但底层 SQL 更新语句仅包含 `WHERE document_id = ?`，静默更新成功并返回 HTTP 200，并发竞争被静默覆盖。
- **修复后表现**：
  严格比较 `revision = :expected_revision`，版本不一致时抛出 `OptimisticLockConflictError`，API 返回 HTTP 409 Conflict。

---

### 3. P0-03 & P0-04: CitationVerifier 默认信任与 TOCTOU 撤销穿透
- **反例用例**：
  ```python
  # 知识条款在中途被紧急撤销
  st.revoke_knowledge_version(tenant_id="t1", document_id=did, revoker_id="admin")
  # 流水线 Stage 11 继续复核
  stage11.execute(ctx)
  ```
- **旧代码复现报错**：
  ```text
  AssertionError: assert 'CHECKED' == 'INSUFFICIENT_EVIDENCE'
  ```
  旧代码 Stage 11 仅在内存中查看 Stage 9 传来的缓存对象（标记为 `APPROVED`），直接放行违规判定，未能回查真实数据库识别出 `APPROVAL_REVOKED`。
- **修复后表现**：
  Stage 11 强制注入 `storage` 按 `tenant_id + doc_id + clause_id` 实时回查数据库，将被撤销的条款降级为 `INSUFFICIENT_EVIDENCE` 并标记人工复核。

---

### 4. P0-05: 消融实验无环境时伪造 96% MRR
- **反例用例**：
  ```python
  # 无 sentence-transformers / torch 环境
  res = evaluate_configuration(cfg_dense_real, st, allow_mock_for_contract_test=False)
  assert res["status"] == "NOT_EXECUTED_MODEL_UNAVAILABLE"
  ```
- **旧代码复现表现**：
  旧代码在本地调用 MD5 散列截断构造伪向量，谎称 BGE-M3 语义检索在当前机器上达到 0.9634 MRR，构成严重的技术失真。
- **修复后表现**：
  真实模型环境检测失败时，诚实返回 `NOT_EXECUTED_MODEL_UNAVAILABLE` 并记录 `BLOCKED_MODEL_RESOURCE`，指标严格置空。

---

### 5. P1-06: PostgreSQL 真实环境兼容性崩溃
- **反例用例**：
  在未安装 `vector` 扩展的真实 PostgreSQL 实例上初始化 `PgVectorStorage`。
- **旧代码复现报错**：
  ```text
  psycopg2.errors.FeatureNotSupported: extension "vector" is not available
  psycopg2.errors.DatatypeMismatch: column "at" is of type timestamp with time zone but expression is of type numeric
  psycopg2.errors.UndefinedTable: relation "embeddings" does not exist
  ```
- **修复后表现**：
  自动捕获并无损降级至 `migrate_relational()`，解耦非向量查询对 `embeddings` 表的强依赖，CJK 词法检索采用 `ILIKE ANY`，实现 100% 稳定运行。
