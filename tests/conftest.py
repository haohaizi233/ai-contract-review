# -*- coding: utf-8 -*-
"""测试公共夹具（Phase 0 安全治理后）：

- 默认使用**合成语料**（contract_kb.evaluation.datasets.synthetic 生成，不含真实企业信息），CI/公开环境安全；
- 真实模板回归测试仅在显式授权环境变量下启用：
      CONTRACT_KB_REAL_CORPUS=1  且 CONTRACT_KB_TEMPLATE_DIR 指向受控真实模板目录
  真实用例统一打 @pytest.mark.real_corpus，未授权时自动 skip。
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from contract_kb import config  # noqa: E402

REAL_CORPUS_ENABLED = os.environ.get("CONTRACT_KB_REAL_CORPUS") == "1"


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "real_corpus: 需授权真实模板语料的回归用例（默认跳过）"
    )
    config.addinivalue_line(
        "markers", "real_model: 需授权真实 LLM 端点的受控 E2E（默认跳过）"
    )


@pytest.fixture(scope="session")
def workspace_ready():
    config.ensure_dirs()
    return config


@pytest.fixture(scope="session")
def corpus_dir(workspace_ready) -> Path:
    """测试语料目录：默认合成语料。"""
    from contract_kb.evaluation.datasets.synthetic import generate_corpus

    d = ROOT / "tests" / "fixtures" / "synth_corpus"
    generate_corpus(d)
    return d


@pytest.fixture(scope="session")
def import_result(workspace_ready, corpus_dir, tmp_path_factory):
    """导入合成语料到独立测试数据库（不触碰 data/ 生产库）。"""
    from contract_kb.ingest import scan_and_import

    result = scan_and_import(corpus_dir)
    result.pop("_raw", None)
    return result


@pytest.fixture(scope="session")
def layer_a(workspace_ready, corpus_dir, import_result):
    """合成语料的条款/Schema 层。"""
    from contract_kb.ingest import scan_and_import

    result = scan_and_import(corpus_dir)
    layer = {"clauses": [], "table_schemas": [], "documents": []}
    for d in result.get("_raw", []):
        layer["clauses"].extend(d.clauses)
        layer["table_schemas"].extend(d.table_schemas)
        layer["documents"].append(d.to_dict())
    if not layer["clauses"]:
        pytest.skip("合成语料条款抽取为空")
    return layer


@pytest.fixture(scope="session")
def parsed_docs(workspace_ready, corpus_dir):
    """AST 文档集合（合成语料）。"""
    from contract_kb.docx_parser import parse_docx

    docs = {}
    for f in sorted(corpus_dir.glob("*.docx")):
        if not f.name.startswith("~"):
            docs[f.name] = parse_docx(f)
    return docs


@pytest.fixture(scope="session")
def storage(workspace_ready, corpus_dir, tmp_path_factory):
    """独立测试存储（临时 SQLite，session 级）。"""
    from contract_kb.storage import Storage
    from contract_kb.ingest import scan_and_import

    db = tmp_path_factory.mktemp("db") / "test_contract_kb.db"
    st = Storage(db)
    result = scan_and_import(corpus_dir)
    for d in result.get("_raw", []):
        did, _ = st.upsert_document(d.to_dict(), tenant="t1")
        st.approve_knowledge_version(
            tenant_id="t1",
            document_id=did,
            approver_id="legal_admin",
            approval_reason="合成标准模板授权测试基准",
        )
    yield st
    st.close()


@pytest.fixture(scope="session")
def golden_samples(workspace_ready):
    from contract_kb.evaluation.datasets.golden import generate_all

    out = ROOT / "tests" / "fixtures" / "golden"
    manifest = generate_all(out)
    return {"dir": out, "items": manifest}


# ---------------- 真实语料（授权回归） ----------------


@pytest.fixture(scope="session")
def real_corpus_dir():
    if not REAL_CORPUS_ENABLED:
        pytest.skip("真实语料回归未授权（CONTRACT_KB_REAL_CORPUS=1 启用）")
    d = config.TEMPLATE_DIR
    if not d.exists():
        pytest.skip(f"真实模板目录不存在：{d}")
    return d


def is_real_corpus_marked(request) -> bool:
    return any(m.name == "real_corpus" for m in request.node.own_markers)


def pytest_runtest_setup(item):
    if any(m.name == "real_corpus" for m in item.iter_markers()):
        if not REAL_CORPUS_ENABLED:
            pytest.skip("真实语料回归未授权（需设置 CONTRACT_KB_REAL_CORPUS=1）")
