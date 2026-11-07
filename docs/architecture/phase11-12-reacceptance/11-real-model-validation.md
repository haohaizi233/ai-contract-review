# Phase 11-R + Phase 12-R: 真实语义模型验证与两级验收规范

**报告版本**：1.0.0-REACCEPTANCE  
**日期**：2026-11-07  
**责任角色**：ML Evaluation Engineer & Principal Software Architect  
**对应目标**：能区分“规则能力已实现”和“真实语义模型效果已验证”的验收机制  

---

## 一、 为什么必须实施“两级验收分离”

在过去的交付报告中，存在将“工程代码已编写并跑通 Mock”与“真实大模型或 Dense Embedding 算法效果已达标”混为一谈的现象：
- 在没有安装 `torch` 和 `sentence-transformers` 的极简 CI 环境中，使用基于散列的伪随机向量冒充真实模型，并在报告中声称“BGE-M3 语义检索 MRR 达到 0.9634”；
- 这种做法混淆了**工程契约实现（L1）**与**真实语义效果（L2）**，极易误导技术决策并掩盖线上实际推理性能瓶颈。

本次联合整改确立了不可动摇的**两级验收分离机制**。

---

## 二、 两级验收层级定义

### 1. Level 1: 工程与安全契约验收 (Engineering & Security Contract Acceptance)
- **目标**：验证全链路架构接线、数据流转、租户隔离、CAS 乐观锁、Fail-Closed 安全边界、组件调用追踪和单元/契约测试；
- **允许手段**：允许在契约测试模式 (`allow_mock_for_contract_test=True`) 下注入规范的 Mock 服务（如 `MockDenseEmbedding`、`MockCrossEncoderReranker`）；
- **衡量指标**：代码测试覆盖率、断言通过率、组件调用计数递增（`ComponentCallTracker` 验证）；
- **交付结论**：L1 契约 100% 通过即代表**代码工程正确且安全具备上线前置条件**。

### 2. Level 2: 真实模型与算法效果验收 (Real Model & Algorithm Evaluation)
- **目标**：在具备真实 GPU/CPU 推理环境、真实模型权重（如 `BAAI/bge-m3`、`BAAI/bge-reranker-large`）以及合规数据集上，实测真正的端到端 MRR、NDCG@8、困难负样本规避率与推理延迟；
- **硬性约束**：在生产和真实评测模式 (`allow_mock_for_contract_test=False`) 下：
  - 若环境缺少推理库或模型权重，**必须 Fail-Closed 阻断**；
  - 必须返回 `status="NOT_EXECUTED_MODEL_UNAVAILABLE"`；
  - 必须记录 `blocking_reason="BLOCKED_MODEL_RESOURCE: sentence-transformers 未安装..."`；
  - **严禁使用任何随机或哈希算法编造假数字填充表格**。

---

## 三、 当前环境实测与阻断证明

在当前标准测试环境中实测执行结果：
```python
res = run_full_ablation_study(tmp_path, allow_mock_for_contract_test=False)
```
- **Config A (基线: 纯词法 FTS5 + LocalHash)**：
  - 状态：`EXECUTED`
  - 真实实测 MRR：`0.8750`
  - 真实实测 NDCG@8：`0.8750`
  - 困难负样本规避率：`1.0000`
- **Config B ~ Config F (含真实 Dense 与 Cross-Encoder)**：
  - 状态：`NOT_EXECUTED_MODEL_UNAVAILABLE`
  - 阻塞代码：`BLOCKED_MODEL_RESOURCE`
  - 指标：`None`（诚实置空）

---

## 四、 生产部署 L2 验证路线图与前置要求

为在目标 GPU 生产环境完成 L2 真实模型效果验收，需满足以下环境准备：
1. **硬件配置**：至少 1 块 NVIDIA GPU (显存 >= 16GB, CUDA 12.x)；
2. **软件依赖**：
   ```bash
   pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
   pip install sentence-transformers transformers accelerate
   ```
3. **模型权重下载**：
   - 稠密检索：`BAAI/bge-m3`
   - 交叉精排：`BAAI/bge-reranker-large`
4. **验证命令**：
   ```bash
   python -m contract_kb.ablation_study --real-model
   ```
