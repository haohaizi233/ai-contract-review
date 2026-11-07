# AI 合同审查系统：检索与审核评估协议标准（Evaluation Protocol）

> 文档编号：PROTO-2026-EVAL-01  
> 对应任务：T13（纠正 F14 指标混淆与口径缺陷）  
> 状态：APPROVED

---

## 1. 评估目标与设计原则

为彻底解决历史报告中存在的以下指标口径缺陷：
1. **指标混淆**：历史版本中 `Hit@K` 与 `Recall@K` 采用相同算法（任一相关即算 1.0），未能反映实际漏召回率；
2. **IDCG 退化**：历史 NDCG 仅对检索召回的候选进行局部重排作为理想折损值（IDCG），导致漏召回严重时 NDCG 虚假偏高；
3. **数据集混淆**：将 `retrieval_golden.py` 的 15 条困难负样本基准（`BENCHMARK_15_HARD_NEGATIVE`）与 `evaluate.py` 的 30 条广泛业务评估集（`EVALUATE_30_BROAD_BUSINESS`）混为一谈；
4. **证据层级冒充**：将离线合成哈希（`local_hash_256`）零成本运行结果冒充为企业真实语义检索和生产调用成本。

本协议确立数学严谨的评价指标、独立 Qrels 标注规范与分级评估准则。

---

## 2. 评测证据层级划分（Evaluation Tiers）

任何评测输出必须显式声明其所属证据层级，严禁跨层级宣称效果：

| 层级标识 (`provenance_tier`) | 语料来源 | 向量/模型配置 | 法律效力与发布范围 |
|---|---|---|---|
| `SYNTHETIC` | 合成模板、规则注入与困难负样本 | 离线哈希或轻量本地嵌入 | CI 自动化回归，仅验证管线健壮性，不代表实际法律审核能力 |
| `REAL_DESENSITIZED` | 经企业法务授权并严格脱敏的真实合同模板 | 生产级语义 Embedding (如 bge-large-zh) | 离线检索召回率与分级相关性基准评测 |
| `REAL_MODEL_E2E` | 脱敏真实合同 + 真实外部/私有 LLM | 授权 LLM + 语义 Embedding + Reranker | 真实语义审核风险召回率、误报率与端到端耗时/Token成本评测 |

---

## 3. 信息检索指标数学定义与契约

### 3.1 命中率（Hit@K / Success@K）
衡量前 $K$ 个检索结果中是否存在**至少一个**相关文档：

$$\text{Hit@K} = \begin{cases} 1.0, & \text{若 } |\text{TopK} \cap \text{Relevant}| \ge 1 \\ 0.0, & \text{若 } |\text{TopK} \cap \text{Relevant}| = 0 \end{cases}$$

当地面真值（Ground Truth）为空时，返回 `None`（N/A），不得返回虚假数值。

### 3.2 真正召回率（Recall@K）
衡量前 $K$ 个检索结果召回的相关文档数占地面真值全部相关文档总数的比例：

$$\text{Recall@K} = \frac{|\text{TopK} \cap \text{Relevant}|}{|\text{Relevant}|}$$

- 若地面真值包含 3 条相关条款，系统在 Top-2 中召回 1 条，则：
  - $\text{Hit@2} = 1.0$
  - $\text{Recall@2} = 1 / 3 \approx 0.3333$
- 历史代码中将 $\text{Hit@K}$ 误标为 $\text{Recall@K}$ 的指标保留为 `legacy_success_rate_at_k` 仅供历史回归对照。

### 3.3 平均倒数排名（MRR）
衡量首个相关文档在检索列表中排名的倒数均值：

$$\text{MRR} = \frac{1}{\min_{d \in \text{Relevant}} \text{rank}(d)}$$

### 3.4 归一折损累计增益（NDCG@K）
基于独立 Ground Truth Qrels 计算理想折损累计增益（IDCG），**严禁**仅对已召回候选项排序求 IDCG：

$$\text{DCG@K} = \sum_{i=1}^K \frac{2^{g_i} - 1}{\log_2(i + 1)}$$

$$\text{IDCG@K} = \sum_{i=1}^{\min(K, |\text{Qrels}|)} \frac{2^{g^*_i} - 1}{\log_2(i + 1)}$$

其中 $g^*_i$ 为独立 Qrels 中所有相关等级降序排列后的第 $i$ 项。

$$\text{NDCG@K} = \frac{\text{DCG@K}}{\text{IDCG@K}}$$

---

## 4. 查询集分离与版本控制

系统中两套核心检索集定义如下：

1. **`BENCHMARK_15_HARD_NEGATIVE`** (`contract_kb.retrieval_golden`):
   - 样本量：15 条精准查询。
   - 特点：包含困难负样本（HN1-HN5：相似措辞不同比率、诉讼 vs 仲裁、失效版本等），专门用于评测 Reranker 辨析能力与负样本规避率（HardNegativeAvoidance）。
2. **`EVALUATE_30_BROAD_BUSINESS`** (`contract_kb.evaluate`):
   - 样本量：30 条核心业务查询。
   - 特点：覆盖 8 大业务板块（违约责任、争议管辖、保密、费用支付、生效终止、当事人义务等），用于全面评估 RAG 知识覆盖度与词汇/语义混合召回率。

---

## 5. 法务标注与真实评测门禁规范

对于后续进入 `REAL_DESENSITIZED` 和 `REAL_MODEL_E2E` 的真实评测集：
1. **样本切分**：按合同族/模板类型严格切分开发集（Dev）与测试集（Test），严禁将同一合同模板的不同变体同时放入开发集与测试集；
2. **双人背对背标注**：由企业法务专家独立标注高风险条款、必备证据与人工复核项；至少 20% 样本执行双人背对背复核，争议项由法务负责人仲裁；
3. **准入基准门槛（候选）**：
   - 核心法律风险召回率（Critical Risk Recall）$\ge 95\%$；
   - 误报率（False Positive Rate）$\le 10\%$；
   - 引用可定位率（Citation Groundedness）$= 100\%$。
4. **成本与调用核对**：真实 LLM 评估报告必须记录实际 Token 输入输出及费用账本，不得使用 Token=0 冒充外部供应商成本。
