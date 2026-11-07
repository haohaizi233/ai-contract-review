# ADR-001：采用基于整洁架构与端口适配器的模块化单体（Modular Monolith）

- **状态**：已采纳（Accepted）
- **日期**：2026-11-07
- **决策者**：首席软件架构师（Principal Architect）

## 1. 背景与问题

基线架构审计显示，项目原本是平铺在 `contract_kb/` 下的 25 个 Python 模块与 `tools/` 下的 18 个运维同步脚本：
- 存在 2 组强连通循环依赖（`embeddings <-> embeddings_real`、`review_engine <-> rule_engine`）及 2 处核心包反向依赖外部工具目录（`classifier -> tools`、`distill -> tools`）。
- 团队当前部署规模面向企业内部法务与业务中台（78 份核心合同模板、数千级在审与归档合同案卷），尚未达到需要按领域拆分独立网络进程、分布式事务（Saga/2PC）或服务网格的吞吐量级。

## 2. 备选方案评估

| 方案 | 优势 | 劣势与风险 | 结论 |
| :--- | :--- | :--- | :---: |
| **方案 A：维持平铺过程式脚本结构** | 改动最小 | 循环依赖恶化、数据库直连泄漏、单函数圈复杂度 > 100、无法保障租户隔离与事务一致性 | 拒绝 |
| **方案 B：直接拆分为多微服务 + Kafka + K8s** | 进程级物理隔离 | 引入巨大分布式运维复杂度、网络序列化开销、跨服务分布式事务难题，且严重违背“渐进式重构、禁止无证据过度架构”原则 | 拒绝 |
| **方案 C：Modular Monolith + Clean Architecture + Port/Adapter** | 进程内零网络开销、支持单库 ACID 原子工作单元（UoW）、通过 AST 架构契约测试强制单向依赖，同时保留既有 `contract_kb.*` 与 `tools/*.py` 导入兼容 | 需要明确定义分层目录与架构守护测试防止未来退化 | **采纳** |

## 3. 决策内容

在既有 `contract_kb` 命名空间内构建清晰四层模块化单体结构（不进行破坏性顶层包重命名）：
1. **`contract_kb/domain/`**：纯领域模型、值对象（`Decimal` 金额、合同编号）、领域事件与无状态领域服务，严禁依赖 Web 框架、SQL 驱动或外部 SDK。
2. **`contract_kb/application/`**：14 阶段 `ReviewPipeline`、受控 Agent 状态机工作流与抽象端口协议（`KnowledgeStoragePort`、`UnitOfWorkPort`）。
3. **`contract_kb/modules/`**：按限界上下文划分业务模块（`templates`、`rules`、`retrieval`、`cases`、`integrations`）。
4. **`contract_kb/infrastructure/` & `contract_kb/interfaces/`**：持久化工作单元、轻量异步任务执行器、结构化脱敏观测组件及 HTTP API 路由适配。

## 4. 影响与验证方式

- **兼容性**：`from contract_kb.review_engine import ReviewEngine`、`from contract_kb.api import app` 及全部 CLI 命令 100% 原地兼容。
- **架构守护**：通过自动化 AST 依赖审计测试持续强制 **0 循环依赖、0 反向 `tools/` 依赖、0 跨层原始连接泄漏**。
