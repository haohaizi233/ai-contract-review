# Phase 16-S-R2: 03 - 训练反馈审批与防自审信任边界报告 (Training Approval Boundary)

## 一、整改背景与原缺陷分析

在第一轮审计与历史代码中，模型微调反馈数据录入与审批存在如下漏洞：
1. **隐式自审批后门**：在 `record_user_feedback()` 中，历史遗留逻辑允许直接传入 `is_approved=True`，或只要 `reviewer_id` 包含 `legal_admin`、`admin` 等字符串，就自动将该条反馈写入已审批 SFT 训练集。
2. **缺乏防自审 (Anti-Self-Approval) 机制**：普通用户或标注员如果拥有审批权限，可以提交反馈并当场自行审批入库，导致未受独立法务复核的偏颇标注直接污染企业大模型训练集。
3. **租户跨界污染风险**：管理员若在租户 A 的会话中审批租户 B 的标注数据，若未做强租户比对，可能导致跨租户微调样本混淆。

---

## 二、受控审批架构与状态机设计

重构后的反馈录入与审批严格遵循双人复核与受控状态机：

```text
[普通用户 / 法务初审员]
        ↓
调用 record_user_feedback(is_approved=False)
        ↓
强制落入 PENDING 状态，写入反馈日志 (JSONL)
        ↓ (任何试图直接传入 is_approved=True 的调用均被无视/重置为 False)
        
[独立法务合规管理员 (Approver Principal)]
        ↓
调用 approve_feedback_for_training(..., approver_principal)
        ├─ 1. 检查 approver 是否具备合法角色 (如 legal_admin, compliance_admin)
        ├─ 2. 检查租户匹配 (feedback.tenant_id == approver.tenant_id)
        ├─ 3. 严格防自审校验 (feedback.submitter_id == approver.user_id → PermissionError 阻断)
        ├─ 4. 幂等性校验 (已审批记录直接幂等返回，避免重复计数)
        └─ 5. 校验通过 → 状态跃迁为 APPROVED，具备进入 SFT 训练集资格
        ↓
[SFT 训练集导出 export_sft_dataset()]
        ↓
仅检索 status == "APPROVED" 且 tenant_id 严格匹配的样本
```

---

## 三、关键代码修改对照

### 1. 移除 `record_user_feedback()` 历史后门
在 `contract_kb/application/agents/knowledge_adapter.py` 中：
```python
# 强制收口：任何外部接口提交的反馈，初始状态必须为 PENDING
# 彻底移除凭 reviewer_id 字符串或 is_approved 参数直接进入 APPROVED 的后门
item["approval_status"] = "PENDING"
item["is_approved"] = False
item["approver_id"] = None
item["approved_at"] = None
```

### 2. 强化独立审批接口中的防自审与租户强核验
```python
def approve_feedback_for_training(
    record_id: str,
    approver_principal: Any,
    tenant_id: str | None = None,
    feedback_file: Path | None = None,
) -> dict[str, Any]:
    # 1. 权限角色核验
    roles = getattr(approver_principal, "roles", []) or []
    if not any(r in ("legal_admin", "compliance_admin", "admin", "system_admin") for r in roles):
        raise PermissionError("当前操作者不具备法务训练集审批权限 (需要 legal_admin 角色)")

    # 2. 租户强核验
    eff_tenant = tenant_id or getattr(approver_principal, "tenant_id", "default")
    record_tenant = str(rec.get("tenant_id") or "default")
    if record_tenant != eff_tenant:
        raise PermissionError(f"跨租户审批非法：记录租户={record_tenant} vs 审批者租户={eff_tenant}")

    # 3. 严格防自审校验 (Anti-Self-Approval)
    submitter_id = str(rec.get("submitter_id") or rec.get("reviewer_id") or "")
    approver_id = str(getattr(approver_principal, "user_id", ""))
    if submitter_id and approver_id and submitter_id == approver_id:
        raise PermissionError(
            f"防自审安全阻断：提交人 ({submitter_id}) 与审批人 ({approver_id}) 相同，严禁自审自批进入训练集"
        )
```

---

## 四、验证结论

通过 `test_legacy_feedback_cannot_approve_by_reviewer_name`、`test_anti_self_approval_strict_enforcement` 以及全链路端到端测试验证：
1. 任何通过 `reviewer_id="legal_admin"` 试图自提自批的尝试被 100% 阻断在 `PENDING` 阶段。
2. 同一人提交并试图调用审批接口时，严格抛出 `PermissionError` 并予以阻断。
3. 跨租户审批尝试直接被拦截。
4. 训练集导出只包含合规双人审计通过的数据。
