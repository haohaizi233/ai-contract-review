# Phase 13-R10: L1 工程验证与 L2 真实模型评测报告 (`12-real-model-evaluation.md`)

## 1. 评测分层声明（严格遵循 Gate 5 诚实性准则）

根据 Phase 13-R 规范第 3.2 节与第 15 节要求，本报告严格区分 **L1（工程确定性验证与契约测试）** 与 **L2（真实外部大模型端到端语义评测）**，绝不将 Mock 或本地规则兜底结果冒充为真实大模型语义评测指标。

| 评测层级 | 状态 | 环境与说明 |
| :--- | :--- | :--- |
| **L1：工程链路、安全门禁、可信 RAG 过滤、结构对齐与规则兜底验证** | **PASS (已执行并通过)** | 本地受控沙箱执行，覆盖 Case A–J 全部 10 类强制场景，552 项自动化测试通过。 |
| **L2：真实大模型 (`deepseek-chat` / 生产 LLM) 在线语义质量评测** | **BLOCKED_RESOURCE / NOT_EXECUTED** | 当前本地沙箱未注入经过企业安全审批的真实外部大模型 API Key，且默认安全策略 `CONTRACT_KB_ALLOW_EXTERNAL_LLM=false` 处于 Fail-Closed 阻断状态。 |

## 2. L1 强制测试场景 (Case A – Case J) 验收结果

| 场景编号 | 场景描述 | 验证测试文件 | 结果 |
| :--- | :--- | :--- | :--- |
| **Case A** | 真实企业名称（如“广东北极星微电子有限公司”）与金额保留，严禁出现“深圳市某某科技有限公司”等硬编码 | `tests/contract/test_contract_source_truth.py` | **PASS** |
| **Case B** | 12 行分阶段官费表格完整保留（12 行 vs 3 行对照），严禁静默截断为 2 行 | `tests/contract/test_contract_source_truth.py` | **PASS** |
| **Case C** | 已撤销规则 (`REVOKED`) 严禁被引用，仅已审批 (`APPROVED`) 规则进入 Citation | `tests/integration/test_legal_agent_trusted_rag.py` | **PASS** |
| **Case D** | 外部 LLM 未授权或超时故障时，显式标记 `RULE_REVIEW_COMPLETED`，严禁冒充 `AI_REVIEW_COMPLETED` | `tests/integration/test_legal_agent_trusted_rag.py`, `tests/integration/test_agent_worker_recovery.py` | **PASS** |
| **Case E** | 金额、比例、期限篡改（如“5个工作日”改“45个工作日”、“30%”改“3%”）精准检出 | `tests/e2e/test_contract_diff_source_fidelity.py` | **PASS** |
| **Case F** | 两个用户并发上传不同合同，会话目录、任务队列、取消操作与 SSE 完全隔离 | `tests/integration/test_agent_session_isolation.py` | **PASS** |
| **Case G** | 文档哈希、母本哈希、规则版本等任一因子变更或 JSON 篡改时，检查点拒绝恢复 | `tests/integration/test_agent_checkpoint_fingerprint.py` | **PASS** |
| **Case H** | 责任方向反转（“乙方承担全部赔偿责任”改为“甲方承担全部赔偿责任”）精准标记主体变更风险 | `tests/e2e/test_contract_diff_source_fidelity.py` | **PASS** |
| **Case I** | 管辖法院异地篡改（“深圳仲裁委员会”改为“哈尔滨市松北区人民法院”）精准检出 | `tests/e2e/test_contract_diff_source_fidelity.py` | **PASS** |
| **Case J** | 扫描件 PDF 触发 `OCR_REQUIRED` 告警并生成 `scanned_placeholder`，严禁生成虚假无差异合规结论 | `tests/e2e/test_contract_diff_source_fidelity.py` | **PASS** |

## 3. L2 真实大模型接入验收复现手册

在具备企业内网大模型网关或受控 API 凭证的预发环境中，按以下步骤执行 L2 验收：
```powershell
$env:CONTRACT_KB_ALLOW_EXTERNAL_LLM="true"
$env:CONTRACT_KB_LLM_BASE_URL="https://api.deepseek.com"
$env:CONTRACT_KB_LLM_API_KEY="<YOUR_APPROVED_API_KEY>"
$env:CONTRACT_KB_LLM_MODEL="deepseek-chat"
python -m pytest tests/integration/test_legal_agent_trusted_rag.py -v
```
