# 企微 CLI → RAG 部署与运维

部署时间：2026-11-09，Asia/Singapore。

## 已部署范围

- 计划任务：`WeCom_CLI_RAG_Sync`；当前 Windows 用户登录时启动，每 5 分钟执行一次，错过启动可补跑，禁止重叠实例，单轮上限 15 分钟。
- 唯一来源：`法律事务部（含商务合同等文件）/商务合同模板/深圳市世纪恒程知识产权代理事务所` 的后代文件。
- 用户已明确选择将该目录设为受信任来源；每次版本激活通过 `approve_knowledge_version` 记录 `TRUSTED_SOURCE_SYNC` 审计。审批前的新版本为 PENDING_REVIEW，不抢占旧有效版本。
- 使用本机扫码绑定的官方 CLI 凭证。不使用旧自建应用 token，因此此次部署不依赖此前 640008 接口链路。
- 解析 DOCX、文本型 PDF；旧版 DOC 在独立缓存中通过本机 Word/WPS 转换为 DOCX，禁用宏、只读打开，源微盘不写入。
- SQLite：`data/contract-kb/contract_kb.db`；当前 Embedding 为 `local_hash`，不调用外部模型。
- 云端文件缺失、删除、移出目录均不自动停用知识，避免授权和搜索异常导致误删。需要停用的模板由人工处理。

## 查看状态和手动执行

状态：`data/wecom_cli_sync/health.json`；历史运行日志：`data/wecom_cli_sync/task.log`（超过 5 MB 滚动一份）。缓存和检查点：`data/wecom_cli_sync/files/`、`manifest.json`。这些含合同数据，不应提交 Git 或对外分享。

```powershell
Get-ScheduledTaskInfo -TaskName WeCom_CLI_RAG_Sync
Get-Content data/wecom_cli_sync/health.json
.\.venv\Scripts\python.exe tools/sync_wecom_cli.py --trusted-source
```

正常状态为 OK，任务退出码 0；SOURCE_UNAVAILABLE 表示云端查询失败，PARTIAL_FAILURE 表示部分文件失败，UNSUPPORTED_FILES 表示有未支持格式，EMPTY_SOURCE_CHECK_REQUIRED 表示未找到目标文件，STARTUP_OR_RUNTIME_FAILURE 表示初始化或运行异常。失败时不要清空 manifest 或知识库，先看 error 的阶段/错误码。

## 授权期限和恢复

本机加密保存凭证不等于永久业务授权，准确业务到期时间尚不能从当前状态确认，health 明确记为 UNKNOWN_REQUIRES_USER_RENEWAL。在企微当前绑定机器人的管理页面检查微盘相关“可使用权限”及显示的有效期，到期或撤销后按页面重新授权原机器人，再手动启动任务验证。不要把 `auth show --status` 的 authorized 当作所有业务权限均有效的证明，也不要反复 auth init 生成新机器人。

CLI 支持短期 token 自动刷新；业务授权过期或源目录无权限时不会通过重试绕过。任务会保留已有索引、返回非零并写状态/日志；恢复授权后下轮继续。当前没有向聊天或邮件推送告警。

## 验证记录

- 27 项新旧同步测试通过；ruff 静态检查通过。
- 当前目标目录实际 5 份 DOC 已下载、大小校验、转换并导入。
- 对应 248 个条款均 APPROVED，5 份文档均 ACTIVE，逐份通过生产 `GovernedRetrievalService.retrieve_authoritative` 检索。
- 重复真实扫描 changed=0、skipped=5、failed=0。
- 新计划任务已实际运行，退出码 0；原 `WeDrive_RAG_KnowledgeBase_Sync` 已停用。

## 限制

- 不是实时推送，延迟取决于五分钟轮询、云端搜索可见性、网络与转换时间。搜索结果不是官方完整变更日志；新同步器处理搜索返回的全部 cursor 页，但不能声称任何云端搜索都具备强一致完备性。
- 只有 Windows 用户登录时运行；锁屏后通常可继续，但未验证注销或无人登录场景。Office COM 转换依赖本机 Office/WPS，损坏、加密 DOC 可能失败；转换超时后需检查是否遗留 Office 进程，不能直接批量终止用户正在编辑的 Office。
- 扫描 PDF 需要 OCR，此管道不提供 OCR。未支持格式会在健康状态体现，不会悄悄视为成功。

## 回滚

停止并禁用新任务：

```powershell
Stop-ScheduledTask -TaskName WeCom_CLI_RAG_Sync
Disable-ScheduledTask -TaskName WeCom_CLI_RAG_Sync
```

原任务 XML 保存在 `data/wecom_cli_sync/previous-directory-sync-task.xml`。原任务默认带自动软删除且监控范围较大，不建议未经修复直接重新启用。

首次导入前的 SQLite 备份为 `data/contract-kb/contract_kb.before_cli_sync_20261109.db`。需要恢复数据库时先停止所有知识库写入者和服务、备份当前库，再通过 SQLite backup API 恢复；勿直接在活跃 WAL 数据库上覆盖文件。

## 本地同步目录（2026-11-09 更新）

用户指定本地落盘为项目 `参考/`。同步方向为截图中的云端事务所目录 → 本地参考目录 → RAG；保留云端相对分类和原文件名，例如 `参考/专利/国内专利服务合同（许可备案）-事务所.doc`。转换后的 DOCX 留在缓存，不与原件混放。既有编号模板保留，不自动上传本地文件，不同步删除。

首次落盘遇到不同的同名文件，将旧内容按哈希备份至 `data/wecom_cli_sync/local_backups/` 再更新。已管理文件如果发生本地修改，记录 `local_edit_conflict_preserved`，停止覆盖该文件并保留旧检查点；须人工确认本地编辑后再恢复。文件在本地缺失但缓存有效时自动补齐，不重建索引。`--local-dir` 可显式指定其他目标路径，默认值为项目 `参考/`。当前五份原件已逐份与云端下载指纹匹配。
