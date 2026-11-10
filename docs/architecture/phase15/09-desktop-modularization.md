# 09 — 桌面 GUI 模块化拆解（Desktop GUI Modularization）

## 1. 重构背景

仓库根目录下的 `start_gui.py` 原先为 **1,012 行** 的单文件脚本，同时包含：
- `PROJECT_ROOT` / `PYTHON_EXE` / `PYTEST_EXE` 路径探测；
- `ProcessRunner` 子进程异步执行器、实时日志流读取与 Windows `taskkill` 进程树终止；
- `StartGUI` 四大功能选项卡（RAG 与标准模板管理、样本库与回归复验、合同比对与 Diff Web、资产清单与报告直达）与底部 Console 终端；
- 14 组外部 CLI / Pytest / PowerShell 命令拼接逻辑。

---

## 2. 模块化目录结构

重构后，桌面端代码归入 `contract_kb/interfaces/desktop/`：

```text
contract_kb/interfaces/desktop/
├── __init__.py               # 惰性导出 StartGUI, ProcessRunner, main, PROJECT_ROOT, PYTHON_EXE, PYTEST_EXE
├── app.py                    # 桌面应用入口 main() 与窗口关闭生命周期钩子 (WM_DELETE_WINDOW)
├── main_window.py            # StartGUI 主窗口布局、4 个 Notebook 选项卡、异步日志队列轮询与状态栏控制
├── process_runner.py         # ProcessRunner 后台线程子进程执行器、实时输出着色分类与安全终止
├── commands.py               # 统一管理 PROJECT_ROOT / PYTHON_EXE / PYTEST_EXE 定位与全部子进程命令构造器
└── widgets/
    ├── __init__.py
    └── cards.py              # 可复用 UI 组件：create_action_card, create_quick_link, create_dir_link
```

仓库根目录保留：
- `start_gui.py`（31 行薄兼容入口，重导出 `PROJECT_ROOT`, `PYTHON_EXE`, `PYTEST_EXE`, `ProcessRunner`, `StartGUI`, `main`）；
- `start_gui.bat`（双击启动脚本保持不变）。

---

## 3. 关键路径与命令修正

1. **`PROJECT_ROOT` 精确定位**：在 `contract_kb/interfaces/desktop/commands.py` 中使用 `Path(__file__).resolve().parents[3]` 准确定位回仓库根目录，并由自动化烟雾测试断言 `(PROJECT_ROOT / "start_gui.py").exists()`。
2. **子进程脚本路径同步更新**：
   - `get_validate_contract_merged_cmd()` 已更新为指向迁移后的 `tools/data_ops/validate_contract_merge_repair.py`；
   - 其余通过兼容入口调用的命令（如 `tools/sync_wecom_wedrive.py`、`tools/diff_contract.py`、`tools/contract_diff_web.py`、`python -m contract_kb.cli`）均经过实测验证。
