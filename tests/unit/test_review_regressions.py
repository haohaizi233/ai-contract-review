"""Regressions found in the 2026-11-10 review; all inputs are isolated fixtures."""

from types import SimpleNamespace

import pytest

from contract_kb.modules.contract_comparison import full_diff
from contract_kb.domain.models.source_node import ContractSourceNode


def table_node():
    return ContractSourceNode(
        document_id="std",
        document_hash="hash",
        node_id="std:tbl0:r0:c0",
        node_type="table_cell",
        table_index=0,
        row_index=0,
        column_index=0,
        original_text="应支付服务费",
    )


@pytest.mark.parametrize(
    "category,source_ids",
    [
        ("OTHER", ["std:p8"]),
        ("TABLE_NOTE", ["std:p8"]),
        ("OTHER", ["std:tbl0:r0:c0", "std:p8"]),
    ],
)
def test_table_dedup_keeps_body_obligation_and_annotation(category, source_ids):
    block = {
        "block_id": "body",
        "clause_category": category,
        "standard_text": "应支付服务费",
        "reviewed_text": "无需支付服务费",
        "standard_source_node_ids": source_ids,
    }
    annotation = {"id": "risk", "block_id": "body", "index": 1}
    blocks, annotations = full_diff._enhance_diff_tables(
        [block], [annotation], [table_node()], [table_node()]
    )
    assert any(b["block_id"] == "body" for b in blocks)
    assert any(a["id"] == "risk" for a in annotations)


def test_table_dedup_removes_only_proven_table_fragments():
    block = {
        "block_id": "fragment",
        "standard_source_node_ids": ["std:tbl0:r0:c0"],
        "standard_text": "应支付服务费",
    }
    blocks, annotations = full_diff._enhance_diff_tables(
        [block],
        [{"id": "old", "block_id": "fragment", "index": 1}],
        [table_node()],
        [table_node()],
    )
    assert not any(b["block_id"] == "fragment" for b in blocks)
    assert not any(a["id"] == "old" for a in annotations)


def test_missing_matching_template_never_uses_arbitrary_inactive_record(
    tmp_path, monkeypatch
):
    path = tmp_path / "review.docx"
    path.touch()
    monkeypatch.setattr(
        full_diff, "security_check_file", lambda p: SimpleNamespace(passed=True)
    )
    monkeypatch.setattr(
        full_diff,
        "import_template_file",
        lambda p: (
            SimpleNamespace(sha256="new", subtype="CONFIDENTIALITY", category="NDA"),
            None,
        ),
    )

    class Store:
        def find_document_by_hash(self, value):
            return None

        def find_active_template(self, **kwargs):
            return None

        def list_documents(self):
            return [
                {
                    "document_id": "MB_INACTIVE",
                    "status": "INACTIVE",
                    "business_category": "PATENT",
                }
            ]

        def get_document_clauses(self, document_id):
            pytest.fail("an unrelated inactive record was selected as the baseline")

    with pytest.raises(ValueError, match="母本|模板"):
        full_diff.build_full_contract_diff(path, storage=Store())


def test_material_card_delegates_default_attachment_choice_to_backend():
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if not node:
        pytest.skip("Node required")
    source = Path("tools/contract_diff_web.py").read_text(encoding="utf-8")
    render = (
        "function renderMaterialCases(data) {"
        + source.split("function renderMaterialCases(data) {")[1].split(
            "function loadMaterialContract("
        )[0]
    )
    harness = r"""
    const assert = require('node:assert/strict');
    let cards = [], chosen;
    const document = { getElementById: () => ({appendChild: card => cards.push(card)}), createElement: () => ({}) };
    const escapeHtml = text => text;
    const loadMaterialContract = (...args) => { chosen = args; };
    /* RENDER */
    renderMaterialCases({cases:[{no:'CASE1', attachments:[{ext:'.docx',local:'drawing.docx'},{ext:'.docx',local:'contract.docx'}]}]});
    const onclick = cards[0].innerHTML.match(/onclick="(loadMaterialContract\([^"]+)"/)[1];
    eval(onclick);
    assert.equal(chosen[0], 'CASE1');
    assert.ok(!chosen[1], 'default load must let the backend rank contract attachments');
    """
    result = subprocess.run(
        [node, "-e", harness.replace("/* RENDER */", render)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
