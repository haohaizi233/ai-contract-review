# Phase 13-R：核心缺陷复现与根因定位报告 (01-defect-reproduction)

本文件记录对 `ai-contract-review` 仓库在审计基线 `85c77d8` 上各 P0/P1 缺陷的复现命令、输入构造与修复前真实输出证据。

---

## 1. P13-P0-01：正式合同对照流程写死演示数据 (`contract_kb/full_diff.py::_enhance_diff_tables`)

### 复现步骤
在修复前，调用 `build_full_contract_diff()` 或直接调用 `_enhance_diff_tables()` 处理任意真实合同：
```python
from contract_kb.full_diff import _enhance_diff_tables

blocks = [
    {
        "clause_title": "双方主体与基本信息",
        "standard_text": "甲方：甲公司\n乙方：乙公司",
        "reviewed_text": "甲方：北京量子跃迁智能科技有限公司\n乙方：上海领航航天科技合伙企业",
        "status": "DIFF",
        "annotation_ids": ["ann-1"],
    }
]
nb, na = _enhance_diff_tables(blocks, [])
```

### 修复前真实缺陷表现
- 输出 `nb[0]["reviewed_html"]` 中直接写死了 `"深圳市某某科技有限公司"`、`"深圳市世纪恒程知识产权代理事务所"`、`"600"`（官费）、`"150"`（服务费）以及固定专利号 `"202410000001.2"`。
- 真实当事人名称与真实费用金额被彻底抹除。
- 原生差异批注 `ann-1` 被丢弃，替换为固定示例批注。

---

## 2. P13-P0-02：Agent 检查点身份绑定不充分 (`contract_kb/agent_queue_manager.py`)

### 复现步骤
修复前，检查点文件名与身份仅由 `f"{doc_name}_{matched_tpl}_{len(raw_anns)}"` 生成：
- 上传同名文件 `contract.docx`（第一版：违约金 `3‰`），完成审核并生成检查点。
- 修改文件内容（第二版：违约金篡改为 `0.1‰`，但批注总数未变），再次以 `contract.docx` 上传。

### 修复前真实缺陷表现
- 因文件名、匹配模板名、批注数量三者相同，系统直接命中旧检查点，将第二版篡改后的高危合同复用第一版审核结论。

---

## 3. P13-P0-03：法务 Agent 绕过外部模型授权与预算账本 (`contract_kb/legal_agent.py`)

### 复现步骤
设置环境变量 `CONTRACT_KB_ALLOW_EXTERNAL_LLM=0`，调用旧版 `LegalReviewAgent.review_clause()`。

### 修复前真实缺陷表现
- 旧版 `LegalReviewAgent` 直接使用 `urllib.request.urlopen` 请求外部 API，未检查 `CONTRACT_KB_ALLOW_EXTERNAL_LLM`，未调用 `BudgetLedgerService.reserve_budget()`，且在 JSON 解析失败时通过正则截取半截文本伪装为审核成功。

---

## 4. P13-P0-04：Web 审核工作台全局文件覆盖与跨会话串线 (`tools/contract_diff_web.py`)

### 复现步骤
修复前，`tools/contract_diff_web.py` 定义了全局唯一路径：
`CURRENT_SESSION_FILE = SESSION_CACHE_DIR / "current_active_review.docx"`
当用户 A 与用户 B 先后上传不同合同时：

### 修复前真实缺陷表现
- 用户 B 的上传直接覆写 `current_active_review.docx`，导致用户 A 后续切换母本或触发 Agent 审查时读取到用户 B 的合同内容；SSE 广播流向所有浏览器页面无差别推送。

---

## 5. P13-P0-05：CI `test-synthetic` 失败与违规 `pytest.skip` (`tests/test_full_contract_diff.py`)

### 复现步骤
在无本地私有数据库的隔离环境运行：
```bash
CONTRACT_KB_DB_PATH=test_nonexistent.db python -m pytest tests/test_full_contract_diff.py -v
```

### 修复前真实缺陷表现
- GitHub Actions 运行 `42649085523` 报错：`ValueError: 未能在标准库中找到业务子类 [PATENT_ANNUAL_FEE] 的基准模板`。
- 提交 `85c77d8` 违规添加 `except ValueError: pytest.skip(...)`，导致隔离环境下 `2 skipped`，掩盖了测试未注入 `storage` 与 `golden_samples` 夹具的根因。
