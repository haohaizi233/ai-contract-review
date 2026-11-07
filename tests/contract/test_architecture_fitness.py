# -*- coding: utf-8 -*-
"""架构适应度契约测试（Architecture Fitness & Port Parity Contract Tests）。

持续守护 Clean Architecture 核心边界：
1. 零模块循环依赖（Tarjan SCC）；
2. 零 `contract_kb -> tools` 反向依赖；
3. 零仓储层外 `.conn.execute` 裸连接越界；
4. `domain/` 领域层零框架与外部 I/O 依赖；
5. `Storage`（SQLite）与 `PgVectorStorage`（PostgreSQL）公共仓储方法签名 100% 对齐；
6. 全仓跟踪文件零硬编码密钥/密码泄漏。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
import re
import subprocess

from contract_kb.storage import Storage
from contract_kb.storage_pg import PgVectorStorage

REPO_ROOT = Path(__file__).resolve().parents[2]
PKG_ROOT = REPO_ROOT / "contract_kb"


def _collect_internal_graph() -> tuple[dict[str, Path], dict[str, set[str]]]:
    mods: dict[str, Path] = {}
    for f in PKG_ROOT.rglob("*.py"):
        rel = f.relative_to(PKG_ROOT).with_suffix("")
        name = ".".join(p for p in rel.parts if p != "__init__") or "__init__"
        mods[name] = f

    edges: dict[str, set[str]] = {m: set() for m in mods}
    for m, f in mods.items():
        tree = ast.parse(f.read_text(encoding="utf-8"))
        pkg_parts = (
            m.split(".")[:-1]
            if f.name != "__init__.py"
            else (m.split(".") if m != "__init__" else [])
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    base = pkg_parts[: len(pkg_parts) - (node.level - 1)]
                    target = ".".join(base + ([node.module] if node.module else []))
                    for cand in (
                        target,
                        *[f"{target}.{n.name}" for n in node.names],
                    ):
                        if cand in mods and cand != m:
                            edges[m].add(cand)
                elif node.module and node.module.startswith("contract_kb."):
                    cand = node.module[len("contract_kb.") :]
                    if cand in mods and cand != m:
                        edges[m].add(cand)
    return mods, edges


def test_zero_circular_dependencies_in_contract_kb():
    mods, edges = _collect_internal_graph()
    idx_counter = [0]
    stack: list[str] = []
    lowlink: dict[str, int] = {}
    index: dict[str, int] = {}
    on_stack: set[str] = set()
    cycles: list[list[str]] = []

    def strongconnect(v: str) -> None:
        index[v] = idx_counter[0]
        lowlink[v] = idx_counter[0]
        idx_counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in edges.get(v, ()):
            if w not in index:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], index[w])
        if lowlink[v] == index[v]:
            scc: list[str] = []
            while True:
                w = stack.pop()
                on_stack.remove(w)
                scc.append(w)
                if w == v:
                    break
            if len(scc) > 1:
                cycles.append(scc)

    for v in mods:
        if v not in index:
            strongconnect(v)

    assert cycles == [], f"检测到模块循环依赖 SCC：{cycles}"


def test_zero_reverse_dependencies_and_zero_raw_sql_leaks():
    rev_deps: list[str] = []
    raw_sql: list[str] = []
    for f in PKG_ROOT.rglob("*.py"):
        src = f.read_text(encoding="utf-8")
        if f.name not in ("storage.py", "storage_pg.py") and ".conn.execute" in src:
            raw_sql.append(str(f.relative_to(REPO_ROOT)))
        if "template_5th_layer_data" in src:
            rev_deps.append(str(f.relative_to(REPO_ROOT)))
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and (node.module == "tools" or node.module.startswith("tools."))
            ):
                rev_deps.append(str(f.relative_to(REPO_ROOT)))
    assert rev_deps == [], f"核心包存在向 tools/ 的反向依赖：{rev_deps}"
    assert raw_sql == [], f"仓储层外存在 .conn.execute 裸 SQL 越界调用：{raw_sql}"


def test_domain_layer_purity():
    domain_dir = PKG_ROOT / "domain"
    forbidden_roots = {
        "fastapi",
        "starlette",
        "sqlite3",
        "psycopg2",
        "pymysql",
        "pytds",
        "urllib",
        "httpx",
        "requests",
    }
    violations: list[str] = []
    for f in domain_dir.rglob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root in forbidden_roots:
                        violations.append(f"{f.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                root = mod.split(".")[0]
                if (
                    root in forbidden_roots
                    or "infrastructure" in mod
                    or "interfaces" in mod
                ):
                    violations.append(f"{f.name}: from {mod} import ...")
    assert violations == [], f"domain 领域层违反纯净性约束：{violations}"


def test_storage_and_pgvector_storage_repository_contract_parity():
    sqlite_methods = {
        name: inspect.signature(fn)
        for name, fn in inspect.getmembers(Storage, predicate=inspect.isfunction)
        if not name.startswith("_")
    }
    pg_methods = {
        name: inspect.signature(fn)
        for name, fn in inspect.getmembers(
            PgVectorStorage, predicate=inspect.isfunction
        )
        if not name.startswith("_")
    }
    missing_in_pg = set(sqlite_methods) - set(pg_methods)
    assert missing_in_pg == set(), f"PgVectorStorage 缺失仓储接口方法：{missing_in_pg}"

    param_mismatches: list[str] = []
    for name, sq_sig in sqlite_methods.items():
        pg_sig = pg_methods[name]
        sq_params = list(sq_sig.parameters.keys())
        pg_params = list(pg_sig.parameters.keys())
        if sq_params != pg_params:
            param_mismatches.append(f"{name}: sqlite={sq_params} vs pg={pg_params}")
    assert param_mismatches == [], f"双存储实现参数签名不一致：{param_mismatches}"


def test_zero_secret_or_credential_leaks_in_tracked_files():
    secret_pat = re.compile(
        r"BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY|"
        r"ww(?!0000000000000000)[0-9a-f]{16}|"
        r"s\.w[0-9a-f]{16}\.[0-9A-Za-z_-]{16,}|"
        r"mysql(?:\.exe)?\s+.*-p[^<$\s\{\"']{4,}"
    )
    tracked = subprocess.check_output(
        ["git", "ls-files"], cwd=REPO_ROOT, text=True, encoding="utf-8"
    ).splitlines()
    hits: list[str] = []
    for rel in tracked:
        p = REPO_ROOT / rel
        if p.exists() and p.suffix in (
            ".py",
            ".md",
            ".yml",
            ".yaml",
            ".toml",
            ".json",
            ".sql",
        ):
            txt = p.read_text(encoding="utf-8", errors="ignore")
            if secret_pat.search(txt):
                hits.append(rel)
    assert hits == [], f"检测到疑似硬编码凭据或敏感标识：{hits}"
