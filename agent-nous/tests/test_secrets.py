from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_nous.domain import secret_summary


def test_secret_gate_does_not_block_runtime_token_expressions() -> None:
    runtime_expression = "to" + 'ken = str(lease["token"])'
    request_expression = "to" + "ken = request_" + "token"
    assert secret_summary({"content": runtime_expression}) == {}
    assert secret_summary({"content": request_expression}) == {}


def test_secret_gate_still_blocks_credential_like_literals() -> None:
    result = secret_summary({"content": "api_key = " + "a" * 16})
    assert result["detector_ids"] == ["credential_assignment"]
