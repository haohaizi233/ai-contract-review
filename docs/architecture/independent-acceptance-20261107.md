# 架构与 ADR 独立全量验收（2026-11-07）

**结论：整体验收不通过，允许作为阶段性重构成果保留，不能签收为企业生产就绪。** 四份 ADR 的选型方向合理，也有真实代码实现；但权限、事务一致性、崩溃恢复、超时与预算等关键承诺未落实。原 `architecture-acceptance.md` 的多项“100%”“彻底阻断”“全仓零问题”陈述必须修订。

本次是验收，不是执行附件中嵌入的重构指令。未修改业务源码、未推送/合并、未轮换凭据、未清洗 Git 历史、未调用真实外部模型或企业生产系统。新增本报告与脱敏复现材料。

## 1. 验收对象与边界

| 项目 | 本次核验事实 |
|---|---|
| 任务标准 | 用户附件《AI 合同审查系统：企业级架构全面审计与渐进式重构任务》，覆盖第 1—17 节及 Phase A—F |
| 本地分支 | `refactor/enterprise-architecture` |
| 本地被审 SHA | `4eabf0edf21aceb046e9600c0b661e93c3fd019a` |
| 远端 HEAD/main | `ab17bf08ab359dff30b2d826431866fa1ae1105d`，`git ls-remote` 实查 |
| 相对基线 | 本地有 A—F 六个独立提交，132 文件变动、约 19,212 行增加/4,950 行删除；远端尚未发布这些提交 |
| 初始工作区 | `git status --short` 为空；未覆盖已有修改 |
| 执行环境 | Windows、现有 `.venv` Python 3.13；真实本地 PostgreSQL 18.6，关系型测试使用随机隔离 schema |
| 数据边界 | 临时 SQLite、生成的合成 DOCX；不读取企业合同正文作评估；未启用真实语料/真实模型授权 |

验收方法包括任务书逐项对照、基线/当前 AST 检查、主入口调用链检查、全套 pytest、CI 同范围及扩大范围静态检查、真实 PG 关系测试、远端 Actions 作业与失败日志核验、故障注入和最小反例。静态结构检查不能替代动态行为证明；未进行独立渗透测试和真实生产负载测试。

## 2. 四份 ADR 的签收判断

| ADR | 设计判断 | 实现验收 | 已有成果 | 阻止完整签收的缺口 |
|---|---|---|---|---|
| ADR-001 模块化单体 | 方向合理 | 部分通过 | 25→74 个 Python 模块，真实 domain/application/modules 分层，旧门面保留，当前架构契约测试通过 | 大量 `Any/getattr` 绕开端口；阶段输入输出是字段名 tuple，并非独立强类型 Schema；旧 tools 未迁入 adapters；分层守护覆盖不完整 |
| ADR-002 Repository/UoW | 方向合理 | 部分通过 | API 使用 `create_storage()`、失败不静默回退；SQLite/PG SAVEPOINT 已实现，3 项真实关系测试通过 | 人工覆写没有统一事务；签名名称相同不代表双库行为等价；pgvector 行为未在本机验证；来源映射/版本更新有破坏性缺陷 |
| ADR-003 受控状态机 | 确定性优先合理；不要求强行上线 Agent | 组件部分通过，主线未验收 | 状态机、工具 Schema/权限检查、基本审计与单测真实存在 | 主 API/ReviewEngine 未经过该状态机；工具超时等待不受限；单次 Token 超限仍成功，不能宣称主线已有硬预算保护 |
| ADR-004 关系库轻量队列 | 规模下选择合理 | 部分通过 | 持久化入队、正常异常重试、退避、死信、租户幂等与基本认领已实现 | 无 lease/heartbeat/失联重认领，进程崩溃任务永久 RUNNING；幂等重用绕过 owner 授权；缺少恢复语义证明 |

“Accepted”表示架构决策被采纳，不表示所有承诺已开发和验证完毕。建议在 ADR 中分别列出“已实现”“已验证”“延期/未验证”和重验条件。

## 3. 独立实测结果

| 检查 | 命令/范围 | 退出码 | 本次结果 |
|---|---|---:|---|
| 全套测试 | `.venv\Scripts\python.exe -m pytest -ra --junitxml=<temp>/ai-contract-architecture-acceptance-20261107.xml` | 0 | **249 passed，22 skipped，1 warning，10.19s** |
| PG 关系集成 | `python -m pytest tests/integration/test_uow_and_pg18_relational.py -ra` | 0 | **3 passed，0.93s**；已包含在上述249中，不可重复累计 |
| CI 范围 lint | `python -m ruff check contract_kb tests tools/wecom_wedrive_probe --output-format=json` | 0 | 0 findings |
| 全仓 lint | `python -m ruff check . --output-format=json` | 1 | **120 findings，31 files**，主要为 tools/scripts；本次无自动修复 |
| CI 范围格式 | `python -m ruff format --check contract_kb tests tools/wecom_wedrive_probe --output-format=concise` | 0 | 108 files already formatted |
| 全仓格式 | `python -m ruff format --check . --output-format=concise` | 1 | **41 files would be reformatted，148 files already formatted** |
| 类型检查 | `python -m mypy contract_kb --ignore-missing-imports` | 0 | 74 source files 无报错；不是全仓严格类型证明 |
| 依赖一致性 | `python -m pip check` | 0 | 无已安装依赖冲突；不是漏洞扫描 |
| 差异检查 | `git diff --check` | 0 | 被审工作区无空白问题 |
| 故障反例 | 随附 6 个 `*-repro.txt` | 全部0 | 成功执行并证实缺陷；退出0不表示产品合格 |
| 当前凭据窄模式扫描 | tracked 文本文件，私钥/企微ID/部分mysql CLI模式 | 0 | 零命中；不等于一般API key/DSN/cookie/企业实体和完整历史扫描通过 |

22 项跳过包括：2 项未授权真实语料、1 项未授权真实模型 E2E、19 项未配置显式 PG DSN 的测试。`tests/test_pg_storage.py` 的模块级 skip 还会跳过其中静态测试。本次额外核验本机仅有 `pg_trgm` 可用、未安装，`vector` 不在可用扩展列表，因此不能把关系型 PG 测试当作 pgvector 验收。

原有232个测试函数未发现被删除，原有测试内 assert 数量未发现减少。此检查只证明函数和断言数量保留，不能证明断言语义完全没有放宽，也不证明关键负向路径覆盖充分。

独立合成基准，使用临时库、本地 hash256，**无真实语义模型**：

| 套件 | 本次结果 | 解释 |
|---|---|---|
| `benchmark_retrieval.GOLDEN_QUERIES` | **15** 条；Hit@1=.8667，Hit@3/5/8=1，MRR=.9333，NDCG@8=.9229 | 这组数值与原报告相同，但不是报告声称的30条查询 |
| `evaluate.RAG_GOLDEN` | **30** 条；Hit@1=.9，Hit@8=.9667，MRR=.9208，NDCG@8=.9129 | 是另一套查询与相关性判定，不可和上行混用 |
| `evaluate.eval_review` | 14个合成样本，micro P/R/F1=1，P50=57.72ms，P95=83.94ms，P99=99.55ms | 只证明该合成集下确定性流程；Token=0，不能作为真实模型成本和法律审核效果证明 |

没有重跑同环境基线延迟，故不签收“降低33.2%/37.8%”的改善幅度。本次只报告当前实测。AST 自定义决策计数确认核心函数有拆分改善，但不是标准 Radon 圈复杂度报告，也不采纳原文“其余核心均<15”“代码异味清零”。

## 4. 阻断性问题与修复要求

优先级：P1为生产签收前必须修复或明确收窄交付范围的缺口；P2为治理和验证缺口。本次未证实可定为P0的当前漏洞；不能因此宣称不存在P0。

### F01 [P1] 同步和异步幂等短路绕过资源所有权，且未绑定请求内容

- 位置：`contract_kb/api.py:498`、`:673`；`application/pipeline/runner.py:74`；`infrastructure/task_queue/runner.py:108`。
- 异步复现：Alice创建键 `reused-key`，Bob同租户用该键提交，拿到Alice完整job及私有合成 `custom_requirements`；而正常GET有 `_owned_job` 校验。
- 同步复现：Alice审核文件A，Bob用同幂等键提交不同文件B，返回同task、`created_by=alice`、文件A结果，B未审核。
- 影响：已授权用户可通过共享/可预测键读取同租户他人任务，并可能把旧报告误当成新合同审核结果。不是已证实的跨租户漏洞。
- 修复：在任何缓存/幂等返回前做owner授权；幂等作用域绑定tenant+owner，持久化文件哈希、mode、审核要求等请求指纹，冲突返回409；并发唯一约束与事务保证一致。
- 重验：跨owner同键拒绝；同owner不同正文/参数同键409；同owner同内容并发仅一次执行；不同租户隔离。

### F02 [P1] 人工覆写与完整覆写记录非原子

- 位置：`modules/rules/governance.py:248`、`:255`；`storage.py:1497`、`:1518`。
- 注入 `record_human_override` 失败后，接口抛错但 `human_state=IGNORE` 已提交，`human_overrides=0`。普通audit有部分记录，不应表述为“完全无审计”。
- 修复：服务级UoW包住状态、审计、覆写历史；记录失败整体回滚。对双库加入提交点故障测试。

### F03 [P1] 已认领任务崩溃后永久RUNNING

- 位置：`storage.py:1738`、`:1763`；`storage_pg.py:1885`、`:1914`。
- 复现：入队→claim→关闭连接模拟退出→重建runner，原job保持RUNNING，`process_next()`返回None。
- 修复：引入租约、心跳、超时重认领、尝试次数及fencing/幂等提交；kill worker后验证恢复和旧worker迟到提交。未实现前不得承诺at-least-once崩溃恢复。

### F04 [P1] 超时与取消只在边界检查，工具超时仍等待

- 位置：`application/workflow/tool_executor.py:279`；`application/pipeline/runner.py:101`、`:141`。
- 工具超时=.02秒，休眠=.3秒，实际约.303秒才返回TIMEOUT：executor上下文退出等待线程结束。
- 最后阶段延迟1.1秒、全流程timeout=1秒，实测约1.195秒后结果和落库仍COMPLETED。
- 修复：传递统一deadline、为实际网络IO设置剩余timeout、协作取消或可终止隔离执行；完成/提交前校验期限；明确已发生副作用不能被线程取消撤销。

### F05 [P1] Token与成本预算无法阻止单次超额

- 位置：`application/workflow/tool_executor.py:230`、`:243`、`:330`。
- `max_llm_tokens=1`，本次返回9999 tokens仍SUCCEEDED。调用前仅检查既有消耗，未预留或限制本次额度。
- 修复：调用前预估/预留、向provider传递可执行上限、并发预算原子记账、超额结果不得作为成功；账单不能仅靠调用后判断避免。

### F06 [P1] 增量合流丢失既有来源映射，版本号不递增

- 位置：`modules/integrations/adapters.py:263`；`modules/cases/reconciliation.py:158`；`storage.py:1560`。
- 两批临时库复现：首批同合同有TENDER_NEW+WEAVER_E9；后批仅Tender变化，原WEAVER_E9映射丢失，version仍1。
- 修复：以稳定业务实体ID加载已有合同并合并来源；按来源键upsert、保留历史版本；版本在事务内可靠递增，重复同内容不增；变更与删除采用明确事件/水位线策略。

### F07 [P1] 审计和日志脱敏存在实证缺口

- 位置：`infrastructure/observability/redaction.py:43`、`:86`；`storage.py:662`、`:1513`；`storage_pg.py:509`、`:1605`。
- JSON字符串内api_key/password未遮盖，list内字典未递归；audit detail原样保存合成 `password=...`。
- 修复：仓储审计入口集中按结构/字段白名单脱敏，递归处理映射与列表；异常和JSON文本统一安全处理，不把任意用户note直接拼入audit。

### F08 [P1，部署前提明确] HMAC不提供真正防重放

- 位置：`auth.py:117`—`:139`；`config.py:56`。
- 签名未绑定method/path/body，无nonce消费。即使强制时间戳，同一签名GET后再次POST仍被认证；默认还可不要求时间戳。
- 风险前提：合法签名被截获/泄漏或受信边界失守；不是可以未知密钥伪造身份。
- 修复：强制短效时间戳、绑定请求digest、nonce单次消费，或标准OIDC/JWT加明确网关隔离；认证票据可复用时应准确声明其语义，不宣称防重放。

### F09 [P1/P2] “断点恢复”和“同版本复现”证据不成立

- 位置：`application/pipeline/runner.py:87`、`:141`、`:210`；`application/pipeline/stages.py:86`、`:999`。
- 第9阶段首次失败后恢复，前8阶段全部执行2次；恢复只加载attempt，不恢复阶段输出。RUNNING也未在stage执行前真正持久化。
- 同provider的model-a/model-b记录的model_version相同；模型型号、参数、prompt/schema/模板/索引版本未完整锁定。
- 修复：阶段快照+版本manifest+已提交阶段跳过；或准确改称全流程重放并证明副作用幂等。保存实际模型/部署/参数/prompt等版本，不能以报告内容hash替代可复现配置。

### F10 [P2] 所有者过滤晚于分页，total错误

- 位置：`api.py:556`、`:719`。
- Alice第一页limit1得到items=[]、total0，第二页才出现自己的任务；前端可能认定完全没有任务。
- 修复：授权条件下推SQL，count与items使用同一过滤，再分页；测试交错owner和多页。

### F11 [P2] 知识治理未知来源升格，缺审批字段默认放行

- 位置：`modules/retrieval/governance.py:102`、`:134`、`:138`。
- 未知knowledge_type默认TEMPLATE，随后可标记为支持强制发现；APPROVED_RULE缺审批元数据默认APPROVED。
- PENDING_REVIEW模板通过本身不必然违反任务书（任务明确要求未审批规则不得成为标准、过期模板不得静默使用），但与原报告“仅ACTIVE/APPROVED准入”不一致。
- 修复：未知类型/审批状态fail-closed，规范元数据来源，分别定义模板/规则/历史案例/法律参考的准入与信任规则。

### F12 [P2；上生产前需明确范围] Agent与集成adapter未接到已有业务入口

- `ControlledReviewAgentWorkflow`的构造调用仅出现在单元测试；`api.py:516`→ReviewEngine→ReviewPipeline，不执行Agent工具预算/权限层。
- 新ContractIntegrationOrchestrator/adapters主要为单元测试调用；重点脚本 `tools/tender_case_export.py`、`live_db_merge.py`、`cross_verify_and_merge.py`仍直接访问外部数据库/子进程。
- 组件不是空目录，但“组件存在”不等于“主链路受保护/旧工具迁移完成”。Agent可明确延期；工具迁移须保留兼容CLI、dry-run对照并逐个接线。
- `TenderMysqlAdapter`仅以id游标发现新增，不能捕获已有行更新；水位线仅按source、不含tenant；JSON水位线与业务DB提交不原子。需增加更新/删除捕获、租户水位线和重放对账。

### F13 [P2] CI、依赖和安全门禁尚未闭环

- `.github/workflows/ci.yml:29`等仍 `pip install -e ".[dev]"`，未使用requirements-lock；没有可见依赖漏洞审计步骤。
- `test-pg`仅跑旧两个PG文件，未包含新增UoW关系测试；synthetic job虽会发现新测试，在无PG环境会skip。
- 企业实体扫描在缺少SENSITIVE_MARKERS且未配置强制开关时成功退出：应列为“未配置/未验证”，不能算完成原任务中的精确实体安全验收。
- 当前安全测试不覆盖完整Git历史；历史风险仍有元数据证据。凭据轮换/历史清洗需要管理员授权，验收不能自动代做，也不能签收已清除。
- 修复：CI使用可复现锁定安装；PG必需job纳入新测试并在配置测试DSN后禁止不可达skip掩盖；建立独立凭据/历史与依赖审计；新SHA运行全套必需CI。

### F14 [P2] 可观测性部分是未接线定义，评测指标需改名/修正

- `metrics.py:72` 的record_llm_call没有业务调用点；LLM调用/超时/解析错误计数保持0。DB计时主要接readiness，不是全仓DB观测。缺少运行时P50/P95/P99、真实token成本等完整业务指标。
- `evaluate.py:194`把Recall@K算成“任一相关即召回”的Hit@K，缺全部相关集合分母；NDCG理想序列基于已召回结果，不是独立完整相关性标注。AnswerGroundedness度量检索文本一致性，不能当作生成法律结论的真实依据度。
- 修复：接入实际provider/检索/DB调用点；明确指标口径，建立独立qrels和答案评审，分开报告合成、真实脱敏和真实模型E2E。

## 5. 任务书全范围覆盖矩阵

| 任务域/阶段 | 状态 | 验收判断 |
|---|---|---|
| 仓库基线、分支、渐进提交 | 通过基础项 | SHA与六阶段提交可核对；未发布远端，不违规推main |
| Phase A 架构图/边界/依赖/风险 | 部分 | 文档齐全，缺原文所引审计JSON与可执行复杂度证据，若干数字错位 |
| Phase B P0治理与数据库接线 | 部分 | 工厂、当前窄模式脱敏、局部CI修复存在；完整历史/轮换/实体扫描未闭环 |
| 业务领域10个上下文 | 设计基本覆盖 | 文档有范围划分；并非每个均已成为独立成熟模块，不能仅按目录数验收 |
| Phase C 14阶段/领域/规则/仓储 | 部分 | 真拆分、旧测试通过；强类型阶段契约、恢复、版本、覆写事务有缺口 |
| 规则防回归/人工结论区分 | 基础通过，负向不通过 | 原测试保留、来源模型存在；完整override事务须修复 |
| 持久化端口/SQLite/PG/UoW | 部分 | PG关系实测通过；生产pgvector/API到PG完整E2E/行为等价未全部验证 |
| 数据迁移、版本、来源、水位线 | 不通过完整范围 | 有候选模型与dry-run，已有映射丢失实证；无迁移前后企业规模对账演练 |
| Phase D RAG/Embedding/Reranker | 部分 | 词汇混合检索与引用回归可用；真实语义质量/模型对照不具备签收证据 |
| Agent权限/预算/超时 | 组件部分，主线未完成 | 可不引入自治Agent；现有硬约束声明必须撤回或补齐 |
| 外部LLM授权与提示词隔离 | 部分 | LLM from-env对非loopback严格要求授权=1；nonce拼装存在但不证明模型攻击绝对阻断 |
| 云Embedding | 占位/未验收 | config默认ALLOW_EXTERNAL_LLM=true与工厂gate不一致；但CloudEmbedding.embed当前直接抛未配置，不能据此声称已真实外发；真实SDK实现未完成 |
| Phase E 长任务/取消/重试/恢复 | 不通过完整范围 | 持久化与正常重试存在；crash恢复、deadline、权限幂等存在P1 |
| 文件安全/上传清理 | 基础安全测试通过，生产策略未验收 | ZIP/类型/限额/正常清理有代码测试；加密、备份、保留期、失联作业文件回收无完整部署证据 |
| 重点tools迁移/外部系统边界 | 部分 | 新adapter可Mock；旧主链路未迁移，真实连接/限流/分页/幂等/更新捕获未完整验收 |
| API版本/错误/trace/分页/鉴权 | 部分 | v1/兼容/错误信封存在；owner幂等、分页、重放及脱敏缺陷必须处理 |
| 可观测性 | 部分 | HTTP与部分review指标可用；LLM等存在摆设调用点，完整延迟/成本/积压/同步延迟未交付 |
| 测试金字塔与失败路径 | 部分 | 249通过，分层测试目录真实；本次故障反例暴露覆盖盲区，无变异测试报告 |
| Phase F 远端必需CI | 未通过 | 新SHA无可用远端CI证据；远端旧SHA最新run仍失败，见下节 |
| 真实语料/真实模型/真实pgvector | 未验证 | 外部前提与授权未满足；应延期专项验收，不能换成合成证据 |
| 部署/回滚演练 | 未验证 | 有文字方案；无同环境真实部署/故障恢复/数据回滚演练记录，git revert不等于数据回滚 |
| 强制11项文档交付 | 文件存在通过，内容一致性部分 | 10份md+architecture-decisions目录均在；引用路径/数字/完成度需修订 |

## 6. 远端CI与原验收报告可信度

本次实际读取最新远端Actions，而非沿用历史说法：

- [run 42331042999](https://github.com/haohaizi233/ai-contract-review/actions/runs/42331042999)，SHA=ab17bf0，结论failure。
- lint-and-type：日志明确79 errors。
- test-synthetic：PIL缺失导致收集失败；报告上传又遇Artifact quota。
- security-gate：SENSITIVE_MARKERS未配置导致阻断。
- test-pg：作业失败步骤为报告上传额度，不应把整个job红灯直接解释成PG行为测试失败。
- 新SHA=4eabf0e只存在本地；不能把旧SHA失败算成新SHA已修复，也不能把本地测试绿灯当成新SHA必需CI通过。

原报告需要修订的具体事实：

1. 全仓lint零错误/全格式合规 → 实际只在CI覆盖范围成立，全仓120/41。
2. 30条查询得到MRR=.9333 → 对应的是15条benchmark；30条evaluate是另一套指标。
3. `ReviewEngine.review` 原1272行God Method → 基线AST函数实际335行；模块长不等于函数长。
4. 本地真实PG关系通过 → 不能等同pgvector、生产级可靠性或部署通过。
5. `baseline_audit_results.json`、`post_refactor_audit_results.json` → 未在tracked文件或工作区文件名搜索中找到；没有可交付来源可核验改善百分比。
6. 单元测试层写“16项”，其逐文件列出的9+7+8也不一致；描述中的部分测试名称与实际名称不匹配。
7. `dependency-rules.md`引用的`tests/contract/test_architecture_dependencies.py`不存在，实际为`test_architecture_fitness.py`；migration-plan的001文件名与实际`001_init_pgvector.sql`不同；ADR004关联003文件名亦不一致。
8. nonce“彻底/绝对阻断提示词注入”、扫描“零泄漏”、组件“100%稳定”等绝对声明没有对应证明，应改成已执行测试的有限结论。

这不代表改造没有价值：真实模块拆分、工厂接线、零已检测循环/反向tools依赖、规则注册、基础事务与249项回归均值得保留。问题在于把阶段成果扩写为整体生产验收结论。

## 7. 建议整改顺序与重验门槛

1. **先堵权限和事务口**：F01/F02/F07/F08；对同步与异步所有提交/重试/查询路径做一致授权测试。
2. **补可靠性**：F03/F04/F05/F09，明确deadline、crash恢复、幂等副作用和版本manifest；加入进程杀死、重复消费、并发和最终阶段超时测试。
3. **保住数据来源**：F06及F12的更新捕获、租户水位线、合流和版本；通过三批以上增量、同号多来源、乱序、删除、回滚对账。
4. **接实主线并收窄声明**：确定Agent是否本轮交付；逐一迁移重点CLI；修正评测、分页与指标调用点。
5. **独立环境完成Phase F**：锁定依赖；真实PG+pgvector+API E2E必需CI全部通过；故障反例从“证实缺陷”转为“证明已修复”；复测原249项；当前SHA发布到review分支/PR后核对远端结果。
6. **分离管理员授权项**：真实语料外发、真实模型、密钥轮换、历史重写、生产迁移及回滚演练单独审批。无法执行的项目明确列延期验收，不能计作通过。

最终签收应同时满足：没有未处置P1、无权限泄露/假成功、实际数据与审计原子一致、恢复与重复执行有证据、真实PG向量行为通过、CI对应确切SHA、文档完成度与实现一致。不得仅以pytest绿灯解除这些门槛。

## 8. 随附证据与复现

见同目录 `acceptance-evidence-20261107/`：`checks.json`、`pytest.xml`、`benchmark.json`、`reproductions.json`及6个复现脚本。脚本以`.txt`保存避免混入产品代码/常规测试发现，但内容为Python，可直接由Python执行。所有样例均合成，路径不包含生产DSN或真实密钥。

从仓库根目录执行：

```powershell
$env:PYTHONPATH=(Get-Location).Path
.\.venv\Scripts\python.exe docs\architecture\acceptance-evidence-20261107\core-repro.txt
.\.venv\Scripts\python.exe docs\architecture\acceptance-evidence-20261107\ai-tasks-repro.txt
.\.venv\Scripts\python.exe docs\architecture\acceptance-evidence-20261107\security-repro.txt
.\.venv\Scripts\python.exe docs\architecture\acceptance-evidence-20261107\job-owner-repro.txt
.\.venv\Scripts\python.exe docs\architecture\acceptance-evidence-20261107\sync-owner-repro.txt
.\.venv\Scripts\python.exe docs\architecture\acceptance-evidence-20261107\sync-loss-repro.txt
```

这些脚本用于重现当前缺陷，不是修复后应当原样通过的安全断言测试；部分脚本会在本目录更新对应脱敏JSON。`pytest.xml`保留本机路径用于本地核查，未上传外部Artifact。
