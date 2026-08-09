from __future__ import annotations

import json
import os
from pathlib import Path

context = json.loads(Path(os.environ["NOUS_AGENT_CONTEXT_FILE"]).read_text(encoding="utf-8"))
perspective = context.get("review_perspective") or "quality"
cycle = int(context.get("cycle", 1))
repository = Path(context["repository_root"])
commands = [
    ["/usr/bin/python3", "-B", "scripts/validate_compile.py"],
    ["/usr/bin/python3", "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider"],
]
findings = []
orchestrator = (repository / "src/nous_pilot/orchestrator.py").read_text(encoding="utf-8")
runtime_path = repository / "src/nous_pilot/runtime.py"
runtime_source = runtime_path.read_text(encoding="utf-8") if runtime_path.is_file() else ""
if "if test.status != \"success\":" not in orchestrator:
    findings.append({"finding_id": f"review-cycle-{cycle}-test-gate", "severity": "major", "summary": "Test Agent failure is not treated as a blocking gate."})
if not runtime_path.is_file():
    findings.append({"finding_id": f"review-cycle-{cycle}-runtime-adapter", "severity": "major", "summary": "The durable agent-nous runtime adapter is missing."})
if perspective == "security" and "shell=False" not in runtime_source:
    findings.append({"finding_id": f"review-cycle-{cycle}-shell-boundary", "severity": "blocker", "summary": "Runtime command execution does not explicitly disable shell interpretation."})
required_modules = (
    "capabilities.py",
    "child_loops.py",
    "verification_profiles.py",
    "handoff.py",
    "evaluation.py",
    "adapters.py",
    "persistence.py",
    "observability.py",
    "mcp.py",
    "distribution.py",
)
for module in required_modules:
    if not (repository / "src/nous_pilot" / module).is_file():
        findings.append({"finding_id": f"review-cycle-{cycle}-missing-{module}", "severity": "major", "summary": f"Required Phase 2–4 module is missing: {module}"})
observability_source = (repository / "src/nous_pilot/observability.py").read_text(encoding="utf-8") if (repository / "src/nous_pilot/observability.py").is_file() else ""
mcp_source = (repository / "src/nous_pilot/mcp.py").read_text(encoding="utf-8") if (repository / "src/nous_pilot/mcp.py").is_file() else ""
if "class OTLPExporter" not in observability_source:
    findings.append({"finding_id": f"review-cycle-{cycle}-otlp-boundary", "severity": "major", "summary": "Vendor-neutral OTLP exporter boundary is missing."})
if "class MCPHTTPGateway" not in mcp_source:
    findings.append({"finding_id": f"review-cycle-{cycle}-mcp-http-boundary", "severity": "major", "summary": "MCP HTTP/OAuth gateway boundary is missing."})
if not (repository / "nous-pilot.toml").is_file():
    findings.append({"finding_id": f"review-cycle-{cycle}-simple-config", "severity": "major", "summary": "Single user-facing nous-pilot.toml configuration is missing."})
config_source = (repository / "src/nous_pilot/config.py").read_text(encoding="utf-8") if (repository / "src/nous_pilot/config.py").is_file() else ""
if "write_internal_engine_config" not in config_source:
    findings.append({"finding_id": f"review-cycle-{cycle}-config-generation", "severity": "major", "summary": "Internal runtime config generation is not hidden behind the user config boundary."})
summary = f"Independent {perspective} review: implementation is checked by the managed compile and test validations."
print(json.dumps({
    "summary": summary,
    "decision": "completed",
    "outputs": {
        "findings": findings,
        "validation_proposals": [
            {"action_id": f"pilot-cycle-{cycle}-review-compile", "kind": "process.run", "targets": ["."], "command": commands[0], "criterion_id": "AC-001", "expected_effect": "compile source and tests"},
            {"action_id": f"pilot-cycle-{cycle}-review-tests", "kind": "process.run", "targets": ["."], "command": commands[1], "criterion_id": "AC-001", "expected_effect": "run Pilot contract tests"},
        ],
    }
}, ensure_ascii=False))
