# 企业级 HTTP API 文档（contract-kb v1.0）

## 1. 认证、授权、多租户与版本约定

- **推荐前缀**：`/api/v1/*`（历史 `/api/*` 路由处于平滑兼容期，自动返回 RFC 8594 `Deprecation: true` 与 `Link` 继任路由响应头）。
- **受信任网关注入头**：
  - `X-User`：调用者唯一用户标识（审批人、反馈人、任务创建者均强制取自该认证身份，拒绝客户端报文伪造 `actor`/`approver`）。
  - `X-Roles`：逗号分隔角色（`viewer` < `reviewer` < `admin`）。
  - `X-Tenant`：租户隔离标识（默认 `default`，全链路 SQL 级隔离）。
  - `X-Auth-Timestamp` / `X-Auth-Signature`：配置 `CONTRACT_KB_AUTH_SHARED_KEY` 时强制校验 HMAC-SHA256 签名与防重放时间窗。
- **链路追踪头**：支持客户端透传或服务端自动生成 `X-Request-ID` 与 `X-Trace-ID`，并在所有正常及异常响应头中回传。

---

## 2. 探针与可观测性

| 方法与路径 | 权限 | 说明 |
|---|---|---|
| `GET /healthz` (`/api/v1/healthz`) | 公开探针 | 进程存活探针（Liveness），返回 `{"status":"ok","service":"contract-kb","version":"1.0.0"}` |
| `GET /readyz` (`/api/v1/readyz`) | 公开探针 | 存储就绪探针（Readiness），实测数据库查询并返回 `db_latency_ms`，异常返回 `503` |
| `GET /api/v1/metrics` | `admin` | 返回审核吞吐、14 阶段耗时、规则命中分布、LLM 降级率与 RAG 命中率；支持 `?format=prometheus` |

---

## 3. 合同模板知识库与规则治理

- `POST /api/v1/contract-kb/import`（`admin`）：幂等扫描导入标准合同模板库。
- `GET /api/v1/contract-kb/documents?limit=50&offset=0`（`viewer`）：v1 标准分页文档列表（注：兼容路径 `GET /api/contract-kb/documents` 保持返回裸数组）。
- `GET /api/v1/contract-kb/documents/{doc_id}`（`viewer`）：查询同租户模板详情与结构化条款。
- `GET /api/v1/contract-kb/taxonomy`（`viewer`）：查询合同五级业务分类体系。
- `POST /api/v1/contract-kb/search`（`viewer`）：租户隔离混合检索（FTS + 向量 + RRF + Rerank）。
- `POST /api/v1/contract-kb/compare`（`viewer`）：双条款结构化差异比对（金额/期限/否定词/主体/义务强度专项检测；相似度仅作检索提示，不作“语义等价”断言）。
- `GET /api/v1/contract-kb/rules` / `POST /api/v1/contract-kb/rules` / `POST /api/v1/contract-kb/rules/{rule_id}/approve`：版本化规则创建（默认 `DRAFT`）与法务审批（`APPROVED`）。
- `GET /api/v1/contract-kb/format-standards` / `POST /api/v1/contract-kb/format-standards` / `POST /api/v1/contract-kb/format-standards/{standard_id}/approve`：公司 Word 排版标准版本化治理（仅 `ACTIVE` 标准产生正式违规结论）。

---

## 4. 合同同步审核、阶段追踪与带审计人工覆盖

- `POST /api/v1/contract-review/tasks`（`reviewer`）：
  - Multipart 上传 `.docx`（流式分块限额 20MB），参数 `mode`（`STANDARD` | `CUSTOM` | `COMBINED`）、`custom`（分号分隔自定义要求），可选请求头 `X-Idempotency-Key`。
  - 返回完整 `ReviewResult`（含 `task_id`、`rule_set_version`、`model_version`、`reproducibility_hash`、`review_gate`、`finding_breakdown`、14 阶段摘要 `stages` 与定位到段落节点的 `issues`）。
- `GET /api/v1/contract-review/tasks?limit=20&offset=0&status=COMPLETED`（`reviewer`）：分页查询审核任务。
- `GET /api/v1/contract-review/tasks/{task_id}`（`reviewer`，限创建者或 `admin`）：任务详情。
- `GET /api/v1/contract-review/tasks/{task_id}/issues`（`reviewer`）：风险发现明细列表。
- `GET /api/v1/contract-review/tasks/{task_id}/stages`（`reviewer`）：14 阶段执行耗时与状态留痕。
- `GET /api/v1/contract-review/tasks/{task_id}/overrides`（`reviewer`）：不可篡改人工复核覆盖历史。
- `POST /api/v1/contract-review/tasks/{task_id}/cancel`（`reviewer`）：请求协作式取消审核任务。
- `POST /api/v1/contract-review/issues/{issue_id}/feedback`（`reviewer`）：
  - 请求体：`{"action": "ACCEPT|IGNORE|EDIT|NOTE|FALSE_POSITIVE|FALSE_NEGATIVE", "note": "..."}`
  - 经 `HumanOverrideService` 更新当前任务问题状态并同时写入 `human_overrides` 与 `audit_log`，绝不静默篡改企业全局规则表。

---

## 5. 标准合同实体与持久化异步任务队列

- `GET /api/v1/canonical-contracts?limit=50&offset=0` / `GET /api/v1/canonical-contracts/{contract_id}`（`viewer`）：查询统一防腐标准合同实体。
- `POST /api/v1/jobs/review`（`reviewer`，返回 `202 Accepted`）：提交持久化异步合同审核作业（支持 `X-Idempotency-Key`）。
- `GET /api/v1/jobs?limit=20&offset=0&status=PENDING` / `GET /api/v1/jobs/{job_id}`（`reviewer`）：查询异步作业进度（`0..100`）、重试次数与结果。
- `POST /api/v1/jobs/{job_id}/cancel`（`reviewer`）：取消排队或重试中的异步作业并自动清理临时上传文件。
- `POST /api/v1/jobs/worker/drain`（`admin`）：受控触发内嵌异步作业消费。

---

## 6. 统一错误响应信封（`ErrorEnvelope`）

所有 `4xx` / `5xx` 错误统一返回脱敏 JSON（绝不泄露内部堆栈、文件路径或数据库 DSN）：

```json
{
  "error": "权限不足：需要 admin",
  "code": "HTTP_403",
  "status_code": 403,
  "request_id": "req_8f1d3012d855",
  "trace_id": "trc_ac58073d4f95469f"
}
```
