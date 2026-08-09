from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .models import ContractError, Finding, safe_identifier


class PolicyDenied(ContractError):
    """Raised when an external adapter crosses its explicit policy boundary."""


@dataclass(frozen=True)
class IssueRecord:
    issue_id: str
    title: str
    body: str
    labels: tuple[str, ...] = ()
    source: str = "local"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "IssueRecord":
        issue_id = safe_identifier(str(value.get("issue_id", "")), "issue_id")
        title = str(value.get("title", "")).strip()
        if not title:
            raise ContractError("issue title is required")
        return cls(issue_id, title, str(value.get("body", "")), tuple(str(label) for label in value.get("labels", ())), str(value.get("source", "local")))


class ReadOnlyIssueAdapter:
    def intake(self, value: Mapping[str, Any]) -> IssueRecord:
        return IssueRecord.from_mapping(value)


@dataclass(frozen=True)
class CIEvent:
    event_id: str
    status: str
    conclusion: str
    findings: tuple[Finding, ...] = ()


class CIEventAdapter:
    def from_mapping(self, value: Mapping[str, Any]) -> CIEvent:
        event_id = safe_identifier(str(value.get("event_id", "")), "event_id")
        status = str(value.get("status", "")).strip()
        conclusion = str(value.get("conclusion", "")).strip()
        findings: list[Finding] = []
        if conclusion.lower() in {"failure", "failed", "cancelled"}:
            findings.append(Finding(f"ci-{event_id}", "major", f"CI concluded with {conclusion}"))
        return CIEvent(event_id, status, conclusion, tuple(findings))


@dataclass(frozen=True)
class DraftPRProposal:
    task_id: str
    title: str
    body: str
    changed_paths: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    requires_human_approval: bool = True


class DraftPRAdapter:
    def build(self, task_id: str, title: str, body: str, changed_paths: Sequence[str], evidence_refs: Sequence[str]) -> DraftPRProposal:
        safe_identifier(task_id, "task_id")
        if not title.strip() or not body.strip():
            raise ContractError("draft PR title and body are required")
        return DraftPRProposal(task_id, title.strip(), body.strip(), tuple(changed_paths), tuple(evidence_refs))


@dataclass(frozen=True)
class RemoteGitPolicy:
    allowed_operations: frozenset[str] = frozenset()
    require_human_approval: bool = True
    network_allowed: bool = False
    credentials_allowed: bool = False

    def authorize(self, operation: str, *, human_approved: bool = False) -> None:
        if operation not in self.allowed_operations or not self.network_allowed or not self.credentials_allowed:
            raise PolicyDenied(f"remote Git operation is denied by policy: {operation}")
        if self.require_human_approval and not human_approved:
            raise PolicyDenied("remote Git operation requires human approval")


class RemoteGitAdapter:
    def __init__(self, policy: RemoteGitPolicy) -> None:
        self.policy = policy

    def execute(self, operation: str, *, human_approved: bool = False) -> None:
        self.policy.authorize(operation, human_approved=human_approved)
        raise NotImplementedError("remote Git execution is a separately deployed adapter")


@dataclass(frozen=True)
class BrowserVerificationResult:
    status: str
    summary: str
    evidence_refs: tuple[str, ...] = ()


class BrowserVerificationAdapter:
    def verify(self, url: str, *, host_capability_granted: bool = False) -> BrowserVerificationResult:
        if not host_capability_granted:
            return BrowserVerificationResult("blocked", "browser verification requires an explicit host capability")
        if not url.startswith(("http://", "https://")):
            raise ContractError("browser URL must use HTTP or HTTPS")
        return BrowserVerificationResult("not_configured", "browser provider is not configured in the local Phase 3 slice")
