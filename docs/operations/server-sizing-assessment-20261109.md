# 服务器容量与操作系统评估

日期：2026-11-09，Asia/Singapore。依据当前工作区代码、实际数据盘点、Windows 单进程抽样及官方资料。本文是容量规划建议，不是 Linux 压测或生产并发承诺。

## 建议采购与部署选择

建议主服务器：x86_64 Linux（Ubuntu Server 24.04 LTS）、8 vCPU、32 GB RAM、300 GB 可扩容 SSD/NVMe。初期无需 GPU。若明确使用经授权的外部/独立模型服务且并发很小，可先使用 4 vCPU、16 GB、200 GB SSD。先完成迁移验收再切生产。

如果现阶段仍依赖企微 Windows 桌面客户端落盘，将采集放在独立 Windows 工作站/虚拟机，建议 2–4 vCPU、8 GB RAM、100 GB SSD；可复用现有工作站。不要因为采集器依赖 Windows 就把数据库、API 和模型全部绑定到桌面会话。

上述配置不包含本地部署生成式大语言模型的容量；如果要在这台机器上运行生成模型，需要按模型、量化、上下文长度、并发和延迟单独选显卡及显存。

## 代码与数据事实

当前核心为 Python/FastAPI + NumPy + DOCX/PDF 解析；异步执行器默认两个线程，不要求预先部署 Redis/Celery/Kafka。主库当前 SQLite，真实生产可选择已实现的 PostgreSQL/pgvector 后端，但迁移不是仅改环境变量就视为验收完成。

本次只读盘点：

| 项目 | 实测 |
|---|---:|
| 当前 SQLite 主库文件 | 33.18 MiB |
| documents / clauses / embeddings | 208 / 9,565 / 9,565 |
| data 目录合计 | 27.846 GiB |
| e9-opinion-export | 21.449 GiB |
| tender_files | 3.483 GiB |
| tender-case-export | 1.806 GiB |

导出原件与缓存占据大部分空间。不能将所有文件数视作已入库合同数，也不能仅按 53 份微盘合同估算整个项目磁盘。需要明确保留哪些历史原件、缓存和版本，再制定备份保留期。

当前 local_hash 只是轻量离线检索基线，不等同于真实中文语义 Embedding；LLM 配置为 none，不能把此配置的资源消耗当作完整大模型审核的消耗。

抽样新 Python 进程导入 API、打开现有库并执行检索：工作集约 59.7 MiB、峰值 61.1 MiB，Private Commit 687.9 MiB。Windows 工作集与提交量不同，不能据此承诺服务仅需 60 MB，也不能直接当作 Linux RSS。另一次顺序五查询耗时约 133–1746 ms，未隔离本机其他任务，不是并发压测或端到端审核性能。原始记录：reports/server-sizing-baseline-20261109.json。

## 配置档位（工程估算）

| 场景 | vCPU | RAM | SSD | 条件 |
|---|---:|---:|---:|---|
| 开发/功能验证 | 2 | 4 GB | 100 GB | 少量数据、低并发、无本地生成模型 |
| 小部门试运行 | 4 | 16 GB | 200 GB | API、PG、少量并行任务；模型在外部授权服务或只用轻量基线 |
| 建议正式起步 | 8 | 32 GB | 300 GB，可扩容 | 数据历史增长、本地 Embedding/精排预留、批量解析与备份余量 |
| 本地 Embedding + 精排 | 8–16 | 32–64 GB | 300–500 GB | 必须测序列长度、batch 和并行模型副本；GPU 可选 |
| 本地生成式 LLM | 单独规划 | 单独规划 | 单独规划 | 不能用上一行推导生成模型的显存和吞吐 |

CPU 选现代通用型实例，关注持续单核性能和 SSD 随机 IO。核心 Python/NumPy 服务不需要桌面级旗舰 CPU。初始解析任务并行度建议 1–2，再根据队列积压及内存测量增加；不要将办公室用户人数等同于同时审核合同的任务数。

## 为什么建议 Linux 主服务

API、解析器和数据库接口主要为跨平台 Python；项目现有生产运维文档也面向 Linux/systemd/Docker。官方企微 CLI 支持 Linux，因此 CLI 本身不强制使用 Windows。

当前同步部署脚本并不能原样复制到 Linux：

- tools/sync_wecom_cli.py 使用 msvcrt 进程锁，需要替换为跨平台锁或数据库租约。
- convert_legacy_doc.ps1 使用 Word COM；现有计划任务要求交互登录。
- 绝对本地路径、凭据存储方式、任务启动及日志路径需迁移。

DOC 转换可评估 Linux LibreOffice `--headless --convert-to`，使用独立 profile、超时和受限工作目录；必须以真实 DOC 语料核对段落、表格、编号与正文是否丢失。本轮未安装 LibreOffice，也未做转换等价性验收，因此这是迁移方案，不是现成可用能力。若文档还用于最终签署版式，应单独验证渲染保真。

不建议把 Word/WPS COM 当成长期无人值守服务器基础。微软明确提示 Office 的非交互服务器自动化可能不稳定或死锁；该官方声明针对 Microsoft Office，不能据此声称 WPS 具有相同厂商支持政策，但当前 WPS COM 已实际出现清理阶段 RPC 异常，需单独评估。

## 生产部署结构

Windows 采集节点（仅在需要桌面微盘同步时） → 受控文件传输/导入接口 → Linux 原件暂存与转换 Worker → RAG 导入 Worker → PostgreSQL/pgvector → FastAPI。

文件、数据库、日志、检查点及备份必须放在持久卷，不能仅存在容器可写层。模型权重单独缓存；密钥由受控凭据存储管理。为原件、索引、WAL、模型下载和备份留空间，备份应有独立故障域，不应把同一数据盘上的副本当作完整灾备。

API 可先配置 1–2 进程，模型服务独立成单实例，避免每个 API worker 各自加载一套模型。FastAPI 文档明确多进程通常不共享模型内存。若 API 与 PG 同机，不照抄专用数据库机器的内存比例；保留 OS、模型与转换 Worker 的余量。

SQLite 当前向量检索会读取候选向量并在 Python 构建 NumPy 矩阵；数据扩大时搜索内存和 CPU 增长。100,000 条 1024 维 float32 向量原始大小约 390.6 MiB，这只是原始向量，不包括行对象、文本、矩阵副本、数据库索引、模型和并发。不能只靠加内存弥补检索路径，需验收 PG/pgvector 索引及执行计划。

## 当前上线阻断：不是硬件问题

微盘 CLI 没有已确认可用的“指定目录直接子项列表”接口；当前发现方式为搜索结果合并，未证明全量完备。实际批量下载触发 640459（日下载次数限制）及 850005。增加 CPU/RAM、改 Linux 或 Windows 都不能解除服务端权限/配额和授权有效期限制。当前自动同步任务已停用，需先获得可完整列举目录且允许长期同步的接入方式。

## 上线前验证

1. 确认用户数之外的实际峰值：同时检索数、同时审核数、合同页数、扫描件比例、日新增量。
2. 按实景分别测查询、全文审核、批量入库；初始 1–2 个并发审核任务，逐级增加。记录 P95/P99、队列等待、RSS/提交峰值、CPU、磁盘延迟和 DB 连接数。
3. 验证真实语义模型、精排及生成模型的配置，不能以 local_hash/none 替代容量测量。
4. 验证重启恢复、授权失效、配额熔断、备份恢复、目录改名/移动及文件更新。
5. 持续负载下保留约 25%–30% 内存及 CPU 余量；磁盘保留约 30% 空闲是本项目规划目标，非厂商保证。

## 官方参考

- 企微 CLI 平台支持：https://github.com/WecomTeam/wecom-cli
- Office 无人值守自动化限制：https://learn.microsoft.com/en-us/office/client-developer/integration/considerations-unattended-automation-office-microsoft-365-for-unattended-rpa
- LibreOffice headless/转换参数：https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html
- FastAPI 多进程与内存：https://fastapi.tiangolo.com/deployment/concepts/
- PostgreSQL 内存参数：https://www.postgresql.org/docs/current/runtime-config-resource.html
- pgvector 索引与内存：https://github.com/pgvector/pgvector
- BGE-M3 模型：https://huggingface.co/BAAI/bge-m3
- Sentence Transformers 推理后端：https://www.sbert.net/docs/sentence_transformer/usage/efficiency.html
