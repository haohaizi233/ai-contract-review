from unittest.mock import Mock
from pathlib import Path
import shutil
import subprocess
import pytest

from contract_kb.interfaces.web.services import diff_service as service


def test_catalog_only_lists_current_registered_sources(monkeypatch, tmp_path):
    import hashlib

    source = tmp_path / "MB123_V2.0_正式合同.docx"
    source.write_bytes(b"current template")
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    current = dict(
        document_id="current",
        source_file=source.name,
        source_hash=sha,
        status="ACTIVE",
        business_category="PATENT",
    )
    rows = [
        dict(current, document_id="obsolete", source_hash="old hash"),
        dict(current, document_id="synthetic", source_file="synth_ip_framework.docx"),
        dict(current, document_id="archived", source_file="历史合同.docx"),
        dict(current, document_id="inactive", status="INACTIVE"),
        current,
        dict(current, document_id="duplicate"),
    ]
    storage = Mock()
    storage.list_documents.return_value = rows
    storage.find_active_template.return_value = current
    monkeypatch.setattr(service, "create_storage", lambda: storage)
    monkeypatch.setattr(service.config, "TEMPLATE_DIR", tmp_path)
    result = service.get_ranked_templates_catalog(tmp_path / "missing.docx")
    assert [row["document_id"] for row in result] == ["current"]
    assert result[0]["source_hash"] == sha
    assert result[0]["template_code"] == "MB123"


def test_empty_library_does_not_fall_back_to_database(monkeypatch, tmp_path):
    storage = Mock()
    storage.list_documents.return_value = [
        dict(document_id="test", source_file="synth_test.docx")
    ]
    storage.find_active_template.return_value = None
    monkeypatch.setattr(service, "create_storage", lambda: storage)
    monkeypatch.setattr(service.config, "TEMPLATE_DIR", tmp_path)
    assert service.get_ranked_templates_catalog(tmp_path / "missing.docx") == []


def test_current_template_uses_raw_id_and_content_hash():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required")
    source = Path("contract_kb/interfaces/web/static/js/api_client.js").read_text(
        encoding="utf-8"
    )
    helper = source.split("function isCurrentTemplate(")[1].split("\n}")[0]
    script = (
        "function isCurrentTemplate("
        + helper
        + "\n}\n"
        + """
const assert = require('node:assert/strict');
let currentData = {matched_template_id:'MB123', matched_template_raw_id:'raw', template_content_hash:'hash'};
assert.equal(isCurrentTemplate({document_id:'raw'}), true);
assert.equal(isCurrentTemplate({document_id:'alias',source_hash:'hash'}), true);
assert.equal(isCurrentTemplate({document_id:'older',template_code:'MB123',source_hash:'old'}), false);
currentData = {matched_template_id:'MB123'};
assert.equal(isCurrentTemplate({document_id:'raw',template_code:'MB123'}), true);
currentData = null;
assert.equal(isCurrentTemplate({document_id:'raw'}), false);
"""
    )
    result = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
