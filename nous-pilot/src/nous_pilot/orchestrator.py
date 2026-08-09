from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .context import RepositoryContext, build_repository_context
from .intake import normalize_request
from .models import AgentResult, ContractError, DevelopmentRequest, Finding, RunReport, VerificationEvidence
from .provider import AgentProvider
from .verification import VerificationRunner
from .workspace import DraftChange, WorkspaceManager


@dataclass(frozen=True)
class PilotEngineConfig:
    min_cycles: int = 1
    max_cycles: int = 3
    review_quorum: int = 2
    runtime_root: Path = Path(".nous-pilot-runtime")

    def __post_init__(self) -> None:
        if self.min_cycles < 1 or self.max_cycles < self.min_cycles:
            raise ContractError("cycle bounds are invalid")
        if self.review_quorum < 2:
            raise ContractError("review_quorum must be at least two")


class PilotEngine:
    """Execute a development workflow with explicit role separation."""

    def __init__(self, provider: AgentProvider, verifier: VerificationRunner, config: PilotEngineConfig | None = None) -> None:
        self.provider = provider
        self.verifier = verifier
        self.config = config or PilotEngineConfig()

    @staticmethod
    def _record(calls: list[dict[str, Any]], result: AgentResult, cycle: int, reviewer_id: str | None = None) -> None:
        calls.append({
            "role": result.role,
            "cycle": cycle,
            "reviewer_id": reviewer_id,
            "provider_id": result.provider_id,
            "run_id": result.run_id,
            "status": result.status,
            "summary": result.summary,
        })

    @staticmethod
    def _parse_changes(value: Any) -> list[DraftChange]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ContractError("implement changes must be a list")
        changes: list[DraftChange] = []
        for item in value:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("content"), str):
                raise ContractError("each change requires path and content")
            changes.append(DraftChange(item["path"], item["content"]))
        return changes

    @staticmethod
    def _parse_findings(value: Any, reviewer_id: str) -> list[Finding]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ContractError(f"{reviewer_id} findings must be a list")
        findings: list[Finding] = []
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise ContractError(f"{reviewer_id} finding {index} must be an object")
            findings.append(Finding(
                str(item.get("finding_id", f"{reviewer_id}-{index}")),
                item.get("severity", "major"),
                str(item.get("summary", "")),
                tuple(str(ref) for ref in item.get("evidence_refs", [])),
            ))
        return findings

    def run(self, request_value: dict[str, Any]) -> RunReport:
        request = normalize_request(request_value)
        context = build_repository_context(request.repository_root)
        calls: list[dict[str, Any]] = []
        all_findings: list[Finding] = []
        all_evidence: list[VerificationEvidence] = []
        staged_paths: list[str] = []
        diagnostics: list[str] = []
        workspace_manager = WorkspaceManager(self.config.runtime_root)

        for cycle in range(1, self.config.max_cycles + 1):
            research = self.provider.invoke("research", {"request": request.as_dict(), "context": context.as_dict(), "cycle": cycle})
            self._record(calls, research, cycle)
            if research.status != "success":
                diagnostics.append(f"research blocked in cycle {cycle}")
                return RunReport(request.request_id, "blocked", cycle, tuple(calls), tuple(all_findings), tuple(all_evidence), tuple(staged_paths), tuple(diagnostics))

            plan = self.provider.invoke("plan", {"request": request.as_dict(), "context": context.as_dict(), "research": research.outputs, "cycle": cycle})
            self._record(calls, plan, cycle)
            if plan.status != "success":
                diagnostics.append(f"plan blocked in cycle {cycle}")
                return RunReport(request.request_id, "blocked", cycle, tuple(calls), tuple(all_findings), tuple(all_evidence), tuple(staged_paths), tuple(diagnostics))

            implementation = self.provider.invoke("implement", {"request": request.as_dict(), "context": context.as_dict(), "plan": plan.outputs, "cycle": cycle})
            self._record(calls, implementation, cycle)
            if implementation.status != "success":
                diagnostics.append(f"implementation blocked in cycle {cycle}")
                return RunReport(request.request_id, "blocked", cycle, tuple(calls), tuple(all_findings), tuple(all_evidence), tuple(staged_paths), tuple(diagnostics))
            changes = self._parse_changes(implementation.outputs.get("changes"))
            if changes:
                staged = workspace_manager.stage(f"{request.request_id}-cycle-{cycle}", changes)
                staged_paths.extend(staged.paths)

            test = self.provider.invoke("test", {"request": request.as_dict(), "context": context.as_dict(), "plan": plan.outputs, "implementation": implementation.outputs, "cycle": cycle})
            self._record(calls, test, cycle)
            if test.status != "success":
                diagnostics.append(f"test provider blocked in cycle {cycle}")
                return RunReport(request.request_id, "blocked", cycle, tuple(calls), tuple(all_findings), tuple(all_evidence), tuple(staged_paths), tuple(diagnostics))
            commands = test.outputs.get("commands", plan.outputs.get("validation_commands", []))
            if commands is None:
                commands = []
            if not isinstance(commands, list):
                raise ContractError("test commands must be a list")
            for index, raw_command in enumerate(commands):
                if not isinstance(raw_command, list) or any(not isinstance(item, str) for item in raw_command):
                    raise ContractError("test command must be a list of strings")
                evidence = self.verifier.run(f"cycle-{cycle}-test-{index}", tuple(raw_command), request.repository_root)
                all_evidence.append(evidence)

            cycle_findings: list[Finding] = []
            for reviewer_index in range(self.config.review_quorum):
                reviewer_id = f"reviewer-{reviewer_index + 1}"
                review = self.provider.invoke("review", {
                    "request": request.as_dict(),
                    "context": context.as_dict(),
                    "plan": plan.outputs,
                    "implementation": implementation.outputs,
                    "test": test.outputs,
                    "evidence": [item.__dict__ for item in all_evidence if item.verification_id.startswith(f"cycle-{cycle}-")],
                    "reviewer_id": reviewer_id,
                    "cycle": cycle,
                })
                self._record(calls, review, cycle, reviewer_id)
                if review.status != "success":
                    cycle_findings.append(Finding(f"{reviewer_id}-provider", "major", "review provider failed"))
                    continue
                cycle_findings.extend(self._parse_findings(review.outputs.get("findings"), reviewer_id))
            all_findings.extend(cycle_findings)
            evidence_failed = any(item.status != "passed" for item in all_evidence if item.verification_id.startswith(f"cycle-{cycle}-"))
            blocking = any(item.blocking for item in cycle_findings) or evidence_failed
            if cycle >= self.config.min_cycles and not blocking:
                return RunReport(request.request_id, "completed", cycle, tuple(calls), tuple(all_findings), tuple(all_evidence), tuple(staged_paths), tuple(diagnostics))
            if blocking:
                diagnostics.append(f"cycle {cycle} requested re-plan")
            else:
                diagnostics.append(f"cycle {cycle} satisfied gates; minimum cycle count requires another loop")

        diagnostics.append("max_cycles reached before acceptance")
        return RunReport(request.request_id, "needs_plan", self.config.max_cycles, tuple(calls), tuple(all_findings), tuple(all_evidence), tuple(staged_paths), tuple(diagnostics))
