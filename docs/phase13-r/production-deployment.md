# AI 合同智能审核系统生产部署与安全配置指南 (`production-deployment.md`)

## 1. 核心环境变量配置

在生产环境中部署本系统时，必须通过环境变量或受控密钥管理服务（KMS）显式配置以下参数：

```bash
# 1. 存储后端配置（生产环境强烈建议使用 PostgreSQL）
export CONTRACT_KB_STORAGE_BACKEND="postgres"
export CONTRACT_KB_PG_DSN="postgresql://contract_user:<STRONG_PASSWORD>@pg-prod.internal:5432/contract_kb"

# 2. 大模型出境与端点安全配置（默认 Fail-Closed 关闭外部调用）
export CONTRACT_KB_ALLOW_EXTERNAL_LLM="true"          # 仅在完成数据出境合规审批或指向内网私有网关时开启
export CONTRACT_KB_ALLOW_PRIVATE_LLM_ENDPOINT="true"  # 若使用企业内网 vLLM/Ollama 私有端点，显式开启内网许可
export CONTRACT_KB_LLM_BASE_URL="https://llm-gateway.internal.example.com"
export CONTRACT_KB_LLM_API_KEY="<SECRET_API_KEY>"
export CONTRACT_KB_LLM_MODEL="deepseek-chat"

# 3. Token 预算上限控制
export CONTRACT_KB_MAX_RUN_TOKENS="50000"
export CONTRACT_KB_MAX_TASK_TOKENS="12000"
```

## 2. 文档解析与 OCR 依赖部署

- **标准 `.docx` 与文本型 `.pdf`**：由 Python 依赖包（`python-docx`、`pypdf`、`pdfplumber`）直接支持。
- **旧版 `.doc` 二进制文档转换**：生产容器需安装 LibreOffice（`apt-get install -y libreoffice-writer`），系统通过无头模式自动安全转换为 `.docx`。
- **扫描件 `.pdf`**：系统会自动识别无文本流页面并标记 `OCR_REQUIRED` 阻断虚假合规结论；如需开启扫描件自动识别，请接入企业内部 OCR 微服务将扫描件预转换为双层可检索 PDF 后再行上传。

## 3. Web 审阅工作台启动与会话清理

- 启动服务：
  ```bash
  python tools/contract_diff_web.py --host 0.0.0.0 --port 8765
  ```
- 会话文件隔离路径：`data/web_upload_cache/sessions/<session_id>/contract.docx`（单次上传硬上限 25MB）。
- 检查点持久化路径：`data/agent_checkpoints/<tenant_id>/`（具备 12 因子指纹与 SHA-256 完整性防篡改校验）。
