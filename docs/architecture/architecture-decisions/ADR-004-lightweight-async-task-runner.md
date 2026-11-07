# ADR-004：基于关系数据库持久化的轻量异步任务执行器（PersistentTaskRunner）

- **状态**：已采纳（Accepted）
- **日期**：2026-11-07
- **关联模块**：`contract_kb/infrastructure/task_queue/runner.py`、`contract_kb/storage.py`、`contract_kb/storage_pg.py`、`migrations/003_review_pipeline_and_canonical_contract.sql`

## 1. 背景与问题

批量合同审查、大体积 `.docx` 深度语义审核及跨库增量同步耗时可能超过同步 HTTP 网关超时阈值（通常 30s~60s）。
架构原则明确要求：
- **禁止在缺少容量瓶颈证据时引入 Kafka、RabbitMQ、Celery + Redis 或 Kubernetes 等过量中间件**；
- 同时必须具备企业级可靠性：**状态持久化、幂等提交、进度上报、取消控制、指数退避重试、死信诊断落盘（Dead-Letter）、并发度上限以及临时上传文件自动清理**。

## 2. 方案对比

| 候选方案 | 优点 | 缺点与风险 | 结论 |
|---|---|---|---|
| FastAPI `BackgroundTasks` 纯内存协程/线程 | 零开发成本 | 进程重启即丢任务、无持久化进度、无死信留痕、无跨实例并发锁 | **拒绝** |
| Celery + Redis / RabbitMQ | 高吞吐分布式队列成熟 | 额外引入 Broker 运维、鉴权与高可用负担，超出模块化单体当前吞吐需求 | **暂不引入（保留端口未来平滑替换）** |
| **基于既有 SQLite / PostgreSQL 的 `PersistentTaskRunner`** | 零额外中间件依赖；PostgreSQL 下利用 `FOR UPDATE SKIP LOCKED` 原生实现多 Worker 无锁竞争消费；事务与业务库一致 | 极端超高并发（>1,000 QPS 写入）下受限于关系库行锁 | **采纳** |

## 3. 核心设计决策

1. **统一 `async_jobs` 持久化模型**：
   - 状态流转：`PENDING` → `RUNNING` → `SUCCEEDED` | `RETRYING` → `DEAD_LETTER` | `CANCELLED`。
   - 租户级幂等：`(tenant_id, idempotency_key)` 唯一约束，重复提交直接返回既有 `job_id` 并立即清理冗余临时文件。
2. **双数据库原子认领（Claim）**：
   - SQLite 使用进程内 `RLock` + 事务原子更新 `status='RUNNING', attempts=attempts+1`；
   - PostgreSQL 使用 `SELECT ... FROM async_jobs WHERE status IN ('PENDING','RETRYING') AND next_run_at <= NOW() ORDER BY next_run_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED` 实现多进程安全消费。
3. **指数退避与死信隔离（DLQ）**：
   - 第 `k` 次失败按 `base_backoff * 2^(k-1)` 推迟 `next_run_at` 并置为 `RETRYING`；
   - 达到 `max_attempts` 后转入 `DEAD_LETTER`，错误信息先经 `redact_sensitive_text()` 脱敏后再写入 `dead_letter_reason`。
4. **双模式驱动**：
   - 守护进程模式：设置 `CONTRACT_KB_ENABLE_JOB_WORKER=1`，随 FastAPI Lifespan 启动受 `max_workers` 限制的后台线程池；
   - 受控步进模式：通过 `runner.process_next()` / `POST /api/v1/jobs/worker/drain` 同步确定性消费，保障单元测试与批处理零竞态抖动。

## 4. 演进触发条件

当未来单库异步作业峰值持续超过 `200 jobs/sec` 或出现跨独立服务的事件扇出需求时，只需新增实现相同 `submit / cancel / process_next` 契约的外部队列适配器，无需改动上层 HTTP 控制器或 `ReviewEngine`。
