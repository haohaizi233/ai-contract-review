# Phase 13-R2：Agent 任务身份绑定、检查点防篡改与并发治理报告 (04-agent-identity-and-checkpoints)

## 1. 12 维防篡改执行指纹 (`ReviewExecutionFingerprint`)

为解决旧版仅依靠 `doc_name + template_name + len(anns)` 导致同名不同内容合同、母本升级或知识库变更后错误复用旧结论的问题，在 `contract_kb/domain/models/review_identity.py` 中定义了 `ReviewExecutionFingerprint`，强制绑定 12 维要素：

1. `tenant_id`（租户归属）
2. `user_id`（审查用户归属）
3. `file_sha256`（送审文件全文 SHA-256 哈希）
4. `template_id`（匹配基准母本 ID）
5. `template_content_hash`（基准母本内容哈希）
6. `template_version`（母本版本号）
7. `rule_set_version`（企业审核规则版本）
8. `kb_snapshot_version`（知识库快照版本）
9. `agent_prompt_version`（Agent 提示词版本）
10. `model_config_version`（模型配置版本）
11. `diff_engine_version`（Diff 引擎版本）
12. `annotations_fingerprint`（所有待审批注项的确定性内容指纹）

---

## 2. 检查点 8 项安全门禁与原子落盘 (`contract_kb/agent_checkpoint.py`)

`CheckpointStorage` 实现了：
- **原子写入**：先写入 `.tmp_{ns}` 临时文件，再通过 `replace()` 原子替换目标文件；
- **租户目录物理隔离**：检查点存储于 `data/contract-kb/agent_checkpoints/{tenant_id}/ckpt_{hash}.json`；
- **恢复时严格校验 8 项门禁 (`verify_and_restore`)**：
  1. 租户 ID 一致性 (`TENANT_MISMATCH`)
  2. 用户所有权一致性 (`USER_MISMATCH`)
  3. 送审文件 SHA-256 一致性 (`FILE_CONTENT_MODIFIED`)
  4. 母本 ID、内容哈希与版本一致性 (`TEMPLATE_VERSION_CHANGED`)
  5. 规则集与知识库快照版本一致性 (`KNOWLEDGE_BASE_UPDATED`)
  6. 待审批注内容指纹一致性 (`ANNOTATION_ITEMS_CHANGED`)
  7. SHA-256 内容签名防篡改核验 (`TAMPERED_CHECKPOINT_SIGNATURE_MISMATCH`)
  8. 已完成卡片列表结构有效性 (`INVALID_CARDS_STRUCTURE`)

任一门禁不满足立即拒绝恢复，并返回结构化失效原因。

---

## 3. 防僵尸线程与重复提交治理 (`contract_kb/agent_queue_manager.py`)

- **代际令牌 (`_worker_generation`)**：每次启动新任务、取消或重置时递增代际令牌；旧线程在进入循环与写回结果前双重核对 `self._worker_generation == my_generation`，不一致立即丢弃结果并退出。
- **Watchdog / 跳过防二次提交**：当某张批注卡片已被 Watchdog 超时接管或被人工跳过（`target.get("_aiDone") is True`）时，迟到的慢工作线程返回后自动丢弃结果，严禁覆盖状态或二次广播 `card_complete`。
