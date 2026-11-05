# 新系统（tender_* 库）取数指南

> 调研日期：2026-11-05 ｜ 数据源：`192.168.2.125:3308`（MySQL 8.0.12，readonly 只读账号）
> 本文回答一个问题：**怎么从新系统把合同数据和文件取出来**。所有链路均已实测验证。

## TL;DR — 三条取数路径

| 路径 | 取什么 | 怎么取 | 状态 |
|---|---|---|---|
| ① 业务元数据 | 合同/客户/财务/审批流 | SQL 直查 tender_contract 等库 | ✅ 已验证 |
| ② 文件实体 | 42,231 个 PDF/DOCX/ZIP… | `infra_file.url` **HTTP 直链免签名下载** | ✅ 已验证（HTTP 200/206） |
| ③ 合同↔文件关联 | 单据挂了哪些文件 | `*_file_ids` 逗号字段 + `contract_attachment.oss_id` | ✅ 已验证 |

工具入口：`tools/tender_attach_export.py`（文件清单导出 + 批量下载，零第三方依赖）。

---

## 一、库表地图（13 个 schema）

| 库 | 内容 | 关键表（行数） |
|---|---|---|
| **tender_contract** | 合同全生命周期 | contract_apply 合同申请(46,401)、contract_archive 合同档案(44,861)、contract_attachment 合同附件/模版(484)、contract_data_share(782,716)、contract_customer、*_change/_termination/_performance 等变更/终止/履约 |
| **tender_bpm** | 审批流（Flowable 引擎） | act_hi_varinst(1,355,418)、act_hi_actinst、act_hi_taskinst、act_ge_bytearray(表单/变量内容) |
| **tender_infra** | 基础设施：**文件**、API 日志 | infra_file(42,231)、infra_file_config、infra_api_request_log(4.6GB) |
| **tender_finance** | 财务 | finance_accounts_receivable 应收(195,018)、finance_invoice_records 发票、finance_net_receipts 回款；含按日期快照表 |
| **tender_crm** | 客户/报销/专利情报 | crm_customer(196,767)、crm_reimbursement、crm_tyc_patent_info（天眼查专利） |
| tender_portal / tender_erp / tender_ipeasy / tender_supplier / tender_sync / tender_system / tender_bid | 门户、ERP、IPEasy、供应商、同步、系统、招投标 | portal_done_task、sync_task、system_sms_log 等 |

注意：`contract_apply__20260815`、`finance_accounts_receivable_20260928` 这类**带日期后缀的是快照/备份表**，做统计时排除。

## 二、文件体系（核心）

```
tender_infra.infra_file          元数据：name/path/url/type/size/md5/bucket…（42,231 行）
tender_infra.infra_file_config   存储后端配置（MinIO/S3 连接串，含 AK/SK，勿外传）
tender_infra.infra_file_content  DB 存储后端的二进制内容 —— 实测 0 行，未启用
```

**文件实体全部在 MinIO 对象存储**：

- endpoint：`http://183.36.37.85:48080`，bucket `prerelease`（另有测试桶 `test` @192.168.2.159:9090）
- `infra_file.url` 是完整直链，**实测免签名可直接 GET**（`HTTP 200, Content-Type: application/pdf, Content-Length` 与 `size` 一致，支持 Range 断点续传）
- URL 形态：`http://183.36.37.85:48080/prerelease/2026/11/05/<原文件名去符号><32位随机>.<ext>`
- ID 为雪花号（如 `2118160871610552321`），但库里混有早期自增小 ID（1660 等），join 时不做格式假设
- `deleted` 为 `bit(1)`，过滤条件写 `deleted = 0`（b'0'）

样例（最新一条）：
```
name: 深南电路合并.pdf   size: 260795   type: pdf
url : http://183.36.37.85:48080/prerelease/2026/11/05/深南电路合并pdfa5f9....pdf
```

## 三、合同 ↔ 文件的两种关联

**模式 A：`*_file_ids` 逗号分隔字段（合同的盖章文件，主力）**

contract_apply / contract_archive 上：

| 字段 | 含义 |
|---|---|
| `require_fill_file_id` | 需盖章填充文件（单个） |
| `require_file_ids` | 需盖章文件（多个，逗号分隔） |
| `no_require_file_ids` | 不需盖章文件 |
| `change_agreement_file_ids` | 变更协议文件 |

取值就是 `infra_file.id`（或逗号串），join 即得 URL。终止协议在 `*_termination.termination_file_ids/corroboration_file_ids`，顺延在 `contract_archive_deferred.contract_extended_files_ids`。

**模式 B：`contract_attachment`（模版类，source_type=3）**

`oss_id` → `infra_file.id`，另有冗余的 `oss_url` 直链；`source_type` 1=申请合同 2=档案 3=模版。实测当前 484 条全是模版（知识产权服务合同.docx 等）。

**其他**：AI 检索流程 `contract_flow_patent_search_assist_ai_file.file_id`、`_item.final_report_file_ids`（交底/检索报告文件）。

## 四、取数配方（可直接执行）

```sql
-- 1) 合同主档（业务字段以实际表结构为准：SHOW CREATE TABLE tender_contract.contract_apply）
SELECT id, contract_no, create_time FROM tender_contract.contract_apply
WHERE deleted = 0 ORDER BY id DESC LIMIT 100;

-- 2) 合同 → 盖章文件 → 下载地址（取 require_file_ids 的第一个 id 演示）
SELECT a.id, a.contract_no,
       SUBSTRING_INDEX(SUBSTRING_INDEX(a.require_file_ids, ',', n.n), ',', -1) AS file_id,
       f.name, f.size, f.url
FROM tender_contract.contract_apply a
JOIN (SELECT 1 n UNION SELECT 2 UNION SELECT 3 UNION SELECT 4 UNION SELECT 5) n
     ON n.n <= 1 + LENGTH(a.require_file_ids) - LENGTH(REPLACE(a.require_file_ids, ',', ''))
JOIN tender_infra.infra_file f
     ON f.id = SUBSTRING_INDEX(SUBSTRING_INDEX(a.require_file_ids, ',', n.n), ',', -1)
WHERE a.deleted = 0 AND a.require_file_ids IS NOT NULL AND a.require_file_ids <> '';

-- 3) 模版附件清单
SELECT ca.doc_name, ca.doc_type, ca.upload_person_name, ca.upload_time, f.url, f.size
FROM tender_contract.contract_attachment ca
JOIN tender_infra.infra_file f ON f.id = ca.oss_id
WHERE ca.deleted = 0;

-- 4) 全量文件清单（配合工具做批量下载）
SELECT id, name, type, size, md5, url FROM tender_infra.infra_file
WHERE deleted = 0 AND temporary_file_flag = 0;
```

## 五、工具

```bash
# 只导出文件清单（TSV+manifest.csv）
python tools/tender_attach_export.py --manifest-only

# 限量下载 100 个 PDF/DOCX（断点续传，已存在自动跳过）
python tools/tender_attach_export.py --limit 100 --type pdf,docx

# 按时间范围全量下载到指定目录
python tools/tender_attach_export.py --since 2026-01-01 --out data/tender_files
```

实现要点：SQL 走 `mysql` CLI（免装驱动），下载走 `urllib`（stdlib），按 `infra_file.md5` 校验，URL 天然支持 Range 续传。

## 六、注意事项

1. **权限**：readonly 账号只有 SELECT；`SHOW`/`information_schema` 可用。跨库 join（tender_contract × tender_infra）实测可用（同实例）。
2. **删除位**：所有业务表都有 `deleted bit(1)`，查询别忘 `deleted = 0`。
3. **快照表**：`*_20260815` 这类后缀表是历史备份，统计时排除。
4. **凭据**：MySQL 凭据在 `data/.db_env`（`MYSQL_*`）；MinIO 的 AK/SK 在 `infra_file_config.config` 里，但**下载文件不需要**——URL 公开可读。如需上传/删除才用得上。
5. **与旧 e9 管线的差异**：旧 OA 要靠 fileid 去重/重下、修 imagefile 死链；新系统直接拿 `infra_file.url` 就能下载，链路短得多。老的 `tools/e9_*` 工具链可整体退役为参考。
6. **体量预估**：42,231 个文件、含 zip 2.8MB 级单件，全量下完预计几十 GB，建议按 `--since`/`--type` 分批。
