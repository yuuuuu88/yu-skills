from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_nous.engine import AgentContext


def test_external_context_carries_correlated_trace_identifiers_without_payload_data() -> None:
    context = AgentContext(
        task_id="task-trace",
        phase="implement",
        cycle=2,
        definition={"objective": "trace the loop"},
        state={"revision": 3},
        stable_context={},
        active_artifacts={},
        repository_root="/repo",
        trace_id="0123456789abcdef0123456789abcdef",
        span_id="1111111111111111",
        parent_span_id="2222222222222222",
    )

    trace = context.payload()["trace_context"]

    assert trace == {
        "trace_id": "0123456789abcdef0123456789abcdef",
        "span_id": "1111111111111111",
        "parent_span_id": "2222222222222222",
        "format": "w3c-trace-context-compatible",
    }
    assert "prompt" not in trace
    assert "secret" not in trace
