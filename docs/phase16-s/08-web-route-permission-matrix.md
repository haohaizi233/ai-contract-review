# Phase 16-S: 08 - Web 工作台路由权限矩阵与安全防护规范 (Web Route Permission Matrix)

## 1. 对应实际代码路径
- `contract_kb/interfaces/web/app.py`: `WEB_ROUTE_ROLES` 字典与 `_get_request_identity()`
- `contract_kb/interfaces/http/auth.py`: `Principal.has_role()` 角色阶梯判定

## 2. 集中式路由权限矩阵

| HTTP 方法与路径 | 最低角色要求 | 是否绑定租户 | 是否绑定 Session | 接口功能与安全约束 |
|---|---|---|---|---|
| `GET /` / `GET /index.html` | Anonymous / Viewer | 否 | 否 | Web Diff 工作台首页，企业模式无认证会话时 `INITIAL_DATA = null`，禁止泄露合同 |
| `GET /api/templates/list` | Viewer | 是 | 否 | 获取可用标准合同母本列表 |
| `GET /api/ref_templates/catalog` | Viewer | 是 | 否 | 获取标准参考模板目录及元数据 |
| `GET /api/materials/stats` | Viewer | 是 | 否 | 获取已审批招投标素材库统计数据 |
| `GET /api/materials/cases` | Viewer | 是 | 否 | 获取同类合同参考案例摘要 |
| `GET /api/materials/case_detail` | Viewer | 是 | 否 | 获取参考案例详情 |
| `GET /api/diff/agent_stream` | Viewer | 是 | 是 | SSE 实时事件流订阅，强校验 Session 所有权，禁止跨用户窃听 |
| `GET /api/diff/agent_status` | Viewer | 是 | 是 | 查询当前合同审查进度与熔断器状态，严格隔离不同用户会话 |
| `GET /api/diff/sample` | Reviewer | 是 | 否 | 加载内置示例合同（仅用于本地演示与测试评估） |
| `POST /api/diff/upload` | Reviewer | 是 | 是 | 合同文件上传，执行 MIME 检查、路径穿越过滤与大小配额限制 |
| `POST /api/diff/switch-template` | Reviewer | 是 | 是 | 切换待比对标准合同母本版本 |
| `POST /api/diff/prioritize_card` | Reviewer | 是 | 是 | 优先审查指定条款卡片，必须验证会话所有权 |
| `POST /api/diff/toggle_pause` | Reviewer | 是 | 是 | 暂停或继续当前审查任务，仅任务发起人可操作 |
| `POST /api/diff/retry_card` | Reviewer | 是 | 是 | 重新审查失败或需重新研判的条款卡片 |
| `POST /api/diff/skip_card` | Reviewer | 是 | 是 | 手动跳过特定条款审查，记录跳过审计人 |
| `POST /api/diff/cancel` | Reviewer | 是 | 是 | 取消当前审查任务并释放工作线程 |
| `POST /api/diff/human_decision` | Reviewer | 是 | 是 | 法务人员提交条款判定结论（采纳/修改/驳回） |
| `POST /api/diff/feedback` | Reviewer | 是 | 是 | 提交法务修改意见，服务端强制覆盖身份，杜绝客户端自批 |
| `POST /api/templates/reload` | Admin | 是 | 否 | 重新热重载并编译受控标准知识库与母本目录 |
| `POST /api/diff/reset_circuit` | Admin | 是 | 否 | 重置大模型调用自适应熔断器状态 |
| `POST /api/diff/export_training_data`| Admin | 是 | 否 | 导出当前租户已脱敏微调数据集，严格阻断普通用户与跨租户导出 |

## 3. 角色层级继承模型 (Role Hierarchy)
```text
Admin (拥有全部写权限、模板重载、熔断重置与训练集导出权限)
  ↑
Reviewer (拥有合同上传、比对、Agent 任务控制、批注判定与修改意见提交权限)
  ↑
Viewer (拥有合同查看、SSE 订阅、状态与案例检索只读权限)
```

## 4. 上传与文件安全规范
- **路径穿越防御**：所有上传文件名必须通过 `pathlib.Path(fn).name` 提取基准文件名，并去除 `..`、`/`、`\` 等非法字符。
- **文件体积防护**：Web 层在单次读取正文前强校验 `Content-Length <= 50MB`，超限直接阻断拒绝服务攻击。
- **租户会话目录物理隔离**：上传合同统一落盘至 `data/sessions/{tenant_id}_{hash}/{session_id}/`，禁止任何不同租户共享物理目录。

## 5. 测试用例
- `tests/security/test_web_enterprise_auth_fail_closed.py::test_rbac_viewer_blocked_from_mutating_actions`
- `tests/security/test_feedback_approval_authorization.py::test_sft_export_requires_admin_and_tenant_scoped`
- `tests/security/test_e2e_authenticated_review_workflow.py`
