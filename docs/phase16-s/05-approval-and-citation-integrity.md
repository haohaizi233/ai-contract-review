# Phase 16-S: 05 - 审批与引用核验完整性及 TOCTOU 防御 (Approval and Citation Integrity)

## 1. 对应实际代码路径
- `contract_kb/application/agents/legal_review_agent.py`
  - `review_clause()` 后置 TOCTOU 审批撤销复核逻辑（Line 730-760）
  - 缓存有效性复核逻辑（Line 415-442）
- `contract_kb/modules/retrieval/citation.py`: `CitationVerifier` 校验原文与引文一致性
- `contract_kb/modules/retrieval/governance.py`: 知识治理状态机与版本追踪

## 2. 原始问题
1. **TOCTOU (Time-of-Check to Time-of-Use) 审批撤销竞争漏洞**：
   Agent 在推理开始（T1）检索到一条 `APPROVED` 规则；在 LLM 长耗时生成期间（T2），法务管理员在后台发现该规则有误并执行了撤销（REVOKED）；在模型生成完毕后（T3），旧系统直接将依据旧规则生成的结论持久化并存入缓存，导致撤销的失效规则在系统中“诈尸”，误导法务人员。
2. **陈旧缓存跨版本污染**：
   母本或规则版本升级后，旧缓存键若未包含版本与租户信息，会把历史审查结果持续返回给当前用户。

## 3. 修复方案
1. **推理后 TOCTOU 证据有效性强复核**：
   在大模型生成返回后、存入持久缓存前，重新向数据库发起生效规则复核：
   ```python
   if storage is not None and hasattr(storage, "get_rules") and evidence_refs:
       active_rules_now = storage.get_rules(tenant_id=tenant_id, status="APPROVED")
       active_ids_now = {str(r.get("rule_id")) for r in active_rules_now}
       revoked_in_flight = [
           str(ref["rule_id"])
           for ref in evidence_refs
           if ref.get("approval_status") == "APPROVED"
           and ref.get("rule_id") not in active_ids_now
       ]
       if revoked_in_flight:
           result["review_status"] = ReviewStatus.NEEDS_HUMAN_REVIEW.value
           result["requires_human_review"] = True
           result["toctou_revocation_detected"] = True
           result["ai_analysis"] = (
               f"【规则撤销失效警报】所依据风控规则在审核期间已被法务管理员撤销！必须重新人工复核。"
           )
           return result  # 阻断存入持久缓存
   ```
2. **读缓存时的审批状态再核验**：
   即便命中了本地或 Redis 缓存，读取时依然核查缓存绑定的 `evidence_references` 中的规则是否依然处于 `APPROVED` 状态。若已撤销，立即主动删除该条缓存（Cache Eviction），触发重审。
3. **缓存键全量维度正规化绑定**：
   `_cache_key()` 联合 `(category, delta_type, std_span, rev_span, clause_title, tenant_id, template_id, template_version, rule_version)` 共同计算 SHA-256，任何版本变动均导致缓存自动失效。

## 4. 测试用例
- `tests/security/test_agent_governed_rules_fail_closed.py`
  - `test_toctou_in_flight_rule_revocation_triggers_alarm_and_blocks_cache`
  - `test_cache_invalidation_on_template_or_rule_version_change`

## 5. 实际执行命令与结果
```bash
python -m pytest tests/security/test_agent_governed_rules_fail_closed.py -k "toctou or cache" -v
```
运行结果：
```text
tests/security/test_agent_governed_rules_fail_closed.py::test_toctou_in_flight_rule_revocation_triggers_alarm_and_blocks_cache PASSED
tests/security/test_agent_governed_rules_fail_closed.py::test_cache_invalidation_on_template_or_rule_version_change PASSED
============================== 2 passed in 0.40s ==============================
```

## 6. 残余风险
- 后置复核依赖数据库查询延迟。在超高并发场景下，频繁复查生效规则可通过短 TTL（如 5 秒）只读缓存降低数据库开销。
