# Phase 12-C: 轻量法律关系抽取与跨条款引用检索规范

- **状态**: IMPLEMENTED & TESTED
- **核心组件**: [`contract_kb/modules/retrieval/relations.py`](file:///d:/Users/Administrator/Desktop/AI合同/contract_kb/modules/retrieval/relations.py)
- **测试用例**: [`tests/unit/test_legal_relation_extraction.py`](file:///d:/Users/Administrator/Desktop/AI合同/tests/unit/test_legal_relation_extraction.py)

---

## 1. 架构目标与轻量原则
区别于重型的通用知识图谱数据库（如 Neo4j，维护成本高且引入额外网络依赖），本项目采用**“轻量关系抽取与内存图遍历”**：
- 聚焦于合同法律审查最敏感的 9 类法律关系；
- 依托现有文档 AST 与确定性语法槽位解析；
- 严格限制跨条款跳转深度（默认 `max_cross_ref_depth=1`），从根本上防范循环引用与无限递归。

## 2. 九类法律关系契约体系

| 关系类型 (`LegalRelationType`) | 典型语法特征 | 抽取字段与槽位 | 业务审查作用 |
|---|---|---|---|
| **`OBLIGATION`** (义务) | “应当”、“必须”、“负责” | `subject`, `action`, `trigger_condition`, `exception_term` | 审查义务主体方向是否被违规对调 |
| **`RIGHT`** (权利) | “有权”、“可以”、“得以” | `subject`, `action` | 审查权利分配是否对等 |
| **`PROHIBITION`** (禁止) | “不得”、“严禁”、“禁止” | `subject`, `action` (含不得) | 审查负向保密与同业竞争义务 |
| **`CONDITION`** (条件) | “自...起”、“若...”、“收到...后” | `trigger_condition` | 区分生效前提与起算时间点 |
| **`EXCEPTION`** (例外) | “但...除外”、“除...外” | `exception_term` | 识别免责范围是否存在单方放大 |
| **`DEADLINE`** (期限) | “...个工作日内”、“...日内” | `deadline`, `trigger_condition` (起算点) | 严格区分工作日与自然日计算 |
| **`LIABILITY`** (违约) | “支付违约金”、“赔偿损失” | `subject`, `action` | 审查违约赔偿责任上限与违约金比例 |
| **`TERMINATION`** (解除) | “单方解除”、“终止合同” | `subject`, `action`, `trigger_condition` | 审查法定/约定解除权触发条件与通知期 |
| **`CROSS_REFERENCE`** (引用) | “依照第X条”、“参见第X条” | `target_reference` | 跨条款检索跳转与矛盾对账依据 |

## 3. 确定性保证与防幻觉
- 所有关系的提取均附带 `raw_quote`（原文引用片断）与字符偏移位置；
- 无法可靠断定主体或起算点时，标记为 `UNKNOWN` 或 `NEEDS_MANUAL_REVIEW`，绝不凭空推断；
- 抽取结果直接输入下游 `ClauseComparison` 差异对比引擎与 `VerifyEvidenceStage` 真实性核验。
