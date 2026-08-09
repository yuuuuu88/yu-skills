from __future__ import annotations

import json
import os
from pathlib import Path

context = json.loads(Path(os.environ["NOUS_AGENT_CONTEXT_FILE"]).read_text(encoding="utf-8"))
print(json.dumps({
    "summary": "Plan: complete the local Phase 2–4 development control plane while preserving the agent-nous boundary, explicit policy gates, durable evidence, and local-only safety defaults.",
    "outputs": {
        "plan": {
            "slices": [
                "Phase 2 provider capabilities, retry/cancellation/cost controls, Child Loop DAG, verification profiles, handoffs, and evaluation reports",
                "Phase 3 read-only Issue and CI adapters, draft PR proposal, policy-gated remote Git and browser boundaries",
                "Phase 4 SQLite shared-store equivalent, local observability exporter, MCP policy host, compatibility and release contracts",
                "contract, integration, security, and failure-path tests for every added boundary",
            ],
            "non_goals": ["remote Git", "PR publication", "secrets", "live paid provider"],
            "subagent_policy": {"required": True, "review_quorum": 2, "coordinator_direct_write": False},
        },
        "acceptance_criteria": [
            {"criterion_id": "AC-001", "statement": "The package and its tests pass under the declared local validation commands."},
            {"criterion_id": "AC-002", "statement": "The control plane records research, plan, implement, test, and two independent review roles."},
            {"criterion_id": "AC-003", "statement": "Unsafe paths and non-allowlisted verification commands are blocked."},
            {"criterion_id": "AC-004", "statement": "A blocking review causes a bounded re-plan cycle."},
            {"criterion_id": "AC-005", "statement": "Local intake can create a durable agent-nous Root Task through an explicit, no-shell runtime adapter."},
            {"criterion_id": "AC-006", "statement": "A failed Test Agent is a blocking outcome and cannot be accepted with empty evidence."},
            {"criterion_id": "AC-007", "statement": "Phase 2 provider, child-loop, verification, handoff, and evaluation boundaries are implemented and tested."},
            {"criterion_id": "AC-008", "statement": "Phase 3 external adapters are read-only or explicit human/policy-gated and tested without remote side effects."},
            {"criterion_id": "AC-009", "statement": "Phase 4 durable storage, observability, MCP policy, compatibility, and release boundaries are implemented and tested locally."},
            {"criterion_id": "AC-010", "statement": "Personal-mode operation is controlled by one small nous-pilot.toml file; detailed runtime JSON is generated internally and is not user-facing."},
        ],
        "validation_commands": [
            ["/usr/bin/python3", "-B", "scripts/validate_compile.py"],
            ["/usr/bin/python3", "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        ],
        "repository_observed": context.get("repository_root"),
        "no_managed_effects": True,
    }
}, ensure_ascii=False))
