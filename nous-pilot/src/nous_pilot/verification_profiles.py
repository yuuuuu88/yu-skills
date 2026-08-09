from __future__ import annotations

from dataclasses import dataclass
import sys

from .models import ContractError


@dataclass(frozen=True)
class VerificationProfile:
    name: str
    commands: tuple[tuple[str, ...], ...]
    network_allowed: bool = False
    timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.commands:
            raise ContractError("verification profile requires a name and commands")
        if self.timeout_seconds <= 0:
            raise ContractError("verification timeout must be positive")
        if self.network_allowed:
            raise ContractError("network-enabled profiles require a dedicated adapter")


class VerificationProfileRegistry:
    def __init__(self, profiles: tuple[VerificationProfile, ...] = ()) -> None:
        self._profiles = {profile.name: profile for profile in profiles}

    def register(self, profile: VerificationProfile) -> None:
        if profile.name in self._profiles:
            raise ContractError(f"duplicate verification profile: {profile.name}")
        self._profiles[profile.name] = profile

    def get(self, name: str) -> VerificationProfile:
        try:
            return self._profiles[name]
        except KeyError as exc:
            raise ContractError(f"unknown verification profile: {name}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._profiles))


def default_profiles() -> VerificationProfileRegistry:
    python = sys.executable
    return VerificationProfileRegistry((
        VerificationProfile("static", ((python, "-B", "scripts/validate_compile.py"),)),
        VerificationProfile("unit", ((python, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider"),)),
        VerificationProfile("security", ((python, "-B", "scripts/validate_compile.py"),)),
    ))
