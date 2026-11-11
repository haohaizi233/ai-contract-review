# Phase 16-S-R2: 04 - 缓存失效机制与空证据防穿透报告 (Cache Knowledge Invalidation)

## 一、整改背景与原缺陷分析

在第一轮整改中，法务审阅 Agent 的缓存存在如下安全性与准确性缺陷：
1. **空证据缓存无法动态失效**：当法务库中最初没有某类别规则时，Agent 推理产生的结论带有“无企业规则约束”的空证据列表并被缓存。当法务团队随后在知识库中发布并审批了针对该类别的新风控基准时，Agent 在面对相同条款时仍然直接命中旧的空证据缓存，导致新发布的法律红线完全无法生效。
2. **缓存核验异常时未 Fail-Closed**：在尝试核验缓存有效性时，若数据库连接断开或抛出异常，原逻辑在部分路径下可能放行旧缓存。
3. **版本变更未穿透至 Cache Key**：模板版本、法规版本未与缓存 Key 深度绑定。

---

## 二、可信缓存核验与失效架构

针对上述缺陷，在 `LegalReviewAgent.review_clause()` 中建立了严格的缓存有效性二次核查机制：

```text
计算 Cache Key (包含 category, delta_type, std_span, rev_span, template_ver, rule_ver)
               ↓
          缓存是否存在？
      ├── 否 → 执行完整检索与模型研判
      └── 是 → 获取缓存记录 cached_entry
               ↓
          缓存中 evidence_references 是否为空？
      ├── 否 (包含具体规则引用):
      │     ↓
      │  向 Storage 回查引用的规则状态与当前版本
      │     ├─ 规则被撤销 (REVOKED) 或不存在 → 缓存判定失效！重新研判
      │     ├─ 规则版本升级或哈希变更 → 缓存判定失效！重新研判
      │     └─ 数据库查询异常 → Fail-Closed 判定失效！重新研判
      │
      └── 是 (空证据引用):
            ↓
         向 Storage 动态查询当前租户/类别下是否存在最新审批生效的规则
            ├─ 存在匹配的 APPROVED 规则 → 缓存立即失效！执行研判以吸收最新规则
            ├─ 数据库查询异常 → Fail-Closed 判定失效！重新研判
            └─ 数据库确认依然无匹配规则 → 允许安全命中缓存
```

---

## 三、关键代码修改对照

在 `contract_kb/application/agents/legal_review_agent.py` 中：
```python
# 缺陷 7 修复：空证据缓存重新验证逻辑
# 当缓存记录的 evidence_references 为空，但知识库中后来新增了已审批生效的规则时，必须强制失效
if cached_res and not cached_evidence and storage is not None and hasattr(storage, "get_rules"):
    try:
        current_active_rules = storage.get_rules(
            tenant_id=eff_tenant, status="APPROVED"
        )
        if isinstance(current_active_rules, list):
            # 检查是否有与当前差异点类别匹配的生效规则
            matching_new_rules = [
                r for r in current_active_rules
                if str(r.get("clause_category") or r.get("category") or "").upper() == str(cat).upper()
                or str(r.get("delta_type") or "").upper() == str(dt).upper()
            ]
            if matching_new_rules:
                # 知识库已有新增合规规则，旧的空证据结论不可再复用
                cached_res = None
    except Exception:
        # Fail-closed: 无法验证当前知识库状态时，不可直接放行空证据缓存
        cached_res = None
```

同时在 TOCTOU 竞争防护中：
```python
if toctou_revocation_detected or has_unverified_rule_citation:
    skip_cache_write = True  # 严禁将存在安全嫌疑的结论写入缓存
```

---

## 四、验证结论

通过 `test_empty_evidence_cache_requires_revalidation` 与 `test_cache_invalidation_on_template_or_rule_version_change` 测试验证：
1. 一旦知识库中审批发布了新规则，先前的空证据缓存立即失效，Agent 自动采用新规则研判。
2. 规则版本变更（如 v1 -> v2）生成互斥的 Cache Key。
3. 数据库故障时，缓存核验严格遵循 Fail-Closed，拒绝放行未经确认的旧缓存。
