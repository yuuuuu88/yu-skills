from __future__ import annotations

"""Typed validation-command policy and disposable-workspace side effects.

Validation commands are deliberately narrower than a general process runner.
They execute in a copied repository and may create only disposable artefacts
that match an explicit, relative glob.  The target repository is never used as
the process cwd and is never copied back from the validation workspace.
"""

import hashlib
import os
import re
import shlex
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from ..errors import ValidationError


_VALIDATION_KEYS = frozenset(
    {
        "cwd",
        "environment",
        "env",
        "timeout_seconds",
        "allowed_side_effects",
        "max_output_bytes",
    }
)
_PUBLIC_VALIDATION_KEYS = frozenset(
    {
        "cwd",
        "environment_keys",
        "environment_value_sha256",
        "timeout_seconds",
        "allowed_side_effects",
        "max_output_bytes",
    }
)
_COMMAND_KEYS = frozenset({"command", *_VALIDATION_KEYS})
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_DEFAULT_SIDE_EFFECTS = (
    ".pytest_cache",
    ".pytest_cache/**",
    "**/.pytest_cache",
    "**/.pytest_cache/**",
    "__pycache__",
    "__pycache__/**",
    "**/__pycache__",
    "**/__pycache__/**",
    "*.pyc",
    "**/*.pyc",
    ".coverage",
    ".coverage.*",
    "validation.log",
    "**/validation.log",
)


def _safe_relative(value: Any, label: str, *, allow_dot: bool = True) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValidationError(f"{label} must be a nonempty relative path")
    path = PurePosixPath(value.replace(os.sep, "/"))
    if path.is_absolute() or ".." in path.parts or (not allow_dot and path == PurePosixPath(".")):
        raise ValidationError(f"{label} must stay below the validation workspace")
    return path.as_posix()


def _side_effect_pattern(value: Any, index: int) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValidationError(f"validation.allowed_side_effects[{index}] must be a nonempty relative glob")
    # A pattern that names the whole workspace would silently turn a source
    # mutation into a successful validation.  Generated runtime directories
    # and concrete log/cache names are safe; broad roots are not.
    pattern = value.replace(os.sep, "/")
    if pattern.startswith("/") or ".." in PurePosixPath(pattern).parts:
        raise ValidationError(f"validation.allowed_side_effects[{index}] must stay below the validation workspace")
    if pattern in {".", "*", "**", "**/*"}:
        raise ValidationError(f"validation.allowed_side_effects[{index}] is too broad")
    return PurePosixPath(pattern).as_posix()


def _value_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ValidationSpec:
    """Runtime settings for one allowlisted validation command.

    Environment values are intentionally kept only in memory.  ``public``
    exposes names and hashes, never values, so action records and diagnostics
    cannot become a secret store.
    """

    cwd: str = "."
    environment: tuple[tuple[str, str], ...] = ()
    timeout_seconds: float | None = None
    allowed_side_effects: tuple[str, ...] = field(default_factory=lambda: _DEFAULT_SIDE_EFFECTS)
    max_output_bytes: int | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None, *, label: str = "validation") -> "ValidationSpec":
        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise ValidationError(f"{label} must be an object")
        unknown = sorted(set(value) - _VALIDATION_KEYS)
        if unknown:
            raise ValidationError(f"{label} has unknown keys: {', '.join(str(item) for item in unknown)}")
        cwd = _safe_relative(value.get("cwd", "."), f"{label}.cwd")
        raw_environment = value.get("environment", value.get("env", {}))
        if not isinstance(raw_environment, Mapping):
            raise ValidationError(f"{label}.environment must be an object")
        environment: list[tuple[str, str]] = []
        for name, raw_value in sorted(raw_environment.items(), key=lambda item: str(item[0])):
            if not isinstance(name, str) or not _ENV_NAME.fullmatch(name):
                raise ValidationError(f"{label}.environment keys must be safe environment names")
            if not isinstance(raw_value, str) or "\x00" in raw_value:
                raise ValidationError(f"{label}.environment.{name} must be a NUL-free string")
            if len(raw_value) > 16 * 1024:
                raise ValidationError(f"{label}.environment.{name} is too large")
            environment.append((name, raw_value))
        timeout = value.get("timeout_seconds")
        if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0 or timeout > 86_400):
            raise ValidationError(f"{label}.timeout_seconds must be a positive number no greater than 86400")
        max_output = value.get("max_output_bytes")
        if max_output is not None and (isinstance(max_output, bool) or not isinstance(max_output, int) or not 1_024 <= max_output <= 16 * 1024 * 1024):
            raise ValidationError(f"{label}.max_output_bytes must be between 1024 and 16777216")
        raw_side_effects = value.get("allowed_side_effects", list(_DEFAULT_SIDE_EFFECTS))
        if not isinstance(raw_side_effects, list) or not raw_side_effects:
            raise ValidationError(f"{label}.allowed_side_effects must be a nonempty list")
        side_effects = tuple(_side_effect_pattern(item, index) for index, item in enumerate(raw_side_effects))
        if len(set(side_effects)) != len(side_effects):
            raise ValidationError(f"{label}.allowed_side_effects must not contain duplicates")
        return cls(
            cwd=cwd,
            environment=tuple(environment),
            timeout_seconds=float(timeout) if timeout is not None else None,
            allowed_side_effects=side_effects,
            max_output_bytes=max_output,
        )

    @classmethod
    def from_command_mapping(cls, value: Mapping[str, Any], *, label: str = "validation command") -> tuple[tuple[str, ...], "ValidationSpec"]:
        if not isinstance(value, Mapping) or "command" not in value:
            raise ValidationError(f"{label} must contain command")
        unknown = sorted(set(value) - _COMMAND_KEYS)
        if unknown:
            raise ValidationError(f"{label} has unknown keys: {', '.join(str(item) for item in unknown)}")
        command_value = value.get("command")
        if isinstance(command_value, str):
            try:
                command = shlex.split(command_value)
            except ValueError as exc:
                raise ValidationError(f"{label}.command has invalid quoting") from exc
        else:
            command = command_value
        if not isinstance(command, list) or not command or any(not isinstance(item, str) or not item for item in command):
            raise ValidationError(f"{label}.command must be a nonempty argv list or command string")
        if any("\x00" in item or "\n" in item or "\r" in item for item in command):
            raise ValidationError(f"{label}.command contains an unsafe argument")
        # ``command`` belongs to the policy envelope, not to ValidationSpec.
        spec_value = {key: item for key, item in value.items() if key != "command"}
        return tuple(command), cls.from_mapping(spec_value, label=label)

    def environment_dict(self) -> dict[str, str]:
        return dict(self.environment)

    @classmethod
    def public_from_mapping(cls, value: Mapping[str, Any], *, label: str = "validation") -> dict[str, Any]:
        """Validate and normalize the redacted policy stored in an Action.

        A canonical Action carries environment names and value hashes rather
        than the in-memory environment values.  That public shape cannot be
        passed through ``from_mapping`` (which intentionally accepts only
        runtime settings), but CLI recovery still needs to deserialize it.
        This method validates the redacted shape without attempting to turn
        hashes back into executable environment values.
        """
        if not isinstance(value, Mapping):
            raise ValidationError(f"{label} must be an object")
        unknown = sorted(set(value) - _PUBLIC_VALIDATION_KEYS)
        if unknown:
            raise ValidationError(f"{label} has unknown public keys: {', '.join(str(item) for item in unknown)}")
        cwd = _safe_relative(value.get("cwd", "."), f"{label}.cwd")
        raw_names = value.get("environment_keys", [])
        if not isinstance(raw_names, list) or any(not isinstance(name, str) or not _ENV_NAME.fullmatch(name) for name in raw_names):
            raise ValidationError(f"{label}.environment_keys must be a list of safe environment names")
        if len(raw_names) != len(set(raw_names)):
            raise ValidationError(f"{label}.environment_keys must not contain duplicates")
        raw_hashes = value.get("environment_value_sha256", {})
        if not isinstance(raw_hashes, Mapping) or set(raw_hashes) != set(raw_names):
            raise ValidationError(f"{label}.environment_value_sha256 must match environment_keys")
        hashes: dict[str, str] = {}
        for name in raw_names:
            digest = raw_hashes.get(name)
            if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
                raise ValidationError(f"{label}.environment_value_sha256.{name} must be a SHA-256 digest")
            hashes[name] = digest
        timeout = value.get("timeout_seconds")
        if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0 or timeout > 86_400):
            raise ValidationError(f"{label}.timeout_seconds must be a positive number no greater than 86400")
        max_output = value.get("max_output_bytes")
        if max_output is not None and (isinstance(max_output, bool) or not isinstance(max_output, int) or not 1_024 <= max_output <= 16 * 1024 * 1024):
            raise ValidationError(f"{label}.max_output_bytes must be between 1024 and 16777216")
        raw_side_effects = value.get("allowed_side_effects")
        if not isinstance(raw_side_effects, list) or not raw_side_effects:
            raise ValidationError(f"{label}.allowed_side_effects must be a nonempty list")
        side_effects = tuple(_side_effect_pattern(item, index) for index, item in enumerate(raw_side_effects))
        if len(set(side_effects)) != len(side_effects):
            raise ValidationError(f"{label}.allowed_side_effects must not contain duplicates")
        return {
            "cwd": cwd,
            "environment_keys": list(raw_names),
            "environment_value_sha256": hashes,
            "timeout_seconds": float(timeout) if timeout is not None else None,
            "allowed_side_effects": list(side_effects),
            "max_output_bytes": max_output,
        }

    def resolve_cwd(self, root: Path) -> Path:
        candidate = (root / self.cwd).resolve()
        root = root.resolve()
        if candidate != root and root not in candidate.parents:
            raise ValidationError("validation cwd escapes the disposable workspace")
        if not candidate.is_dir():
            raise ValidationError(f"validation cwd does not exist: {self.cwd}")
        return candidate

    def is_allowed_side_effect(self, relative: str) -> bool:
        normalized = PurePosixPath(relative.replace(os.sep, "/")).as_posix()
        return any(fnmatchcase(normalized, pattern) for pattern in self.allowed_side_effects)

    def public(self) -> dict[str, Any]:
        return {
            "cwd": self.cwd,
            "environment_keys": [name for name, _ in self.environment],
            "environment_value_sha256": {name: _value_hash(value) for name, value in self.environment},
            "timeout_seconds": self.timeout_seconds,
            "allowed_side_effects": list(self.allowed_side_effects),
            "max_output_bytes": self.max_output_bytes,
        }


@dataclass(frozen=True)
class ValidationCommandPolicy:
    """An allowlist prefix plus its execution policy."""

    command: tuple[str, ...]
    spec: ValidationSpec = field(default_factory=ValidationSpec)

    @classmethod
    def from_value(cls, value: Any, *, label: str) -> "ValidationCommandPolicy":
        if isinstance(value, str):
            try:
                command = shlex.split(value)
            except ValueError as exc:
                raise ValidationError(f"{label} has invalid command quoting") from exc
            if not command:
                raise ValidationError(f"{label} must contain nonempty command arguments")
            if any("\x00" in item or "\n" in item or "\r" in item for item in command):
                raise ValidationError(f"{label} contains an unsafe command argument")
            return cls(tuple(command), ValidationSpec())
        if isinstance(value, list):
            if not value or any(not isinstance(item, str) or not item for item in value):
                raise ValidationError(f"{label} must contain nonempty command arguments")
            if any("\x00" in item or "\n" in item or "\r" in item for item in value):
                raise ValidationError(f"{label} contains an unsafe command argument")
            return cls(tuple(value), ValidationSpec())
        if isinstance(value, Mapping):
            command, spec = ValidationSpec.from_command_mapping(value, label=label)
            return cls(command, spec)
        raise ValidationError(f"{label} must be an argv list or validation command object")

    def matches(self, command: tuple[str, ...]) -> bool:
        return command[: len(self.command)] == self.command

    def public(self) -> dict[str, Any]:
        return {"command": list(self.command), **self.spec.public()}


def default_validation_spec() -> ValidationSpec:
    return ValidationSpec()


__all__ = ["ValidationCommandPolicy", "ValidationSpec", "default_validation_spec"]
