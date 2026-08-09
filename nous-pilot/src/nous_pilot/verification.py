from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .models import ContractError, VerificationEvidence


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class VerificationPolicy:
    allowed_prefixes: tuple[tuple[str, ...], ...]
    timeout_seconds: float = 60.0
    network_allowed: bool = False

    def validate(self) -> None:
        if not self.allowed_prefixes:
            raise ContractError("verification policy requires at least one allowlisted command")
        if self.timeout_seconds <= 0:
            raise ContractError("verification timeout must be positive")
        if self.network_allowed:
            raise ContractError("network-enabled verification requires a dedicated host adapter")

    def allows(self, command: Iterable[str]) -> bool:
        candidate = tuple(command)
        return any(candidate[: len(prefix)] == prefix for prefix in self.allowed_prefixes)


class VerificationRunner:
    def __init__(self, policy: VerificationPolicy) -> None:
        policy.validate()
        self.policy = policy

    def run(self, verification_id: str, command: tuple[str, ...], root: Path) -> VerificationEvidence:
        if not command or not self.policy.allows(command):
            return VerificationEvidence(verification_id, command, 126, "blocked", _digest(""), _digest("command blocked"), "command is not allowlisted")
        started = time.monotonic()
        try:
            result = subprocess.run(
                list(command),
                cwd=root,
                capture_output=True,
                text=True,
                timeout=self.policy.timeout_seconds,
                check=False,
                shell=False,
                env={
                    "PATH": os.pathsep.join((str(Path(sys.executable).resolve().parent), os.defpath)),
                    "PYTHONPATH": "src",
                },
            )
            status = "passed" if result.returncode == 0 else "failed"
            summary = f"returncode={result.returncode}; elapsed_ms={int((time.monotonic() - started) * 1000)}"
            return VerificationEvidence(verification_id, command, result.returncode, status, _digest(result.stdout), _digest(result.stderr), summary)
        except subprocess.TimeoutExpired:
            return VerificationEvidence(verification_id, command, 124, "failed", _digest(""), _digest("timeout"), "verification timed out")
        except OSError as exc:
            return VerificationEvidence(verification_id, command, 127, "failed", _digest(""), _digest(type(exc).__name__), f"verification failed to start: {type(exc).__name__}")
