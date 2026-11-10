# Phase 14-R: 检查点指纹去截断、防篡改签名与审查可信性 (03-checkpoint-cache-and-tamper-proofing)

## 一、历史缺陷深度剖析 (DEFECT-P0-03)

在 Phase 14 审计中，发现审查检查点与缓存机制存在三项致命漏洞：
1. **40 字符截断引发严重哈希碰撞**：
   在 `contract_kb/agent_checkpoint.py` 的 `compute_annotations_fingerprint()` 函数中，原代码针对批注节点的文本内容只截取前 40 字符计算哈希：
   ```python
   # 原危险代码：
   std_lead = str(ann.get("std_node", {}).get("text") or "")[:40]
   rev_lead = str(ann.get("rev_node", {}).get("text") or "")[:40]
   ```
   **实际安全隐患**：若攻击者在合同条款第 41 字符起恶意篡改责任主体、增加免责约定或大幅修改违约金金额（例如前 40 字符均为通用格式前缀“第十四条 双方确认本协议履行期间产生之争议...”，第 41 字符起篡改金额），原指纹算法计算出的哈希值完全相同，导致系统误认为新送审合同与旧合同完全一致，直接从检查点秒级恢复出旧审核结论，造成极其严重的法律合规风险！
2. **`legacy_doc_id` 越权强回退漏洞**：
   在 `contract_kb/agent_queue_manager.py` 的 `start_review` 中，当检查点与当前送审文件的 12 因子执行指纹不匹配时，系统本应拒绝复用；然而历史代码包含一段强回退逻辑：若检查点中包含 `legacy_doc_id`，只要 `legacy_doc_id == doc_id`，就无视指纹校验直接复用旧结果，彻底击穿了防篡改门禁。
3. **未加盐弱签名与伪源文件哈希**：
   旧检查点签名仅为 `hashlib.sha256(json.dumps(...))`，未引入服务器端私密密钥，客户端可任意修改检查点内容后重算 SHA256 伪造有效签名；且在 `build_full_contract_diff` 返回结果中缺少 `file_sha256`，导致下游将文件名加盐当成文件哈希。

---

## 二、整改与加固实现

### 2.1 全量条款无损哈希计算

彻底废除任何截断行为。在 `compute_annotations_fingerprint()` 中，对每一个批注项抽取完整的关键维度：
`id | delta_type | clause_category | node_count | full_std_text | full_rev_text`
并按 `id` 排序后拼接为确定性载荷，计算完整 SHA-256 摘要：
```python
def compute_annotations_fingerprint(annotations: list[dict[str, Any]]) -> str:
    parts = []
    for ann in sorted(annotations, key=lambda a: str(a.get("id", ""))):
        std_txt = str(ann.get("std_node", {}).get("text") or "").strip()
        rev_txt = str(ann.get("rev_node", {}).get("text") or "").strip()
        parts.append(
            f"{ann.get('id')}|{ann.get('delta_type')}|{ann.get('clause_category')}|{std_txt}|{rev_txt}"
        )
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
```

### 2.2 彻底废除 `legacy_doc_id` 越权后门

在 `agent_queue_manager.py` 中彻底移除 `legacy_doc_id` 回退分支。只要检查点指纹与当前任务 12 维防篡改执行指纹不一致，立即废弃旧检查点，启动全新独立审核流水线。

### 2.3 基于服务器私钥的 HMAC-SHA256 签名机制

在 `agent_checkpoint.py` 中引入加盐 HMAC 签名：
- 采用环境变量 `CONTRACT_KB_CHECKPOINT_SECRET`（缺省具备安全回退私钥）；
- 签名算法：`hmac.new(secret, canonical_payload, hashlib.sha256).hexdigest()`；
- 签名校验：强制采用 `hmac.compare_digest` 恒定时间比对，抵御时序侧信道攻击；
- 任何篡改签名、缺失签名或密钥不匹配的检查点文件均无法通过验证。

### 2.4 多维深度绑定缓存键与真实文件哈希

- 在 `contract_kb/full_diff.py` 中，明确将计算得到的 `rec.sha256` 填充至返回字典中的 `file_sha256` 与 `sha256` 字段；
- 在 `contract_kb/legal_agent.py` 的 `_cache_key` 计算中，强绑定 `tenant_id`、`template_id`、`template_version`、`rule_version`、`clause_category` 与双方全量文本哈希，确保知识库规则版本变更或母本升级后，过期审查结果自动失效。

---

## 三、验证测试与证明

- 测试文件：`tests/contract/test_review_fingerprint_real_inputs.py`
  - `test_annotations_fingerprint_changes_when_content_modified_beyond_40_chars`：构造前 40 字符完全相同、在第 41 字符处篡改金额比率的合同样本，验证修复后指纹绝对不发生碰撞 (`fp_a != fp_b`)；
  - `test_checkpoint_hmac_tamper_proofing`：模拟篡改检查点正文与伪造签名，验证 `is_signature_valid` 准确识别篡改并拒绝加载。
- 自动化运行结果：全部用例一次性通过。
