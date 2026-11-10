"""Execute the shipped inline scroll controller in Node, without a web server."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_three_way_scroll_controller():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for inline JavaScript regression tests')
    source = Path('tools/contract_diff_web.py').read_text(encoding='utf-8')
    script = source.split('// ==================== 三栏双向协同联动滚动 ====================')[1].split('function renderAnnotationsSidebar()')[0]
    harness = Path('tests/unit/scroll_controller_harness.cjs').read_text(encoding='utf-8')
    result = subprocess.run([node, '-e', harness.replace('/* CONTROLLER */', script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
