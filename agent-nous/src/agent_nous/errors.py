from __future__ import annotations


class NousError(Exception):
    """Base exception with a stable machine-readable error code."""

    code = "nous_error"
    exit_code = 1


class ValidationError(NousError):
    code = "validation_error"
    exit_code = 2


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
