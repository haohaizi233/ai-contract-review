# 10 — 包构建、静态资源打包与运行入口验证（Packaging & Entrypoints）

## 1. `pyproject.toml` 规范化配置

### 1.1 标准 CLI Entry Points (`[project.scripts]`)

```toml
[project.scripts]
contract-kb = "contract_kb.interfaces.cli.main:main"
contract-kb-web = "contract_kb.interfaces.web.app:main"
contract-kb-gui = "contract_kb.interfaces.desktop.app:main"
```

### 1.2 Web 静态资源打包 (`[tool.setuptools.package-data]`)

```toml
[tool.setuptools.packages.find]
include = ["contract_kb*"]

[tool.setuptools.package-data]
contract_kb = [
    "interfaces/web/templates/*.html",
    "interfaces/web/static/css/*.css",
    "interfaces/web/static/js/*.js",
]
```

---

## 2. Wheel 构建与干净虚拟环境隔离安装验证

执行了完整的打包与非仓库目录运行验证：

1. **构建检查**：
   - `python -m pip install -e . --no-deps` → **PASS**
   - `python -m pip check` → `No broken requirements found.`（**PASS**）
   - `python -m compileall -q contract_kb` → **PASS**（0 语法或字节码编译错误）
   - `python -m pip wheel . --no-deps -w scratch/wheel_test` → 成功构建 `contract_kb-0.3.0-py3-none-any.whl`（461 KB，含全部子包与 HTML/CSS/JS 静态资源）。
2. **干净虚拟环境 + 非仓库工作目录 (`C:\Users\Administrator\AppData\Local\Temp`) 验证**：
   - 将 `contract_kb-0.3.0-py3-none-any.whl` 安装至独立虚拟环境 `clean_venv`；
   - 切换工作目录至 `%TEMP%`（确保不依赖源码仓库当前工作目录）；
   - 验证 `contract_kb.__file__` 指向 `clean_venv/Lib/site-packages/contract_kb/__init__.py`；
   - 调用 `load_html_template()` 成功加载包内 `index.html`、`app.css` 与 4 个 `.js` 文件（总长 > 50,000 字符）；
   - 初始化 `Storage`、`ReviewEngine` 与 `create_web_server('127.0.0.1', 0)` 全部成功；
   - 执行 `contract-kb.exe help` 与 `contract-kb-web.exe --help` 均正常输出帮助信息，退出码为 `0`。

---

## 3. 兼容运行入口验证清单

| 运行方式 | 命令 | 状态 |
|---|---|---|
| 模块化 CLI（原入口） | `python -m contract_kb.cli help` | **PASS**（无 `RuntimeWarning`） |
| 模块化 CLI（新规范入口） | `python -m contract_kb.interfaces.cli.main help` | **PASS** |
| 安装脚本 CLI | `contract-kb help` | **PASS** |
| FastAPI 服务 | `uvicorn contract_kb.api:app` | **PASS** |
| Web 工作台（原入口） | `python tools/contract_diff_web.py --help` | **PASS** |
| Web 工作台（模块入口） | `python -m contract_kb.interfaces.web.server --help` | **PASS**（无 `RuntimeWarning`） |
| Web 工作台（安装脚本） | `contract-kb-web --help` | **PASS** |
| 桌面 GUI（原入口） | `python start_gui.py` / `start_gui.bat` | **PASS** |
| 评测基准 CLI | `python -m contract_kb.evaluation.retrieval.benchmark_retrieval` | **PASS** |
| 历史工具兼容 CLI | `python tools/cross_verify_and_merge.py --dry-run --limit 5` | **PASS** |
| 历史工具兼容 CLI | `python tools/live_db_merge.py --dry-run --limit 5` | **PASS** |
| 历史工具兼容 CLI | `python tools/tender_case_export.py --dry-run --limit 5 --no-web --no-fetch` | **PASS** |
