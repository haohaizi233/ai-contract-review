# SQL Server 连接与 Navicat 配置排障经验（内部沉淀）

> 场景：从本机连接公司 SQL Server 数据库并在 Navicat Premium 17 中配置连接。
> 本文只沉淀**可复用的方法与踩坑结论**；真实地址/账号/密码不入库，存放在本机用户环境变量：
> `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PWD_READONLY`（历史凭据 `DB_PWD` 保留未覆盖）。
> 实测环境：Windows 10 / SQL Server 2014（12.00.2269）/ Navicat Premium 17.0.8 / ODBC Driver 17 & 18。

---

## 1. 症状时间线（为什么一开始连不上）

1. TCP 端口**能建立连接**，但服务端**从不回任何字节**（等 60s 无回包）；
2. 用 MySQL 客户端（pymysql）连接超时：`Lost connection during query (timed out)`；
3. PostgreSQL / Oracle / TLS / HTTP 等协议探测同样被静默丢弃或断开；
4. 最终用 **TDS 协议（SQL Server）** 一次连上——库是 SQL Server 2014，不是 MySQL。

**教训：端口"沉默"先怀疑协议，而不是账号密码。** 不同数据库的握手方向不同：

| 协议 | 谁先发包 | 无 banner 时的典型表现 |
|---|---|---|
| MySQL / MariaDB | **服务端先发握手** | 收不到握手=根本不是 MySQL（或被中间设备拦截） |
| PostgreSQL | 客户端先发 StartupMessage | 发错协议会被直接断开 |
| **SQL Server (TDS)** | **客户端先发 PRELOGIN** | 服务端一直沉默等客户端发包——与本次现象完全吻合 |
| Oracle (TNS) | 客户端先发 CONNECT | 同上 |
| TLS/HTTPS | 客户端先发 ClientHello | 裸 TCP 探测永远无回包 |

**协议识别工具链**：先 `socket` 探测有无 banner；无 banner 时逐个试协议客户端
（`pymysql` / `psycopg2` / `pytds` / `oracledb`）。本次是 `pytds`（纯 Python、免装客户端）：
`pip install python-tds` → `import pytds; pytds.connect(host, port=..., user=..., password=..., database='master')`。

## 2. 凭据管理：注入到用户环境变量（沿用既有约定）

按既有方式把凭据注入 Windows **用户级环境变量**，且**不覆盖历史变量**：

| 变量 | 用途 |
|---|---|
| `DB_HOST` / `DB_PORT` / `DB_USER` | 地址、端口、账号 |
| `DB_PWD_READONLY` | 当前只读账号密码（新） |
| `DB_PWD` | **历史密码，保留原值不动**（如需换用，先备份到 `DB_PWD_PREV` 再覆盖） |

```powershell
[Environment]::SetEnvironmentVariable("DB_HOST", "<host>", "User")
# 其余同理；注意：已运行的进程不会自动继承，脚本里需手动同步
foreach ($k in "DB_HOST","DB_PORT","DB_USER","DB_PWD_READONLY") {
  Set-Item -Path ("env:$k") -Value ([Environment]::GetEnvironmentVariable($k, "User"))
}
```

**教训**：覆盖任何"之前注入过"的凭据前先备份（`DB_PWD_PREV`）；密码永不写进代码仓库/文档。

## 3. Navicat 连接前置：ODBC 驱动 + .msi 关联

Navicat 连 SQL Server 需要 **Microsoft ODBC Driver for SQL Server**。本机安装方式：

```powershell
winget install --id Microsoft.msodbcsql.17 -e --silent --accept-package-agreements --accept-source-agreements
winget install --id Microsoft.msodbcsql.18 -e --silent --accept-package-agreements --accept-source-agreements
```

**坑 A：`.msi` 文件关联丢失** → Navicat 弹"你要如何打开这个文件"导致驱动装不上。
修复（用户级，可逆）：

```powershell
New-Item -Path "HKCU:\Software\Classes\.msi" -Force | Out-Null
Set-Item -Path "HKCU:\Software\Classes\.msi" -Name "(default)" -Value "Msi.Package"
```

**坑 B：别用网页 fwlink 下载驱动**（容易跳到无关页面）；winget 有现成包（`Microsoft.msodbcsql.17/.18`）。

## 4. Navicat 崩溃根因：Unknown Internal Error (c0000005, MSVCP140.dll)

**症状**：连接 SQL Server 时 Navicat 弹"错误报告 / Unknown Internal Error"，异常码 `c0000005`（访问冲突），
模块 `MSVCP140.dll`，每次崩溃点一致；PG 连接却完全正常。

**根因**：连接配置里 `NativeClientDriver` 为空时，Navicat 自动选择了系统里遗留的旧版 **“SQL Server” 驱动**
（`ODBCINST.INI` 里的老驱动，早已废弃），进它的调用路径即崩。指定为新驱动后一次连通：

```
HKCU\Software\PremiumSoft\NavicatMSSQL\Servers\<连接名>\NativeClientDriver = "ODBC Driver 17 for SQL Server"
```

**教训**：①"崩在 C++ 运行库"不等于运行库坏了，多为上层传了坏配置/坏驱动选择；
②新旧 ODBC 驱动共存时，客户端"自动选驱动"是高危行为，**显式指定驱动**；
③SQL Server 2014 这类老服务器优先用 **ODBC 17**（18 默认强制加密，容易额外踩 TLS 坑）。

## 5. Navicat 连接的注册表落点与字段类型坑

Navicat 17 的连接按类型存注册表：`HKCU\Software\PremiumSoft\NavicatMSSQL\Servers\<连接名>`
（PG 对应 `NavicatPG`，通用字段相同）。手工写入/脚本化配置时注意：

| 字段 | 类型 | 说明 |
|---|---|---|
| `Host` / `UserName` / `InitialDatabase` | REG_SZ | — |
| `Port` / `PortSpecifiedForMSSQL` | REG_DWORD | **自定义端口必须 PortSpecifiedForMSSQL=1**，否则被忽略 |
| `AskPassword` / `Pwd` | REG_SZ | `AskPassword=false` + `Pwd=<Navicat 加密串>` = 已保存密码 |
| `MSSQLAuthenMode` | REG_SZ | `mamSQLServer` / `mamWindows` / `mamActiveDirectory*` |
| `NativeClientDriver` | REG_SZ | **必填**，见第 4 节 |
| `CreateTime` / `ModifyTime` / `AccessTime` | **REG_QWORD** | 写成 DWORD 会导致类型错乱（踩过） |

`Pwd` 的加密串只能由 Navicat 自己生成（其加密算法非公开）；做法：配置好其他字段 →
让 Navicat 弹密码框 → 输入并勾选"保存密码" → Navicat 回写加密 `Pwd`。

## 6. UI 自动化经验（脚本化操作 Navicat 这类桌面程序）

- **消息驱动 > 坐标点击**：`PostMessage(WM_LBUTTONDBLCLK)` 到树控件、`WM_SETTEXT` 写输入框、
  `BM_CLICK` 点按钮——不依赖窗口前后台，不干扰用户正在用的电脑；
- **窗口内容用 `PrintWindow` 抓**（Win32 API），无论窗口是否被遮挡/最小化，配合 Read 看图核对每一步；
- **控件定位**：`EnumWindows`/`EnumChildWindows` 拿句柄 + 类名（Navicat 是 Delphi：`TButtonedEdit`=输入框、
  `TCheckBox`、`TButton`、`TAskPasswordDialogForm`=密码框、`TCrashErrorDialogForm`=崩溃框）；
- **坐标点击必须先校验前台窗口**是目标应用，再点击（否则会点到用户正在用的窗口）；
- **DPI 坐标空间**：高缩放屏上截图/鼠标/GetWindowRect 可能落在不同坐标空间（本机 1024×576 逻辑 vs
  1600×900 物理），跨进程坐标换算极易出错——能用消息就别用坐标；
- 中文标题在 PowerShell 控制台会乱码，**按类名匹配**（如 `*CrashErrorDialog*`）比按标题匹配可靠。

## 7. 复刻一个新库连接的检查清单

1. 协议识别：无 banner → 逐协议试（SQL Server 用 `pytds`）；连不上先别怀疑密码；
2. 凭据注入用户环境变量（新变量独立命名，不覆盖历史）；
3. `winget install Microsoft.msodbcsql.17`（老服务器）/ `.18`，确认 `HKLM\SOFTWARE\ODBC\ODBCINST.INI` 已注册；
4. Navicat 建连接（或脚本写注册表）：`Port` + `PortSpecifiedForMSSQL`、`MSSQLAuthenMode`、
   **`NativeClientDriver=ODBC Driver 17 for SQL Server`**、时间戳字段用 QWORD；
5. 首次连接弹密码框 → 输入并勾选"保存密码"（由 Navicat 生成加密 `Pwd`）；
6. 连接后核对：树节点展开出库列表、右侧面板"服务器版本/会话"有值。

## 8. 快速诊断命令备忘

```powershell
# 端口/banner 探测
python -c "import socket;s=socket.socket();s.settimeout(6);s.connect(('<host>',<port>));print(s.recv(80))"

# SQL Server 直连验证（绕开 GUI，定位是网络/账号问题还是 Navicat 问题）
pip install python-tds
python -c "import pytds;print(pytds.connect('<host>',port=<port>,user='<user>',password='<pwd>',database='master',timeout=10).cursor().execute('SELECT @@VERSION').fetchone())"

# 查看已注册 ODBC 驱动
reg query "HKLM\SOFTWARE\ODBC\ODBCINST.INI"

# 查看 Navicat 连接配置
reg query "HKCU\Software\PremiumSoft\NavicatMSSQL\Servers\<连接名>"
```
