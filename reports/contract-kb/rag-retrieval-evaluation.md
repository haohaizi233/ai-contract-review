# RAG 检索评估报告（rag-retrieval-evaluation）

- **评测证据层级（Evaluation Tier）**：`DESENSITIZED_TEMPLATE_BENCHMARK`
- 黄金查询集：30 条
- Embedding：`local_hash_256`
- Reranker：`lexical_2gram_authority_v1 (支持切换 HttpCrossEncoderReranker)`
- 检索延迟：Mean=135.78ms, P50=102.25ms, P95=265.01ms, P99=275.25ms

## 指标（真实测量）

| 指标 | 值 | 说明 |
|---|---|---|
| Hit@1 | 0.5333 |  |
| Recall@1 | 0.0007 |  |
| Hit@3 | 0.6667 |  |
| Recall@3 | 0.0022 |  |
| Hit@5 | 0.7 |  |
| Recall@5 | 0.0035 |  |
| Hit@8 | 0.7667 |  |
| Recall@8 | 0.0064 |  |
| MRR | 0.5989 | 首个相关结果排名倒数均值 |
| NDCG@8 | 0.4385 | 分级相关性（2=类别+关键词，1=同类/同词）归一折损累积增益 |
| CitationCorrectness | 0.95 | 返回 clause_id 在数据库中存在且原文非空的比例 |
| AnswerGroundedness | 0.95 | 检索返回文本与数据库权威条款原文逐字锚定一致度 |

## 逐条查询结果

| 查询 | 期望类别 | 命中数 | 首个相关排名 | NDCG | 引用正确率 | 依据度 | 耗时(ms) |
|---|---|---|---|---|---|---|---|
| 逾期付款违约金是多少 | LIABILITY | 8 | 1 | 1.0 | 1.0 | 1.0 | 105.23 |
| 争议由哪个法院管辖 | DISPUTE_RESOLUTION | 8 | 1 | 0.8906 | 1.0 | 1.0 | 54.6 |
| 保密义务的约定 | CONFIDENTIALITY | 8 | 1 | 1.0 | 1.0 | 1.0 | 88.16 |
| 服务费发票税率 | FEE_AND_PAYMENT | 8 | 1 | 0.7653 | 1.0 | 1.0 | 174.12 |
| 官费怎么开具收据 | FEE_AND_PAYMENT | 8 | 1 | 1.0 | 1.0 | 1.0 | 57.75 |
| 付款期限是多久 | FEE_AND_PAYMENT | 8 | 1 | 0.0 | 1.0 | 1.0 | 259.42 |
| 乙方账户信息 | FEE_AND_PAYMENT | 8 | 1 | 1.0 | 1.0 | 1.0 | 254.41 |
| 合同何时生效何时终止 | EFFECTIVENESS_AND_TERMINATION | 8 | 1 | 1.0 | 1.0 | 1.0 | 62.86 |
| 甲方中途取消案件怎么收费 | LIABILITY | 8 | 1 | 1.0 | 1.0 | 1.0 | 256.35 |
| 预审重复提交的承诺 | PARTY_A_OBLIGATIONS | 8 | 1 | 0.2529 | 0.875 | 0.875 | 53.54 |
| 超权费怎么计算 | FEE_AND_PAYMENT | 8 | 1 | 0.3652 | 1.0 | 1.0 | 53.59 |
| 商标注册风险告知 | PARTY_A_OBLIGATIONS | 8 | 1 | 1.0 | 0.875 | 0.875 | 139.34 |
| 专属联系人和总服务邮箱 | PARTY_B_OBLIGATIONS | 8 | 1 | 1.0 | 0.625 | 0.625 | 252.79 |
| 投诉渠道 | EFFECTIVENESS_AND_TERMINATION | 8 | 1 | 0.3086 | 0.625 | 0.625 | 112.93 |
| 送达条款 | EFFECTIVENESS_AND_TERMINATION | 8 | 5 | 0.0326 | 1.0 | 1.0 | 80.79 |
| 费减不成功谁补差价 | FEE_AND_PAYMENT | 8 | 未命中 | 0.0 | 1.0 | 1.0 | 54.52 |
| 转委托第三方是否允许 | PARTY_B_OBLIGATIONS | 8 | 1 | 0.0 | 1.0 | 1.0 | 53.75 |
| 年费缴纳服务范围 | CONTRACT_SUBJECT | 8 | 1 | 0.0578 | 0.875 | 0.875 | 157.93 |
| 专利转让合同的当事人字段 | CONTRACT_SUBJECT | 8 | 1 | 0.1449 | 1.0 | 1.0 | 258.14 |
| 违约赔偿范围包括什么 | LIABILITY | 8 | 2 | 0.1461 | 0.875 | 0.875 | 263.94 |
| 侵权诉讼及专利无效服务事项 | SERVICE_SCOPE | 8 | 1 | 0.0798 | 1.0 | 1.0 | 279.08 |
| 科技项目申报材料工本费与咨询费 | FEE_AND_PAYMENT | 8 | 1 | 0.3508 | 1.0 | 1.0 | 60.14 |
| 保密协议商业秘密泄露与补救措施 | CONFIDENTIALITY | 8 | 1 | 0.5875 | 1.0 | 1.0 | 82.63 |
| 商标委托代理服务事项及范围 | CONTRACT_SUBJECT | 8 | 1 | 0.4171 | 1.0 | 1.0 | 177.4 |
| 涉外业务外币支付与汇率结算 | FEE_AND_PAYMENT | 8 | 3 | 0.0 | 1.0 | 1.0 | 54.16 |
| 软件著作权登记及转让办理 | CONTRACT_SUBJECT | 8 | 1 | 0.539 | 1.0 | 1.0 | 265.89 |
| 高新技术企业认定咨询及培训辅导 | PARTY_B_OBLIGATIONS | 8 | 1 | 0.0 | 1.0 | 1.0 | 99.28 |
| 知识产权民事诉讼一审二审代理 | PARTY_B_OBLIGATIONS | 8 | 1 | 0.0 | 1.0 | 1.0 | 123.92 |
| 保密信息的使用范围和披露限制 | CONFIDENTIALITY | 8 | 1 | 0.2166 | 1.0 | 1.0 | 82.15 |
| 框架协议下具体业务订单签署 | EFFECTIVENESS_AND_TERMINATION | 8 | 1 | 0.0 | 0.75 | 0.75 | 54.46 |