from __future__ import annotations

import json
import math
import os
import re
import signal
import shutil
import subprocess
import threading
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol

from .handoff import _assert_safe
from .models import AgentResult, ContractError, Phase, safe_identifier
from .workspace import DraftChange


class AgentProvider(Protocol):
    provider_id: str

    def invoke(self, role: Phase, payload: dict[str, Any]) -> AgentResult:
        ...


_DEFAULT_MAX_OUTPUT_BYTES = 1_048_576
_RESERVED_ENVIRONMENT_KEYS = {
    "PATH",
    "PYTHONPATH",
    "NOUS_PILOT_ROLE",
    "NOUS_PILOT_PROVIDER_ID",
}
_SECRET_KEY_MARKERS = (
    "secret",
    "password",
    "token",
    "api_key",
    "apikey",
    "private_key",
    "privatekey",
    "credential",
    "access_key",
    "accesskey",
)
_SECRET_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}\b", re.IGNORECASE),
    re.compile(r"\b(?:token|password|api[_-]?key|secret)\s*[:=]\s*[A-Za-z0-9._~+/=-]{8,}\b", re.IGNORECASE),
)
_RESULT_KEYS = frozenset({
    "status", "summary", "outputs", "run_id", "role", "artifact_refs",
    "evidence_refs", "files_changed", "commands_run", "findings", "usage",
    "failure_code", "resume_hint", "decision",
})
_SHELL_EXECUTABLES = {"sh", "bash", "zsh", "fish", "cmd", "cmd.exe", "powershell", "pwsh", "pwsh.exe"}


def _normalized_key(key: str) -> str:
    with_word_boundaries = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    return re.sub(r"[^a-zA-Z0-9]+", "_", with_word_boundaries).lower()


def _is_secret_key(key: str) -> bool:
    normalized = _normalized_key(key)
    return any(marker in normalized for marker in _SECRET_KEY_MARKERS)


def _is_secret_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS)


def _copy_json_value(value: Any, path: str = "root") -> Any:
    """Validate JSON-compatible data and return a detached copy."""
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and _is_secret_value(value):
            raise ContractError(f"secret-like provider value is not allowed at {path}")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractError(f"provider result contains a non-finite number at {path}")
        return value
    if isinstance(value, Mapping):
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractError(f"provider result contains a non-string key at {path}")
            if _is_secret_key(key):
                raise ContractError(f"secret-like provider result field is not allowed: {path}.{key}")
            copied[key] = _copy_json_value(item, f"{path}.{key}")
        return copied
    if isinstance(value, (list, tuple)):
        return [_copy_json_value(item, f"{path}[{index}]") for index, item in enumerate(value)]
    raise ContractError(f"provider result contains a non-JSON value at {path}")


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _load_result_json(raw: bytes) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ContractError("external Agent returned malformed JSON") from exc


def _validate_command(command: tuple[str, ...]) -> None:
    executable = Path(command[0]).name.lower()
    if executable in _SHELL_EXECUTABLES or any(argument.lower() in {"-c", "/c", "-command"} for argument in command[1:]):
        raise ContractError("external Agent command may not invoke a shell")


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    try:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass


def _run_bounded_process(
    command: tuple[str, ...],
    encoded: bytes,
    *,
    cwd: Path | None,
    environment: Mapping[str, str],
    timeout_seconds: float,
    max_output_bytes: int,
) -> tuple[int, bytes, bytes, bool, bool]:
    """Run a child while reading both pipes and stopping on output overrun."""
    kwargs: dict[str, Any] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "cwd": cwd,
        "env": dict(environment),
        "shell": False,
    }
    if os.name != "nt":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = subprocess.Popen(list(command), **kwargs)
    stdout = bytearray()
    stderr = bytearray()
    output_overrun = threading.Event()
    output_lock = threading.Lock()

    def drain(stream: Any, target: bytearray) -> None:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                return
            with output_lock:
                if len(stdout) + len(stderr) + len(chunk) > max_output_bytes:
                    output_overrun.set()
                    _terminate_process(process)
                    return
                target.extend(chunk)

    threads = [
        threading.Thread(target=drain, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        assert process.stdin is not None
        try:
            process.stdin.write(encoded)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            process.wait(timeout=timeout_seconds)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_process(process)
    finally:
        for thread in threads:
            thread.join(timeout=2)
        if process.poll() is None:
            _terminate_process(process)
    return process.returncode if process.returncode is not None else 1, bytes(stdout), bytes(stderr), timed_out, output_overrun.is_set()


@dataclass(frozen=True)
class SubprocessAgentProvider:
    """Invoke a user-supplied Agent process through a strict JSON boundary."""

    command: tuple[str, ...]
    provider_id: str = "external-subprocess"
    timeout_seconds: float = 300.0
    max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES
    cwd: Path | None = None
    allow_direct_workspace: bool = False
    extra_environment: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.command, (str, bytes)):
            raise ContractError("external Agent command must be a non-empty string list")
        try:
            command = tuple(self.command)
        except TypeError as exc:
            raise ContractError("external Agent command must be a non-empty string list") from exc
        if not command or any(not isinstance(item, str) or not item.strip() for item in command):
            raise ContractError("external Agent command must be a non-empty string list")
        _validate_command(command)
        if (
            not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ContractError("external Agent timeout must be positive")
        if not isinstance(self.max_output_bytes, int) or isinstance(self.max_output_bytes, bool) or self.max_output_bytes <= 0:
            raise ContractError("external Agent output limit must be a positive integer")
        if not isinstance(self.provider_id, str) or not self.provider_id.strip():
            raise ContractError("external Agent provider_id must be a non-empty string")
        safe_identifier(self.provider_id, "provider_id")
        if not isinstance(self.extra_environment, Mapping):
            raise ContractError("external Agent environment must be a mapping")
        for key, value in self.extra_environment.items():
            if (
                not isinstance(key, str)
                or not key
                or key in _RESERVED_ENVIRONMENT_KEYS
                or not key.startswith("NOUS_PILOT_")
            ):
                raise ContractError("external Agent environment may only override NOUS_PILOT_* values")
            if _is_secret_key(key):
                raise ContractError("external Agent environment cannot contain secret-like keys")
            if not isinstance(value, str):
                raise ContractError("external Agent environment values must be strings")
        object.__setattr__(self, "command", command)
        object.__setattr__(self, "extra_environment", dict(self.extra_environment))
        if self.cwd is not None and not isinstance(self.cwd, Path):
            object.__setattr__(self, "cwd", Path(self.cwd))

    def _working_directory(self, payload: Mapping[str, Any]) -> Path | None:
        request = payload.get("request")
        candidate = request.get("repository_root") if isinstance(request, Mapping) else None
        root = self.cwd or (Path(candidate).expanduser().resolve() if isinstance(candidate, str) and candidate else None)
        if root is not None and not root.is_dir():
            raise ContractError("external Agent working directory must be an existing directory")
        return root

    @staticmethod
    def _redirect_workspace(value: Any, source: Path, target: Path) -> Any:
        if isinstance(value, Mapping):
            return {
                key: target.as_posix() if key in {"repository_root", "target_repository"} and item == str(source) else SubprocessAgentProvider._redirect_workspace(item, source, target)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [SubprocessAgentProvider._redirect_workspace(item, source, target) for item in value]
        return value

    @contextmanager
    def _execution_workspace(self, payload: Mapping[str, Any]):
        root = self._working_directory(payload)
        if self.allow_direct_workspace or root is None:
            yield root, dict(payload)
            return
        with tempfile.TemporaryDirectory(prefix="nous-pilot-agent-") as directory:
            target = Path(directory)
            shutil.copytree(root, target, dirs_exist_ok=True, symlinks=True, ignore=shutil.ignore_patterns(".git", ".nous", "__pycache__"))
            for item in sorted(target.rglob("*"), key=lambda path: len(path.parts), reverse=True):
                if item.is_symlink():
                    item.unlink()
            yield target, self._redirect_workspace(payload, root, target)

    def invoke(self, role: Phase, payload: dict[str, Any]) -> AgentResult:
        if role not in {"research", "plan", "implement", "test", "review", "judge"}:
            raise ContractError(f"unsupported external Agent role: {role}")
        if not isinstance(payload, Mapping):
            raise ContractError("external Agent input must be a JSON object")
        original_payload = _copy_json_value(payload)
        _assert_safe(original_payload)
        with self._execution_workspace(original_payload) as (working_directory, safe_payload):
            safe_payload = _copy_json_value(safe_payload)
            _assert_safe(safe_payload)
            environment = {
                "PATH": os.defpath,
                "NOUS_PILOT_ROLE": role,
                "NOUS_PILOT_PROVIDER_ID": self.provider_id,
                **self.extra_environment,
            }
            try:
                encoded = json.dumps(safe_payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            except (TypeError, ValueError, OverflowError) as exc:
                raise ContractError("external Agent input is not valid JSON") from exc
            try:
                returncode, stdout, stderr, timed_out, output_overrun = _run_bounded_process(
                    self.command,
                    encoded,
                    cwd=working_directory,
                    environment=environment,
                    timeout_seconds=self.timeout_seconds,
                    max_output_bytes=self.max_output_bytes,
                )
            except OSError as exc:
                return AgentResult(role, "failed", f"external Agent could not start: {type(exc).__name__}", provider_id=self.provider_id)
            if timed_out:
                return AgentResult(role, "failed", "external Agent timed out", provider_id=self.provider_id)
            if output_overrun:
                return AgentResult(role, "failed", "external Agent output exceeded the configured limit", provider_id=self.provider_id)
            if returncode != 0:
                return AgentResult(role, "failed", f"external Agent exited with code {returncode}", provider_id=self.provider_id)
            output = _load_result_json(stdout)
            if not isinstance(output, Mapping):
                raise ContractError("external Agent result must be a JSON object")
            safe_output = _copy_json_value(output)
            _assert_safe(safe_output)
            return result_from_mapping(role, safe_output, self.provider_id)


@dataclass
class RecordingProvider:
    """Deterministic provider for local contract tests and smoke runs."""

    provider_id: str = "fixture-provider"
    responses: dict[str, list[AgentResult]] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def invoke(self, role: Phase, payload: dict[str, Any]) -> AgentResult:
        self.calls.append((role, payload))
        queue = self.responses.get(role, [])
        if queue:
            result = queue.pop(0)
            if result.role != role:
                raise ContractError(f"provider response role mismatch: expected {role}, got {result.role}")
            return result
        if role == "research":
            return AgentResult(role, "success", "local context inspected", {"claims": [], "sources": []}, self.provider_id, f"fixture-{role}-{len(self.calls)}")
        if role == "plan":
            return AgentResult(role, "success", "smallest safe slice planned", {"acceptance_criteria": ["a reviewable change exists"], "validation_commands": []}, self.provider_id, f"fixture-{role}-{len(self.calls)}")
        if role == "implement":
            return AgentResult(role, "success", "no-op fixture implementation", {"changes": [], "validation_commands": []}, self.provider_id, f"fixture-{role}-{len(self.calls)}")
        if role == "test":
            return AgentResult(role, "success", "fixture tests pass", {"commands": []}, self.provider_id, f"fixture-{role}-{len(self.calls)}")
        if role == "review":
            return AgentResult(role, "success", "independent fixture review passed", {"findings": []}, self.provider_id, f"fixture-{role}-{len(self.calls)}")
        return AgentResult(role, "success", "fixture judge accepted", {}, self.provider_id, f"fixture-{role}-{len(self.calls)}")


def result_from_mapping(role: Phase, value: Mapping[str, Any], provider_id: str = "external") -> AgentResult:
    """Build an AgentResult from a validated, detached provider mapping."""
    if not isinstance(value, Mapping):
        raise ContractError("provider result must be a JSON object")
    safe_value = _copy_json_value(value)
    _assert_safe(safe_value)
    unknown = set(safe_value) - _RESULT_KEYS
    if unknown:
        raise ContractError(f"provider result contains unknown fields: {', '.join(sorted(unknown))}")
    if not isinstance(provider_id, str) or not provider_id.strip():
        raise ContractError("provider_id must be a non-empty string")
    safe_identifier(provider_id, "provider_id")
    declared_role = safe_value.get("role")
    if declared_role is not None and declared_role != role:
        raise ContractError(f"provider response role mismatch: expected {role}, got {declared_role}")
    if "status" not in safe_value:
        raise ContractError("provider status is required")
    status = safe_value["status"]
    if status not in {"success", "blocked", "failed"}:
        raise ContractError("provider status is invalid")
    summary = safe_value.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ContractError("provider summary is required")
    outputs = safe_value.get("outputs", {})
    if not isinstance(outputs, Mapping):
        raise ContractError("provider outputs must be an object")
    changes = outputs.get("changes")
    if changes is not None:
        if not isinstance(changes, list):
            raise ContractError("provider changes must be a list")
        for item in changes:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("content"), str):
                raise ContractError("provider changes require path and content")
            DraftChange(item["path"], item["content"])
    commands = outputs.get("commands")
    if commands is not None:
        if not isinstance(commands, list) or any(
            not isinstance(command, list)
            or not command
            or any(not isinstance(argument, str) or "\x00" in argument for argument in command)
            for command in commands
        ):
            raise ContractError("provider commands must be non-empty string argv lists")
    run_id = safe_value.get("run_id", "")
    if not isinstance(run_id, str):
        raise ContractError("provider run_id must be a string")
    if run_id:
        safe_identifier(run_id, "provider run_id")
    decision = safe_value.get("decision")
    if decision is not None:
        if not isinstance(decision, str) or decision not in {"completed", "needs_plan", "needs_human_input", "reject"}:
            raise ContractError("provider decision is invalid")
        if role != "review":
            raise ContractError("only Review agents may return a decision")
    return AgentResult(role, status, summary, dict(outputs), provider_id, run_id, decision)
