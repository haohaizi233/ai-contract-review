# Phase 16-S-R1: 04 - 缓存失效机制与 TOCTOU 审批撤销复核 Fail-Closed 严密重构

## 一、审计缺陷诊断 (P0-R1-03 与 P0-R1-04)

上一轮 Phase 16-S 实现中存在两个致命的“静默吞异常放行”缺陷：

### 1. 缓存核验吞异常 (P0-R1-03)
在读取缓存并回查数据库规则时：
```python
try:
    active_rules = storage.get_rules(...)
except Exception:
    pass  # 异常被静默吞掉，cache_valid 保持 True！
```
若数据库发生网络闪断、连接池耗尽或读超时，系统不仅没有安全阻断，反而将过时甚至已撤销规则绑定的旧缓存直接放行，返回给前端用户。

### 2. TOCTOU 复核吞异常并持久化脏缓存 (P0-R1-04)
在大模型推理完成后的 TOCTOU（Time-of-Check to Time-of-Use）二次核验阶段：
```python
try:
    active_rules_now = storage.get_rules(...)
except Exception:
    pass  # 异常被吞，继续执行 self._cache[cache_key] = result！
```
在数据库故障时，无法确认规则是否在推理期间被管理员撤销，但代码依然将模型结论标记为 `AI_REVIEW_COMPLETED` 并持久化落入磁盘缓存，导致错误结论长期污染系统。

### 3. 缓存越权击穿外发门禁
缓存读取发生在 `is_external_allowed()` 外部大模型外发授权校验之前，导致未授权环境（如单机离线模式）可以通过命中历史缓存，非法输出大模型生成物。

---

## 二、Fail-Closed 重构方案

本次整改对 `LegalReviewAgent.review_clause()` 的缓存与 TOCTOU 进行了彻底的严密重构：

```text
[阶段 1: 外部模型外发授权前置核验]
    if not self.is_external_allowed():
        → 立即切入本地规则兜底 (build_high_precision_fallback)
        → 绝对不进入大模型缓存查找，杜绝越权输出

[阶段 2: 缓存有效性回查 (Strict Fail-Closed)]
    if not force_refresh:
        if storage is None or not hasattr(storage, "get_rules"):
            cache_valid = False  # 缺存储直接作废缓存
        else:
            try:
                active_rules = storage.get_rules(...)
                # 校验 revision、有效截止期、规则描述是否有变更
                ...
            except Exception:
                cache_valid = False  # 数据库异常强制 Fail-Closed
        if not cache_valid:
            del self._cache[cache_key] # 淘汰脏缓存

[阶段 3: 推理后 TOCTOU 动态撤销复核]
    try:
        active_rules_now = storage.get_rules(tenant_id=eff_tenant_id, status="APPROVED")
        # 比对推理期间是否有规则被撤销、版本升级或内容更新
    except Exception as e:
        # 数据库异常必须 Fail-Closed 阻断入盘：
        result["review_status"] = ReviewStatus.NEEDS_HUMAN_REVIEW.value
        result["requires_human_review"] = True
        result["toctou_revocation_detected"] = True
        for ref in evidence_refs:
            ref["verification_status"] = "UNVERIFIED"
        return result  # 严格阻断写入缓存！
```

---

## 三、代码改动定位

**`contract_kb/application/agents/legal_review_agent.py`**：
1. **前置门禁调整**：将 `self.is_external_allowed()` 调整至 `self._cache` 查找之前执行；
2. **缓存查找防线**：捕获异常时无条件将 `cache_valid = False` 并删除缓存项；
3. **TOCTOU 阻断防线**：在 `try...except Exception as e` 分支中，设置 `review_status = NEEDS_HUMAN_REVIEW`，将所有证据状态置为 `UNVERIFIED`，记录详细审计错误并直接 `return result`，绝不执行 `self._cache[cache_key] = result`。

---

## 四、测试闭环验证

1. **单元与安全测试 (`tests/security/test_phase16_r1_cache_fail_closed.py`)**：
   - `test_cache_pre_authorization_gate`: 未授权时不命中 LLM 缓存；
   - `test_cache_lookup_db_exception_fails_closed`: 数据库故障时不复用旧缓存；
   - `test_cache_invalidated_on_rule_revision_or_description_change`: 规则版本升级或描述变更自动淘汰缓存；
   - `test_toctou_rule_revocation_triggers_human_review_and_aborts_cache`: 推理期间撤销规则触发人工复核且绝不入缓存。
2. **真实数据库集成测试 (`tests/integration/test_phase16_r1_rule_revocation_toctou.py`)**：
   - `test_real_db_toctou_revocation_detected`: 在真实 SQLite 中模拟推理期间异步执行 `UPDATE rules SET status='REVOKED'`，验证系统准确捕获状态突变，证据标记为 `REVOKED`，会话状态降级为 `NEEDS_HUMAN_REVIEW`。
