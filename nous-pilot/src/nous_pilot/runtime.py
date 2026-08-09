from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .models import ContractError, DevelopmentRequest


class RuntimeCommandError(RuntimeError):
    """Raised when the managed agent-nous runtime cannot complete a command."""


@dataclass(frozen=True)
class RuntimeConfig:
    binary: Path
    nous_home: Path
    timeout_seconds: float = 300.0

    def __post_init__(self) -> None:
        binary = self.binary.expanduser().resolve()
        home = self.nous_home.expanduser().resolve()
        if not binary.is_file():
            raise ContractError("agent-nous binary must be an existing file")
        if self.timeout_seconds <= 0:
            raise ContractError("runtime timeout must be positive")
        object.__setattr__(self, "binary", binary)
        object.__setattr__(self, "nous_home", home)


@dataclass(frozen=True)
class RuntimeResponse:
    command: str
    payload: Mapping[str, Any]

    @property
    def ok(self) -> bool:
        return bool(self.payload.get("ok"))


class NousRuntimeClient:
    """Small, explicit adapter around the local agent-nous taskctl boundary."""

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    def _invoke(self, command: list[str], payload: Mapping[str, Any] | None = None) -> RuntimeResponse:
        argv = [str(self.config.binary), "--nous-home", str(self.config.nous_home)]
        if payload is not None:
            argv.extend(["--input", "-"])
        argv.extend(command)
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        python_dir = str(Path(sys.executable).resolve().parent)
        environment = {"PATH": os.pathsep.join((python_dir, os.defpath)), "NOUS_HOME": str(self.config.nous_home)}
        try:
            completed = subprocess.run(
                argv,
                input=encoded,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.config.timeout_seconds,
                check=False,
                shell=False,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeCommandError(f"agent-nous command timed out: {' '.join(command)}") from exc
        except OSError as exc:
            raise RuntimeCommandError(f"agent-nous command could not start: {type(exc).__name__}") from exc
        try:
            result = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeCommandError("agent-nous returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise RuntimeCommandError("agent-nous response must be an object")
        response = RuntimeResponse(command[-1] if command else "", result)
        if completed.returncode != 0 or not response.ok:
            error = result.get("error", {})
            message = error.get("message", "unknown runtime error") if isinstance(error, dict) else "unknown runtime error"
            raise RuntimeCommandError(str(message))
        return response

    def doctor(self) -> RuntimeResponse:
        return self._invoke(["taskctl", "doctor"])

    def status(self, *, include_archived: bool = False) -> RuntimeResponse:
        """Read the runtime task registry without rendering generated views."""
        command = ["taskctl", "list"]
        if include_archived:
            command.append("--include-archived")
        return self._invoke(command)

    def create_root_task(self, request: DevelopmentRequest, *, run_id: str) -> RuntimeResponse:
        payload = {
            "contract_version": 1,
            "request_id": request.request_id,
            "title": request.objective,
            "objective": request.objective,
            "target_repository": str(request.repository_root),
            "actor": {"type": "orchestrator", "run_id": run_id, "actor_id": "nous-pilot"},
            "loop_id": "root",
        }
        return self._invoke(["taskctl", "create"], payload)

    def engine_run(self, task_id: str, *, engine_config: Path) -> RuntimeResponse:
        if not task_id or any(char in task_id for char in "/\\\x00\r\n"):
            raise ContractError("task_id is unsafe")
        config = engine_config.expanduser().resolve()
        if not config.is_file():
            raise ContractError("engine config must be an existing file")
        return self._invoke(["taskctl", "engine-run", task_id, "--engine-config", str(config)])

    def show(self, task_id: str) -> RuntimeResponse:
        if not task_id or any(char in task_id for char in "/\\\x00\r\n"):
            raise ContractError("task_id is unsafe")
        return self._invoke(["taskctl", "show", task_id])

    def resume_prepare(self, task_id: str, *, run_id: str | None = None) -> RuntimeResponse:
        if not task_id or any(char in task_id for char in "/\\\x00\r\n"):
            raise ContractError("task_id is unsafe")
        current = self.show(task_id)
        result = current.payload.get("result")
        state: Mapping[str, Any] | None = None
        if isinstance(result, Mapping) and isinstance(result.get("state"), Mapping):
            state = result["state"]
        elif isinstance(current.payload.get("state"), Mapping):
            state = current.payload["state"]
        revision = state.get("revision") if state is not None else None
        if isinstance(revision, bool) or not isinstance(revision, int):
            raise RuntimeCommandError("agent-nous show response did not contain a state revision")
        request_id = run_id or f"resume-{uuid.uuid4().hex}"
        if not request_id or any(char in request_id for char in "/\\\x00\r\n"):
            raise ContractError("resume run_id is unsafe")
        payload = {
            "contract_version": 1,
            "request_id": request_id,
            "expected_revision": revision,
            "loop_id": "root",
            "actor": {"type": "orchestrator", "run_id": request_id, "actor_id": "nous-pilot"},
        }
        return self._invoke(
            ["taskctl", "resume-prepare", task_id, "--expected-revision", str(revision)],
            payload,
        )
