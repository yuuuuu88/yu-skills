from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping

Phase = Literal["research", "plan", "implement", "test", "review", "judge"]
RiskClass = Literal["green", "yellow", "red"]


class ContractError(ValueError):
    """Raised when an external or internal contract is invalid."""


def safe_identifier(value: str, label: str = "identifier") -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} must be a non-empty string")
    if any(char in value for char in "/\\\x00\r\n"):
        raise ContractError(f"{label} contains an unsafe character")
    return value.strip()


def string_tuple(value: Any, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ContractError(f"{label} must be a list of non-empty strings")
    return tuple(item.strip() for item in value)


@dataclass(frozen=True)
class DevelopmentRequest:
    request_id: str
    objective: str
    repository_root: Path
    constraints: tuple[str, ...] = ()
    non_goals: tuple[str, ...] = ()
    risk_class: RiskClass = "green"
    validation_profile: str = "unit"
    base_revision: str | None = None

    def __post_init__(self) -> None:
        safe_identifier(self.request_id, "request_id")
        if not self.objective.strip():
            raise ContractError("objective must be non-empty")
        if self.risk_class not in {"green", "yellow", "red"}:
            raise ContractError("risk_class must be green, yellow, or red")
        if not self.repository_root.is_dir():
            raise ContractError("repository_root must be an existing directory")
        if not self.validation_profile.strip():
            raise ContractError("validation_profile must be non-empty")

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "objective": self.objective,
            "repository_root": str(self.repository_root),
            "constraints": list(self.constraints),
            "non_goals": list(self.non_goals),
            "risk_class": self.risk_class,
            "validation_profile": self.validation_profile,
            "base_revision": self.base_revision,
        }


@dataclass(frozen=True)
class AgentResult:
    role: Phase
    status: Literal["success", "blocked", "failed"]
    summary: str
    outputs: dict[str, Any] = field(default_factory=dict)
    provider_id: str = "unknown"
    run_id: str = ""
    decision: str | None = None

    def __post_init__(self) -> None:
        if self.role not in {"research", "plan", "implement", "test", "review", "judge"}:
            raise ContractError(f"unsupported agent role: {self.role}")
        if self.status not in {"success", "blocked", "failed"}:
            raise ContractError(f"unsupported agent status: {self.status}")
        if not self.summary.strip():
            raise ContractError("agent summary must be non-empty")
        if self.decision is not None and self.decision not in {"completed", "needs_plan", "needs_human_input", "reject"}:
            raise ContractError("unsupported agent decision")


@dataclass(frozen=True)
class Finding:
    finding_id: str
    severity: Literal["blocker", "major", "minor", "advisory"]
    summary: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        safe_identifier(self.finding_id, "finding_id")
        if self.severity not in {"blocker", "major", "minor", "advisory"}:
            raise ContractError("invalid finding severity")
        if not self.summary.strip():
            raise ContractError("finding summary must be non-empty")

    @property
    def blocking(self) -> bool:
        return self.severity in {"blocker", "major"}


@dataclass(frozen=True)
class VerificationEvidence:
    verification_id: str
    command: tuple[str, ...]
    returncode: int
    status: Literal["passed", "failed", "blocked"]
    stdout_sha256: str
    stderr_sha256: str
    summary: str

    def __post_init__(self) -> None:
        safe_identifier(self.verification_id, "verification_id")
        if not self.command:
            raise ContractError("verification command must be non-empty")
        if self.status not in {"passed", "failed", "blocked"}:
            raise ContractError("invalid verification status")


@dataclass(frozen=True)
class RunReport:
    request_id: str
    outcome: Literal["completed", "needs_plan", "blocked"]
    cycles: int
    agent_calls: tuple[dict[str, Any], ...]
    findings: tuple[Finding, ...]
    evidence: tuple[VerificationEvidence, ...]
    staged_change_paths: tuple[str, ...]
    diagnostics: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "outcome": self.outcome,
            "cycles": self.cycles,
            "agent_calls": [dict(item) for item in self.agent_calls],
            "findings": [
                {"finding_id": item.finding_id, "severity": item.severity, "summary": item.summary, "evidence_refs": list(item.evidence_refs)}
                for item in self.findings
            ],
            "evidence": [
                {
                    "verification_id": item.verification_id,
                    "command": list(item.command),
                    "returncode": item.returncode,
                    "status": item.status,
                    "stdout_sha256": item.stdout_sha256,
                    "stderr_sha256": item.stderr_sha256,
                    "summary": item.summary,
                }
                for item in self.evidence
            ],
            "staged_change_paths": list(self.staged_change_paths),
            "diagnostics": list(self.diagnostics),
        }
