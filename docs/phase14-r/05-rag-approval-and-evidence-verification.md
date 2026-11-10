# Phase 14-R: Agent 可信 RAG、证据链统一与 SSRF 防护 (05-rag-approval-and-evidence-verification)

## 一、真实缺陷场景 (DEFECT-P0-04, DEFECT-P1-05)

1. **静态字典冒充企业审批**：
   在 `contract_kb/legal_agent_kb.py` 中，存在一个全局静态 Python 字典 `GOVERNED_RULES_STORE`，其中的规则预置了 `approval_status="APPROVED"`。在某些代码分支下，Agent 直接读取该静态内存字典，导致即便企业管理员在数据库中撤销了某项规则（例如某项管辖条款废止），Agent 依然认为该规则处于已审批生效状态，输出已经过期的错误合规意见。
2. **引文校验弱校验绕过风险**：
   在 `CitationVerifier` 中，若引用条目缺失 `source_hash` 或声明版本与数据库真实版本不一致，早期存在未严格核验就放行的路径，导致模型幻觉生成的“虚假法条”未被有效拦截。
3. **SSRF 私网与内网探测漏洞**：
   在 `contract_kb/legal_agent.py` 中，用户可配置自定义 `base_url`。原 `_is_safe_endpoint_url` 仅粗糙过滤了 AWS 云元数据地址 `169.254.169.254`，未过滤 IPv4/IPv6 回环地址（`127.0.0.1`、`::1`）、RFC1918 私网网段（`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`）及全零地址（`0.0.0.0`），存在 SSRF 攻击隐患。

---

## 二、整改与加固措施

### 2.1 规则库数据库权威回查

在 `contract_kb/legal_agent_kb.py` 的 `retrieve_governed_rules()` 中，彻底引入数据库权威状态校验：
```python
def retrieve_governed_rules(
    category: str, storage: Any = None, tenant_id: str = "default"
) -> list[GovernedLegalRule]:
    # 优先从权威存储中读取当前租户生效规则
    if storage is not None and hasattr(storage, "get_rules"):
        db_rules = storage.get_rules(tenant_id=tenant_id, status="APPROVED")
        ...
```
当数据库中某规则被执行 `REVOKED` 撤销后，内存静态数据不再被当作权威依据；系统同步废止该规则的召回资格，实现企业规则撤销的毫秒级生效。

### 2.2 CitationVerifier 证据链强校验

在 `contract_kb/modules/retrieval/citation.py` 中：
- 严格校验 `tenant_id` 是否匹配，跨租户引用直接判定 `TENANT_MISMATCH`；
- 强校验引用的 `clause_id` 在租户数据库中是否存在（`MISSING_SOURCE`）；
- 校验引用的文档生命周期是否为 `ACTIVE`（`SOURCE_INACTIVE`）；
- 校验引用的 `source_hash` 与数据库真实文件哈希是否严格一致，哈希不符直接判定 `HASH_MISMATCH`；
- 校验引用携带的版本号是否与数据库当前生效版本一致（`VERSION_MISMATCH`）；
- 权威回查审批状态：若已被撤销，强制判定 `APPROVAL_REVOKED`；若未审批，判定 `APPROVAL_NOT_APPROVED`；
- 只有全部校验通过，才颁发 `VERIFIED` 状态并计入 `verified_count`。

### 2.3 SSRF 严格防护矩阵

在 `contract_kb/legal_agent.py` 的 `_is_safe_endpoint_url` 中，建立完备的 IP 解析与过滤矩阵：
- 解析 URL 主机名为 IP 地址（支持 IPv4 与 IPv6）；
- 严密拦截以下受保护网段：
  - `ip.is_loopback`（`127.0.0.0/8`, `::1`）；
  - `ip.is_private`（RFC1918 私网网段）；
  - `ip.is_link_local`（`169.254.0.0/16`, `fe80::/10`）；
  - `ip.is_unspecified`（`0.0.0.0`, `::`）；
  - `ip.is_reserved`；
- 支持生产环境通过白名单环境变量 `CONTRACT_KB_ALLOWED_INTERNAL_HOSTS` 受控放行特定的企业专有网关。

---

## 三、测试证明

- `tests/security/test_phase11_adversarial_suite.py`：18 项对抗性安全测试全部通过（包含撤销后即刻丧失权威性、同文本跨租户严格隔离等）；
- `tests/security/test_phase11_stage_evidence_fail_closed.py`：在中途撤销标准条款场景下，核验器准确降级 Finding 并标记为证据不足，要求人工介入；
- `tests/security/test_stale_evidence_blocking.py`：未审批与已废止条款拒绝通过核验。
