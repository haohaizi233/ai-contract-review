# Phase 11-R + Phase 12-R: 消融实验方法论重构与真实性治理

**报告版本**：1.0.0-REACCEPTANCE  
**日期**：2026-11-07  
**责任角色**：ML Evaluation Engineer & Independent QA Auditor  
**对应缺陷**：P0-05 (消融实验失真与伪造风险)  

---

## 一、 历史消融实验失真原因深度剖析

在原始 Phase 12 的实现中，消融实验存在重大失真与学术不端风险：
1. **单变量失效（Config A 与 B 实质等价）**：Config A 与 Config B 均使用 `LocalHashEmbedding`，没有引入真实的稠密向量检索通道，二者评测结果完全雷同；
2. **技术组件旁路（未真实接线）**：虽然定义了 Parent-Child 扩展、Cross-Encoder 与 ClauseComparisonEngine，但在消融评测循环中并没有把检索命中结果输入这些组件；
3. **无调用追踪机制**：无法验证评测时是否真实触发了查询规划器、重排器和层级树；
4. **模型缺失时伪造高指标**：在无 `torch` / `sentence-transformers` 环境下，通过简单的伪随机散列或词法重排谎称模型效果达到 `96% MRR`，混淆了工程契约与真实算法效果。

---

## 二、 6 组严谨消融实验矩阵设计（单变量正交控制）

本次重构彻底规范了 6 组系统形态，每组相邻配置之间**有且仅有一个技术自变量发生改变**：

| 配置名称 | 检索查询模式 (`query_mode`) | 向量表征类型 (`embedding_type`) | 层级扩展 (`use_parent_child`) | 重排器类型 (`reranker_type`) | 差异比对 (`use_structural_comparison`) | 评估的技术贡献 |
|:---|:---|:---|:---|:---|:---|:---|
| **Config A (基线)** | FIXED (固定4关键词) | LOCAL_HASH (词法散列) | False | LEXICAL (词法重排) | False | 传统检索基线能力 |
| **Config B** | FIXED | **DENSE_REAL (真实语义嵌入)** | False | LEXICAL | False | 验证 Dense 语义向量表征贡献 |
| **Config C** | **DYNAMIC (动态条款意图规划)** | DENSE_REAL | False | LEXICAL | False | 验证动态意图拆解与精准查询贡献 |
| **Config D** | DYNAMIC | DENSE_REAL | **True (Parent-Child 树)** | False | False | 验证知识层级上下文扩展贡献 |
| **Config E** | DYNAMIC | DENSE_REAL | True | **CROSS_ENCODER (交叉精排)** | False | 验证深度语义匹配重排贡献 |
| **Config F (目标形态)** | DYNAMIC | DENSE_REAL | True | CROSS_ENCODER | **True (结构化差异比对)** | 验证法律逻辑对抗与主体条件比对贡献 |

---

## 三、 组件调用追踪器 (ComponentCallTracker)

为确保每个组件真实在流水线中执行，引入 `ComponentCallTracker`，在评测过程中实时记录：
- `query_planning_calls`: 查询规划调用次数；
- `embedding_calls`: 语义向量推理次数；
- `search_calls`: 存储层实际检索次数；
- `hierarchy_expansions`: 父子层级展开次数；
- `cross_encoder_calls`: Cross-Encoder 语义精排调用次数；
- `structural_comparison_calls`: 结构化比对分析调用次数。

在自动化集成测试中，断言各配置的调用计数是否符合单变量特征（例如：Config A 的 `query_planning_calls` 必须为 0，而 Config C 必须大于 0；Config D 的 `hierarchy_expansions` 必须大于 0，而 Config C 必须为 0）。

---

## 四、 真实模型可用性守卫 (Fail-Closed)

严格践行**两级验收分离**原则：
- **L1 工程与安全契约**：允许在显式标记 `allow_mock_for_contract_test=True` 时使用轻量级 Mock 执行组件接线与数据流打通测试，此时输出指标明确标记为 `CONTRACT_TEST_ONLY`；
- **L2 真实算法与模型效果**：在生产和真实评测模式 (`allow_mock_for_contract_test=False`) 下，若环境缺失 `sentence_transformers` 或 `torch`：
  - 必须返回 `status="NOT_EXECUTED_MODEL_UNAVAILABLE"`；
  - `blocking_reason` 必须明确记录 `BLOCKED_MODEL_RESOURCE: sentence-transformers 未安装`；
  - `metrics` 置为 `None`，坚决不编造假数字。

---

## 五、 测试覆盖与验收证据

新建测试：
1. `tests/unit/test_phase12_ablation_configuration_identity.py`
   - 验证 6 组配置的单变量正交性；
   - 验证无模型环境下的 Fail-Closed 守卫机制；
   - 验证调用追踪器的序列化能力。
2. `tests/integration/test_phase12_ablation_real_execution.py`
   - 验证全矩阵 6 组消融工程调用流水线与组件追踪计数；
   - 验证真实生产评测模式下对缺失环境的诚实拦截。
