# Phase 14-R: 人工审核闭环与训练反馈治理 (06-human-review-and-feedback-loop)

## 一、真实缺陷复现 (DEFECT-P1-01)

1. **接口签名与调用方式不匹配**：
   在 `contract_kb/legal_agent_kb.py` 中，`record_user_feedback` 原定义接收 6 个独立位置参数：
   ```python
   def record_user_feedback(task_id: str, file_sha256: str, annotation_id: str, sample_data: dict, ...)
   ```
   然而在 `tools/contract_diff_web.py` 的 `/api/diff/feedback` 处理逻辑中，却将其作为单参数调用：
   ```python
   saved_path = record_user_feedback(data)
   ```
   导致运行时直接抛出 `TypeError: record_user_feedback() missing 5 required positional arguments` 致命崩溃。
2. **人工研判缺乏状态机流转控制与审计留痕**：
   在 `/api/diff/human_decision` 中，原实现直接通过内存覆写将批注的 `human_review_status` 改为 `HUMAN_ACCEPTED` 等，没有任何状态合法性校验；已采纳的条款可被外部请求直接倒流回 `PENDING`，且无任何审核人、时间戳与修改前后的审计留痕。

---

## 二、整改实现与闭环治理

### 2.1 签名泛化与调用兼容

升级 `contract_kb/legal_agent_kb.py` 中的 `record_user_feedback` 函数：
- 同时支持传入单个 `dict` 载荷（Web API 模式）或分项传参调用；
- 自动提取 `task_id`、`file_sha256`、`annotation_id`、`tenant_id`、`reviewer_id`；
- 统一落盘到受控目录，并强制执行个人隐私与商业机密（手机号、身份证号、银行卡号）脱敏替换。

### 2.2 人工审核 7 态生命周期状态机

在 `contract_kb/domain/models/review_identity.py` 中规范化定义 `HumanReviewStatus` 状态机：

```text
PENDING ──> AI_REVIEWING ──> AI_REVIEWED ──> HUMAN_REVIEW_REQUIRED ──> HUMAN_ACCEPTED
   │                                                 ├──> HUMAN_REJECTED
   └─────────────────────────────────────────────────┴──> HUMAN_MODIFIED
```

通过 `can_transition_to(target)` 校验非法跃迁：
- 严禁从未审核直接变为非法内部态；
- 严禁从最终裁决态（`HUMAN_ACCEPTED`）非法倒流回未审核态（`PENDING` / `AI_REVIEWING`）；
- 任何非法流转请求均被拦截并返回 `HTTP 400 Bad Request`。

### 2.3 结构化人工审核审计记录 (`human_audit_trail`)

每次人工做出裁决（采纳、驳回、修改建议）时，在对应卡片节点中持久化记录不可篡改的审计追踪条目：
```json
{
  "from_status": "AI_REVIEWED",
  "to_status": "HUMAN_MODIFIED",
  "user_id": "legal_admin",
  "tenant_id": "default",
  "timestamp": 1700000000.0,
  "modified_advice": "增加单方解约提前30天书面通知约定"
}
```
并立即触发 `sess._save_checkpoint()` 完成持久化保存。

### 2.4 微调样本与评估测试集物理隔离

在 `export_sft_dataset` 中：
- 只有被明确标记为 `is_approved_for_training=True` 的高质量人工采纳/修改样本，方可进入微调训练集；
- 采用文档哈希确定性切分保留集（Holdout Set），确保同合同、同条款内容绝不发生从训练集向评测集的穿透污染。

---

## 三、测试证明

- 测试文件：`tests/integration/test_agent_human_feedback_e2e.py`
  - 验证单参数字典调用与多参数调用均正常运行且脱敏生效；
  - 验证敏感信息（手机号）被安全脱敏为 `[手机号脱敏]`；
  - 验证状态机非法倒流防护有效；
- 自动化运行：全部用例一次性通过。
