from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_distribution_guard_rejects_no_runtime_paths() -> None:
    if not (ROOT / ".git").exists():
        pytest.skip("managed validation snapshots intentionally omit Git metadata")
    result = subprocess.run(
        [sys.executable, "scripts/check_distribution.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
