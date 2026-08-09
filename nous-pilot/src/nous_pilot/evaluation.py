from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class SubagentUtilizationReport:
    expected_roles: tuple[str, ...]
    invoked_roles: tuple[str, ...]
    missing_roles: tuple[str, ...]
    total_invocations: int
    independent_review_count: int
    fallback_count: int

    @property
    def utilization_rate(self) -> float:
        return len(set(self.invoked_roles)) / len(set(self.expected_roles)) if self.expected_roles else 1.0

    @classmethod
    def from_calls(cls, calls: Iterable[Mapping[str, Any]], expected_roles: Iterable[str], review_quorum: int = 2) -> "SubagentUtilizationReport":
        rows = list(calls)
        expected = tuple(dict.fromkeys(expected_roles))
        invoked = tuple(dict.fromkeys(str(row.get("role")) for row in rows))
        reviews = sum(1 for row in rows if row.get("role") == "review")
        return cls(expected, invoked, tuple(role for role in expected if role not in invoked), len(rows), min(reviews, review_quorum), sum(1 for row in rows if row.get("fallback")))


@dataclass(frozen=True)
class QualityReport:
    acceptance_rate: float
    blocking_findings: int
    passed_verifications: int
    failed_verifications: int

    @classmethod
    def from_evidence(cls, findings: Iterable[Mapping[str, Any]], evidence: Iterable[Mapping[str, Any]]) -> "QualityReport":
        finding_rows = list(findings)
        evidence_rows = list(evidence)
        passed = sum(1 for item in evidence_rows if item.get("status") == "passed")
        failed = sum(1 for item in evidence_rows if item.get("status") != "passed")
        blocking = sum(1 for item in finding_rows if item.get("severity") in {"blocker", "major"})
        return cls(1.0 if not blocking and not failed else 0.0, blocking, passed, failed)
