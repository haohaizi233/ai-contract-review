# Flowable 原生表结构分析：审批通过时的"意见"存在哪个表

> 结论先行：**审批意见（评论文本）存在 `ACT_HI_COMMENT`**——Flowable 原生把任务意见/评论
> 写入历史评论表，`MESSAGE_`（≤4000 字符）或 `FULL_MSG_`（超长时的二进制全文）即意见正文。
> 若系统是二开且把意见当表单变量提交，则落在 `ACT_HI_VARINST` / `ACT_RU_VARIABLE`（长文本进
> `ACT_GE_BYTEARRAY`），排查时需两处都看。"通过/驳回"这个**结论**本身不在评论表，而在流程
> 流转记录（`ACT_HI_ACTINST`）与流程变量中。
>
> 本地资料（爬取快照）见 `docs/flowable/refs/`。适用版本：Flowable 6.x / 2026.1 官方 db-scripts。

---

## 1. 结论：审批意见的原生落点

### 1.1 `ACT_HI_COMMENT`（历史评论/审批意见表）——意见正文在这里

官方 DDL（`flowable.engine.mssql.all.create.sql`，SQL Server 版）：

```sql
create table ACT_HI_COMMENT (
    ID_            nvarchar(64)   not null,   -- 主键
    TYPE_          nvarchar(255),             -- 类型：comment=意见/评论（默认）
    TIME_          datetime       not null,   -- 意见提交时间
    USER_ID_       nvarchar(255),             -- 审批人/评论人
    TASK_ID_       nvarchar(64),              -- 关联任务（审批环节）→ ACT_HI_TASKINST.ID_
    PROC_INST_ID_  nvarchar(64),              -- 关联流程实例 → ACT_HI_PROCINST.ID_
    ACTION_        nvarchar(255),             -- 动作标记（add_comment / Complete 等，随调用方式不同）
    MESSAGE_       nvarchar(4000),            -- ★ 意见正文（≤4000 字符）
    FULL_MSG_      varbinary(max),            -- ★ 意见全文（超长/大对象时启用）
    primary key (ID_)
);
```

字段怎么对应"审批通过时的意见"：

| 业务含义 | 字段 |
|---|---|
| 意见内容 | `MESSAGE_`（短）/ `FULL_MSG_`（长） |
| 谁批的 | `USER_ID_` |
| 什么时候批 | `TIME_` |
| 哪个审批环节 | `TASK_ID_`（联 `ACT_HI_TASKINST` 可得环节名称 `NAME_`） |
| 哪个流程实例 | `PROC_INST_ID_` |
| 什么动作 | `ACTION_`（配合 `TYPE_` 区分普通评论与完成动作备注） |

写入 API：`TaskService.addComment(taskId, processInstanceId, message)`（实体
`CommentEntityImpl`，字段与上表一一对应，见 `refs/CommentEntityImpl.java` 快照）。
Flowable UI（flowable-task）在"完成任务并备注"时同样调用该 API——所以**审批通过那一刻填的意见
就在 `ACT_HI_COMMENT`**。

### 1.2 "审批通过"这个结论存在哪

- 流程走向（批到哪一步）：`ACT_HI_ACTINST` / `ACT_HI_TASKINST`（环节名称、审批人、开始/结束时间、
  `END_TIME_` 非空=已批）；
- 通过/驳回等**结构化结论**：多数实现以流程变量提交（`approvalResult` / `spjy` 之类），
  落在 `ACT_HI_VARINST`（历史）与 `ACT_RU_VARIABLE`（运行中）；变量值超过长度走
  `ACT_GE_BYTEARRAY`（`BYTEARRAY_ID_` 关联）。

## 2. Flowable 原生命名体系（速查）

- `ACT_GE_*` 通用（BYTEARRAY 字节对象、PROPERTY 版本属性）
- `ACT_RU_*` 运行时（流程结束即删除：EXECUTION、TASK、VARIABLE、IDENTITYLINK、JOB…）
- `ACT_HI_*` 历史（**审计留痕都在这**：PROCINST、ACTINST、TASKINST、VARINST、COMMENT、
  ATTACHMENT、DETAIL、IDENTITYLINK、TSK_LOG…）
- `ACT_RE_*` 仓库（DEPLOYMENT、PROCDEF、MODEL 流程定义）
- `ACT_ID_*` 身份；`ACT_CMMN_*`/`ACT_DMN_*`/`ACT_APP_*` 其他引擎；`FLW_*` Flowable 商业组件

完整官方表清单快照：`refs/flowable-db-schema-overview.md`（英文）、
`refs/nutflow-flowable-tables.md`（中文对照，明确标注"ACT_HI_COMMENT 历史流程审批意见"）。

## 3. 意见相关的表怎么区分（排查决策表）

| 你想查的东西 | 表 | 说明 |
|---|---|---|
| 审批意见/评论文本 | **ACT_HI_COMMENT** | `MESSAGE_` / `FULL_MSG_`，`TYPE_='comment'` |
| 意见里带的附件 | ACT_HI_ATTACHMENT | 按 `TASK_ID_`/`PROC_INST_ID_` 关联 |
| 审批人/时间/环节 | ACT_HI_TASKINST | `ASSIGNEE_`、`START_TIME_`、`END_TIME_`、`NAME_` |
| 通过/驳回结论 | ACT_HI_VARINST / ACT_RU_VARIABLE | 变量名随二开定义，先查 `NAME_` 列表 |
| 操作时间线（谁点了什么） | ACT_HI_TSK_LOG | `TYPE_`=事件类型、`DATA_`=备注、`TIME_STAMP_` |
| 运行中未批完的任务 | ACT_RU_TASK | 流程结束会清空，别在这里找历史意见 |

## 4. 常用排查 SQL（SQL Server）

```sql
-- ① 按流程实例查全部审批意见（最常用）
SELECT c.TIME_, c.USER_ID_, t.NAME_ AS 环节, c.MESSAGE_ AS 意见, c.TYPE_, c.ACTION_
FROM ACT_HI_COMMENT c
LEFT JOIN ACT_HI_TASKINST t ON t.ID_ = c.TASK_ID_
WHERE c.PROC_INST_ID_ = '流程实例ID'
ORDER BY c.TIME_;

-- ② 按任务（审批环节）查意见
SELECT * FROM ACT_HI_COMMENT WHERE TASK_ID_ = '任务ID' ORDER BY TIME_;

-- ③ 意见可能被二开塞进变量时的兜底查询
SELECT v.NAME_, v.TEXT_, v.TEXT2_, v.BYTEARRAY_ID_, b.BYTES_
FROM ACT_HI_VARINST v
LEFT JOIN ACT_GE_BYTEARRAY b ON b.ID_ = v.BYTEARRAY_ID_
WHERE v.PROC_INST_ID_ = '流程实例ID'
  AND (v.NAME_ LIKE '%意见%' OR v.NAME_ LIKE '%opinion%' OR v.NAME_ LIKE '%comment%');

-- ④ 某合同/单据的完整审批轨迹（意见 + 环节 + 结论变量）
SELECT t.NAME_ 环节, t.ASSIGNEE_ 审批人, t.END_TIME_ 批准时间,
       c.MESSAGE_ 意见, v.NAME_ 结论变量, v.TEXT_ 结论值
FROM ACT_HI_TASKINST t
LEFT JOIN ACT_HI_COMMENT c ON c.TASK_ID_ = t.ID_
LEFT JOIN ACT_HI_VARINST v ON v.PROC_INST_ID_ = t.PROC_INST_ID_
     AND (v.NAME_ LIKE '%result%' OR v.NAME_ LIKE '%结论%' OR v.NAME_ LIKE '%审批%')
WHERE t.PROC_INST_ID_ = '流程实例ID'
ORDER BY t.END_TIME_;
```

注意：`FULL_MSG_` 是二进制列（Nvarchar 序列化），SQL 里查看用
`CAST(FULL_MSG_ AS nvarchar(max))`；`MESSAGE_` 有 4000 字符上限，长意见看 `FULL_MSG_`。

## 5. 本地爬取资料清单（docs/flowable/refs/）

| 文件 | 来源 | 内容 |
|---|---|---|
| `db-scripts/flowable.engine.mssql.all.create.sql` | 官方 db-scripts 2026.1 | **SQL Server 版全套原生建表语句（权威）** |
| `db-scripts/flowable.engine.postgres.all.create.sql` | 官方 db-scripts 2026.1 | PostgreSQL 版 |
| `flowable.h2.create.history.sql` / `flowable.h2.create.engine.sql` | flowable-engine 仓库（GitHub） | H2 精简版建表 |
| `flowable-db-schema-overview.md` | documentation.flowable.com | 官方数据库表体系说明（GE/RU/HI/RE、FLW_ 系列） |
| `flowable-history-tables.md` | documentation.flowable.com | 历史表清单（页面为图片版字段图） |
| `nutflow-flowable-tables.md` | docs.nutflow.vip | 中文表结构对照（含"ACT_HI_COMMENT=审批意见"结论） |
| `CommentEntityImpl.java` | flowable-engine 仓库（GitHub） | 评论实体字段与表列的映射依据 |
