# 企业微信微盘（WeDrive）API 连通性与文件读取探测工具

独立、可重复运行的企业微信微盘官方 API 连通性探测、权限边界诊断与文件流下载验证工具。

---

## 一、架构设计与目录结构

```text
tools/wecom_wedrive_probe/
├── README.md               # 本使用与架构指南
├── requirements.txt        # 依赖清单 (requests, python-dotenv, pytest)
├── .env.example            # 环境变量配置模板
├── .env                    # 本地真实凭证（已由 .gitignore 忽略，禁止入库）
├── client.py               # 官方微盘 API 客户端（严格只读、TLS 校验、超时重试、敏感信息脱敏）
├── probe.py                # 探测器主编排引擎与报告生成器
├── validators.py           # 错误码归类、出口 IP 探测与文件 SHA256/MD5 完整性校验
├── tests/
│   ├── __init__.py
│   └── test_client.py      # 离线自动化测试套件（10 个用例全部 PASS）
└── reports/
    ├── probe_result.json   # 结构化探测结果（机器可读，凭据脱敏）
    └── probe_report.md     # 中文 Markdown 验收评估报告
```

---

## 二、核心测试流程

本工具按照官方 API 规范，依次执行两阶段七大测试项：

### 阶段 A：环境与网络探测
1. **环境变量完整性**：校验 `WECOM_CORP_ID`、`WECOM_APP_SECRET`、`WECOM_SPACE_ID`，报告缺失项且不打印明文 Secret；
2. **DNS 解析检查**：探测官方接口域名 `qyapi.weixin.qq.com` 解析时延与 IP 集合；
3. **真实出口 IP 识别**：探测当前主机的公网出口 IP（支持系统代理与直连双向识别）。

### 阶段 B：真实微盘 API 验证与权限边界探测
1. **获取 Token (`GET /cgi-bin/gettoken`)**：使用 CorpID 与 AppSecret 换取应用级 `access_token`，并支持本地 TTL 自动缓存；
2. **空间信息查询 (`POST /cgi-bin/wedrive/space_info`)**：携带 `spaceid` 查询指定共享空间的元数据及成员权限；
3. **根目录文件列表 (`POST /cgi-bin/wedrive/file_list`)**：拉取根目录文件及文件夹列表，支持分页游标；
4. **递归目录遍历**：维护已访问文件夹 Set，深度遍历各级子目录，统计文件夹、普通文件、在线文档数量与扩展名分布；
5. **文件信息查询 (`POST /cgi-bin/wedrive/file_info`)**：指定或挑选一份普通文件（<=10MB），获取详细元数据；
6. **文件下载凭证与二进制流下载 (`POST /cgi-bin/wedrive/file_download`)**：获取临时 `download_url` 与 Cookie，通过带鉴权 Header 的流式请求下载文件，内置大小超限熔断保护；
7. **文件完整性校验**：比对本地文件大小、MD5、SHA-1 与 SHA-256 哈希值与接口元数据是否吻合。

---

## 三、快速开始

### 1. 安装依赖

```bash
pip install -r tools/wecom_wedrive_probe/requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env`：

```dotenv
WECOM_CORP_ID=ww0000000000000000
WECOM_APP_SECRET=<YOUR_WECOM_APP_SECRET>
WECOM_SPACE_ID=<YOUR_WECOM_SPACE_ID>
WECOM_TEST_FILE_ID=
WECOM_DOWNLOAD_MAX_MB=10
WECOM_REQUEST_TIMEOUT=20

# 可选网络配置
# WECOM_PROXY=http://127.0.0.1:7890
# WECOM_DISABLE_SYSTEM_PROXY=false
```

### 3. 执行真实 API 探测

```bash
python tools/wecom_wedrive_probe/probe.py
```

执行后将自动生成：
- `reports/probe_result.json`
- `reports/probe_report.md`

### 4. 运行离线单元测试

无需网络连接即可测试全部错误分支与逻辑：

```bash
python -m pytest tools/wecom_wedrive_probe/tests/test_client.py -v
```

---

## 四、安全合规与权限限制说明

1. **绝对只读**：代码中仅包含 GET 查询与 POST 读取类接口，没有任何 `file_create`、`file_delete`、`file_move` 或写操作；
2. **强凭据脱敏**：Secret、Token、下载 URL、Cookie 在任何日志、终端与产物文件中均做掩码脱敏（如 `abcd********wxyz`、`tok123...789xyz`）；
3. **严格 TLS 校验**：全部 HTTPS 请求保持 `verify=True`，严禁关闭证书校验；
4. **下载隔离与熔断**：下载文件存入隔离的 `downloads/` 目录，并在 `.gitignore` 中严格排除；下载超过 `WECOM_DOWNLOAD_MAX_MB` 时自动中断并清理临时文件；
5. **错误码精确分类**：
   - `60020`：**企业可信 IP 限制**（当前公网出口 IP 受到企业微信后台安全拦截）；
   - `48002`：**微盘 API 未授权**（自建应用未在微盘 API 可调用列表中开通权限）；
   - `40001`：**应用 Secret 错误**；
   - `2000003`：**指定微盘空间无访问权限**；
   - `2000004`：**文件不存在**。

---

## 五、企业微信管理员配置指引（针对 60020 错误）

当探测报告给出【结论 C：仅授权成功（拦截于企业可信IP限制）】时：

1. 打开企业微信管理后台 [work.weixin.qq.com](https://work.weixin.qq.com)；
2. 进入【应用管理】-> 选中当前自建应用（如“AI合同审核”）；
3. 在【企业可信 IP】一栏点击修改，将探测报告中提示的出口 IP（如示例文档地址 `203.0.113.10` 或 `198.51.100.20`）添加至白名单中；
4. 保存后等待 1-2 分钟，重新运行 `python tools/wecom_wedrive_probe/probe.py` 即可完成全流程验证。
