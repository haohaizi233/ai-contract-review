from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tools.data_ops import live_db_merge as m


def test_existing_merge_output_is_never_deleted():
    with TemporaryDirectory() as tmp:
        out = Path(tmp) / "existing"
        (out / "cases").mkdir(parents=True)
        sentinel = out / "cases/important.md"
        sentinel.write_text("preserve")
        with patch.multiple(
            m,
            OUT_DIR=out,
            CASES_OUT=out / "cases",
            ATTACH_OUT=out / "attachments",
            ALBUM_OUT=out / "albums",
        ):
            try:
                m.execute_union_merge(*[{} for _ in range(10)])
            except FileExistsError:
                pass
        assert sentinel.read_text() == "preserve"


def test_new_merge_output_can_be_created():
    with TemporaryDirectory() as tmp:
        out = Path(tmp) / "new"
        with patch.multiple(
            m,
            OUT_DIR=out,
            CASES_OUT=out / "cases",
            ATTACH_OUT=out / "attachments",
            ALBUM_OUT=out / "albums",
        ):
            m.execute_union_merge(*[{} for _ in range(10)])
        assert (out / "cases").is_dir()
