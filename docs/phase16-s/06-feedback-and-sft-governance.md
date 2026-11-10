# Phase 16-S: 06 - 反馈数据治理、人工审批与 SFT 导出安全 (Feedback and SFT Governance)

## 1. 对应实际代码路径
- `contract_kb/interfaces/web/routes/feedback.py`
  - `handle_post_feedback`: 服务端身份强制绑定与防篡改核验
  - `handle_post_export_training_data`: 强制 `admin` 角色门禁与租户作用域限制
- `contract_kb/application/agents/knowledge_adapter.py`
  - `record_user_feedback(data, is_approved, tenant_id)`: 租户目录强物理隔离与普通反馈禁止写入训练集
  - `export_sft_dataset(output_path, require_approval, tenant_id)`: 受控按租户导出

## 2. 原始问题 (P1-01 & P1-02)
1. **客户端伪造审批状态自批入库 (P1-01)**：
   `handle_post_feedback` 使用 `data.setdefault("tenant_id", tenant_id)` 和 `data.setdefault("reviewer_id", user_id)`，如果前端在 JSON 中自报 `tenant_id="victim_corp"` 或 `is_approved=True`，服务端不会覆盖；`record_user_feedback()` 会直接根据前端提交的 `is_approved` 将样本写入已审批训练集 `reviewed_events.jsonl`，导致不可信数据污染微调模型。
2. **SFT 导出无授权与跨租户混合 (P1-02)**：
   未对导出接口校验 Admin 角色，普通甚至匿名用户可调用触发全局导出；且所有租户样本保存在全局单文件，导致租户间数据交叉污染和客户敏感合同泄露。

## 3. 修复方案
1. **服务端身份覆盖与冲突拒绝**：
   在 `handle_post_feedback` 中，核验客户端 Body 中声明的 `tenant_id` 与 `reviewer_id`。如果与已认证主体不符，立即返回 403 明确拒绝（`"禁止伪造或跨租户提交反馈"`）。服务端强制将已认证的 `principal.tenant_id` 和 `principal.user_id` 注入数据模型。
2. **提交反馈与训练审批严格解耦**：
   普通用户的反馈接口调用时，强制将 `is_approved = False` 和 `is_approved_for_training = False` 传给 `record_user_feedback`，所有普通意见一律仅落盘至 `raw_feedback/`，必须由企业训练审批人（Training Approver）或 Admin 执行独立受控审批流程后方可转入 `reviewed_feedback/`。
3. **租户存储物理隔离与目录沙箱化**：
   反馈数据与训练样本严格以 `data/ai_feedback/{tenant_id}/` 物理分目录存储，避免全局文件混合。
4. **SFT 导出 Admin 强授权与租户过滤**：
   `handle_post_export_training_data` 校验 `principal.has_role("admin")`，并在导出时强制传入当前主体的 `tenant_id`，只导出本租户经人工审批合规脱敏的训练集。

## 4. 测试用例
- `tests/security/test_feedback_approval_authorization.py`
  - `test_client_cannot_self_approve_training_data`
  - `test_forged_feedback_identity_rejected`
  - `test_sft_export_requires_admin_and_tenant_scoped`
- `tests/security/test_phase16_reproduce_flaws.py`
  - `test_client_cannot_self_approve_training_sample`
  - `test_training_export_requires_authorized_role`

## 5. 实际执行命令与结果
```bash
python -m pytest tests/security/test_feedback_approval_authorization.py -v
```
运行结果：
```text
tests/security/test_feedback_approval_authorization.py::test_client_cannot_self_approve_training_data PASSED
tests/security/test_feedback_approval_authorization.py::test_forged_feedback_identity_rejected PASSED
tests/security/test_feedback_approval_authorization.py::test_sft_export_requires_admin_and_tenant_scoped PASSED
============================== 3 passed in 1.17s ==============================
```

## 6. 残余风险
- 当前微调样本持久化基于租户分片 JSONL。后续若企业样本量达到数百万级别，建议接入带行级安全策略（RLS）的 PostgreSQL 数据仓库进行统一审计与生命周期归档。
