from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))

def main() -> int:
    required=(ROOT/'src/agent_nous/eval/capability.py',ROOT/'src/agent_nous/eval/capability_runner.py',ROOT/'src/agent_nous/eval/capability_ops.py',ROOT/'scripts/nous_capability_gate.py',ROOT/'design/capability-evaluation.md')
    for path in required:
        if not path.is_file(): raise SystemExit(f'required capability artifact missing: {path}')
    basetemp=ROOT/'.nous-capability-production-review-tmp'
    try:
        return pytest.main(['-q','-p','no:cacheprovider','--basetemp',str(basetemp),'tests/test_capability_evaluation.py','tests/test_capability_runner.py','tests/test_capability_production.py','tests/test_eval_gate.py::test_manifest_registers_every_v1_case_at_stage_one'])
    finally:
        shutil.rmtree(basetemp,ignore_errors=True)
if __name__=='__main__': raise SystemExit(main())
