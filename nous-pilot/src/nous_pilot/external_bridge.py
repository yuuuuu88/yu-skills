"""Bridge the generic Nous Pilot provider to agent-nous JsonCommand agents.

The agent-nous engine invokes this module with ``NOUS_AGENT_CONTEXT_FILE``.
The configured Nous Pilot command receives the context as JSON on stdin and
returns the strict provider-result object.  The bridge deliberately exposes
only the engine-facing fields; provider identifiers and status stay in the
provider boundary rather than becoming trusted task evidence.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .models import AgentResult, ContractError
from .provider import SubprocessAgentProvider


def _load_context() -> dict[str, object]:
    context_path = os.environ.get("NOUS_AGENT_CONTEXT_FILE")
    if not context_path:
        raise ContractError("NOUS_AGENT_CONTEXT_FILE is required")
    payload = json.loads(Path(context_path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ContractError("agent context must be a JSON object")
    return payload


def _command(raw: str) -> tuple[str, ...]:
    value = json.loads(raw)
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ContractError("agent command must be a JSON string list")
    return tuple(value)


def _engine_result(result: AgentResult) -> dict[str, object]:
    return {
        "status": result.status,
        "summary": result.summary,
        "outputs": result.outputs,
        "provider_id": result.provider_id,
        "run_id": result.run_id,
        **({"decision": result.decision} if result.decision is not None else {}),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="agent-nous bridge for Nous Pilot external agents")
    parser.add_argument("role")
    parser.add_argument("--command-json", default="[]")
    args = parser.parse_args(argv)
    payload = _load_context()
    command = _command(args.command_json)
    if not command:
        result = AgentResult(args.role, "blocked", "no external Agent command is configured")
    else:
        repository = payload.get("repository_root")
        cwd = Path(repository) if isinstance(repository, str) and repository else None
        result = SubprocessAgentProvider(command, cwd=cwd).invoke(args.role, payload)
    print(json.dumps(_engine_result(result), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ContractError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "summary": f"external bridge rejected input: {type(exc).__name__}", "outputs": {}}), flush=True)
        raise SystemExit(2)
