# 新库三件套关联与 Markdown 导出方案

研究日期：2026-11-05（Asia/Singapore）。本次为只读研究，核对了新库实时数据、已导出的表结构、旧库表单关联及旧库实际 Markdown。本文是关联研究和实施方案，尚未执行逐案 Markdown 全量生成。

## 1. 结论

可以沿用旧库“每案一份 Markdown”的组织方式，包含案件信息、附件、审批流程及意见图片，但必须区分新系统原生合同与旧 OA 迁移合同。

- 新库内优先使用明确的业务主键和 `process_instance_id`，不以合同名称猜关联。
- 历史迁移合同通过 `oa_source + oa_id` 回查旧表单，再取得旧 `requestid`。`oa_id` 不是旧流程 requestid。
- 附件既来自合同主表，也来自归档、终止、延期等子表；审批正文中的图片另行解析。
- 每个关联保存来源表、来源字段、原始 ID、解析结果及失败原因；不能将“没有关联上”表述为“没有数据”。
- 现有全量同步负责原始表与文件采集，现有合并脚本只汇总意见数量；它们还没有完成三件套组装。

## 2. 旧库导出的目标结构

已检查 `scripts/e9_opinion_export.py`、`scripts/e9_form_attach_export.py` 及实际产物 `data/e9-opinion-export/BQ/BQ2025BM00285.md`。该样本包含审批表单、明细表、附件、按时间排序的法务审批意见和图片。旧导出可能使用节点筛选；新导出默认应保留完整历史，另提供法务节点视图。

建议新库结构：

```text
data/tender-case-export/
  cases/<编号前缀>/<合同编号>__apply_<申请ID>.md
  images/<流程实例ID>/<意见ID>_<序号>.<后缀>
  _index.csv
  _relations.jsonl
  _errors.csv
```

附件可直接使用指向现有 `data/tender_files/` 的相对链接，避免再复制几十 GB 文件。若交付给其他电脑，需将所引用文件与图片一并打包，并重写相对路径。文件名须清理非法字符；所有 ID 按字符串处理。

建议 Markdown 章节：

```markdown
# 合同编号

## 案件信息
合同名称、申请人、部门、双方主体、金额、状态、日期及来源主键

## 业务明细
费用、事项、履约、客户等实际存在的子表记录

## 附件
按需盖章、不需盖章、归档、终止、变更等角色分组
每项列来源字段、文件ID、本地链接、下载或校验状态

## 审批流程与意见
按流程实例分组，再按时间展示节点、审批人、操作、意见和图片
包括无意见的任务节点、未完成节点及转办等事件

## 旧 OA 历史
迁移记录的旧申请单、旧表单及历史意见来源链接

## 数据缺口
明确列出失效附件、未解析图片、流程未匹配或有歧义的关联
```

## 3. 本次实测结果

以下为本次研究期间读取的动态快照，各查询不是同一数据库事务，文件下载仍在继续。

| 核对项 | 实测结果 | 含义 |
|---|---:|---|
| 未删除合同申请 | 46,408 | 以申请 ID 为基础组织导出 |
| 申请填写了流程实例 ID | 46,353 | 填写不代表本库仍保留对应流程 |
| 申请流程 ID 能匹配 `act_hi_procinst` | 2,049 | 可直接建立新库审批链 |
| 按 BUSINESS_KEY 匹配到的申请 | 2,050 | 比直接流程关联多 1 条，需核查后补入 |
| 直接关联的审批意见/事件记录 | 14,154 | 不是全部都是人工审批正文 |
| 按 BUSINESS_KEY 关联的意见/事件记录 | 14,166 | 多出的 12 条须通过流程定义核验 |
| 所有业务域的审批 comment 记录 | 49,011 | 包括非合同流程和事件 |
| `FULL_MSG_` 非空记录 | 47,422 | 应保留完整正文，而非只取 MESSAGE_ |
| 完整正文含图片标记的 comment | 2,831 | 全 BPM 范围；直接关联合同的为 1,839 条 |
| 正文 `<img src>` 出现次数 | 4,821 | 包括重复图片 |
| 不同图片地址 | 4,174 | 全 BPM 范围去重，不能当作合同图片总数 |
| 图片地址精确命中下载清单 | 4,154 | 可复用现有文件下载成果 |
| 上述图片已存在本地 | 3,603 | 研究时点快照，仅检查存在，未逐个做完整性校验 |
| 图片地址未精确命中清单 | 20 | 含旧 OA 下载链接、Windows 本地路径或 blob 引用，需专项恢复 |
| `act_hi_attachment` | 0 条 | 不能依赖此表获取当前意见图片 |
| `contract_apply_process` 未删除记录 | 0 条 | 字段存在，但当前不是可用主关联来源 |
| `contract_attachment` | 484 条，均为 source_type=3 | 当前是模板类，不能当作每案业务附件 |

`oa_source=1` 的申请 41,250 条，`oa_source=2` 的申请 3,054 条；其余 2,104 条该字段为空。其中 1,923 条填写 `oa_request_id`，也应保留核对，不能仅按是否有 OA 字段把它们排除。

直接流程匹配的 2,049 条申请，其 `BUSINESS_KEY_` 与申请 ID 本次检查均一致。BUSINESS_KEY 单独多出的例子：申请 `2094961761093029890`、流程 `HCCX-HT001-2026090300019`、定义 `contract_application:15:79458c5d-9d0e-11f1-9578-000c29ee4f47`。需核对该申请当前流程字段、重提记录及历史版本，不能自动覆盖直接关联。

## 4. 案件信息如何关联

### 4.1 申请与档案

主表为 `tender_contract.contract_apply`，档案表为 `contract_archive`。先检查两条显式关联是否一致：

- `contract_apply.contract_archive_id → contract_archive.id`
- `contract_archive.contract_apply_id → contract_apply.id`

两条关联冲突时保留双方原值并报告，不用合同编号强行合并。合同编号用于展示和辅助核验，同号多次申请要保留多个申请 ID；空编号用申请 ID 命名，不能丢弃。

申请、档案均有 `process_instance_id`。应取两侧流程 ID 的并集并去重，不只读取申请当前流程。延期、补充协议、虚拟合同等关联流程另外分组，不能混入初始合同审批。

### 4.2 明细与生命周期

经表结构核对，`contract_apply_cost`、`contract_apply_matter`、`contract_apply_ecology`、`contract_apply_performance`、`contract_customer` 等包含 `contract_apply_id`。费用/事项的 item 子表应继续按其实际父表外键串联，不能假定所有 item 都直接挂申请。

其他重要关系：

| 表 | 关系与用途 |
|---|---|
| `contract_apply_archive` | `contract_apply_id`，归档/作废原因及归档附件 |
| `contract_apply_termination` | `contract_apply_id`，终止日期、协议和佐证 |
| `contract_apply_change` | `before_contract_id`、`after_contract_id`，变更前后合同 |
| `contract_archive_deferred` | `contract_archive_id`、`process_instance_id`，延期及流程 |
| `contract_apply_change_legal` | `contract_apply_id`，法务变更相关信息 |
| `contract_virtual` | `contract_apply_id`、`process_instance_id`，虚拟合同关系 |

以上是字段级确认，不代表所有生命周期样本已逐条核验。正式导出前应分别抽样检查，并排除日期快照/备份表。业务表的 `deleted`、`tenant_id` 必须保留和核对。

## 5. 附件如何关联

### 5.1 附件来源不能只取四个主表字段

| 来源表 | 已确认的文件字段 |
|---|---|
| `contract_apply`、`contract_archive` | `require_fill_file_id`、`require_file_ids`、`no_require_file_ids`、`change_agreement_file_ids` |
| `contract_apply_archive` | `seal_documents`、`mail_scanned_documents`、`evidence_documents` |
| `contract_apply_termination` | `termination_file_ids`、`corroboration_file_ids` |
| `contract_apply_process` | `contract_files`；当前无未删除数据 |

档案终止、延期等表也应纳入字段盘点，不能仅凭 `_file_ids` 后缀自动发现所有附件。相关候选如 `termination_file_ids`、`corroboration_file_ids`、`contract_extended_files_ids`，正式纳入前再按实际表结构确认。

逗号分隔字段在程序里拆分、去空白，保留原始字段值。文件实体关联：

```text
业务行的文件ID → tender_infra.infra_file.id
              → URL + manifest.csv.dest
              → data/tender_files/<id>_<清理后的文件名>
```

对每个引用分别记录“数据库存在、已删除、已下载、校验通过、下载失败、无法解析”。不要删除数据库中已删除文件的引用证据；可在引用查询时读取元数据并标记 deleted，再决定是否补下载。

### 5.2 迁移文件必须区分 ID 命名空间

历史字段里可能残留旧 E9 `imagefileid`。不能因为同一个数字恰好在 `infra_file` 出现，就认定是同一个文件；也不能把“新库查不到”直接定性为旧文件。应综合迁移来源、旧表单文件字段、文件名/大小/内容及旧附件清单建立有证据的映射，并保存 `tender_infra` / `e9_imagefile` 命名空间。

现有两库合并报告中的“悬空”只是待核验候选，不等于已经证明旧库一定有实体。下载存在性与 MD5/格式校验须分开记录；当前下载器的“本地存在即跳过”不足以认定附件完整。

## 6. 审批流程、意见和图片如何关联

### 6.1 明确的流程关系

```text
contract_apply / contract_archive .process_instance_id
  → tender_bpm.act_hi_procinst.PROC_INST_ID_
    → act_hi_taskinst.PROC_INST_ID_（节点、处理人、开始/结束时间）
    → act_hi_comment.PROC_INST_ID_（意见、操作、作者、时间）
    → act_hi_comment.TASK_ID_ = act_hi_taskinst.ID_（对应节点）
    → act_hi_tsk_log（任务操作日志）
```

不能把 comment 与所有任务只按流程 ID 做笛卡尔式连接。应按意见的 TASK_ID_ 对齐节点，并保留 task 为空或已缺失的流程级意见。任务节点单独导出，避免无意见节点被内连接丢掉。排序使用时间加 ID，保留转办、加签、退回和重新提交的多次记录。

`USER_ID_` / `ASSIGNEE_` 可与 `tender_system.system_users.id` 核对昵称；无法解析时保留原 ID，不删除记录。数字比较前验证原字符串，避免隐式类型转换造成串号；人员租户须与业务实例核对。人员当前昵称不一定等于审批时姓名。

`BUSINESS_KEY_ = CAST(contract_apply.id AS CHAR)` 是补查入口，必须同时核对流程定义、业务域和租户，不能对整个 BPM 库无条件按数字 ID 合并。不同业务表可能有相同 ID。

### 6.2 意见正文与操作类型

- 优先解析 `FULL_MSG_`，保留原始字节；本次样本能按 UTF-8 识别正文及 HTML。解析失败时回退 MESSAGE_ 并报告，不默默丢内容。
- `MESSAGE_` 长度受限，不能用它替代完整富文本。
- 实测既有 `TYPE_=1/4/8/...` 的 AddComment，也有 `TYPE_=event`、`ACTION_=AddUserLink`。数字类型的业务含义需对照系统前端/代码或开发者确认，不能直接把所有记录都标为“同意”。
- 完整保留 HTML 表格、换行、颜色和图片；建议 Markdown 中混合保留经过清理的 HTML。导出时移除脚本、事件属性等活动内容，不对正文做会吞掉图片/表格的纯文本转换。

### 6.3 图片本地化

图片主要在 `FULL_MSG_` 的 HTML 中，当前不是来自 `act_hi_attachment`。

1. 从完整正文用 HTML 解析器提取 `<img src>`；另外检查 Markdown 图片、srcset、内联 data 图片。
2. 对标准 URL 先精确匹配 manifest 的 URL，再核对 infra_file；保留查询参数，避免随意截断签名或路径造成误匹配。
3. 已有本地文件且校验通过时直接引用；没有元数据记录但 URL 可访问的图片，以 URL/内容哈希建立独立图片缓存。
4. 旧 OA `FileDownload` 链接通过已授权的旧库下载链路解析；Windows 本地缓存路径、blob URL 不能作为服务器可下载地址，记录为待恢复。data 图片解码后检查 MIME 与文件内容。
5. 将每处图片引用改成相对路径，保留“意见ID、原地址、本地路径、下载状态”映射。
6. 不因图片失败而丢弃整条意见；正文保留原引用及明确失败说明。

典型已识别图片地址形态：`http://183.36.37.85:48080/prerelease/<文件名>.png`。本次 4,174 个不同地址中 4,154 个精确匹配下载清单，说明现有下载成果可大量复用，但剩余项仍须逐个处理。

## 7. 旧 OA 历史如何接回

`oa_source` 表注释：1=130 业务合同，2=474 总委合同。本次已在旧 SQL Server 验证：

| 新库字段 | 旧库关联 | 实测例子 |
|---|---|---|
| source=1，oa_id=28/29/30 | workflow_form.billformid=-130，billid=oa_id | requestid 分别为 33276/33298/33299 |
| source=2，oa_id=9/11/12 | workflow_form.billformid=-474，billid=oa_id | requestid 分别为 946747/968400/968431 |

因此历史链路是：

```text
新库 oa_source + oa_id
 → ecologyE9.workflow_form.billformid + billid
 → workflow_form.requestid
 → workflow_requestbase.requestid
 → workflow_requestLog / workflow_approvelog
 → 旧库按 requestid 生成的表单、意见、图片和附件
```

已有 `data/e9-opinion-export/_index.csv` 保存 requestid 与 Markdown 路径，可先复用旧产物。旧索引没有对应 requestid 时，标记“旧库尚未导出”，而不是随便关联同编号文件；旧导出筛选过法务节点时，也不能据此认定全部审批历史已齐全。

`oa_request_id` 已填写的记录应直接到旧 workflow_requestbase 核验；若同时有其他映射，要比较是否一致。合同编号只作为交叉核验，不作为唯一可信主键。

## 8. 供实现使用的查询 SQL

以下均为只读模板。SQL 中 `@apply_id` 代表待导出的申请 ID；跨库连接若出现 collation 冲突，仅对字符串参数/小范围关联做显式转换，不在生产库做无界大表二进制连接。全量导出建议分批拉取后在内存按字符串 ID 建索引，本次统计也是据此核验。

### 8.1 案件和档案

```sql
SET @apply_id = 2094961761093029890;
SELECT a.id, a.contract_no, a.contract_name, a.tenant_id,
       a.contract_archive_id, a.process_instance_id,
       a.oa_id, a.oa_source, a.oa_request_id,
       a.applicant_name, a.applicant_dept_name,
       a.my_main_body, a.other_main_body,
       a.contract_status, a.create_time, a.update_time,
       a.require_fill_file_id, a.require_file_ids,
       a.no_require_file_ids, a.change_agreement_file_ids
FROM tender_contract.contract_apply a
WHERE a.id = @apply_id AND a.deleted = 0;

-- 两个方向分别核验，避免 OR join 遮盖冲突。
SELECT r.* FROM tender_contract.contract_archive r
WHERE r.contract_apply_id = @apply_id AND r.deleted = 0;

SELECT r.* FROM tender_contract.contract_archive r
JOIN tender_contract.contract_apply a ON a.contract_archive_id = r.id
WHERE a.id = @apply_id AND a.deleted = 0 AND r.deleted = 0;
```

### 8.2 指定流程的任务和完整意见

先从上述业务行读取流程 ID，并转换为与引擎字段一致的字符集/排序规则：

```sql
SET @proc_id = CONVERT('HCCX-HT001-2026090300019' USING utf8) COLLATE utf8_bin;

SELECT PROC_INST_ID_, BUSINESS_KEY_, PROC_DEF_ID_, TENANT_ID_,
       NAME_, START_TIME_, END_TIME_, DELETE_REASON_
FROM tender_bpm.act_hi_procinst
WHERE PROC_INST_ID_ = @proc_id;

SELECT ID_, PROC_INST_ID_, NAME_, TASK_DEF_KEY_, ASSIGNEE_,
       START_TIME_, END_TIME_, DELETE_REASON_
FROM tender_bpm.act_hi_taskinst
WHERE PROC_INST_ID_ = @proc_id
ORDER BY START_TIME_, ID_;

SELECT c.ID_, c.PROC_INST_ID_, c.TASK_ID_, t.NAME_ AS task_name,
       c.USER_ID_, c.TIME_, c.TYPE_, c.ACTION_, c.MESSAGE_,
       HEX(c.FULL_MSG_) AS full_message_hex
FROM tender_bpm.act_hi_comment c
LEFT JOIN tender_bpm.act_hi_taskinst t
  ON t.ID_ = c.TASK_ID_ AND t.PROC_INST_ID_ = c.PROC_INST_ID_
WHERE c.PROC_INST_ID_ = @proc_id
ORDER BY c.TIME_, c.ID_;

-- 在客户端从 HEX 还原完整正文，可避免 mysql -B 对换行/反斜杠转义的干扰。
SELECT ID_, TYPE_, TASK_ID_, TIME_STAMP_, USER_ID_, DATA_
FROM tender_bpm.act_hi_tsk_log
WHERE PROC_INST_ID_ = @proc_id
ORDER BY TIME_STAMP_, ID_;
```

### 8.3 附件元数据及补充附件

```sql
-- @file_id 为业务字段拆分得到的原始文件ID。
SELECT id, name, type, size, md5, url, deleted
FROM tender_infra.infra_file
WHERE id = @file_id;

SELECT id, contract_apply_id, seal_documents,
       mail_scanned_documents, evidence_documents
FROM tender_contract.contract_apply_archive
WHERE contract_apply_id = @apply_id AND deleted = 0;

SELECT id, contract_apply_id, termination_date,
       termination_file_ids, corroboration_file_ids, remark
FROM tender_contract.contract_apply_termination
WHERE contract_apply_id = @apply_id AND deleted = 0;
```

### 8.4 旧 SQL Server 表单回链

以下在 ecologyE9 SQL Server 执行，不能直接混进 MySQL 跨库 JOIN：

```sql
DECLARE @oa_id bigint = 28;
DECLARE @billformid int = -130; -- source=2 时为 -474

SELECT f.requestid, f.billformid, f.billid,
       r.requestmark, r.requestname
FROM workflow_form f
LEFT JOIN workflow_requestbase r ON r.requestid = f.requestid
WHERE f.billformid = @billformid AND f.billid = @oa_id;
```

## 9. 实施顺序与验收

1. 建立案件、档案、流程、文件、用户、旧 OA 映射索引；批量读取，避免每个案件逐字段查数据库。
2. 先做小批样本：新原生有图、迁移有旧历史、多附件、同号多申请、终止/归档、缺失流程、文件失败及业务键补查样本。与系统页面或旧产物逐项核对。
3. 生成结构化中间记录与 Markdown，原子写入；断点键至少包括申请 ID、数据更新时间和导出版本，不能仅凭“MD 文件存在”跳过更新。
4. 全量输出索引和缺口报告；不修改当前下载队列与 WAL，读取正在变化的 manifest/progress 时采用稳定快照。

验收项：

- 每份 MD 可追溯到申请 ID、档案 ID、流程 ID、旧 requestid，重复合同号不会互相覆盖。
- 新库和旧库流程分别标注来源，多次发起/变更不被静默去重。
- 无意见任务仍出现在时间线中；意见数按原始 comment ID 对账，事件与人工意见分别计数。
- 所有附件与图片都能归属于来源字段/意见；逐个检查相对链接、内容格式与可用校验值。
- 三件套完整性分别记录案件信息、业务附件、流程历史、意见图片的覆盖情况；不以“脚本无异常”代替内容齐全。
- 统计确实无附件/无图片、尚未获取、下载失败、来源不明四种情况，避免将缺失误报为正常空值。

## 10. 现阶段需要确认的边界

- 数字审批类型、业务枚举展示名称以及完整表单字段顺序，应与新系统前端/代码确认。
- 变更、终止、延期等业务流程是否都能从现有表字段完整回链，仍需专项样本验证。
- 迁移历史能否完整恢复取决于旧库流程、旧附件权限及旧图片实体是否仍存在。
- 当前存在的 404、403、失效本地路径及 blob 图片，应在 MD 中保留缺口，不能凭数据库记录宣称已恢复。

参考依据：`tools/tender_fullsync.py`、`tools/tender_unify_merge.py`、`scripts/e9_opinion_export.py`、`scripts/e9_form_attach_export.py`、本地 `data/tender_sync/tables/` 表结构，以及本次新 MySQL / 旧 SQL Server 的只读查询。文中数字为研究时点结果，非静态业务总量承诺。
