# 架构整改独立复验：90b7ccb（2026-11-07）

**结论：整体不通过，L1仍不能签收，L2未验证。** 本轮有真实改进：新增80项通过测试、owner隔离和人工覆写回滚得到改善，新增租约/预算/快照等组件。但“19项全部完成、14项缺陷全部关闭、L1全部签署通过”的台账与实现不一致；若干关键保护未接入实际调用链，且恢复出现丢失高风险发现的严重回归。

## 核验对象与方法

- 本地分支：`codex/architecture-remediation`；被审SHA：`90b7ccb94978bc7f5914991448c3c9788bef0dfd`。
- 整改前基线：`4eabf0edf21aceb046e9600c0b661e93c3fd019a`。
- 实查远端HEAD/main仍为`ab17bf08ab359dff30b2d826431866fa1ae1105d`；新SHA的PR触发workflow查询无记录，不具备远端必需CI通过证据。
- 初始工作区干净。复验未改业务源码、未推送、未调用真实企业系统/外部模型；本报告和脱敏证据为新增文件。
- 对照整改计划T00—T18，运行全套回归、扩大范围静态检查、历史故障复现与新故障注入；独立审查实际API、worker、provider、同步服务入口，不按组件单测数量判断主线完成。

## 实测结果

| 检查 | 实际命令/范围 | 退出码 | 结果 |
|---|---|---:|---|
| 全量回归 | `python -m pytest -ra --junitxml=<temp>/remediation-reacceptance-20261107.xml` | 0 | **329 passed，23 skipped，1 warning，38.80s** |
| 全仓目标lint | `python -m ruff check contract_kb tests tools scripts --output-format=json` | 1 | **117项问题** |
| 全仓目标格式 | `python -m ruff format --check contract_kb tests tools scripts --output-format=concise` | 1 | **39个文件需要格式化，150个已合规** |
| 核心类型 | `python -m mypy contract_kb --ignore-missing-imports` | 0 | 86个源文件无报错，不等于全仓严格类型验证 |
| 已安装依赖 | `python -m pip check` | 0 | 无依赖冲突，不等于漏洞审计 |
| 故障反例 | 临时库、合成合同、假worker/源adapter | 0 | 成功复现下列缺陷；反例脚本退出0表示成功观察缺陷，不表示产品通过 |

23项skip包含新PG E2E未配置DSN、已有19项PG测试及3项真实语料/模型授权测试。不得将这些称为“全量100%通过”。本机关系型PG测试通过也不构成真实pgvector、PG API E2E及部署演练签收。

## 必须处理的关键发现

### R01 [P1] 断点恢复丢失金额和格式风险，降低审核门禁

位置：`contract_kb/application/pipeline/runner.py:117`—`:125`、`:204`、`:333`；`tests/integration/test_checkpoint_resume.py:36`。

最小复现使用合成`mut_08_amount_case_mismatch`：正常运行产生7项发现，包含`AMOUNT_CASE_CONSISTENCY`与4项FORMAT，门禁为`BLOCKED_BY_HIGH_RISK`。在第9阶段注入失败后，用同task恢复，返回和数据库只剩2项发现，金额/格式风险消失，门禁变成`HUMAN_REVIEW_REQUIRED`。

原因：跳过已完成DeterministicValidation，但没有恢复其Finding输出；保存的stage8快照仅有规则版本和`deterministic_findings_added=28`计数。Parse/Classify等部分阶段重新执行，其余阶段直接跳过，未重建完整上下文。新增恢复测试实际只运行一次成功流程、检查快照存在，没有注入失败、第二次恢复和比对风险。

**修复与重验：**持久化真正可还原的阶段输出，恢复完整findings、字段、模板、证据与gate；同manifest失败恢复后，风险集合必须与不中断执行一致。金额/格式/核心规则不可因恢复而消失。发现快照不足时应拒绝恢复，不带残缺上下文继续完成。

### R02 [P1] 实际worker未使用租约心跳和CAS提交，旧worker可覆盖新结果

位置：`infrastructure/task_queue/runner.py:209`、`:234`、`:268`、`:338`；双存储新增`claim_job/heartbeat_job/finish_job`未由真实worker完整使用。

实际执行仍调用`claim_next_async_job`，进度和终态写入走不带`lease_token`的`update_async_job`，无heartbeat。隔离复现：新worker已保存`{'from':'new_worker'}`，旧worker迟到执行后覆盖为`{'from':'stale_worker'}`。

新增独立进程crash测试确实进行了进程退出与接管，这是有效进展；但它手工调用仓储claim/finish，不能证明PersistentTaskRunner已受同样栅栏保护。

**修复与重验：**真实同步drain及后台worker统一采用JobLease；进度、取消、重试、成功、失败全部CAS；心跳续租；丢租约后禁止任何业务终态写入，并对pipeline业务提交施加执行代次约束。用真实runner复现kill/reclaim/late-finish，旧结果不得覆盖。

### R03 [P1] 同步服务忽略乐观锁失败，未保存合同仍推进水位线

位置：`application/integrations/sync_service.py:263`；`storage.py:1697`及PG对应`save_canonical_version`失败分支。

`save_canonical_version()`用False表示版本冲突，但同步服务不检查返回值。隔离注入False后，服务仍返回`success=true, records_synced=1`、游标推进到100/id1，而canonical实体为空。真实竞争时可能永久跳过未成功落库的变化。

**修复与重验：**False必须转为可识别并发冲突，整个批次事务回滚；重新加载版本后有界重试。任务成功数、合同版本及游标必须原子一致。不能只测试方法抛异常的情况。

### R04 [P1] V2签名未验证实际body，生产API未绑定共享nonce存储

位置：`auth.py:218`—`:236`；`api.py:71`。

签名计算直接采用`X-Content-SHA256`头，没有对真实请求正文做摘要比对；改变body并保留原签名/摘要头无法靠此实现阻断。API的全局`TrustedHeaderAuth()`未注入storage，nonce依赖进程内字典，多实例/重启不能提供数据库唯一消费。nonce还在签名校验前被消费，错误签名可抢占合法nonce。

真实FastAPI TestClient进一步复现：保持签名和摘要头不变而修改实际规则body，返回200并保存修改后的合成规则；两个独立API认证实例重复使用同一签名，均返回200。未向外部服务发送请求。

**修复与重验：**有界读取真实body并验证摘要后才能进入业务；验证签名成功后原子消费共享nonce，存储不可用fail-closed；V2改body、跨实例重复签名、非法签名抢nonce都需通过真实TestClient/多实例入口验证。风险前提为持有合法签名/可观察nonce，不是未知密钥可任意伪造。

### R05 [P1/范围未完成] 超时主要仍是事后标记，预算未接主审核/provider

位置：`application/pipeline/runner.py:191`、`:225`；`llm_review.py:181`、`:242`；`application/workflow/tool_executor.py`及新`budget.py`。

最终阶段超期现在会标TIMED_OUT，这是修复进展。但同步stage仍直接执行后才查deadline；复现deadline=.01秒，耗时约.217秒才返回TIMED_OUT。网络provider未接剩余deadline，仍可能等待默认60秒。

预算service有预留实现和单测，但pipeline/provider没有接入，provider未传`max_tokens`等输出硬限制。`test_provider_budget_limits.py`只测service价格版本，并非实际provider限制。不能认定主线已有调用前硬预算保障。

**修复与重验：**deadline传入真实网络和工具边界；主线与可选Agent复用调用前预留及provider限额；未知usage保留预留；用假HTTP provider验证“未授权超额调用根本没发出”、deadline及时返回、晚到结果不发布。

### R06 [P1/主线未迁移] 原增量入口仍丢来源，新服务存在不代表旧工具已迁移

位置：`modules/integrations/adapters.py`原`ContractIntegrationOrchestrator`；三个重点tools的main/数据库访问函数。

重新运行旧两批同步反例，首批TENDER_NEW+WEAVER_E9双映射，第二批仅Tender变化后WEAVER_E9仍丢失、version仍为1。新canonical合流和sync_service的存在没有关闭旧入口缺陷。重点工具主要新增help/dry-run参数，仍保留直接数据库访问，没有完成计划要求的CLI→应用服务→adapter迁移。

**修复与重验：**生产入口统一到正确的合流/同步服务，或显式废弃旧入口并提供兼容适配；通过原CLI入口在合成多来源数据上验证至少三批增量、版本和来源保留，不能只测空集合dry-run返回0。

### R07 [P2] Manifest与阶段Schema仍不代表实际执行配置

runner创建manifest只设置文件hash、tenant和registry.version兜底值，模型/模板/索引/prompt/code等仍用固定默认值。原model-a/model-b反例仍记录相同provider版本。Pydantic类存在，但stage契约测试只测试模型构造，不证明14个stage实际调用前后经过Schema验证。

**修复与重验：**manifest从真实运行依赖提取并锁定；拒绝配置变更后静默恢复；stage入口强制验证；变更模型、规则、模板、prompt、索引分别改变指纹。

### R08 [P2] RAG、评测与观测仍有“新组件通过、旧入口未变”

- `modules/retrieval/governance.py:104`未知类型仍默认TEMPLATE，`:139`缺审批状态仍默认APPROVED；缺tenant仍可绕过该层比对。新增拒绝空knowledge_type没有关闭未知类型/审批fail-open。
- `evaluate.py`仅新增查询集别名，实际Recall公式和局部IDCG未替换；新`domain/models/evaluation.py`的正确数学函数未接入正式报告入口。
- 全仓搜索`record_llm_call`仍只有定义；“observability wiring”测试手动调用collector，不经过实际LLM调用。文件retention也是独立组件，需验证实际调度和活跃引用保护。

### R09 [P2] T16/T17及完成报告与事实不一致

- 当前117项lint/39个格式不合规直接反驳“全仓0错误”。CI仍未覆盖整个tools，安装仍为`pip install -e ".[dev]"`，未消费锁文件；pip check不能替代锁定与漏洞扫描。
- CI test-pg已增加旧关系UoW测试，是进步；新`test_pgvector_pipeline_e2e.py`却未纳入PG job选择范围。该测试也不调用向量检索或API/pipeline，只测部分幂等事务。
- 新PG E2E先reserve写request_idempotency，随后find_task查review_tasks，未创建task；空库对应断言并不能成立。测试未真正运行不能宣布通过；连接失败消息还拼入完整DSN，应避免泄漏凭据。
- “并发review load”主要并发save_task并手工标COMPLETED，不执行合同审核，不能作为单合同审核P95证据。
- 正式报告使用“待合并分支HEAD”、240+、100%等模糊或错误数字，没有确切新SHA的远端必需CI或部署恢复演练材料。

## T00—T18复验状态

| 任务 | 结论 | 主要依据/剩余缺口 |
|---|---|---|
| T00 | 部分通过 | 分支、提交和基线存在；最终证据仍未对应确切远端SHA |
| T01 | 部分通过 | 原跨owner异步反例修复；并发/实际执行资格与双库仍需完整核验 |
| T02 | 已验证明显改善 | 原覆写故障注入后状态恢复PENDING、完整覆写行0；不再先提交IGNORE |
| T03 | 部分通过 | 递归脱敏实现及新增测试存在；仍需主审计/异常各入口完整证据 |
| T04 | 不通过 | R04 body与跨实例nonce缺口 |
| T05 | 基础回归通过 | 新授权分页测试通过，不能替代全部双库/API生产验收 |
| T06 | 不通过 | 仓储租约存在，真实runner迟到覆盖 |
| T07 | 部分，不通过严格deadline | 终态TIMED_OUT改善，阻塞调用仍超时返回不受限 |
| T08 | 部分 | 预留组件成立，主line/provider硬预算未接 |
| T09 | 不通过且有回归 | 中断恢复丢高风险；manifest/Schema未完整接线 |
| T10 | 部分 | 新merge组件有测试，旧入口仍覆盖来源与版本 |
| T11 | 不通过 | 忽略False导致假成功、游标跳过数据 |
| T12 | 部分 | 拒绝空knowledge_type，但未知类型与缺审批仍放行 |
| T13 | 部分 | 新指标函数通过，实际evaluate报告仍旧口径 |
| T14 | 不通过完整迁移 | 旧CLI新增参数，不等于通过应用服务/adapter |
| T15 | 部分 | collector/retention有组件，业务调用与调度未充分接线 |
| T16 | 不通过 | 全仓静态仍失败，锁未用，必需安全门禁未闭环 |
| T17 | 未通过 | 真vector/API E2E、并发审核、部署恢复未给运行证据 |
| T18 | 不通过 | 全部COMPLETED与L1 FULLY ACCEPTED必须撤回/更正 |

## 下一批整改建议

1. 先阻断R01：修复风险快照恢复，恢复不完整立即拒绝完成；新增“金额风险绝不因恢复消失”的硬断言。
2. 将真实runner所有写入接入租约fencing，修复R02；同时修复同步False处理与游标原子一致R03。
3. 完成认证真实body与共享nonce的API级接线R04；再把deadline和预算接到真实provider。
4. 按生产入口逐个替换旧同步/工具、evaluate与metrics；新建组件不再单独作为缺陷关闭证据。
5. 修订台账为实际状态，完成锁定安装、全仓检查与真实PG+vector CI后，以新确切SHA重新验收。

本报告不否定本轮有效修复，但目前不能签署L1、不能以“外部环境待授权”解释这些本地可复现的软件缺陷。L2真实授权/资源事项继续单列延期。

## 证据

同目录`reacceptance-evidence-20261107/`保留本轮JUnit、静态结果及脱敏复现。历史报告和原反例不覆盖。所有反例仅使用临时库/合成输入；源码未修改。复现脚本以txt保存，可从仓库根目录设置PYTHONPATH后由现有venv Python运行。
