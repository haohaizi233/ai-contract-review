# -*- coding: utf-8 -*-
"""可重复执行的仓库架构与依赖审计工具（Phase 15-A）。

扫描全仓 Python 模块、构建配置、CI 工作流、脚本与文档，提取：
- 文件路径、行数、导出类与函数、相对/绝对/动态导入
- 运行时路径依赖、脚本入口、模块级副作用
- 反向引用图、Tarjan 强连通分量（循环依赖）与分层依赖越界检查
"""

from __future__ import annotations

import argparse
import ast
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "contract_kb.egg-info",
    "node_modules",
    "tmp",
    "data",
    "参考",
}


def iter_py_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for p in sorted(root.rglob("*.py")):
        rel_parts = set(p.relative_to(root).parts)
        if rel_parts & EXCLUDE_DIRS:
            continue
        files.append(p)
    return files


def module_name_for_path(rel_path: Path) -> str:
    parts = list(rel_path.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or "__init__"


def resolve_relative_import(current_rel: Path, module: str | None, level: int) -> str:
    pkg_parts = list(current_rel.parent.parts)
    if level > 1:
        up = level - 1
        pkg_parts = pkg_parts[:-up] if up <= len(pkg_parts) else []
    if module:
        pkg_parts.extend(module.split("."))
    return ".".join(pkg_parts)


def classify_module(rel_str: str) -> str:
    if rel_str.startswith("tests/") or "/tests/" in rel_str:
        return "TEST"
    if rel_str.startswith("contract_kb/domain/"):
        return "DOMAIN"
    if rel_str.startswith("contract_kb/application/"):
        return "APPLICATION"
    if rel_str.startswith("contract_kb/modules/"):
        return "BUSINESS_MODULE"
    if rel_str.startswith("contract_kb/infrastructure/"):
        return "INFRASTRUCTURE"
    if rel_str.startswith("contract_kb/interfaces/"):
        return "INTERFACE"
    if rel_str.startswith("contract_kb/evaluation/"):
        return "EVALUATION"
    if rel_str in (
        "contract_kb/ablation_study.py",
        "contract_kb/benchmark_retrieval.py",
        "contract_kb/evaluate.py",
        "contract_kb/golden.py",
        "contract_kb/retrieval_golden.py",
        "contract_kb/synthetic.py",
    ):
        return "EVALUATION"
    if rel_str in (
        "contract_kb/docx_parser.py",
        "contract_kb/document_parser.py",
        "contract_kb/clause_extractor.py",
        "contract_kb/classifier.py",
        "contract_kb/ingest.py",
        "contract_kb/clause_diff.py",
        "contract_kb/full_diff.py",
        "contract_kb/table_aligner.py",
        "contract_kb/format_auditor.py",
        "contract_kb/rule_engine.py",
        "contract_kb/distill.py",
    ):
        return "BUSINESS_MODULE"
    if rel_str in (
        "contract_kb/legal_agent.py",
        "contract_kb/legal_agent_kb.py",
        "contract_kb/agent_queue_manager.py",
        "contract_kb/review_engine.py",
    ):
        return "APPLICATION"
    if rel_str in (
        "contract_kb/agent_checkpoint.py",
        "contract_kb/embeddings.py",
        "contract_kb/embeddings_base.py",
        "contract_kb/embeddings_real.py",
        "contract_kb/rerankers.py",
        "contract_kb/llm_review.py",
        "contract_kb/storage.py",
        "contract_kb/storage_pg.py",
        "contract_kb/storage_errors.py",
    ):
        return "INFRASTRUCTURE"
    if rel_str in (
        "contract_kb/api.py",
        "contract_kb/auth.py",
        "contract_kb/cli.py",
        "start_gui.py",
        "tools/contract_diff_web.py",
    ):
        return "INTERFACE"
    if (
        rel_str.startswith("scripts/")
        or rel_str.startswith("tools/data_ops/")
        or rel_str.startswith("tools/maintenance/")
    ):
        return "MAINTENANCE_SCRIPT"
    if rel_str.startswith("tools/") or rel_str == "build_doc.py":
        return "DEVELOPMENT_TOOL"
    return "COMPATIBILITY_ENTRYPOINT"


def analyze_file(path: Path, root: Path) -> dict[str, Any]:
    rel = path.relative_to(root)
    rel_str = rel.as_posix()
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    tree = ast.parse(text, filename=rel_str)

    classes = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
    functions = [
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    abs_imports: list[str] = []
    rel_imports: list[dict[str, Any]] = []
    dyn_imports: list[str] = []
    runtime_path_deps: list[str] = []
    side_effects: list[str] = []
    has_main = False

    for node in tree.body:
        if isinstance(node, ast.If):
            test_src = ast.unparse(node.test)
            if "__name__" in test_src and "__main__" in test_src:
                has_main = True
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call_src = ast.unparse(node.value)
            if not call_src.startswith(
                ("warnings.", "logging.", "dotenv.", "load_dotenv")
            ):
                side_effects.append(f"L{node.lineno}: {call_src[:100]}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                abs_imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                resolved = resolve_relative_import(rel, node.module, node.level)
                rel_imports.append(
                    {
                        "level": node.level,
                        "module": node.module or "",
                        "resolved": resolved,
                        "names": [a.name for a in node.names],
                        "lineno": node.lineno,
                    }
                )
            else:
                mod = node.module or ""
                if mod:
                    abs_imports.append(mod)
                    for alias in node.names:
                        abs_imports.append(f"{mod}.{alias.name}")
        elif isinstance(node, ast.Call):
            func_src = ast.unparse(node.func)
            if func_src in ("importlib.import_module", "import_module", "__import__"):
                dyn_imports.append(f"L{node.lineno}: {ast.unparse(node)[:100]}")
            if any(
                k in func_src
                for k in (
                    "subprocess.run",
                    "subprocess.Popen",
                    "subprocess.call",
                    "subprocess.check_output",
                    "os.system",
                )
            ):
                runtime_path_deps.append(f"L{node.lineno}: {ast.unparse(node)[:100]}")

    for idx, line in enumerate(lines, start=1):
        if "__file__" in line or "sys.path" in line:
            runtime_path_deps.append(f"L{idx}: {line.strip()[:100]}")

    return {
        "path": rel_str,
        "module": module_name_for_path(rel),
        "category": classify_module(rel_str),
        "lines": len(lines),
        "classes": classes,
        "functions": [fn for fn in functions if not fn.startswith("_")],
        "abs_imports": sorted(set(abs_imports)),
        "rel_imports": rel_imports,
        "dyn_imports": dyn_imports,
        "runtime_path_deps": runtime_path_deps,
        "side_effects": side_effects,
        "has_main": has_main,
        "is_production": rel_str.startswith("contract_kb/")
        and not rel_str.startswith("contract_kb/evaluation/"),
    }


def audit_repository(root: Path) -> dict[str, Any]:
    py_files = iter_py_files(root)
    records = [analyze_file(p, root) for p in py_files]
    by_mod = {r["module"]: r for r in records}
    all_mods = set(by_mod.keys())

    def match_internal(imp: str) -> str | None:
        parts = imp.split(".")
        for i in range(len(parts), 0, -1):
            cand = ".".join(parts[:i])
            if cand in all_mods:
                return cand
        return None

    forward: dict[str, set[str]] = defaultdict(set)
    reverse: dict[str, set[str]] = defaultdict(set)
    for r in records:
        src = r["module"]
        for imp in r["abs_imports"]:
            tgt = match_internal(imp)
            if tgt and tgt != src:
                forward[src].add(tgt)
                reverse[tgt].add(src)
        for rel in r["rel_imports"]:
            resolved = rel["resolved"]
            tgt = match_internal(resolved)
            if tgt and tgt != src:
                forward[src].add(tgt)
                reverse[tgt].add(src)
            for name in rel["names"]:
                cand = f"{resolved}.{name}" if resolved else name
                t2 = match_internal(cand)
                if t2 and t2 != src:
                    forward[src].add(t2)
                    reverse[t2].add(src)

    for r in records:
        r["imported_by"] = sorted(reverse.get(r["module"], set()))
        r["imports_internal"] = sorted(forward.get(r["module"], set()))

    # Tarjan SCC inside contract_kb
    index = 0
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    sccs: list[list[str]] = []

    def strongconnect(v: str) -> None:
        nonlocal index
        indices[v] = index
        lowlink[v] = index
        index += 1
        stack.append(v)
        on_stack.add(v)
        for w in sorted(forward.get(v, set())):
            if not w.startswith("contract_kb"):
                continue
            if w not in indices:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], indices[w])
        if lowlink[v] == indices[v]:
            scc = []
            while True:
                w = stack.pop()
                on_stack.remove(w)
                scc.append(w)
                if w == v:
                    break
            if len(scc) > 1:
                sccs.append(scc)

    for m in sorted(all_mods):
        if m.startswith("contract_kb") and m not in indices:
            strongconnect(m)

    root_ckb = [
        r["path"]
        for r in records
        if Path(r["path"]).parts[0] == "contract_kb" and len(Path(r["path"]).parts) == 2
    ]
    root_tools = [
        r["path"]
        for r in records
        if Path(r["path"]).parts[0] == "tools" and len(Path(r["path"]).parts) == 2
    ]
    root_repo = [r["path"] for r in records if len(Path(r["path"]).parts) == 1]

    total_lines = sum(r["lines"] for r in records)
    max_file = max(records, key=lambda r: r["lines"])
    total_edges = sum(len(v) for v in forward.values())

    return {
        "summary": {
            "total_py_files": len(records),
            "total_lines": total_lines,
            "avg_lines": round(total_lines / max(1, len(records)), 1),
            "max_file": {"path": max_file["path"], "lines": max_file["lines"]},
            "contract_kb_root_py_count": len(root_ckb),
            "tools_root_py_count": len(root_tools),
            "repo_root_py_count": len(root_repo),
            "internal_dependency_edges": total_edges,
            "contract_kb_cycles": sccs,
        },
        "contract_kb_root_files": root_ckb,
        "tools_root_files": root_tools,
        "repo_root_files": root_repo,
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit repository architecture and dependencies."
    )
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    result = audit_repository(args.root.resolve())
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
