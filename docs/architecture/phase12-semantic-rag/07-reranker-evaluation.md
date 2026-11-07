# Phase 12-E: 语义重排评测与安全降级规范 (Reranker Evaluation)

- **状态**: IMPLEMENTED & TESTED
- **核心组件**: [`contract_kb/rerankers.py`](file:///d:/Users/Administrator/Desktop/AI合同/contract_kb/rerankers.py)、[`contract_kb/modules/retrieval/reranker.py`](file:///d:/Users/Administrator/Desktop/AI合同/contract_kb/modules/retrieval/reranker.py)
- **测试用例**: [`tests/integration/test_hybrid_cross_encoder.py`](file:///d:/Users/Administrator/Desktop/AI合同/tests/integration/test_hybrid_cross_encoder.py)

---

## 1. 架构定位：相关性排序而非法律等价性裁定
Cross-Encoder 模型通过细粒度的 Cross-Attention 机制，有效克服了 Bi-Encoder 向量空间表征过于压缩的缺陷。然而，在法律与合同审核场景中必须坚守一条底线：
> **“语义相似不等于法律含义相同”**。
> 例如，“甲方不得向第三方披露”与“甲方可以向第三方披露”，在 Token 层面具有 90% 以上的重合度，Cross-Encoder 会给出极高相关性得分，但两者法律效力完全相反。

因此，Cross-Encoder 的定位仅为**“初筛候选粗排序（Top-50 -> Top-5）”**，其后必须紧跟结构化校验流水线。

## 2. Reranker 组合与降级矩阵

| 模式 | 实现类 | 外部依赖 | 降级机制 | 适用场景 |
|---|---|---|---|---|
| **Mode A (默认离线)** | `LexicalCoverageReranker` | 无 (标准库 `re` + 字符 2-gram) | 恒定可用 | 单元测试、离线部署、零数据出境安全环境 |
| **Mode B (本地模型)** | `CrossEncoderReranker` | `sentence-transformers` + `BAAI/bge-reranker-v2-m3` | 依赖缺失或显存不足时自动回退 Mode A | 私有化 GPU 服务器、高精度精排 |
| **Mode C (HTTP 端点)** | `HttpCrossEncoderReranker` | 授权专用推理服务端点 | 超时 (默认 1.5s) 或网络中断自动回退 Mode A | 分布式微服务生产集群 |

## 3. 防数据泄露与出境安全门禁
在 `HttpCrossEncoderReranker` 初始化与调用前，严格校验 `config.ALLOW_EXTERNAL_LLM`：
- 若未显式授权且尝试请求外网非回环地址，抛出 `PermissionError` 阻断执行；
- 仅允许经审批的内网端点或回环地址（`127.0.0.1`），保障企业机密合同内容零未授权泄露。
