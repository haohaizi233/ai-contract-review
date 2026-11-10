# Phase 14-R: 生产就绪与部署运维指南 (10-production-readiness-and-deployment-guide)

## 一、生产环境基线配置要求

| 基础设施组件 | 推荐规格 / 版本 | 关键配置要求 |
|---|---|---|
| **操作系统** | Linux (Ubuntu 22.04 LTS / RHEL 9) 或容器化运行 | 内核开启 TCP BBR、文件句柄数 `ulimit -n 65536` |
| **Python 运行时** | Python 3.10 / 3.11 / 3.12 | 使用专用虚拟环境或轻量 Docker 镜像 (python:3.11-slim) |
| **数据库** | PostgreSQL 14+ (生产) / SQLite 3.38+ (轻量/本地) | PG 开启 SSL 连接、连接池大小 $\ge 20$、预写日志 WAL 保留 |
| **反向代理网关** | Nginx 1.22+ / Envoy / Traefik | 强制 HTTPS、剥离不受信请求头、重写可信客户端身份 |
| **文件存储** | 分布式对象存储 (S3 / MinIO) 或受限本地 NAS | 开启服务端静态加密 (SSE-KMS)、私有 Bucket、租户隔离前缀 |

---

## 二、关键环境变量配置清单

生产环境中，所有敏感密钥必须由秘密管理工具 (如 Vault, AWS Secrets Manager, K8s Secrets) 注入，严禁明文提交至代码仓库。

| 环境变量名称 | 生产取值示例 | 默认值 | 作用与安全约束 |
|---|---|---|---|
| `DIFF_WEB_MODE` | `ENTERPRISE` | `LOCAL_DEV` | 运行模式。生产必须设为 `ENTERPRISE`，强制开启 HMAC-SHA256 签名校验与多租户权限隔离。 |
| `CONTRACT_KB_AUTH_SHARED_KEY` | `base64:AbCdEf123456...` (32+ 字节高熵串) | 无 | 企业工作台与 API 间鉴权 HMAC 密钥。未配置在企业模式下拒绝启动。 |
| `CONTRACT_KB_CHECKPOINT_SECRET` | `base64:987654XyZ...` (32+ 字节高熵串) | 无 | 审查任务检查点 (Checkpoint) HMAC-SHA256 签名密钥，防状态篡改。 |
| `CONTRACT_KB_STORAGE_BACKEND` | `postgres` | `sqlite` | 持久化后端选择。生产环境推荐配置为 `postgres`。 |
| `DATABASE_URL` | `postgresql://user:pwd@pg-ha:5432/contract_db?sslmode=require` | 无 | PostgreSQL 连接串，必须开启 `sslmode=require`。 |
| `CONTRACT_KB_ALLOWED_INTERNAL_HOSTS` | `internal-llm.corp.local,agent-hub.vpc` | 空 | SSRF 保护白名单。仅允许与受信任的内部微服务通信，杜绝内网探测。 |
| `SESSION_CACHE_DIR` | `/var/lib/contract_review/sessions` | 系统临时目录 | 会话工作文件存储路径。必须设置专用卷并定期清理。 |

---

## 三、反向代理 (Nginx) 标准安全配置

```nginx
# 生产 Nginx 配置示例
upstream contract_backend {
    server 127.0.0.1:8000 max_fails=3 fail_timeout=10s;
    keepalive 32;
}

server {
    listen 443 ssl http2;
    server_name contract-review.internal.corp;

    ssl_certificate     /etc/ssl/certs/contract.crt;
    ssl_certificate_key /etc/ssl/private/contract.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # 1. 彻底剥离客户端伪造的身份 Header
    proxy_set_header X-Tenant-ID "";
    proxy_set_header X-User-ID "";
    proxy_set_header X-User-Role "";

    # 2. 从网关认证层 (OAuth2/SSO/mTLS) 注入权威身份
    proxy_set_header X-Tenant-ID $http_x_auth_tenant_id;
    proxy_set_header X-User-ID $http_x_auth_user_id;
    proxy_set_header X-User-Role $http_x_auth_user_role;

    # 3. 标准网络追踪头
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # 4. SSE (Server-Sent Events) 长连接支持
    location /api/agent/stream {
        proxy_pass http://contract_backend;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 600s;
    }

    # 5. 常规 API 与静态资源
    location / {
        proxy_pass http://contract_backend;
        client_max_body_size 50M;
    }
}
```

---

## 四、数据库迁移与版本回滚 SOP (Standard Operating Procedure)

涉及任何知识库、条款表或审批状态表结构变动时，必须严格执行以下四步法：

```mermaid
flowchart LR
    Step1[1. 数据库热备与统计] --> Step2[2. Dry Run 演练与验证]
    Step2 --> Step3[3. 执行正式迁移]
    Step3 --> Step4[4. 后置校验与一致性检查]
    Step3 -.->|迁移异常| Rollback[回滚并恢复快照]
```

### 步骤 1：迁移前快照与统计
```bash
# 1. 创建带时间戳的只读快照
pg_dump -U postgres -d contract_db -Fc -f /backups/contract_db_pre_phase14_$(date +%Y%m%d%H%M).dump

# 2. 迁移前记录数与哈希基线统计
psql -U postgres -d contract_db -c "
SELECT tenant_id, count(*), sum(version) FROM clauses GROUP BY tenant_id;
SELECT count(*) FROM audit_logs;
"
```

### 步骤 2：Dry Run 演练
- 在 Staging 预发环境对影子库执行迁移；
- 校验迁移脚本中无未提交事务、无锁表超时风险；
- 验证回滚脚本逆向执行成功率达 100%。

### 步骤 3：正式迁移与权限变更
```bash
# 执行迁移脚本
python -m contract_kb.storage_pg --migrate-db
```

### 步骤 4：后置一致性校验
- 执行 `tests/contract/test_pg_sqlite_governed_retrieval_parity.py` 验证 SQLite 与 PG 双引擎行级表现一致性；
- 验证所有历史条款的 `approval_status` 与 `source_type` 映射无遗漏；
- 启动应用并执行健康检查探测。

---

## 五、健康检查与可观测性监控

1. **健康检查端点**：
   - `GET /healthz`：基础进程存活性检查（Liveness Probe）。
   - `GET /readyz`：依赖就绪检查（Readiness Probe，检查 DB 联通性与存储读写权限）。
2. **审计留痕监控**：
   - 生产环境中，`human_audit_trail`（人工审核痕迹）与 `agent_audit_trail`（Agent 推理痕迹）应定期归档至冷存或只读安全日志库（如 ELK / ClickHouse）。
   - 告警规则：单租户 5 分钟内连续触发 CSRF 拦截或 HMAC 校验失败超过 10 次，立即上报企业安全运营中心 (SOC)。
