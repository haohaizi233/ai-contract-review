# RAG 检索评估报告（rag-retrieval-evaluation）

- **评测证据层级（Evaluation Tier）**：`DESENSITIZED_TEMPLATE_BENCHMARK`
- 黄金查询集：30 条
- Embedding：`local_hash_256`
- Reranker：`lexical_2gram_authority_v1 (支持切换 HttpCrossEncoderReranker)`
- 检索延迟：Mean=127.68ms, P50=85.84ms, P95=273.5ms, P99=287.48ms

## 指标（真实测量）

| 指标 | 值 | 说明 |
|---|---|---|
| Hit@1 | 0.5333 |  |
| Recall@1 | 0.0012 |  |
| Hit@3 | 0.6667 |  |
| Recall@3 | 0.0037 |  |
| Hit@5 | 0.7333 |  |
| Recall@5 | 0.0063 |  |
| Hit@8 | 0.8333 |  |
| Recall@8 | 0.0113 |  |
| MRR | 0.6119 | 首个相关结果排名倒数均值 |
| NDCG@8 | 0.4587 | 分级相关性（2=类别+关键词，1=同类/同词）归一折损累积增益 |
| CitationCorrectness | 1.0 | 返回 clause_id 在数据库中存在且原文非空的比例 |
| AnswerGroundedness | 1.0 | 检索返回文本与数据库权威条款原文逐字锚定一致度 |

## 逐条查询结果

| 查询 | 期望类别 | 命中数 | 首个相关排名 | NDCG | 引用正确率 | 依据度 | 耗时(ms) |
|---|---|---|---|---|---|---|---|
| 逾期付款违约金是多少 | LIABILITY | 8 | 1 | 1.0 | 1.0 | 1.0 | 66.42 |
| 争议由哪个法院管辖 | DISPUTE_RESOLUTION | 8 | 1 | 0.7653 | 1.0 | 1.0 | 53.64 |
| 保密义务的约定 | CONFIDENTIALITY | 8 | 1 | 1.0 | 1.0 | 1.0 | 74.01 |
| 服务费发票税率 | FEE_AND_PAYMENT | 8 | 1 | 0.6927 | 1.0 | 1.0 | 125.24 |
| 官费怎么开具收据 | FEE_AND_PAYMENT | 8 | 1 | 1.0 | 1.0 | 1.0 | 46.63 |
| 付款期限是多久 | FEE_AND_PAYMENT | 8 | 1 | 0.0 | 1.0 | 1.0 | 277.1 |
| 乙方账户信息 | FEE_AND_PAYMENT | 8 | 1 | 1.0 | 1.0 | 1.0 | 291.72 |
| 合同何时生效何时终止 | EFFECTIVENESS_AND_TERMINATION | 8 | 1 | 0.7653 | 1.0 | 1.0 | 93.83 |
| 甲方中途取消案件怎么收费 | LIABILITY | 8 | 1 | 1.0 | 1.0 | 1.0 | 262.99 |
| 预审重复提交的承诺 | PARTY_A_OBLIGATIONS | 8 | 1 | 0.1174 | 1.0 | 1.0 | 83.1 |
| 超权费怎么计算 | FEE_AND_PAYMENT | 8 | 1 | 0.4158 | 1.0 | 1.0 | 71.4 |
| 商标注册风险告知 | PARTY_A_OBLIGATIONS | 8 | 1 | 0.9386 | 1.0 | 1.0 | 162.75 |
| 专属联系人和总服务邮箱 | PARTY_B_OBLIGATIONS | 8 | 1 | 0.8359 | 1.0 | 1.0 | 269.11 |
| 投诉渠道 | EFFECTIVENESS_AND_TERMINATION | 8 | 1 | 0.3086 | 1.0 | 1.0 | 172.56 |
| 送达条款 | EFFECTIVENESS_AND_TERMINATION | 8 | 6 | 0.0847 | 1.0 | 1.0 | 70.18 |
| 费减不成功谁补差价 | FEE_AND_PAYMENT | 8 | 1 | 0.8374 | 1.0 | 1.0 | 52.82 |
| 转委托第三方是否允许 | PARTY_B_OBLIGATIONS | 8 | 1 | 0.0 | 1.0 | 1.0 | 43.02 |
| 年费缴纳服务范围 | CONTRACT_SUBJECT | 8 | 1 | 0.131 | 1.0 | 1.0 | 107.9 |
| 专利转让合同的当事人字段 | CONTRACT_SUBJECT | 8 | 1 | 0.4884 | 1.0 | 1.0 | 218.99 |
| 违约赔偿范围包括什么 | LIABILITY | 8 | 2 | 0.1461 | 1.0 | 1.0 | 200.96 |
| 侵权诉讼及专利无效服务事项 | SERVICE_SCOPE | 8 | 1 | 0.1744 | 1.0 | 1.0 | 260.72 |
| 科技项目申报材料工本费与咨询费 | FEE_AND_PAYMENT | 8 | 1 | 0.3508 | 1.0 | 1.0 | 44.99 |
| 保密协议商业秘密泄露与补救措施 | CONFIDENTIALITY | 8 | 1 | 0.5875 | 1.0 | 1.0 | 68.79 |
| 商标委托代理服务事项及范围 | CONTRACT_SUBJECT | 8 | 1 | 0.539 | 1.0 | 1.0 | 141.12 |
| 涉外业务外币支付与汇率结算 | FEE_AND_PAYMENT | 8 | 3 | 0.0798 | 1.0 | 1.0 | 58.22 |
| 软件著作权登记及转让办理 | CONTRACT_SUBJECT | 8 | 1 | 0.2529 | 1.0 | 1.0 | 237.07 |
| 高新技术企业认定咨询及培训辅导 | PARTY_B_OBLIGATIONS | 8 | 1 | 0.0 | 1.0 | 1.0 | 57.64 |
| 知识产权民事诉讼一审二审代理 | PARTY_B_OBLIGATIONS | 8 | 1 | 0.0 | 1.0 | 1.0 | 88.59 |
| 保密信息的使用范围和披露限制 | CONFIDENTIALITY | 8 | 1 | 0.2509 | 1.0 | 1.0 | 78.62 |
| 框架协议下具体业务订单签署 | EFFECTIVENESS_AND_TERMINATION | 8 | 1 | 0.0 | 1.0 | 1.0 | 50.36 |