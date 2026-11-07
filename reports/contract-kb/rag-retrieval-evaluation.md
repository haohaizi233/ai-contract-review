# RAG 检索评估报告（rag-retrieval-evaluation）

- 黄金查询集：30 条（含困难负样本：同类型不同期限/同类条款不同金额/商专近似条款/过期版本）
- Embedding：local_hash_256（本地离线哈希嵌入；生产建议替换为授权中文 Embedding 模型）
- Reranker：char-2gram-coverage+deterministic (生产建议 cross-encoder)

## 指标定义（严格区分，不混用）

- **Hit@K**：首个相关结果进入 Top-K 的查询占比（Success@K）
- **Recall@K**：相关结果进入 Top-K 的查询占比（任一相关即召回）
- **MRR**：首个相关结果排名倒数均值
- **NDCG@8**：分级相关性（2=类别+关键词，1=同类/同词）归一折损累积增益
- **CitationCorrectness**：clause_id 可回查到非空原文的比例

## 指标（真实测量）

| 指标 | 值 |
|---|---|
| Hit@1 | 0.9333 |
| Recall@1 | 0.9333 |
| Hit@3 | 1.0 |
| Recall@3 | 1.0 |
| Hit@5 | 1.0 |
| Recall@5 | 1.0 |
| Hit@8 | 1.0 |
| Recall@8 | 1.0 |
| MRR | 0.9611 |
| NDCG@8 | 0.9372 |
| CitationCorrectness | 1.0 |

## 逐条查询结果

| 查询 | 期望类别 | 命中数 | 首个相关排名 | 强相关排名 | NDCG | 引用可回查 |
|---|---|---|---|---|---|---|
| 逾期付款违约金是多少 | LIABILITY | 8 | 1 | [1, 2, 3, 4, 5] | 1.0 | 是 |
| 争议由哪个法院管辖 | DISPUTE_RESOLUTION | 8 | 1 | [1, 2] | 1.0 | 是 |
| 保密义务的约定 | CONFIDENTIALITY | 8 | 1 | [1, 2, 3, 4, 5] | 1.0 | 是 |
| 服务费发票税率 | FEE_AND_PAYMENT | 8 | 1 | [1] | 1.0 | 是 |
| 官费怎么开具收据 | FEE_AND_PAYMENT | 8 | 1 | [1, 2, 3, 4, 5] | 1.0 | 是 |
| 付款期限是多久 | FEE_AND_PAYMENT | 8 | 1 | - | 1.0 | 是 |
| 乙方账户信息 | FEE_AND_PAYMENT | 8 | 1 | [1, 2, 3, 4, 5] | 1.0 | 是 |
| 合同何时生效何时终止 | EFFECTIVENESS_AND_TERMINATION | 8 | 1 | [1, 2, 3, 4, 5] | 1.0 | 是 |
| 甲方中途取消案件怎么收费 | LIABILITY | 8 | 1 | [1, 2, 3, 4, 5] | 1.0 | 是 |
| 预审重复提交的承诺 | PARTY_A_OBLIGATIONS | 8 | 1 | [1, 2, 3, 4, 5] | 1.0 | 是 |
| 超权费怎么计算 | FEE_AND_PAYMENT | 8 | 1 | [1] | 1.0 | 是 |
| 商标注册风险告知 | PARTY_A_OBLIGATIONS | 8 | 1 | [1, 2, 3, 4, 5] | 1.0 | 是 |
| 专属联系人和总服务邮箱 | PARTY_B_OBLIGATIONS | 8 | 1 | [1, 2, 3, 5, 6] | 0.9795 | 是 |
| 投诉渠道 | EFFECTIVENESS_AND_TERMINATION | 8 | 1 | [3, 5, 7] | 0.7783 | 是 |
| 送达条款 | EFFECTIVENESS_AND_TERMINATION | 8 | 3 | - | 0.7028 | 是 |
| 费减不成功谁补差价 | FEE_AND_PAYMENT | 8 | 1 | [1, 2, 3, 4] | 1.0 | 是 |
| 转委托第三方是否允许 | PARTY_B_OBLIGATIONS | 8 | 1 | - | 1.0 | 是 |
| 年费缴纳服务范围 | CONTRACT_SUBJECT | 8 | 1 | - | 0.9804 | 是 |
| 专利转让合同的当事人字段 | CONTRACT_SUBJECT | 8 | 1 | - | 0.9197 | 是 |
| 违约赔偿范围包括什么 | LIABILITY | 8 | 2 | - | 0.6766 | 是 |
| 侵权诉讼及专利无效服务事项 | SERVICE_SCOPE | 8 | 1 | [5] | 0.794 | 是 |
| 科技项目申报材料工本费与咨询费 | FEE_AND_PAYMENT | 8 | 1 | [1, 5] | 0.9323 | 是 |
| 保密协议商业秘密泄露与补救措施 | CONFIDENTIALITY | 8 | 1 | [2, 3, 4, 5, 6] | 0.8781 | 是 |
| 商标委托代理服务事项及范围 | CONTRACT_SUBJECT | 8 | 1 | [1, 2, 8] | 0.9551 | 是 |
| 涉外业务外币支付与汇率结算 | FEE_AND_PAYMENT | 8 | 1 | - | 1.0 | 是 |
| 软件著作权登记及转让办理 | CONTRACT_SUBJECT | 8 | 1 | [6, 7] | 0.7391 | 是 |
| 高新技术企业认定咨询及培训辅导 | PARTY_B_OBLIGATIONS | 8 | 1 | - | 1.0 | 是 |
| 知识产权民事诉讼一审二审代理 | PARTY_B_OBLIGATIONS | 8 | 1 | - | 1.0 | 是 |
| 保密信息的使用范围和披露限制 | CONFIDENTIALITY | 8 | 1 | [3, 5, 6, 7, 8] | 0.7855 | 是 |
| 框架协议下具体业务订单签署 | EFFECTIVENESS_AND_TERMINATION | 8 | 1 | - | 0.9951 | 是 |

## 局限性（如实标注）

- 本地哈希嵌入仅保证词汇级匹配，语义指标低于深度模型属预期；
- 中文 FTS5 默认分词不足，已用逐词 OR + 向量通道补偿；
- 评估在合成语料 + 黄金集上测量，接入真实 Embedding 后须重测对比。