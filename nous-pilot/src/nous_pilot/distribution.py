from __future__ import annotations

import platform as host_platform
import sys
from collections.abc import Sequence
from dataclasses import dataclass


SUPPORTED_PYTHON = (3, 11)
SUPPORTED_PLATFORMS = ("Linux", "Darwin", "Windows")
_PLATFORM_LABELS = {"Linux": "Linux", "Darwin": "macOS", "Windows": "Windows"}


@dataclass(frozen=True)
class CompatibilityReport:
    supported: bool
    python_version: str
    platform: str
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "supported": self.supported,
            "python_version": self.python_version,
            "platform": self.platform,
            "reasons": list(self.reasons),
        }


class UnsupportedRuntimeError(RuntimeError):
    """Raised when the current Python or operating system is unsupported."""

    def __init__(self, report: CompatibilityReport) -> None:
        self.report = report
        reasons = "; ".join(report.reasons) or "the runtime is not supported"
        super().__init__(
            f"Nous Pilot does not support Python {report.python_version} on "
            f"{report.platform or 'an unknown platform'}: {reasons}"
        )


def _version_tuple(value: Sequence[int]) -> tuple[int, ...]:
    """Return the comparable part of a ``sys.version_info``-like value."""
    return tuple(int(part) for part in value[:3])


def _version_string(value: Sequence[int]) -> str:
    parts = _version_tuple(value)
    return ".".join(str(part) for part in parts)


def check_compatibility(
    *,
    python_version_info: Sequence[int] | None = None,
    platform_name: str | None = None,
) -> CompatibilityReport:
    """Return a deterministic compatibility report for the current runtime.

    ``python_version_info`` and ``platform_name`` are optional injection points
    for callers that need to inspect another runtime, and keep the check easy
    to test without changing the host process.
    """
    reasons: list[str] = []
    version_info = python_version_info if python_version_info is not None else sys.version_info
    current_platform = platform_name if platform_name is not None else host_platform.system()
    version = _version_string(version_info)

    if _version_tuple(version_info) < SUPPORTED_PYTHON:
        reasons.append(f"Python 3.11 or newer is required; detected Python {version}")
    if current_platform not in SUPPORTED_PLATFORMS:
        supported = ", ".join(_PLATFORM_LABELS[item] for item in SUPPORTED_PLATFORMS[:-1])
        supported += f", and {_PLATFORM_LABELS[SUPPORTED_PLATFORMS[-1]]}"
        reasons.append(
            f"Unsupported platform {current_platform or 'unknown'!r}; "
            f"supported platforms are {supported}"
        )

    return CompatibilityReport(not reasons, version, current_platform, tuple(reasons))


def require_compatibility(
    *,
    python_version_info: Sequence[int] | None = None,
    platform_name: str | None = None,
) -> CompatibilityReport:
    """Return the report or fail fast with actionable runtime diagnostics."""
    report = check_compatibility(
        python_version_info=python_version_info,
        platform_name=platform_name,
    )
    if not report.supported:
        raise UnsupportedRuntimeError(report)
    return report


@dataclass(frozen=True)
class ReleaseManifest:
    version: str
    required_python: str
    migrations: tuple[str, ...]
    compatibility: CompatibilityReport

    def as_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "required_python": self.required_python,
            "migrations": list(self.migrations),
            "compatibility": self.compatibility.as_dict(),
        }
