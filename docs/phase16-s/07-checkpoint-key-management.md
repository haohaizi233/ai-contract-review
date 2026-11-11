# Phase 16-S: 07 - Checkpoint 完整性与密钥管理 (Checkpoint Key Management)

## 1. 对应实际代码路径
- `contract_kb/infrastructure/checkpoints/storage.py`
  - `get_checkpoint_secret()`: 生产模式密钥缺失强门禁
  - `_compute_checkpoint_signature()`: 基于 HMAC-SHA256 的检查点内容防篡改签名
  - `CheckpointStorage.save_checkpoint()`: 原子落盘保存
  - `CheckpointStorage.verify_and_restore()`: 恢复前防篡改核验
  - `CheckpointVerificationResult`: 具备结构化理由与恢复结果的数据模型

## 2. 原始问题 (P1-03 & Case 11 & Case 12)
1. **公开硬编码固定默认密钥 (P1-03)**：
   历史代码中包含：
   ```python
   CHECKPOINT_SECRET = os.environ.get(
       "CONTRACT_KB_CHECKPOINT_KEY", "contract_kb_checkpoint_integrity_secret_key_v14"
   )
   ```
   生产环境若未配置环境变量，会静默使用开源仓库中的公开固定字符串，攻击者可自行离线计算合法 HMAC 签名，篡改或伪造任意审查结论。
2. **缺乏篡改恢复语义区分**：
   恢复失败时仅返回简单 False 或空，无法向法务审计系统精确反馈是“未找到检查点”还是“签名被篡改拦截”。

## 3. 修复方案
1. **生产环境强报错 (Fail-Closed)**：
   在 `get_checkpoint_secret()` 中明确加入环境门禁：
   ```python
   if os.environ.get("CONTRACT_KB_ENV", "").lower() == "production":
       key = os.environ.get("CONTRACT_KB_CHECKPOINT_KEY", "").strip()
       if not key or key == "contract_kb_checkpoint_integrity_secret_key_v14":
           raise RuntimeError(
               "生产环境必须配置高强度 CONTRACT_KB_CHECKPOINT_KEY，严禁使用公开默认密钥！"
           )
       return key
   ```
2. **防篡改 HMAC-SHA256 强绑定**：
   签名将 `fingerprint_hash`（涵盖母本哈希、规则版本、提示词版本、模型配置、批注摘要）与格式化排序后的已完成卡片内容 `raw_cards` 进行双重摘要，任何风险等级、建议正文、租户或用户变动均导致签名失效。
3. **结构化恢复语义**：
   通过 `CheckpointVerificationResult(is_valid, reason, restored_cards)` 明确区分签名有效放行与篡改拒绝。

## 4. 测试用例
- `tests/security/test_checkpoint_key_management.py`
  - `test_production_mode_requires_explicit_checkpoint_secret`
  - `test_checkpoint_tamper_detection`
  - `test_checkpoint_restores_cleanly_when_valid`
- `tests/security/test_phase16_reproduce_flaws.py`
  - `test_production_checkpoint_requires_secret`

## 5. 实际执行命令与结果
```bash
python -m pytest tests/security/test_checkpoint_key_management.py -v
```
运行结果：
```text
tests/security/test_checkpoint_key_management.py::test_production_mode_requires_explicit_checkpoint_secret PASSED
tests/security/test_checkpoint_key_management.py::test_checkpoint_tamper_detection PASSED
tests/security/test_checkpoint_key_management.py::test_checkpoint_restores_cleanly_when_valid PASSED
============================== 3 passed in 0.15s ==============================
```

## 6. 残余风险
- HMAC 保证了数据完整性（Integrity）与真实性（Authenticity），但不提供机密性加密（Confidentiality）。若服务器磁盘遭未授权物理窃取，检查点中的合同片段仍可能被明文查看。对于绝密级合同，建议底层存储采用 AES-256 加密或挂载全盘加密存储卷。
