from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    manifest = ROOT / "src" / "agent_nous" / "eval" / "capability_manifest.json"
    schema = ROOT / "src" / "agent_nous" / "eval" / "capability_manifest.schema.json"
    for path in (manifest, schema, ROOT / "design" / "capability-evaluation.md"):
        if not path.is_file():
            raise SystemExit(f"required capability artifact missing: {path}")
    basetemp = ROOT / ".nous-capability-review-tmp"
    try:
        return pytest.main([
            "-q", "-p", "no:cacheprovider", "--basetemp", str(basetemp),
            "tests/test_capability_evaluation.py",
            "tests/test_capability_runner.py",
            "tests/test_eval_gate.py::test_manifest_registers_every_v1_case_at_stage_one",
        ])
    finally:
        shutil.rmtree(basetemp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
