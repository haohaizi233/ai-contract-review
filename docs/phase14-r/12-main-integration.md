# Phase 14 与 main（Phase 15 架构）的集成记录

## 合并基线

- Phase 14 原分支：`fix/phase14-cross-boundary-integrity`，提交 `37d8a9d`。
- 集成目标：`origin/main`，提交 `cd74b8f`（已合入 Phase 15 包架构迁移）。
- 共同祖先：`16bf8f8`。
- 采用将 main 合入 Phase 14 的 merge commit，保留两边历史；不重写远端历史。

## 冲突处理

6 个文本冲突集中在旧单体实现与新兼容入口之间。保留 main 的包结构和兼容入口，把 Phase 14 的行为改动移到运行时真正使用的模块，避免只消除文本标记却丢失修复。

| 原实现 | 集成后的实现 |
|---|---|
| `contract_kb/full_diff.py` | `contract_kb/modules/contract_comparison/full_diff.py` |
| `contract_kb/legal_agent.py` | `contract_kb/application/agents/legal_review_agent.py` |
| `contract_kb/storage.py` | `contract_kb/infrastructure/persistence/sqlite_storage.py` |
| `contract_kb/storage_pg.py` | `contract_kb/infrastructure/persistence/postgres_storage.py` |
| `tools/contract_diff_web.py` | `contract_kb/interfaces/web/app.py`、`routes/`、`services/`、`static/js/` |
| `tools/sync_wecom_wedrive.py` | `tools/integrations/wecom/sync_wecom_wedrive.py` |

同时核查自动识别重命名的检查点、审查会话、知识适配器与 PDF 解析模块，保留 Phase 14 的全文指纹、HMAC、缓存隔离、OCR 占位和知识类型兼容映射；新增测试中的导入与 monkeypatch 路径改为 Phase 15 实现模块。

Web 修复分配到对应应用入口和路由：签名身份校验、拒绝越权与跨站写入、租户会话文件隔离、初始 JSON 安全嵌入、人工研判状态流转及审计、前端请求与 SSE 会话身份传递。

## 验证

全量测试、Ruff、格式、mypy、架构适应性测试及远端 CI 的实际结果记录在本次 PR 描述中。涉及真实 PostgreSQL、真实企业语料和真实模型的用例，仅在具备对应配置的环境执行；跳过不能作为这些能力已经验证的证据。

本地串行全量验证：`pytest tests tools/wecom_wedrive_probe/tests` 为 **589 passed、38 skipped**（59.61 秒）；有 1 条第三方 FastAPI/Starlette 测试客户端弃用提示。跳过项由真实 PG、真实语料、真实模型及本机素材库配置决定。

`ruff check .`、`ruff format --check .`（485 文件）和 `mypy contract_kb --ignore-missing-imports --no-error-summary` 均通过。源码无残留冲突标记，`git diff --cached --check` 通过。首次全量曾出现共享合成语料目录在两个测试进程间重建的哈希不一致；停止并行测试后完整串行复测通过，未修改业务逻辑以绕过幂等断言。

## CI 首轮反馈与修复

首轮远端 CI 暴露本地旧 editable 安装掩盖的两处 `contract_kb.auth` 导入，已改为 `contract_kb.interfaces.http.auth`。新增架构测试按当前仓库的物理文件核验内部绝对导入，避免从其他工作区意外加载已删除模块。使用 `python -S` 加载依赖但不执行旧 editable `.pth`，完整串行复测为 **590 passed、38 skipped**。

真实 PG 测试原先直接要求包含 PENDING_REVIEW 条款的版本回滚成功，与 Phase 14 新增的回滚审批校验冲突。两处 PG 测试夹具改为调用公开审批接口，先验证未审批回滚被拒绝，再验证已审批历史版本回滚成功；未放宽生产审批检查。最终 PG 与 CI 门禁结果以 PR 检查为准。
