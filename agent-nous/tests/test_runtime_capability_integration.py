from __future__ import annotations

import json
import tempfile
from pathlib import Path

from agent_nous.cli import _prepare_engine_payload
from agent_nous.engine import AgentContext, AgentOutput, AutonomousEngine, CallableAgentBackend
from agent_nous.engine_config import load_engine_config
from agent_nous.observability import InMemoryTraceExporter
from agent_nous.resources import ExecutionResourcePolicy, ResourceBudget
from agent_nous.taskctl import TaskStore

def test_config_round_trips_portable_runtime_options() -> None:
    with tempfile.TemporaryDirectory(prefix="nous-runtime-config-", dir="/tmp") as raw:
        tmp_path = Path(raw)
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({"engine": {
            "agents": {"plan": "plan", "implement": "implement", "review": "review"},
            "allowed_processes": ["true"],
            "resources": {"max_concurrent_agents": 2, "max_output_bytes": 2048, "max_tool_calls": 3, "max_wall_seconds": 20},
            "durability": {"mode": "local"},
            "observability": {"trace_export_path": "runtime/trace.jsonl", "max_event_bytes": 2048},
        }}), encoding="utf-8")
        config = load_engine_config(config_path)
        args = type("Args", (), {"engine_config": str(config_path), "config": None})()
        payload = _prepare_engine_payload(args, {})
        assert config.resources["max_output_bytes"] == 2048
        assert payload["durability"]["mode"] == "local"
        assert payload["observability"]["trace_export_path"] == "runtime/trace.jsonl"

def test_engine_exports_sanitized_trace_and_accounts_for_phase_output() -> None:
    with tempfile.TemporaryDirectory(prefix="nous-runtime-engine-", dir="/tmp") as raw:
        tmp_path = Path(raw)
        exporter = InMemoryTraceExporter()
        budget = ResourceBudget(ExecutionResourcePolicy(max_output_bytes=2048, max_wall_seconds=30))
        backend = CallableAgentBackend({"plan": lambda context: AgentOutput("plan", {"safe": "value"})})
        engine = AutonomousEngine(TaskStore(tmp_path / "nous-home"), backend, require_external_subagents=False, trace_exporter=exporter, resource_budget=budget)
        context = AgentContext("task-1", "plan", 1, {}, {}, {}, {}, str(tmp_path))
        output = engine._launch_phase(context, "")
        assert output.summary == "plan"
        assert exporter.events[0]["phase"] == "plan"
        assert "outputs" not in exporter.events[0]
        assert budget.usage().output_bytes > 0
