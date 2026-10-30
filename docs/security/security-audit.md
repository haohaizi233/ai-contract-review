# 安全审计报告（Phase 0）— 受控文件与敏感数据识别

> 审计日期：2026-10-30 | 仓库：github.com/haohaizi233/ai-contract-review
> 结论：**确认泄露**——真实合同模板原件及派生数据已被提交至 Git（含历史），仓库现已按用户指令转为 **PRIVATE**。
> 本报告仅含文件清单与标记统计，不含合同原文。

## 1. 受控文件分类（按敏感度）

### S1 — 企业合同原件（最高敏感）
| 路径 | 数量 | 说明 |
|---|---|---|
| `参考/*.docx` | 26 | 真实合同模板原件（含主体、银行账户、联系人） |
| `新系统合同审批注意事项20261015.docx` | 1 | 企业内部审批文档 |
| `深圳市统一电子印章管理平台…/` | 5 | 第三方接入材料（PDF+DOCX） |
| `_docx_extract.txt` | 1 | 内部文档摘录 |

### S2 — 原件派生数据（高敏感，含合同全文/账户/联系人）
| 路径 | 说明 |
|---|---|
| `_tools/extracted/*.txt`（29 个） | 26 模板 + 3 份内部文档的全文提取 |
| `data/contract-kb/ast/*.json`（26 个） | 全文 AST（每节点含原文） |
| `data/contract-kb/layer_a_raw.json` | 1050 条款原文库 |
| `data/contract-kb/import_result.json` / `clause_diff.json` / `taxonomy.json` / `field_schema.json` / `rule_candidates.json` / `eval_*.json` | 脱敏不完整的蒸馏产物（含条款摘录） |
| `data/contract-kb/contract_kb.db` | SQLite（条款全文+嵌入） |
| `reports/contract-kb/*.md`（7 份含摘录） | 报告内嵌合同原文片段 |
| `知识库_合同模板蒸馏*.md`（2 份） | 含账户号、条款原文 |
| `_tools/*.txt` 报告（5 份） | 聚类报告含原文 |

### S3 — 代码内敏感字面量（中敏感，需脱敏）
| 文件 | 泄露内容 |
|---|---|
| `contract_kb/distill.py` | 真实公司全称（规则映射+报告文案） |
| `contract_kb/evaluate.py` | 真实企业邮箱（黄金查询关键词） |
| `contract_kb/golden.py` | 真实公司全称（合成样本乙方） |

## 2. 敏感标记扫描结果（git grep，已跟踪文件；此处仅列类别与规模，不复述原值）

| 标记类别 | 命中文件数（约） | 说明 |
|---|---|---|
| 银行账号 / 行号 | ~70 | 财务账户 |
| 企业邮箱（服务/投诉/指示） | ~75 | 联系方式 |
| 乙方公司全称 | ~80 | 主体信息 |
| 人名（代表人、经办人） | ~30 | 个人信息 |
| 手机号 | ~30 | 个人信息 |
| 开户行地址 | ~70 | 地理信息 |

**Git 历史**：上述内容存在于 `8e7e116`、`193e1c4` 两个提交及其文件版本中，仅从 HEAD 移除不会消除历史泄露。

## 3. 风险评估

1. 泄露性质：合同模板（未含具体客户交易），但含**我方签约主体、银行账户、法定代表人、员工联系方式**——可被用于钓鱼、诈骗收款、冒名签约。
2. 处置状态：仓库已转私有（用户明确指令）→ 外部未认证用户不可见；但协作者/令牌持有者仍可见历史。
3. 必须执行：HEAD 清理（本次执行）+ 历史重写（方案见 `history-cleanup-plan.md`，**待用户确认后执行**）。

## 4. 已执行的处置（本次）

1. 仓库可见性：PUBLIC → **PRIVATE**（依据用户明确指令"改成私有仓库"）。
2. `.gitignore` 扩展：排除 S1/S2 全部路径、上传目录、本地企业配置。
3. `git rm --cached` 移除 S1/S2 文件跟踪（本地磁盘保留，导入链路不受影响）。
4. 代码脱敏：S3 三个文件的真实企业字面量替换为合成占位（`示例知识产权代理有限公司` / `@example.com`），真实映射改由**本地配置文件**提供（`config/company_profile.local.json`，已 gitignore）。
5. 测试去真实化：测试语料改为**确定性合成合同集**（`contract_kb/synthetic.py` 生成），真实模板回归测试独立为 `real_corpus` 标记用例，仅在显式授权环境变量下运行。
6. 历史清理方案：`docs/security/history-cleanup-plan.md`（仅方案，未执行）。

## 5. 清理后授权导入路径（保持可用）

```powershell
# 真实模板仍在本地 参考/ 目录（未删除，仅取消 Git 跟踪）
python -m contract_kb.cli import                       # 默认读 参考/
$env:CONTRACT_KB_TEMPLATE_DIR="D:\受控目录\模板"         # 或指定受控路径
python -m contract_kb.cli import
```

本地 `data/` 知识库产物由导入管线按需重新生成，不再入库。
