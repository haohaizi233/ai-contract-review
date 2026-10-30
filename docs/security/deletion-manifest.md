# 待删除文件清单（deletion-manifest）

> 用途：从仓库 HEAD 与 Git 历史中移除受控数据。**HEAD 清理已执行；历史重写待用户确认。**
> 本地磁盘文件保留（git rm --cached 不删除工作区文件），导入链路不受影响。

## A. HEAD 中已取消跟踪（git rm --cached，本地保留）

```
参考/                                                  # 26 份真实合同 DOCX
新系统合同审批注意事项20261015.docx
深圳市统一电子印章管理平台第三方应用接入指引及申请材料20260508/
_docx_extract.txt
知识库_合同模板蒸馏.md
知识库_合同模板蒸馏_深度版.md
_tools/extracted/
_tools/clause_library.json
_tools/compare_result.txt
_tools/deep_distill_report.txt
_tools/deep_distill_report2.txt
_tools/fee_liab_clusters.txt
_tools/missing_report.txt
_tools/per_template_digest.txt
data/contract-kb/                                      # 整目录（AST/JSON/SQLite/派生报告数据）
reports/contract-kb/                                   # 整目录（含合同摘录的报告）
```

## B. 代码内敏感字面量（已脱敏提交，历史版本含原文）

```
contract_kb/distill.py      # 公司全称 → 占位符
contract_kb/evaluate.py     # 企业邮箱 → @example.com
contract_kb/golden.py       # 公司全称 → 合成名称
```

## C. 历史重写目标（git filter-repo path-glob，见 history-cleanup-plan.md）

两个历史提交（8e7e116、193e1c4）中包含 A/B 全部内容，需一并清理。

## D. 非删除项（确认安全，保留）

```
contract_kb/            # 代码（脱敏后）
tests/                  # 测试（合成语料化后）
migrations/ docs/ README.md pyproject.toml .gitignore .github/
```
