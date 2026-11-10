import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.data_ops.contract_merge_repair import (  # noqa: E402
    group_sources,
    ResourceStore,
    rewrite_body,
    render_gaps,
    MD_LINK,
)
from PIL import Image


def test_blank_and_multiple_applications_survive():
    groups = group_sources(
        [],
        [
            {"apply_id": "1", "contract_no": ""},
            {"apply_id": "2", "contract_no": "ABC2025"},
            {"apply_id": "3", "contract_no": "ABC2025"},
        ],
    )
    assert len(groups) == 2
    assert {r["apply_id"] for g in groups.values() for r in g["relations"]} == {
        "1",
        "2",
        "3",
    }


def test_body_and_parenthesized_links_survive(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    (src / "a (1).pdf").write_bytes(b"%PDF-1.7 example")
    store = ResourceStore(tmp_path / "out")
    body = "### 审批人 张三 2025-01-01\n同意，但须补件\n[合同](a (1).pdf)"
    result = rewrite_body(body, src / "case.md", store, "case", "source")
    assert "同意，但须补件" in result
    assert "../attachments/" in result
    assert len(store.refs) == 1
    assert store.refs[0]["status"] == "local"


def test_jpeg_is_really_converted_to_png(tmp_path):
    src = tmp_path / "wrong.png"
    Image.new("RGB", (2, 2)).save(src, format="JPEG")
    store = ResourceStore(tmp_path / "out")
    target = store.add(src, "c", "opinion", image=True)
    with Image.open(store.out / target) as im:
        assert im.format == "PNG"


def test_missing_resource_is_a_gap(tmp_path):
    store = ResourceStore(tmp_path / "out")
    result = rewrite_body("[附件](missing.docx)", tmp_path / "case.md", store, "c", "s")
    assert "未取得" in result
    assert len(store.gaps) == 1
    assert store.refs[0]["status"] == "missing"


def test_old_export_root_relative_attachment(tmp_path):
    root = tmp_path / "e9-opinion-export"
    source = root / "ABC" / "ABC2025.md"
    source.parent.mkdir(parents=True)
    attachment = root / "attachments" / "ABC2025" / "1.pdf"
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b"%PDF-1.7 source evidence")
    store = ResourceStore(tmp_path / "out")
    result = rewrite_body(
        "[旧附件](attachments/ABC2025/1.pdf)", source, store, "ABC2025", "e9:1"
    )
    assert "../attachments/" in result
    assert not store.gaps


def test_same_content_different_names_has_one_entity(tmp_path):
    first, second = tmp_path / "original.docx", tmp_path / "legacy.bin"
    first.write_bytes(b"PK source document")
    second.write_bytes(first.read_bytes())
    store = ResourceStore(tmp_path / "out")
    assert store.add(first, "a", "new") == store.add(second, "a", "old")
    assert len(list((store.out / "attachments").iterdir())) == 1
    assert len(store.refs) == 2


def test_gap_filename_does_not_create_a_fake_link():
    lines = render_gaps(
        [{"source": "apply:1", "reason": "附件名[简称](V3.0)未取得", "original": ""}]
    )
    assert not MD_LINK.search("\n".join(lines))


if __name__ == "__main__":
    import tempfile

    for name, function in sorted(list(globals().items())):
        if name.startswith("test_") and callable(function):
            if function.__code__.co_argcount:
                with tempfile.TemporaryDirectory() as directory:
                    function(Path(directory))
            else:
                function()
            print("PASS", name)
