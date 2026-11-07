# 01 - Baseline Audit: RAG Trust Boundary & Evidence Integrity

## 1. 审计基线与运行环境

- **审计基线 Git SHA**: `defadd67ce2b2defe7bcdd14af7a1dcc8bef2d9c`
- **修复分支**: `fix/phase11-governed-rag-integrity`
- **Python 环境**: 3.13.17 (`.venv`)
- **测试框架**: pytest 9.2.0, ruff 0.17.4, mypy 2.4.1
- **数据库目标**: PostgreSQL 18 + pgvector / SQLite 3 (FTS5)

---

## 2. 状态维度与概念混淆的根因分析

在本次专项整改前，系统代码中存在严重的状态混淆，将以下本应独立的五个概念混为一谈：
1. **Document Lifecycle**：文档是否在生命周期内处于有效状态（`DRAFT`, `ACTIVE`, `SUPERSEDED`, `ARCHIVED`, `DELETED`）。
2. **Approval Workflow State**：法务或企业合规部门是否已完成审批（`PENDING_REVIEW`, `APPROVED`, `REJECTED`, `REVOKED`）。
3. **Knowledge Source Type**：知识来源的业务资产类别（`TEMPLATE`, `APPROVED_RULE`, `HISTORICAL_CASE`, `LEGAL_REFERENCE`）。
4. **Authority Capability**：知识在审核决策中被允许充当的依据角色（`can_drive_mandatory_finding`, `can_act_as_standard_baseline`）。
5. **Effective Version**：条款引用的时态版本与内容一致性哈希（`source_version`, `source_hash`, `effective_at`）。

### 典型的代码级概念混淆示例：

```python
# 错误逻辑：contract_kb/storage.py
is_std_template = bool(
    c.get("is_company_standard")
    or doc.get("document_type") == "contract_template"
    or doc.get("status") == "ACTIVE"  # 混淆：文档 ACTIVE != 条款已获法务 APPROVED
)
default_appr = "APPROVED" if is_std_template else "PENDING_REVIEW"
if is_std_template and (
    not c.get("approval_status") or c.get("approval_status") == "PENDING_REVIEW"
):
    clause_appr = "APPROVED"  # 致命：未经审批自动被提升为企业正式依据！
```

以及在 `storage_pg.py` 中的降级硬编码：

```python
# 错误逻辑：contract_kb/storage_pg.py (L908)
"'TEMPLATE', 'APPROVED' FROM clauses c"  # 伪造元数据！未审批或历史合同被虚构为 APPROVED TEMPLATE
```

---

## 3. 影响面与安全风险评估

1. **合规审查有效性穿透**：未获审批的合同草稿或部门自制合同，仅需以标准模板名义导入，即可成为判定其他合同“违约”或“违规”的强制依据。
2. **跨租户知识混淆**：若检索未强制在数据库层下发 `tenant_id`，且引文核验未核对租户归属，多租户隔离将被穿透。
3. **虚假证据与幻觉掩盖**：`verify_retrieval_hits` 在无任何证据召回时汇报 `1.0` 依据度，导致系统在缺乏事实支撑时报告虚假的“高置信度”。
4. **Prompt 权威投毒**：历史案例未经授权混入 LLM 上下文，导致 LLM 将历史个案协商条款当作企业标准强加给待审合同。

---

## 4. 可复现性验证结论

通过对抗性测试用例集 `tests/security/test_phase11_reproduce_flaws.py`，前述问题均已得到 100% 确定性的失败复现，证明缺陷的真实存在。
整改后，该测试集及扩展的对抗矩阵必须全部转绿。
