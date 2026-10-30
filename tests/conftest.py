# -*- coding: utf-8 -*-
"""测试公共夹具：真实 26 模板导入一次（session 级缓存）。"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from contract_kb import config  # noqa: E402


@pytest.fixture(scope="session")
def workspace_ready():
    config.ensure_dirs()
    return config


@pytest.fixture(scope="session")
def import_result(workspace_ready):
    p = config.DATA_DIR / "import_result.json"
    if not p.exists():
        from contract_kb.ingest import scan_and_import
        result = scan_and_import(config.TEMPLATE_DIR)
        result.pop("_raw", None)
        p.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return json.loads(p.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def layer_a(workspace_ready, import_result):
    p = config.DATA_DIR / "layer_a_raw.json"
    if not p.exists():
        pytest.skip("layer_a_raw.json 未生成，请先运行 python -m contract_kb.cli import")
    return json.loads(p.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def parsed_docs(workspace_ready, import_result):
    """AST 文档集合（真实模板）。"""
    from contract_kb.docx_parser import parse_docx
    docs = {}
    for f in sorted(config.TEMPLATE_DIR.iterdir()):
        if f.suffix.lower() == ".docx" and not f.name.startswith("~"):
            docs[f.name] = parse_docx(f)
    return docs


@pytest.fixture(scope="session")
def storage(workspace_ready, import_result):
    from contract_kb.storage import Storage
    st = Storage(config.DB_PATH)
    docs = json.loads((config.DATA_DIR / "import_result.json").read_text(encoding="utf-8"))
    layer = json.loads((config.DATA_DIR / "layer_a_raw.json").read_text(encoding="utf-8"))
    by_tid = {d["template_id"]: d for d in layer["documents"]}
    for d in docs["imported"]:
        merged = dict(d)
        merged["clauses"] = by_tid.get(d["template_id"], {}).get("clauses", d.get("clauses", []))
        st.upsert_document(merged)
    yield st
    st.close()


@pytest.fixture(scope="session")
def golden_samples(workspace_ready):
    from contract_kb.golden import generate_all
    return generate_all()
