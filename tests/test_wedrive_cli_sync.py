import pytest
from docx import Document

from tools.sync_wecom_cli import CliClient, CliSync, scoped_files, fetch_inventory

PREFIX = "法务/商务合同模板/事务所"


def test_folder_boundary():
    rows = [
        {"path": PREFIX + "/a.docx", "type": "file"},
        {"path": PREFIX + "其他/a.docx", "type": "file"},
        {"path": PREFIX + "/../a.docx", "type": "file"},
    ]
    assert scoped_files(rows, PREFIX) == rows[:1]


def test_pagination():
    calls = []

    def call(args):
        calls.append(args)
        return {
            "files": [{"id": str(len(calls))}],
            "has_more": len(calls) == 1,
            "next_cursor": "page2" if len(calls) == 1 else "",
        }

    assert len(fetch_inventory(call, "法务")) == 2
    assert "--cursor" in calls[1]


def test_inventory_supplements_partial_unfiltered_search():
    c = CliClient("unused", "法务")

    def call(args, **kwargs):
        if "--keywords" in args:
            return {"files": [{"id": "old"}, {"id": "previously-missing"}]}
        return {"files": [{"id": "old"}]}

    c.call = call
    assert {x["id"] for x in c.inventory()} == {"old", "previously-missing"}


def test_cli_download_uses_returned_file_not_json_output(tmp_path):
    client = CliClient("unused", "法务")

    def call(args, **kwargs):
        assert "--output-dir" in args and "--output" not in args
        assert kwargs["cwd"] == tmp_path
        downloaded = tmp_path / "cloud-name.doc"
        downloaded.write_bytes(b"file bytes")
        return {"file_path": "\\\\?\\" + str(downloaded), "size": 10}

    client.call = call
    target = tmp_path / "incoming.doc"
    client.download({"id": "id"}, target)
    assert target.read_bytes() == b"file bytes"


@pytest.fixture
def env(tmp_path):
    f = tmp_path / "source.docx"

    def write(text):
        d = Document()
        d.add_paragraph("第一条 " + text)
        d.save(f)

    write("唯一付款约定甲方支付伍万元")
    row = {
        "id": "cloud-1",
        "path": PREFIX + "/合同.docx",
        "file_name": "合同.docx",
        "type": "file",
        "file_size": f.stat().st_size,
        "update_time": "v1",
    }

    class Fake:
        fail = False
        downloads = 0

        def inventory(self):
            return [row]

        def download(self, item, target):
            if self.fail:
                raise RuntimeError("simulated authorization failure")
            self.downloads += 1
            target.write_bytes(f.read_bytes())

    fake = Fake()
    sync = CliSync(
        tmp_path / "cache", PREFIX, fake, db_path=tmp_path / "kb.db", trusted=True
    )
    return sync, fake, row, f, write


def test_repeat_is_idempotent(env):
    s, c, row, f, write = env
    assert s.run()["changed"] == 1
    assert s.run()["skipped"] == 1
    assert c.downloads == 1


def test_update_replaces_old_active_and_retrieves(env):
    s, c, row, f, write = env
    s.run()
    write("唯一争议约定深圳国际仲裁院仲裁")
    row.update(file_size=f.stat().st_size, update_time="v2")
    assert s.run()["changed"] == 1
    rows = s.storage.conn.execute("SELECT status FROM documents").fetchall()
    assert sorted(r["status"] for r in rows) == ["ACTIVE", "SUPERSEDED"]
    assert s.storage.search("深圳国际仲裁院")
    from contract_kb.modules.retrieval.service import GovernedRetrievalService

    assert GovernedRetrievalService(s.storage).retrieve_authoritative(
        "深圳国际仲裁院", tenant_id="default"
    )


def test_untrusted_update_keeps_approved_version(env):
    s, c, row, f, write = env
    s.run()
    s.trusted = False
    write("新版本尚未批准")
    row.update(file_size=f.stat().st_size, update_time="v2")
    assert s.run()["changed"] == 1
    rows = s.storage.conn.execute("SELECT status FROM documents").fetchall()
    assert sorted(r["status"] for r in rows) == ["ACTIVE", "PENDING_REVIEW"]


def test_failure_preserves_version_and_retries(env):
    s, c, row, f, write = env
    s.run()
    row["update_time"] = "v2"
    c.fail = True
    assert s.run()["failed"] == 1
    assert s.manifest["cloud-1"]["update_time"] == "v1"
    assert s.storage.search("伍万元")
    c.fail = False
    assert s.run()["failed"] == 0


def test_size_mismatch_does_not_checkpoint(env):
    s, c, row, f, write = env
    row["file_size"] += 100
    assert s.run()["failed"] == 1
    assert not s.manifest


def test_partial_approval_failure_rolls_back_and_preserves_old(env, monkeypatch):
    s, c, row, f, write = env
    s.run()
    old_id = s.manifest["cloud-1"]["document_id"]
    write("新审批失败必须回滚")
    row.update(file_size=f.stat().st_size, update_time="v2")

    def broken(**kwargs):
        s.storage.conn.execute(
            "UPDATE documents SET status='SUPERSEDED' WHERE document_id=?", (old_id,)
        )
        s.storage.conn.execute(
            "UPDATE documents SET status='ACTIVE' WHERE document_id=?",
            (kwargs["document_id"],),
        )
        raise RuntimeError("approval_audit_failure")

    monkeypatch.setattr(s.storage, "approve_knowledge_version", broken)
    assert s.run()["failed"] == 1
    assert s.storage.get_document(old_id, tenant_id="default")["status"] == "ACTIVE"
    assert s.manifest["cloud-1"]["update_time"] == "v1"


def test_mirror_preserves_relative_path_and_restores_missing_without_reindex(
    env, tmp_path
):
    s, c, row, f, write = env
    s.mirror = tmp_path / "reference"
    row["path"] = PREFIX + "/专利/合同.docx"
    assert s.run()["changed"] == 1
    target = s.mirror / "专利/合同.docx"
    assert target.read_bytes() == f.read_bytes()
    target.unlink()
    assert s.run()["skipped"] == 1
    assert target.read_bytes() == f.read_bytes()
    assert c.downloads == 1


def test_mirror_local_edit_is_preserved_as_conflict(env, tmp_path):
    s, c, row, f, write = env
    s.mirror = tmp_path / "reference"
    s.run()
    target = s.mirror / "合同.docx"
    target.write_bytes(b"local edit")
    row["update_time"] = "v2"
    assert s.run()["failed"] == 1
    assert target.read_bytes() == b"local edit"
    assert s.manifest["cloud-1"]["update_time"] == "v1"
