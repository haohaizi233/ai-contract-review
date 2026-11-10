# Phase 13-R8: 安全红队与对抗测试报告 (`10-security-adversarial-tests.md`)

## 1. 威胁建模与防御矩阵

| 攻击向量 / 风险场景 | 防御实现位置 | 防御机制 | 验证测试 |
| :--- | :--- | :--- | :--- |
| **存储型/反射型 XSS 注入**（恶意合同正文或批注包含 `<script>`、`onload=`） | `contract_kb/table_aligner.py`, `contract_kb/full_diff.py`, `tools/contract_diff_web.py` | 服务端 `html.escape(..., quote=True)` 与前端 `esc()` 双重转义 | `tests/security/test_diff_html_injection.py` |
| **Prompt Injection 越狱**（合同正文伪造 `</UNTRUSTED_CONTRACT_BODY>` 或指令覆盖） | `contract_kb/legal_agent.py` | 每次调用生成 `secrets.token_hex(8)` 动态随机 Nonce 隔离围栏，并清理输入中的闭合标签 | `tests/integration/test_legal_agent_trusted_rag.py` |
| **SSRF 服务端请求伪造**（篡改 `base_url` 指向 `169.254.169.254` 或内网 IP） | `contract_kb/legal_agent.py::_is_safe_endpoint_url` | 解析 IP 与主机名，默认拦截云元数据地址、回环地址、链路本地与私网地址 | `tests/integration/test_legal_agent_trusted_rag.py` |
| **未授权外部大模型数据出境** | `contract_kb/legal_agent.py` | 强校验 `CONTRACT_KB_ALLOW_EXTERNAL_LLM`，未开启时直接阻断并转入本地规则兜底 | `tests/integration/test_legal_agent_trusted_rag.py` |
| **跨租户会话劫持与检查点越权读取** | `contract_kb/agent_queue_manager.py`, `contract_kb/agent_checkpoint.py` | 会话访问核验 `sess.tenant_id == tenant_id`；检查点按租户分目录并做 8 重门禁 + SHA-256 完整性校验 | `tests/security/test_agent_cross_tenant_access.py`, `tests/integration/test_agent_checkpoint_fingerprint.py` |

## 2. 对抗测试执行结果

执行命令：
```bash
python -m pytest tests/security/ tests/integration/test_agent_checkpoint_fingerprint.py tests/integration/test_legal_agent_trusted_rag.py -v
```
结果：**14 passed, 0 failed**。所有红队攻击载荷均被成功拦截或转义。
