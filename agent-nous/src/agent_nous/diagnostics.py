from __future__ import annotations

"""Redacted diagnostics shared by provider, validation, and runtime reports."""

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping


PROVIDER_ERROR_CATEGORIES = frozenset(
    {
        "invalid_json",
        "invalid_envelope",
        "invalid_schema",
        "missing_required_field",
        "invalid_managed_action",
        "output_too_large",
        "timeout",
        "provider_process_failure",
        "write_proposal_conflict",
        "unsafe_path",
        "validation_environment_failure",
    }
)

REVIEW_CLASSIFICATIONS = frozenset(
    {
        "code_gap",
        "test_gap",
        "validation_environment_gap",
        "provider_gap",
        "schema_gap",
        "documentation_gap",
        "safety_gap",
        "completed",
        "human_required",
    }
)

_ABSOLUTE_PATH = re.compile(r"(?:(?:[A-Za-z]:)?[/\\])[^\s'\"]+")
_HOME_PATH = re.compile(r"(?<![A-Za-z0-9_])[~][/\\][^\s'\"]+")
_SECRET_TOKEN = re.compile(r"(?i)(?:sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9_-]{8,}|xox[baprs]-[A-Za-z0-9-]{8,}|-----begin [^-]+ private key-----)")


def redact_text(value: str, *, limit: int = 240) -> str:
    """Return a bounded, path- and secret-redacted text summary."""
    text = _SECRET_TOKEN.sub("<redacted-secret>", value)
    text = _ABSOLUTE_PATH.sub("<path>", text)
    text = _HOME_PATH.sub("<home-path>", text)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: max(0, limit - 1)] + "…"
    return text


def safe_text_summary(value: str | None, *, limit: int = 240) -> dict[str, Any]:
    raw = value or ""
    return {
        "sha256": hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest(),
        "preview": redact_text(raw, limit=limit),
        "line_count": raw.count("\n") + (1 if raw else 0),
        "truncated": len(raw) > limit,
    }


def safe_actual_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


@dataclass(frozen=True)
class ProviderDiagnostic:
    """A safe machine-readable provider/validation failure."""

    category: str
    phase: str | None = None
    cycle: int | None = None
    provider: str | None = None
    action_id: str | None = None
    error_path: str = "$"
    expected: str | None = None
    actual_type: str | None = None
    stderr_summary: dict[str, Any] = field(default_factory=dict)
    retryable: bool = False
    message: str = "provider operation failed"
    details: dict[str, Any] = field(default_factory=dict)
    diagnostic_id: str = field(default_factory=lambda: f"diag-{uuid.uuid4().hex}")

    def __post_init__(self) -> None:
        if self.category not in PROVIDER_ERROR_CATEGORIES:
            raise ValueError(f"unknown provider error category: {self.category}")
        if self.phase is not None and self.phase not in {"plan", "implement", "review"}:
            raise ValueError("provider diagnostic phase is invalid")
        if self.cycle is not None and self.cycle < 1:
            raise ValueError("provider diagnostic cycle must be positive")

    def with_context(
        self,
        *,
        phase: str | None = None,
        cycle: int | None = None,
        provider: str | None = None,
        action_id: str | None = None,
    ) -> "ProviderDiagnostic":
        return ProviderDiagnostic(
            category=self.category,
            phase=phase if phase is not None else self.phase,
            cycle=cycle if cycle is not None else self.cycle,
            provider=provider if provider is not None else self.provider,
            action_id=action_id if action_id is not None else self.action_id,
            error_path=self.error_path,
            expected=self.expected,
            actual_type=self.actual_type,
            stderr_summary=dict(self.stderr_summary),
            retryable=self.retryable,
            message=self.message,
            details=dict(self.details),
            diagnostic_id=self.diagnostic_id,
        )

    def public(self) -> dict[str, Any]:
        stderr = self.stderr_summary if isinstance(self.stderr_summary, Mapping) else {}
        stderr_sha256 = stderr.get("sha256")
        stderr_summary = {
            "sha256": stderr_sha256 if isinstance(stderr_sha256, str) and re.fullmatch(r"[a-f0-9]{64}", stderr_sha256) else None,
            "preview": redact_text(str(stderr.get("preview", "")), limit=240),
            "line_count": stderr.get("line_count") if isinstance(stderr.get("line_count"), int) else 0,
            "truncated": bool(stderr.get("truncated", False)),
        }
        return {
            "schema_version": 1,
            "diagnostic_id": self.diagnostic_id,
            "category": self.category,
            "phase": self.phase,
            "cycle": self.cycle,
            "provider": redact_text(self.provider, limit=120) if self.provider is not None else None,
            "action_id": redact_text(self.action_id, limit=120) if self.action_id is not None else None,
            "error_path": self.error_path,
            "expected": redact_text(self.expected, limit=240) if self.expected is not None else None,
            "actual_type": self.actual_type,
            "stderr_summary": stderr_summary,
            "retryable": self.retryable,
            "message": redact_text(self.message, limit=320),
            "details": _sanitize_details(self.details),
        }


def _sanitize_details(value: Any, *, depth: int = 0) -> Any:
    if depth > 3:
        return "<nested>"
    if isinstance(value, Mapping):
        return {str(key): _sanitize_details(item, depth=depth + 1) for key, item in list(value.items())[:32]}
    if isinstance(value, list):
        return [_sanitize_details(item, depth=depth + 1) for item in value[:32]]
    if isinstance(value, str):
        return redact_text(value, limit=320)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return safe_actual_type(value)


def make_provider_diagnostic(
    category: str,
    *,
    phase: str | None = None,
    cycle: int | None = None,
    provider: str | None = None,
    action_id: str | None = None,
    error_path: str = "$",
    expected: str | None = None,
    actual: Any = None,
    stderr: str | None = None,
    retryable: bool = False,
    message: str = "provider operation failed",
    details: Mapping[str, Any] | None = None,
) -> ProviderDiagnostic:
    return ProviderDiagnostic(
        category=category,
        phase=phase,
        cycle=cycle,
        provider=provider,
        action_id=action_id,
        error_path=error_path,
        expected=expected,
        actual_type=safe_actual_type(actual),
        stderr_summary=safe_text_summary(stderr),
        retryable=retryable,
        message=message,
        details=dict(details or {}),
    )


__all__ = [
    "PROVIDER_ERROR_CATEGORIES",
    "REVIEW_CLASSIFICATIONS",
    "ProviderDiagnostic",
    "make_provider_diagnostic",
    "redact_text",
    "safe_actual_type",
    "safe_text_summary",
]
