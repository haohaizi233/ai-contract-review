# -*- coding: utf-8 -*-
"""Desktop GUI command builders and environment resolution."""

from __future__ import annotations

from pathlib import Path
import sys

# 项目根目录自动定位 (contract_kb/interfaces/desktop/commands.py -> parents[3])
PROJECT_ROOT = Path(__file__).resolve().parents[3]
PYTHON_EXE = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON_EXE.exists():
    PYTHON_EXE = Path(sys.executable)

PYTEST_EXE = PROJECT_ROOT / ".venv" / "Scripts" / "pytest.exe"
if not PYTEST_EXE.exists():
    PYTEST_EXE = PYTHON_EXE


def get_convert_doc_cmd() -> list[str]:
    """Build inline Python command to convert legacy .doc files to .docx."""
    code = "\n".join(
        [
            "import subprocess",
            "from pathlib import Path",
            "script = Path('tools/deploy/convert_legacy_doc.ps1').resolve()",
            "docs = sorted(f for f in Path('参考').rglob('*') if f.is_file() and f.suffix.lower() == '.doc' and not f.name.startswith('~'))",
            "print(f'共扫描到 {len(docs)} 个 .doc 待转换文件...', flush=True)",
            "converted = skipped = failed = 0",
            "for i, f in enumerate(docs, 1):",
            "    tgt = f.with_suffix('.docx')",
            "    if tgt.exists() and tgt.stat().st_size > 0 and tgt.stat().st_mtime >= f.stat().st_mtime:",
            "        skipped += 1",
            "        print(f'[{i}/{len(docs)}] OK (已存在最新 .docx): {f.name}', flush=True)",
            "        continue",
            "    cmd = ['powershell.exe', '-NoProfile', '-NonInteractive', '-File', str(script), '-Source', str(f.resolve()), '-Destination', str(tgt.resolve())]",
            "    p = subprocess.run(cmd, capture_output=True)",
            "    if p.returncode == 0:",
            "        converted += 1",
            "        print(f'[{i}/{len(docs)}] OK: {f.name}', flush=True)",
            "    else:",
            "        failed += 1",
            "        print(f'[{i}/{len(docs)}] FAIL: {f.name}', flush=True)",
            "print(f'转换任务处理完成: converted={converted} skipped={skipped} failures={failed}', flush=True)",
        ]
    )
    return [str(PYTHON_EXE), "-c", code]


def get_full_pipeline_cmds() -> list[tuple[str, list[str]]]:
    """Return sequential command list for full pipeline run."""
    return [
        ("老旧 .doc 转为 .docx", get_convert_doc_cmd()),
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
            [
                str(PYTHON_EXE),
                "-m",
                "contract_kb.evaluation.retrieval.benchmark_retrieval",
            ],
        ),
        (
            "端到端审核质量评估",
            [str(PYTHON_EXE), "-m", "contract_kb.evaluation.review.evaluate"],
        ),
    ]


def get_wedrive_sync_diff_cmd() -> list[str]:
    return [
        str(PYTHON_EXE),
        "tools/sync_wecom_wedrive.py",
        "--local-dir",
        "参考",
        "--sync-deletion",
        "--grace-seconds",
        "60",
    ]


def get_cli_import_cmd() -> list[str]:
    return [str(PYTHON_EXE), "-m", "contract_kb.cli", "import"]


def get_cli_refresh_cmd() -> list[str]:
    return [str(PYTHON_EXE), "-m", "contract_kb.cli", "refresh"]


def get_rag_benchmark_cmd() -> list[str]:
    return [
        str(PYTHON_EXE),
        "-m",
        "contract_kb.evaluation.retrieval.benchmark_retrieval",
    ]


def get_review_evaluate_cmd() -> list[str]:
    return [str(PYTHON_EXE), "-m", "contract_kb.evaluation.review.evaluate"]


def get_validate_contract_merged_cmd() -> list[str]:
    return [
        str(PYTHON_EXE),
        "tools/data_ops/validate_contract_merge_repair.py",
        "data/contract_merged",
    ]


def get_test_merge_repair_cmd() -> list[str]:
    return [str(PYTHON_EXE), "tests/test_contract_merge_repair.py"]


def get_test_case_quality_cmd() -> list[str]:
    return [str(PYTEST_EXE), "tests/test_tender_case_quality.py", "-v"]


def get_test_wedrive_rag_sync_cmd() -> list[str]:
    return [str(PYTEST_EXE), "tests/test_wedrive_rag_sync_e2e.py", "-v"]


def get_test_pipeline_stages_cmd() -> list[str]:
    return [str(PYTEST_EXE), "tests/unit/test_review_pipeline_stages.py", "-v"]


def get_test_all_core_cmds() -> list[tuple[str, list[str]]]:
    return [
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


def get_run_diff_cmd(target: str) -> list[str]:
    return [str(PYTHON_EXE), "tools/diff_contract.py", target]


def get_diff_web_cmd(port: str) -> list[str]:
    return [str(PYTHON_EXE), "tools/contract_diff_web.py", "--port", port]
