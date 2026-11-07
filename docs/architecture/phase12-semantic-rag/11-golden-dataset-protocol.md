# 11. 黄金检索与审核评测基准协议（Golden Dataset Protocol）

## 1. 协议设计背景与原则

在法律与企业知识产权合同审核系统中，“语义相似不等于法律等价”，“检索召回不等于合规”。为防止大模型或检索算法被表面措辞误导，必须建立具备对抗性判别力的黄金评测基准（Golden Dataset）。

评测基准必须满足：
1. **条款级语义真值（Ground Truth）**：以具体条款的法律权利、义务与违约后果为判定单元，而非宽泛的大类标签；
2. **对抗性困难负样本（Hard Negatives）**：注入表面措辞极度相似但关键法律要件倒错的干扰项；
3. **分层分级度量**：区分 Hit@K、Recall@K、MRR、NDCG 与困难负样本规避率（HardNegAvoidance）；
4. **诚实标注证据层级**：明确标注评测处于 `SYNTHETIC_UNIT_REGRESSION`、`DESENSITIZED_TEMPLATE_BENCHMARK` 还是 `REAL_MODEL_END_TO_END`，禁止以哈希合成结果伪造生产语义准确率。

---

## 2. 困难负样本（Hard Negatives）分类与注入规范

评测集内置 5 类典型对抗性困难负样本：

| 标识 | 负样本类型 | 典型设计示例 | 破坏性与考查点 |
|---|---|---|---|
| **HN1** | 期限与计算口径错位 | “甲方应在15个工作日内支付” vs “甲方应在15日内支付” | 考查系统是否区分工作日与自然日 |
| **HN2** | 金额与费率量级偏差 | “按每日 3‰ 计算违约金” vs “按每日 0.3%（等价）” vs “按每日 0.05%” | 考查 Decimal 精度核验与千分比/百分比折算 |
| **HN3** | 跨业务领域措辞混淆 | 商标独占许可中的保密条款 vs 专利转让中的保密条款 | 考查业务分类与子分类的元数据隔离能力 |
| **HN4** | 失效与未审批版本 | 历史已废止模板条款（`status='SUPERSEDED'`） | 考查 SQL 过滤层是否严格拦截非 ACTIVE/未审批条目 |
| **HN5** | 法律极性与义务反转 | “未经书面许可，乙方不得披露” vs “未经书面许可，乙方可以披露” | 考查否定词倒错与许可/禁止性质反转识别 |

---

## 3. 评测集查询定义规范

每条黄金查询采用 `GoldenQuery` 强类型定义：

```python
@dataclass
class GoldenQuery:
    qid: str
    query: str
    expected_category: str
    expected_keywords: list[str]  # 命中预期条款必备特征词
    avoid_keywords: list[str]  # 困难负样本特征词（命中则扣分）
    note: str = ""
```

评测打分严格依据：
- **相关性分级（Relevance Level）**：
  - `rel = 2`：条款类别匹配且包含全部 `expected_keywords`，且未包含 `avoid_keywords`；
  - `rel = 1`：类别匹配或部分关键词匹配；
  - `rel = 0`：无关条款或命中困难负样本（`avoid_keywords`）。
- **归一化折损累计增益（NDCG@8）**：
  $$\text{DCG@K} = \sum_{i=1}^K \frac{2^{\text{rel}_i} - 1}{\log_2(i + 1)}$$
  $$\text{NDCG@K} = \frac{\text{DCG@K}}{\text{IDCG@K}}$$
- **困难负样本规避率（HardNegAvoidance@8）**：
  Top-8 结果中完全不包含 `avoid_keywords` 的查询比例。

---

## 4. 自动化评测运行入口

```bash
# 1. 运行 RAG 黄金检索与审核端到端评测
python -m contract_kb.evaluate

# 2. 运行检索对比与困难负样本规避专项评测
python -m contract_kb.benchmark_retrieval

# 3. 运行 6 组架构消融实验对比
python -m contract_kb.ablation_study
```
