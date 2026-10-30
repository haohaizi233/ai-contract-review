# Git 历史清理方案（history-cleanup-plan）

> ⚠️ 状态：**仅方案，未执行**。历史重写属破坏性操作，须用户确认后执行。
> 前置事实：仓库已转 PRIVATE（用户指令已执行）；HEAD 层敏感文件已取消跟踪。

## 背景

提交 `8e7e116`（初始提交）与 `193e1c4`（功能提交）包含真实合同 DOCX、全文提取件、
SQLite/AST 派生数据、报告摘录与代码内企业字面量。仅删除 HEAD 无法消除历史访问。

## 推荐方案：git filter-repo（单次执行，快于 BFG）

### 0. 前置

```powershell
pip install git-filter-repo
git clone --mirror https://github.com/haohaizi233/ai-contract-review.git _rewrite_backup.git
# ↑ 先做镜像备份，重写失败可恢复
```

### 1. 移除受控路径（按 deletion-manifest §A/B）

```powershell
cd ai-contract-review
git filter-repo --invert-paths ^
  --path 参考/ ^
  --path 新系统合同审批注意事项20261015.docx ^
  --path 深圳市统一电子印章管理平台第三方应用接入指引及申请材料20260508/ ^
  --path _docx_extract.txt ^
  --path 知识库_合同模板蒸馏.md ^
  --path 知识库_合同模板蒸馏_深度版.md ^
  --path _tools/extracted/ ^
  --path data/contract-kb/ ^
  --path reports/contract-kb/
# _tools/*.txt 报告、clause_library.json 同理追加 --path
```

### 2. 替换代码历史中的企业字面量

```powershell
git filter-repo --replace-text expressions.txt
```

`expressions.txt` **本地生成、不入库**（避免二次泄露），格式 `原值==>替换值`，
需覆盖的敏感类别（具体原值见本地受控资料，勿写入仓库）：

| 类别 | 替换值（合成） |
|---|---|
| 乙方公司全称 | 示例知识产权代理有限公司 |
| 银行账号 / 行号 | 6222000000000000000 / 000000000000 |
| 企业邮箱（服务/投诉/指示） | service@ / complaint@ / notice@example.com |
| 手机号 | 13800000000 |
| 人名（代表人、经办人） | 张某某 / 李某某 |
| 开户行及地址 | 示例银行测试支行 |

### 3. 强制推送与收尾（破坏性，需确认）

```powershell
git push --force --all origin
git push --force --tags origin
```

随后必须：
1. 通知所有协作者重新 clone（旧本地仓库会把旧历史推回来）；
2. 在 GitHub 仓库 Settings → 临时关闭 fork 能力（重写会断 fork 关系）；
3. **轮换凭证**：历史中的账户/联系人信息视为已泄露，银行账户信息应通报财务风控；
4. GitHub Support 可请求清除缓存视图（转私有后通常无需）；
5. 检查 Actions 构件/日志是否曾上传敏感件（本项目 CI 未运行过，无构件）。

## 备选方案：BFG Repo-Cleaner（Java，等效）

```powershell
java -jar bfg.jar --delete-folders data --delete-files "*.docx" --replace-text expressions.txt
git reflog expire --expire=now --all; git gc --prune=now --aggressive
git push --force --all origin
```

## 风险表

| 风险 | 缓解 |
|---|---|
| 重写失败丢历史 | 先做 --mirror 备份（步骤 0） |
| 协作者旧仓库推回旧历史 | 通知重 clone；推回后可再跑一次 filter-repo |
| 提交哈希全变 | 已知代价；PR 引用需更新 |
| 泄露信息已被外部抓取 | 历史清理无法召回；账户信息须业务侧做风险处置（改收款账户备案/风控提示） |

## 执行确认清单（用户确认后逐项打勾）

- [ ] 已做 mirror 备份
- [ ] 确认执行 filter-repo 路径删除 + 字面量替换
- [ ] 确认 force push
- [ ] 已通知协作者重新 clone
- [ ] 已启动账户信息风险处置（财务/法务）
