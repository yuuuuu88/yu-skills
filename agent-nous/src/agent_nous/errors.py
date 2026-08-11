from __future__ import annotations

from typing import Any, Mapping

from .diagnostics import ProviderDiagnostic


class NousError(Exception):
    """Base exception with a stable machine-readable error code."""

    code = "nous_error"
    exit_code = 1


class ValidationError(NousError):
    code = "validation_error"
    exit_code = 2


class ProviderFailureError(ValidationError):
    """A provider-boundary failure with a safe, typed diagnosis.

    It remains a ``ValidationError`` for callers that only understand the v1
    exception hierarchy, while exposing category/retry metadata to the
    durable engine.
    """

    code = "provider_failure"

    def __init__(self, diagnostic: ProviderDiagnostic, message: str | None = None) -> None:
        self.diagnostic = diagnostic
        super().__init__(message or diagnostic.message)

    @property
    def category(self) -> str:
        return self.diagnostic.category

    @property
    def retryable(self) -> bool:
        return self.diagnostic.retryable

    @property
    def details(self) -> Mapping[str, Any]:
        return self.diagnostic.public()


class NotFoundError(NousError):
    code = "not_found"
    exit_code = 3


class ConflictError(NousError):
    code = "conflict"
    exit_code = 4


class PolicyDeniedError(NousError):
    code = "policy_denied"
    exit_code = 5


class UnsupportedActionError(NousError):
    code = "unsupported_action"
    exit_code = 6


class RecoveryRequiredError(NousError):
    code = "recovery_required"
    exit_code = 7
