"""Regression checks for byte-exact legacy image recovery and safe publication."""

import hashlib
import io
import os

import pytest
from PIL import Image

from tools.reports import compile_b64_to_png as legacy
from tools.data_ops import recover_e9_ledger_assets as recovery


def source_image_bytes(mode):
    image = Image.new(mode, (3, 2))
    values = {
        "1": [0, 255, 0, 255, 0, 255],
        "L": [0, 37, 128, 170, 230, 255],
        "P": [0, 1, 2, 1, 2, 0],
        "RGB": [(1, 2, 3), (60, 70, 80), (250, 200, 150)] * 2,
        "RGBA": [(1, 2, 3, 0), (60, 70, 80, 128), (250, 200, 150, 255)] * 2,
        "CMYK": [(0, 10, 20, 30), (80, 0, 20, 5), (20, 40, 0, 100)] * 2,
    }
    image.putdata(values[mode])
    if mode == "P":
        image.putpalette([0, 0, 0, 255, 80, 20, 20, 120, 255] + [0] * 759)
        image.info["transparency"] = 0
    buffer = io.BytesIO()
    image.save(buffer, format="TIFF" if mode == "CMYK" else "PNG")
    return buffer.getvalue()


@pytest.mark.parametrize("mode", ["L", "1", "P", "RGB", "RGBA", "CMYK"])
def test_variants_include_byte_exact_legacy_png(mode, tmp_path):
    raw = source_image_bytes(mode)
    expected_file = tmp_path / "legacy.png"
    legacy.convert_and_save_png(raw, [expected_file])

    variants = list(recovery.variants(raw))

    assert expected_file.read_bytes() in variants


def test_publication_quarantines_partial_target_and_restores_it(tmp_path):
    target = tmp_path / "albums" / "asset.png"
    target.parent.mkdir()
    partial = b"incomplete prior write"
    target.write_bytes(partial)
    data = source_image_bytes("RGB")

    recovery.publish_verified_asset(target, data, hashlib.sha256(data).hexdigest())

    assert target.read_bytes() == data
    preserved = [
        path
        for path in tmp_path.rglob("*")
        if path.is_file() and path != target and path.read_bytes() == partial
    ]
    assert preserved, "The prior partial file must remain available for diagnosis"


def test_publication_keeps_existing_correct_target_unchanged(tmp_path):
    target = tmp_path / "asset.png"
    data = source_image_bytes("RGBA")
    target.write_bytes(data)
    os.utime(target, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
    before = target.stat()

    recovery.publish_verified_asset(target, data, hashlib.sha256(data).hexdigest())

    after = target.stat()
    assert target.read_bytes() == data
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ino == before.st_ino
    assert list(tmp_path.iterdir()) == [target]


def test_publication_rejects_wrong_bytes_without_creating_target(tmp_path):
    target = tmp_path / "asset.png"
    expected = hashlib.sha256(b"expected source bytes").hexdigest()

    with pytest.raises(ValueError):
        recovery.publish_verified_asset(target, b"wrong source bytes", expected)

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []
