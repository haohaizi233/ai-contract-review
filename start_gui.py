# -*- coding: utf-8 -*-
"""AI合同审查系统 - 自动化控制台 (GUI Dashboard)
提供图形化界面一键调用 RAG 增量同步、模板格式升级、入库与评测、样本库校验、自动化测试及微观条款 Diff。
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# 项目根目录自动定位
PROJECT_ROOT = Path(__file__).resolve().parent
PYTHON_EXE = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON_EXE.exists():
    PYTHON_EXE = Path(sys.executable)

PYTEST_EXE = PROJECT_ROOT / ".venv" / "Scripts" / "pytest.exe"
if not PYTEST_EXE.exists():
    PYTEST_EXE = PYTHON_EXE


class ProcessRunner:
    """子进程异步执行器，保证 UI 界面在耗时命令运行期间完全流畅无卡顿。"""

    def __init__(self, log_callback, status_callback, finish_callback):
        self.log_callback = log_callback
        self.status_callback = status_callback
        self.finish_callback = finish_callback
        self.current_process: subprocess.Popen | None = None
        self._is_running = False
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._is_running

    def run_commands_sequential(self, cmd_list: list[tuple[str, list[str]]]):
        """顺序执行一组命令"""
        if self._is_running:
            messagebox.showwarning(
                "提示", "当前已有命令正在执行中，请等待其完成或先点击【终止】。"
            )
            return

        self._is_running = True
        self._thread = threading.Thread(
            target=self._worker_sequential, args=(cmd_list,), daemon=True
        )
        self._thread.start()

    def run_command(self, title: str, cmd: list[str], cwd: Path | None = None):
        """执行单条命令"""
        self.run_commands_sequential([(title, cmd)])

    def _worker_sequential(self, cmd_list: list[tuple[str, list[str]]]):
        total = len(cmd_list)
        all_success = True

        for idx, (title, cmd) in enumerate(cmd_list, 1):
            if not self._is_running:
                break

            self.status_callback(f"[{idx}/{total}] 正在执行: {title} ...", is_busy=True)
            self.log_callback(
                f"\n{'=' * 70}\n>>> [{idx}/{total}] 任务开始: {title}\n>>> 执行指令: {' '.join(str(c) for c in cmd)}\n{'=' * 70}\n",
                tag="header",
            )

            t0 = time.time()
            try:
                # Windows 下隐藏窗口调用
                creationflags = 0
                if sys.platform == "win32":
                    creationflags = subprocess.CREATE_NO_WINDOW

                self.current_process = subprocess.Popen(
                    cmd,
                    cwd=str(PROJECT_ROOT),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=creationflags,
                )

                # 实时捕获标准输出流
                for line in iter(self.current_process.stdout.readline, ""):
                    if not line:
                        break
                    self.log_callback(line, tag=self._categorize_line(line))

                self.current_process.stdout.close()
                ret_code = self.current_process.wait()
                cost = time.time() - t0

                if ret_code == 0:
                    self.log_callback(
                        f"\n>>> [OK] {title} 执行成功 (耗时: {cost:.2f}s, 退出码: 0)\n",
                        tag="success",
                    )
                else:
                    self.log_callback(
                        f"\n>>> [FAIL] {title} 异常退出 (耗时: {cost:.2f}s, 退出码: {ret_code})\n",
                        tag="error",
                    )
                    all_success = False
                    # 多步执行遇错中断
                    if total > 1:
                        self.log_callback(
                            ">>> [ABORT] 后续任务已取消执行。\n", tag="warning"
                        )
                        break

            except Exception as e:
                self.log_callback(
                    f"\n>>> [ERROR] 进程启动或执行发生异常: {e}\n", tag="error"
                )
                all_success = False
                break
            finally:
                self.current_process = None

        self._is_running = False
        self.finish_callback(all_success)

    def terminate(self):
        """强制终止正在执行的子进程"""
        if self.current_process and self.current_process.poll() is None:
            try:
                self.status_callback("正在强制终止进程...", is_busy=True)
                self.current_process.terminate()
                # Windows taskkill 彻底终止进程树
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(self.current_process.pid)],
                        capture_output=True,
                    )
                self.log_callback(
                    "\n>>> [TERMINATED] 用户已手动终止当前任务。\n", tag="warning"
                )
            except Exception as e:
                self.log_callback(f"\n>>> 终止失败: {e}\n", tag="error")
        self._is_running = False
        self.finish_callback(False)

    @staticmethod
    def _categorize_line(line: str) -> str:
        lower = line.lower()
        if any(
            w in lower for w in ["fail", "error", "traceback", "exception", "failed"]
        ):
            return "error"
        if any(
            w in lower
            for w in ["[ok]", "pass", "success", "imported", "100%", "completed"]
        ):
            return "success"
        if any(w in lower for w in ["warn", "skipped", "pending"]):
            return "warning"
        return "normal"


class StartGUI:
    """主界面窗口应用程序"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("AI合同审查系统 - 自动化控制台 (GUI Dashboard)")
        self.root.geometry("1100x760")
        self.root.minsize(920, 640)

        # 居中显示
        self._center_window()

        # Web服务后台句柄
        self.web_process: subprocess.Popen | None = None

        # 异步日志队列
        self.log_queue: queue.Queue = queue.Queue()

        # 进程管理器
        self.runner = ProcessRunner(
            log_callback=self._queue_log,
            status_callback=self._set_status,
            finish_callback=self._on_task_finished,
        )

        # 样式定义
        self._setup_styles()

        # UI 构建
        self._build_ui()

        # 启动日志轮询定时器
        self.root.after(50, self._process_log_queue)

    def _center_window(self):
        self.root.update_idletasks()
        w = 1100
        h = 760
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _setup_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except Exception:
            pass

        style.configure(
            "Title.TLabel",
            font=("Microsoft YaHei UI", 13, "bold"),
            foreground="#1E293B",
        )
        style.configure(
            "Subtitle.TLabel", font=("Microsoft YaHei UI", 9), foreground="#64748B"
        )
        style.configure(
            "Group.TLabelframe.Label",
            font=("Microsoft YaHei UI", 10, "bold"),
            foreground="#0F172A",
        )
        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Action.TButton", font=("Microsoft YaHei UI", 9))
        style.configure("Status.TLabel", font=("Microsoft YaHei UI", 9))

    def _build_ui(self):
        # 顶层容器
        main_frame = ttk.Frame(self.root, padding="12 10 12 10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 1. 顶部 Header
        header_frame = ttk.Frame(main_frame)
        header_frame.pack(fill=tk.X, pady=(0, 10))

        title_lbl = ttk.Label(
            header_frame, text="AI合同审查与知识库智能运维控制台", style="Title.TLabel"
        )
        title_lbl.pack(anchor=tk.W)

        desc_lbl = ttk.Label(
            header_frame,
            text=f"工作区: {PROJECT_ROOT} | Python 环境: {PYTHON_EXE.name}",
            style="Subtitle.TLabel",
        )
        desc_lbl.pack(anchor=tk.W, pady=(2, 0))

        # 2. 分割面板（上部分为功能选项卡，下部分为日志终端）
        paned = ttk.PanedWindow(main_frame, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # 上半部分：Notebook 选项卡容器
        top_container = ttk.Frame(paned)
        paned.add(top_container, weight=1)

        notebook = ttk.Notebook(top_container)
        notebook.pack(fill=tk.BOTH, expand=True)

        # 创建选项卡
        tab_rag = ttk.Frame(notebook, padding=10)
        tab_validation = ttk.Frame(notebook, padding=10)
        tab_diff = ttk.Frame(notebook, padding=10)
        tab_reports = ttk.Frame(notebook, padding=10)

        notebook.add(tab_rag, text="  📚 RAG 与标准模板管理  ")
        notebook.add(tab_validation, text="  🧪 样本库与回归复验  ")
        notebook.add(tab_diff, text="  ⚖️ 合同比对与 Diff Web  ")
        notebook.add(tab_reports, text="  📁 资产清单与报告直达  ")

        # 填充选项卡内容
        self._build_tab_rag(tab_rag)
        self._build_tab_validation(tab_validation)
        self._build_tab_diff(tab_diff)
        self._build_tab_reports(tab_reports)

        # 下半部分：日志终端 Console
        bottom_container = ttk.Frame(paned, padding=(0, 8, 0, 0))
        paned.add(bottom_container, weight=1)
        self._build_console(bottom_container)

        # 3. 底部状态栏
        status_bar = ttk.Frame(main_frame)
        status_bar.pack(fill=tk.X, pady=(8, 0))

        self.status_lbl = ttk.Label(
            status_bar,
            text="就绪：选择上方功能卡片即可一键图形化调用。",
            style="Status.TLabel",
        )
        self.status_lbl.pack(side=tk.LEFT)

        self.prog_bar = ttk.Progressbar(status_bar, mode="indeterminate", length=140)
        # 默认不显示，任务执行时启动

    # ==================== Tab 1: RAG 与模板管理 ====================
    def _build_tab_rag(self, parent):
        # 左右两栏布局
        left_col = ttk.Frame(parent)
        left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))

        right_col = ttk.Frame(parent)
        right_col.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(6, 0))

        # 左栏：增量更新与入库
        grp_sync = ttk.LabelFrame(left_col, text="一键增量比对与自动入库", padding=10)
        grp_sync.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        self._create_btn(
            grp_sync,
            text="🚀 一键全流程重跑 (格式升级 ➔ 增量入库 ➔ 刷新 ➔ 评测)",
            desc="自动完成老旧.doc转换、增量入库、分类刷新、清单生成与RAG基准实测，全程无缝执行。",
            cmd=self._cmd_full_pipeline,
            primary=True,
        )

        self._create_btn(
            grp_sync,
            text="🔄 微盘增量 Diff 同步 (含软删除防抖)",
            desc="基于内容哈希(SHA-256)比对，未变动秒级跳过；文件消失触发60s宽限期并软删除(INACTIVE)。",
            cmd=self._cmd_wedrive_sync_diff,
        )

        self._create_btn(
            grp_sync,
            text="📑 老旧 .doc 一键批量转为 .docx",
            desc="扫描【参考/】目录，调用 Word COM 引擎将老旧二进制 .doc 转换为受控 OOXML .docx。",
            cmd=self._cmd_convert_doc,
        )

        # 右栏：分类刷新与评测
        grp_eval = ttk.LabelFrame(right_col, text="入库持久化与评测度量", padding=10)
        grp_eval.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        self._create_btn(
            grp_eval,
            text="📥 全量扫描导入并构建向量 (CLI import)",
            desc="扫描参考目录所有 DOCX，抽取 AST、段落与条款，持久化至 SQLite 数据库并构建检索向量。",
            cmd=self._cmd_cli_import,
        )

        self._create_btn(
            grp_eval,
            text="🏷️ 存量模板分类与元数据原子刷新",
            desc="对已有模板重新计算五级分类与路由规则，原子刷新数据库分类字段与元数据。",
            cmd=self._cmd_cli_refresh,
        )

        self._create_btn(
            grp_eval,
            text="🎯 RAG 检索对比评测基准实测",
            desc="运行15组黄金查询与8组困难负样本对比测试，实测 Hit@K、MRR、NDCG@8 与负样本避开率。",
            cmd=self._cmd_rag_benchmark,
        )

        self._create_btn(
            grp_eval,
            text="📊 端到端审核质量与延迟评估",
            desc="针对 30 份合同变异样本执行端到端审查，检验检出率(P/R/F1)与 P95 延迟。",
            cmd=self._cmd_review_evaluate,
        )

    # ==================== Tab 2: 样本库与回归 ====================
    def _build_tab_validation(self, parent):
        left_col = ttk.Frame(parent)
        left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))

        right_col = ttk.Frame(parent)
        right_col.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(6, 0))

        grp_merged = ttk.LabelFrame(
            left_col, text="contract_merged 样本库复验", padding=10
        )
        grp_merged.pack(fill=tk.BOTH, expand=True)

        self._create_btn(
            grp_merged,
            text="🔍 contract_merged 整库独立全量复验",
            desc="深度验证 2.1万案卷、1.9万图片实体(Pillow解码)、2.1万附件及9.2万链接是否存在断链或损坏。",
            cmd=self._cmd_validate_contract_merged,
            primary=True,
        )

        self._create_btn(
            grp_merged,
            text="⚡ 两库合并核心修复逻辑回归测试",
            desc="秒级运行 test_contract_merge_repair.py，验证多案归卷、括号链接解析、PNG转码与无编号申请保留。",
            cmd=self._cmd_test_merge_repair,
        )

        self._create_btn(
            grp_merged,
            text="🛡️ 导出质量规则与业务日志去重质量测试",
            desc="执行 pytest test_tender_case_quality.py，验证审批日志(E9双表)跨源去重与图片覆盖度计算。",
            cmd=self._cmd_test_case_quality,
        )

        grp_sys = ttk.LabelFrame(
            right_col, text="企业微盘与审查核心流水线回归", padding=10
        )
        grp_sys.pack(fill=tk.BOTH, expand=True)

        self._create_btn(
            grp_sys,
            text="☁️ 企微/微盘同步 E01-E14 端到端用例",
            desc="执行 16 项自动化用例，覆盖稳定性探测、哈希去重、软删除恢复、并发互斥与防穿越校验。",
            cmd=self._cmd_test_wedrive_rag_sync,
        )

        self._create_btn(
            grp_sys,
            text="⚙️ 审查流水线核心算子测试 (Stages)",
            desc="执行 unit/test_review_pipeline_stages.py，校验合同分类、条款匹配与审查算子执行状态。",
            cmd=self._cmd_test_pipeline_stages,
        )

        self._create_btn(
            grp_sys,
            text="🧪 全套核心单元测试套件一键回归 (Pytest)",
            desc="顺序运行检索、解析、流水线及企微同步所有核心自动化测试，生成整套通过报告。",
            cmd=self._cmd_test_all_core,
        )

    # ==================== Tab 3: 合同比对与 Diff ====================
    def _build_tab_diff(self, parent):
        grp_single = ttk.LabelFrame(
            parent, text="单份送审合同微观条款 Diff 比对", padding=12
        )
        grp_single.pack(fill=tk.X, pady=(0, 10))

        row1 = ttk.Frame(grp_single)
        row1.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(
            row1, text="待比对合同文件 (.docx):", font=("Microsoft YaHei UI", 9, "bold")
        ).pack(side=tk.LEFT)
        self.entry_doc_path = ttk.Entry(row1)
        self.entry_doc_path.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)

        # 默认填入一个已有样本
        default_sample = (
            PROJECT_ROOT
            / "tools"
            / "test_assets"
            / "verification_run"
            / "MB20261109001_知识产权代理框架协议.docx"
        )
        if default_sample.exists():
            self.entry_doc_path.insert(0, str(default_sample))

        ttk.Button(
            row1, text="📂 浏览选择...", command=self._browse_contract_file
        ).pack(side=tk.RIGHT)

        row2 = ttk.Frame(grp_single)
        row2.pack(fill=tk.X, pady=(4, 0))

        ttk.Button(
            row2,
            text="⚡ 执行逐条款微观比对 (diff_contract.py)",
            command=self._cmd_run_diff,
            style="Primary.TButton",
        ).pack(side=tk.LEFT)

        ttk.Label(
            row2,
            text="  自动匹配 78 套标准模板库对应基准母本，逐条对齐并深度检查数值、期限、主体及违约金变动。",
            style="Subtitle.TLabel",
        ).pack(side=tk.LEFT)

        # Web 可视化服务
        grp_web = ttk.LabelFrame(
            parent, text="条款比对可视化 Web 服务 (contract_diff_web)", padding=12
        )
        grp_web.pack(fill=tk.BOTH, expand=True)

        w_row1 = ttk.Frame(grp_web)
        w_row1.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(w_row1, text="服务监听端口:").pack(side=tk.LEFT)
        self.entry_web_port = ttk.Entry(w_row1, width=8)
        self.entry_web_port.insert(0, "8089")
        self.entry_web_port.pack(side=tk.LEFT, padx=6)

        self.btn_start_web = ttk.Button(
            w_row1,
            text="🚀 启动 Diff Web 服务",
            command=self._cmd_start_diff_web,
            style="Primary.TButton",
        )
        self.btn_start_web.pack(side=tk.LEFT, padx=4)

        self.btn_stop_web = ttk.Button(
            w_row1,
            text="⏹️ 停止服务",
            command=self._cmd_stop_diff_web,
            state=tk.DISABLED,
        )
        self.btn_stop_web.pack(side=tk.LEFT, padx=4)

        self.btn_open_web = ttk.Button(
            w_row1, text="🌐 在浏览器中打开", command=self._cmd_open_diff_web
        )
        self.btn_open_web.pack(side=tk.LEFT, padx=4)

        info_box = tk.Text(
            grp_web,
            height=6,
            bg="#F8FAFC",
            relief=tk.FLAT,
            font=("Consolas", 9),
            padx=8,
            pady=8,
        )
        info_box.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        info_box.insert(
            tk.END,
            "Diff Web 服务特性：\n"
            "1. 整版双栏对照：左侧标准母本全文，右侧送审合同全文，自然阅读流自上而下逐行精准对齐；\n"
            "2. 改变的地方加批注：高危篡改、核心缺失、主体倒置、期限放宽自动生成右侧专业法务审阅批注卡片；\n"
            "3. 双向交互联动：点击正文修改处直达批注，点击批注卡片正文发光脉冲聚焦，支持快捷键定位；\n"
            "4. 默认本地地址：http://localhost:8089 (亦可直接双击打开 data/contract_merged/ 离线可视化网页)\n",
        )
        info_box.config(state=tk.DISABLED)

    # ==================== Tab 4: 报告与直达 ====================
    def _build_tab_reports(self, parent):
        grp_reports = ttk.LabelFrame(parent, text="生成报告快速预览", padding=12)
        grp_reports.pack(fill=tk.X, pady=(0, 10))

        r_grid = ttk.Frame(grp_reports)
        r_grid.pack(fill=tk.X)

        self._create_quick_link(
            r_grid,
            0,
            0,
            text="📑 打开【源文件清单】 (source-inventory.md)",
            path=PROJECT_ROOT / "reports" / "contract-kb" / "source-inventory.md",
        )
        self._create_quick_link(
            r_grid,
            0,
            1,
            text="📈 打开【RAG 检索评估报告】 (rag-retrieval-evaluation.md)",
            path=PROJECT_ROOT
            / "reports"
            / "contract-kb"
            / "rag-retrieval-evaluation.md",
        )
        self._create_quick_link(
            r_grid,
            1,
            0,
            text="📋 打开【合同审核质量评估】 (contract-review-evaluation.md)",
            path=PROJECT_ROOT
            / "reports"
            / "contract-kb"
            / "contract-review-evaluation.md",
        )
        self._create_quick_link(
            r_grid,
            1,
            1,
            text="🌐 打开【五级知识图谱 HTML】 (合同分类与78套模板五级知识图谱.html)",
            path=PROJECT_ROOT
            / "data"
            / "contract_merged"
            / "合同分类与78套模板五级知识图谱.html",
        )

        grp_dirs = ttk.LabelFrame(
            parent, text="核心目录直接在文件资源管理器中打开", padding=12
        )
        grp_dirs.pack(fill=tk.BOTH, expand=True)

        d_grid = ttk.Frame(grp_dirs)
        d_grid.pack(fill=tk.X)

        self._create_dir_link(
            d_grid,
            0,
            0,
            text="🗂️ 打开【参考/】标准模板源目录",
            path=PROJECT_ROOT / "参考",
        )
        self._create_dir_link(
            d_grid,
            0,
            1,
            text="🗂️ 打开【data/contract-kb/】数据库与向量目录",
            path=PROJECT_ROOT / "data" / "contract-kb",
        )
        self._create_dir_link(
            d_grid,
            1,
            0,
            text="🗂️ 打开【data/contract_merged/】资料合并目录",
            path=PROJECT_ROOT / "data" / "contract_merged",
        )
        self._create_dir_link(
            d_grid,
            1,
            1,
            text="🗂️ 打开【reports/】报告汇总目录",
            path=PROJECT_ROOT / "reports",
        )

    # ==================== 下半部分：Console 控制台 ====================
    def _build_console(self, parent):
        ctrl_bar = ttk.Frame(parent)
        ctrl_bar.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(
            ctrl_bar,
            text="💻 运行日志与执行输出 (Console Output):",
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(side=tk.LEFT)

        self.btn_stop = ttk.Button(
            ctrl_bar, text="⏹️ 终止任务", command=self._cmd_terminate, state=tk.DISABLED
        )
        self.btn_stop.pack(side=tk.RIGHT, padx=(4, 0))

        ttk.Button(ctrl_bar, text="📋 复制全部日志", command=self._cmd_copy_logs).pack(
            side=tk.RIGHT, padx=4
        )
        ttk.Button(ctrl_bar, text="🧹 清空控制台", command=self._cmd_clear_logs).pack(
            side=tk.RIGHT, padx=4
        )

        # 文本框与滚动条
        text_frame = ttk.Frame(parent)
        text_frame.pack(fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.txt_console = tk.Text(
            text_frame,
            wrap=tk.WORD,
            bg="#0F172A",
            fg="#F8FAFC",
            insertbackground="#38BDF8",
            font=("Consolas", 10),
            padx=8,
            pady=8,
            yscrollcommand=scrollbar.set,
        )
        self.txt_console.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.txt_console.yview)

        # 颜色高亮标签
        self.txt_console.tag_config(
            "header", foreground="#38BDF8", font=("Consolas", 10, "bold")
        )
        self.txt_console.tag_config(
            "success", foreground="#4ADE80", font=("Consolas", 10, "bold")
        )
        self.txt_console.tag_config(
            "error", foreground="#F87171", font=("Consolas", 10, "bold")
        )
        self.txt_console.tag_config("warning", foreground="#FBBF24")
        self.txt_console.tag_config("normal", foreground="#E2E8F0")

        # 初始欢迎信息
        self._queue_log(
            "=== AI合同审查系统 - 自动化控制台就绪 ===\n"
            f"运行环境: Python {sys.version.split()[0]} | 当前平台: {sys.platform}\n"
            "提示: 点击上方卡片按钮可一键执行各模块命令，日志将在此处实时刷新。\n\n",
            tag="header",
        )

    # ==================== 辅助 UI 构造函数 ====================
    def _create_btn(self, parent, text: str, desc: str, cmd, primary: bool = False):
        card = ttk.Frame(parent)
        card.pack(fill=tk.X, pady=4)

        style = "Primary.TButton" if primary else "Action.TButton"
        btn = ttk.Button(card, text=text, command=cmd, style=style)
        btn.pack(anchor=tk.W, fill=tk.X)

        lbl = ttk.Label(card, text=desc, style="Subtitle.TLabel", wraplength=480)
        lbl.pack(anchor=tk.W, padx=4, pady=(2, 4))

    def _create_quick_link(self, parent, r, c, text: str, path: Path):
        def _open():
            if path.exists():
                webbrowser.open(str(path))
            else:
                messagebox.showinfo(
                    "提示", f"该报告尚未生成，请先执行对应生成命令：\n{path.name}"
                )

        btn = ttk.Button(parent, text=text, command=_open)
        btn.grid(row=r, column=c, padx=6, pady=6, sticky="ew")
        parent.grid_columnconfigure(c, weight=1)

    def _create_dir_link(self, parent, r, c, text: str, path: Path):
        def _open():
            if path.exists():
                os.startfile(str(path))
            else:
                messagebox.showinfo("提示", f"目录尚不存在：\n{path}")

        btn = ttk.Button(parent, text=text, command=_open)
        btn.grid(row=r, column=c, padx=6, pady=6, sticky="ew")
        parent.grid_columnconfigure(c, weight=1)

    # ==================== 日志与状态更新 ====================
    def _queue_log(self, text: str, tag: str = "normal"):
        self.log_queue.put((text, tag))

    def _process_log_queue(self):
        try:
            while True:
                text, tag = self.log_queue.get_nowait()
                self.txt_console.insert(tk.END, text, tag)
                self.txt_console.see(tk.END)
                self.log_queue.task_done()
        except queue.Empty:
            pass
        self.root.after(50, self._process_log_queue)

    def _set_status(self, text: str, is_busy: bool = False):
        self.status_lbl.config(text=text)
        if is_busy:
            self.prog_bar.pack(side=tk.RIGHT, padx=10)
            self.prog_bar.start(10)
            self.btn_stop.config(state=tk.NORMAL)
        else:
            self.prog_bar.stop()
            self.prog_bar.pack_forget()
            self.btn_stop.config(state=tk.DISABLED)

    def _on_task_finished(self, success: bool):
        self._set_status(
            f"完成: 上次任务已结束 (状态: {'成功' if success else '中断/异常'})",
            is_busy=False,
        )

    # ==================== 命令调用绑定 ====================
    def _cmd_full_pipeline(self):
        """一键全流程自动重跑"""
        cmds = [
            ("老旧 .doc 转为 .docx", self._get_convert_doc_cmd()),
            (
                "全量扫描入库与向量构建",
                [str(PYTHON_EXE), "-m", "contract_kb.cli", "import"],
            ),
            (
                "存量分类与元数据刷新",
                [str(PYTHON_EXE), "-m", "contract_kb.cli", "refresh"],
            ),
            (
                "重新生成源文件清单",
                [str(PYTHON_EXE), "-m", "contract_kb.cli", "inventory"],
            ),
            (
                "RAG 检索对比评测基准实测",
                [str(PYTHON_EXE), "-m", "contract_kb.benchmark_retrieval"],
            ),
            ("端到端审核质量评估", [str(PYTHON_EXE), "-m", "contract_kb.evaluate"]),
        ]
        self.runner.run_commands_sequential(cmds)

    def _cmd_wedrive_sync_diff(self):
        cmd = [
            str(PYTHON_EXE),
            "tools/sync_wecom_wedrive.py",
            "--local-dir",
            "参考",
            "--sync-deletion",
            "--grace-seconds",
            "60",
        ]
        self.runner.run_command("微盘增量 Diff 比对与软删除更新", cmd)

    def _cmd_convert_doc(self):
        cmd = self._get_convert_doc_cmd()
        self.runner.run_command("老旧 .doc 一键批量转为 .docx", cmd)

    def _get_convert_doc_cmd(self) -> list[str]:
        # 通过 Python 脚本驱动以保证跨平台稳定性
        code = (
            "import os, subprocess; from pathlib import Path; "
            "script = Path('tools/deploy/convert_legacy_doc.ps1').resolve(); "
            "docs = [f for f in Path('参考').rglob('*') if f.suffix.lower() == '.doc']; "
            "print(f'共扫描到 {len(docs)} 个 .doc 待转换文件...'); "
            "for i, f in enumerate(docs, 1): "
            "    tgt = f.with_suffix('.docx'); "
            "    cmd = ['powershell.exe', '-NoProfile', '-NonInteractive', '-File', str(script), '-Source', str(f.resolve()), '-Destination', str(tgt.resolve())]; "
            "    p = subprocess.run(cmd, capture_output=True); "
            "    print(f'[{i}/{len(docs)}] ' + ('OK: ' + f.name if p.returncode == 0 else 'FAIL: ' + f.name)); "
            "print('转换任务处理完成。')"
        )
        return [str(PYTHON_EXE), "-c", code]

    def _cmd_cli_import(self):
        self.runner.run_command(
            "全量扫描导入并构建向量",
            [str(PYTHON_EXE), "-m", "contract_kb.cli", "import"],
        )

    def _cmd_cli_refresh(self):
        self.runner.run_command(
            "存量模板分类与元数据刷新",
            [str(PYTHON_EXE), "-m", "contract_kb.cli", "refresh"],
        )

    def _cmd_rag_benchmark(self):
        self.runner.run_command(
            "RAG 检索对比评测基准实测",
            [str(PYTHON_EXE), "-m", "contract_kb.benchmark_retrieval"],
        )

    def _cmd_review_evaluate(self):
        self.runner.run_command(
            "端到端审核质量与延迟评估", [str(PYTHON_EXE), "-m", "contract_kb.evaluate"]
        )

    def _cmd_validate_contract_merged(self):
        self.runner.run_command(
            "contract_merged 整库独立全量复验",
            [
                str(PYTHON_EXE),
                "tools/validate_contract_merge_repair.py",
                "data/contract_merged",
            ],
        )

    def _cmd_test_merge_repair(self):
        self.runner.run_command(
            "两库合并核心修复逻辑回归测试",
            [str(PYTHON_EXE), "tests/test_contract_merge_repair.py"],
        )

    def _cmd_test_case_quality(self):
        self.runner.run_command(
            "导出质量规则与业务日志去重质量测试",
            [str(PYTEST_EXE), "tests/test_tender_case_quality.py", "-v"],
        )

    def _cmd_test_wedrive_rag_sync(self):
        self.runner.run_command(
            "企微/微盘同步 E01-E14 端到端用例",
            [str(PYTEST_EXE), "tests/test_wedrive_rag_sync_e2e.py", "-v"],
        )

    def _cmd_test_pipeline_stages(self):
        self.runner.run_command(
            "审查流水线核心算子测试 (Stages)",
            [str(PYTEST_EXE), "tests/unit/test_review_pipeline_stages.py", "-v"],
        )

    def _cmd_test_all_core(self):
        cmds = [
            (
                "两库合并逻辑回归",
                [str(PYTHON_EXE), "tests/test_contract_merge_repair.py"],
            ),
            (
                "三件套导出质量回归",
                [str(PYTEST_EXE), "tests/test_tender_case_quality.py", "-q"],
            ),
            (
                "企微同步 E01-E14 回归",
                [str(PYTEST_EXE), "tests/test_wedrive_rag_sync_e2e.py", "-q"],
            ),
            (
                "审查流水线算子回归",
                [str(PYTEST_EXE), "tests/unit/test_review_pipeline_stages.py", "-q"],
            ),
        ]
        self.runner.run_commands_sequential(cmds)

    def _browse_contract_file(self):
        initial_dir = PROJECT_ROOT / "data" / "tender_files"
        if not initial_dir.exists():
            initial_dir = PROJECT_ROOT
        fpath = filedialog.askopenfilename(
            title="选择待比对的送审合同 (.docx)",
            initialdir=str(initial_dir),
            filetypes=[("Word 文档", "*.docx"), ("所有文件", "*.*")],
        )
        if fpath:
            self.entry_doc_path.delete(0, tk.END)
            self.entry_doc_path.insert(0, fpath)

    def _cmd_run_diff(self):
        target = self.entry_doc_path.get().strip()
        if not target or not Path(target).exists():
            messagebox.showerror("错误", "请先选择有效的待比对合同 (.docx) 文件！")
            return
        cmd = [str(PYTHON_EXE), "tools/diff_contract.py", target]
        self.runner.run_command(f"微观条款比对 ({Path(target).name})", cmd)

    def _cmd_start_diff_web(self):
        port = self.entry_web_port.get().strip() or "8089"
        if self.web_process and self.web_process.poll() is None:
            messagebox.showinfo("提示", f"Diff Web 服务已经在运行中 (端口: {port})")
            return

        cmd = [str(PYTHON_EXE), "tools/contract_diff_web.py", "--port", port]
        try:
            self.web_process = subprocess.Popen(
                cmd,
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.btn_start_web.config(state=tk.DISABLED)
            self.btn_stop_web.config(state=tk.NORMAL)
            self._queue_log(
                f">>> [WEB] 条款比对 Web 服务已启动，端口: {port}\n>>> 地址: http://localhost:{port}\n",
                tag="success",
            )
            self._set_status(f"Diff Web 服务正在运行 (端口: {port})", is_busy=False)
        except Exception as e:
            messagebox.showerror("启动失败", f"无法启动 Web 服务: {e}")

    def _cmd_stop_diff_web(self):
        if self.web_process:
            try:
                self.web_process.terminate()
                self.web_process = None
                self._queue_log(">>> [WEB] 条款比对 Web 服务已停止。\n", tag="warning")
            except Exception as e:
                self._queue_log(f">>> 停止 Web 服务失败: {e}\n", tag="error")
        self.btn_start_web.config(state=tk.NORMAL)
        self.btn_stop_web.config(state=tk.DISABLED)
        self._set_status("就绪", is_busy=False)

    def _cmd_open_diff_web(self):
        port = self.entry_web_port.get().strip() or "8089"
        webbrowser.open(f"http://localhost:{port}")

    def _cmd_terminate(self):
        self.runner.terminate()

    def _cmd_clear_logs(self):
        self.txt_console.delete("1.0", tk.END)

    def _cmd_copy_logs(self):
        content = self.txt_console.get("1.0", tk.END)
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        messagebox.showinfo("提示", "控制台日志已成功复制到剪贴板。")


def main():
    root = tk.Tk()
    app = StartGUI(root)

    def on_closing():
        if app.runner.is_running:
            if not messagebox.askyesno(
                "确认退出", "当前仍有正在运行的任务，确认强制终止并退出吗？"
            ):
                return
            app.runner.terminate()
        if app.web_process:
            try:
                app.web_process.terminate()
            except Exception:
                pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
