# 生产部署、故障演练与回滚操作手册（Deployment and Rollback Runbook）

> 文档编号：OPS-2026-RUNBOOK-01  
> 对应任务：T17（真实环境部署演练与回滚闭环）  
> 适用环境：PostgreSQL 16/18 + pgvector、Linux Docker / Kubernetes / Systemd

---

## 1. 部署架构与版本前置要求

| 组件 | 生产最低规格 / 版本要求 | 作用说明 |
|---|---|---|
| **Python 运行时** | Python 3.12 或 3.13 | 核心 API 服务与异步 Worker 运行环境 |
| **主数据库** | PostgreSQL 16.x / 18.x | 关系型业务数据、租户隔离、审计追踪与 UoW 事务管理 |
| **向量扩展** | `pgvector` >= 0.7.0 (锁定 digest) | 合同条款与模板知识库的语义向量相似度索引 |
| **持久化存储** | 本地 SSD / NVMe 或受控 MinIO 对象存储 | 合同文档暂存、解析缓存与相册多媒体存储 |

---

## 2. 数据库迁移执行流程（Migrations 001–010）

### 2.1 迁移执行前预检（Preflight）
```bash
# 1. 验证目标数据库连通性及 pgvector 扩展可用性
psql "$CONTRACT_KB_PG_DSN" -c "CREATE EXTENSION IF NOT EXISTS vector;"
psql "$CONTRACT_KB_PG_DSN" -c "SELECT extversion FROM pg_extension WHERE extname = 'vector';"

# 2. 验证当前数据库迁移版本记录
psql "$CONTRACT_KB_PG_DSN" -c "SELECT id, version, applied_at FROM schema_migrations ORDER BY id ASC;"
```

### 2.2 增量迁移清单（向前兼容）
1. `001_init.sql`: 核心文档表与基础向量表；
2. `002_embedding_meta_task_tenant_format_std.sql`: 租户隔离列、任务记录与格式标准；
3. `003_human_review_and_pipeline_stages.sql`: 人工复核与流水线阶段状态；
4. `004_request_idempotency.sql`: 请求指纹与 Owner 绑定幂等表；
5. `005_auth_replay_nonces.sql`: HMAC V2 签名防重放 Nonce 记录；
6. `006_job_leases.sql`: 持久化作业租约（Fenced Lease）与心跳表；
7. `007_execution_checkpoints.sql`: 流水线阶段快照与恢复 Manifest；
8. `008_contract_source_history.sql`: 稳定合同实体与多来源合流版本历史；
9. `009_sync_checkpoints.sql`: 外部异构系统增量同步水位线与批次审计；
10. `010_knowledge_governance_metadata.sql`: 知识资产审批与来源准入元数据。

---

## 3. Worker 进程管理与租约心跳配置

系统采用 **乐观锁租约栅栏（Fenced Leases）** 机制管理后台并发审核作业：
- `lease_duration_seconds`: 默认 90 秒；
- `heartbeat_interval_seconds`: 默认 30 秒（Worker 活跃期间定期向 DB 上报心跳）；
- `max_claim_attempts`: 3 次；
- **优雅停机（Graceful Shutdown）**：
  收到 `SIGTERM` 或 `SIGINT` 时，Worker 标记当前阶段保存快照（Checkpoint），主动调用 `finish_job(status="CANCELLED")` 或释放租约，等待当前 IO 阶段结束（超时不超过 15 秒）。
- **进程崩溃自动接管（Crash Recovery）**：
  若 Worker 进程因 OOM 或宿主机断电突发退出，租约在 90 秒后自然过期（`lease_until < now()`），其他空闲 Worker 进程通过 CAS 原语自动接管作业，从最新阶段快照无损恢复执行，避免重新执行前置阶段。

---

## 4. 故障演练场景与恢复步骤

| 故障场景 | 触发机制 | 恢复验证指标 |
|---|---|---|
| **Worker 突然被杀** | `kill -9 <worker_pid>` | 超过租约期限后其他 Worker 接管，状态从 `RUNNING` 恢复，已完成阶段不重复跑 |
| **外部 LLM 超时 / 限额熔断** | 注入超时异常或超限响应 | 预算预留立即回退，降级为确定性规则结论与人工复核，不悬挂僵尸任务 |
| **数据库事务异常** | 覆写保存阶段注入故障 | 触发 `UoW.rollback()`，任务状态、审计记录与覆写历史全部原子回滚 |
| **磁盘空间告警** | 模拟临时上传目录积压 | 执行 `collect_expired_uploads(now, dry_run=False)` 清理 24h 以外孤儿文件 |

---

## 5. 紧急回滚操作规范（Rollback Runbook）

### 5.1 纯代码版本回滚
若新版本代码在流量观察期发现异常：
```bash
# 1. 将服务切流回旧版本镜像
kubectl rollout undo deployment/contract-review-api

# 2. 验证旧版本服务健康状态与就绪检查
curl -f http://127.0.0.1:8000/readyz
```

### 5.2 数据库兼容性保障纪律
- 新增迁移（004–010）全部采用 **向前兼容（Forward-Compatible）设计**，仅新增独立表与可选列，旧版本代码可继续只读访问老表；
- **严禁** 在未备份的情况下执行 `DROP TABLE` 或 `DROP COLUMN`；
- 如需回退幂等或租约配置，暂停新 Worker 认领，将未决 Job 置为 `PENDING`，由维护人员根据审计对账单统一重审。
