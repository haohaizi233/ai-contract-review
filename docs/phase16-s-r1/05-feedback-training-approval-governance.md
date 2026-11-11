# Phase 16-S-R1: 05 - 用户反馈治理、审批权属隔离与 SFT 微调防投毒

## 一、审计缺陷与假阳性根因诊断 (P1-R1-05 与 P1-R1-06)

### 1. 服务层自审批漏洞 (P1-R1-05)
在上一轮 Phase 16-S 中，`record_user_feedback()` 直接接受客户端 payload 字典中的 `is_approved=True` 或 `is_approved_for_training=True`，导致攻击者可以通过向普通反馈接口发送构造参数，将未经法务审核的恶意、篡改或污染样本直接注入企业已审批微调集 `reviewed_events.jsonl`，造成严重的数据投毒（Data Poisoning）风险。

### 2. 测试假阳性根因 (P1-R1-06)
上一轮测试 `test_client_cannot_self_approve_training_data` 存在重大设计错误：
```python
# 测试代码构造了 doc_id: unique_doc
sample_data = {"doc_id": unique_doc, "is_approved": True, ...}
# 原测试断言：
assert unique_doc not in content
```
**假阳性真相**：
底层数据模型 `FeedbackSampleRecord` 根本没有定义 `doc_id` 属性，它只存储 `task_id` 和 `annotation_id`！
因此，即使自提自批记录已经被违规写入了 `reviewed_events.jsonl`，文件内容中也永远不可能出现 `unique_doc`！原测试因此“假阳性通过”，严重掩盖了数据未受保护的致命漏洞。

---

## 二、治理体系与权限隔离设计

本次整改建立了严格的四层治理架构：

```text
[阶段 1: 反馈收集]
  普通用户/初级法务通过 POST /api/diff/feedback 提交建议
       ↓
  submit_feedback() 强制置 is_approved=False
       ↓
  仅写入 tenant/raw_feedback/ 原始待审目录 (PENDING_REVIEW)
  绝不进入已审批训练集！

[阶段 2: 授权审批流程]
  仅管理员或高级法务复核员 (roles: admin/reviewer) 登录
       ↓
  发起 POST /api/diff/feedback/approve
       ↓
  approve_feedback_for_training()
  校验调用方身份与角色，记录 approved_by, approved_at, approval_reason
       ↓
  正式追加写入 tenant/reviewed_feedback/reviewed_events.jsonl (APPROVED)

[阶段 3: 审计与撤销管理]
  法务管理员发现商务或法务条款变更
       ↓
  调用 revoke_training_approval()
       ↓
  追加记录标记 approval_revoked=True，作废该样本的微调资格

[阶段 4: SFT 训练集导出隔离]
  仅 Admin 角色可调用 POST /api/diff/export_training_data
       ↓
  export_sft_dataset(require_approval=True)
       ↓
  严格过滤已撤销样本，仅导出具备合法审批签名的有效样本
  按租户目录物理隔离，绝对禁止跨租户数据穿透！
```

### 租户路径强校验 (`sanitize_tenant_id`)
防止恶意调用者通过 `tenant_id="../../../etc"` 进行目录遍历攻击，或在 Windows 下使用保留设备名（如 `CON`, `PRN`, `AUX`）造成 I/O 阻塞。

---

## 三、代码改动定位

1. **`contract_kb/application/agents/knowledge_adapter.py`**：
   - 增加 `sanitize_tenant_id` 与 `get_tenant_feedback_dir`；
   - `FeedbackSampleRecord` 扩充审批审计元数据字段；
   - `record_user_feedback` 对字典类型传入的自批参数无条件置为 `False`；
   - 新增 `submit_feedback`（纯待审）、`approve_feedback_for_training`（需受权审批人）与 `revoke_training_approval`（可审计撤销）；
   - `export_sft_dataset` 移除跨租户 fallback，强化租户边界。
2. **`contract_kb/interfaces/web/routes/feedback.py`**：
   - `handle_post_feedback` 强置 `is_approved = False`；
   - 新增 `handle_post_approve_feedback`（路由 `/api/diff/feedback/approve`），基于 RBAC 门禁核验。

---

## 四、测试与变异测试闭环

1. **真实断言修复 (`tests/security/test_feedback_approval_authorization.py`)**：
   - 彻底清除对无意义 `doc_id` 的搜索；
   - 逐行将 `reviewed_events.jsonl` 反序列化为 JSON 字典，严格断言该条记录的 `task_id` 和 `annotation_id` 绝对不存在；
   - 通过真实 HTTP 接口发送带 `is_approved: True` 的 payload，验证仅落入 `raw_feedback`。
2. **独立治理生命周期验证 (`tests/security/test_phase16_r1_training_approval.py`)**：
   - `test_submit_feedback_strictly_pending_review`: 提交严格置为 `is_approved_for_training=False`；
   - `test_unauthorized_user_cannot_approve_training_data`: 普通 viewer 审批返回 403 / PermissionError；
   - `test_authorized_reviewer_approves_and_revokes_training_data`: 授权审批并可审计撤回；
   - `test_export_sft_dataset_filters_revoked_and_cross_tenant`: 导出严格过滤撤销与跨租户数据。
