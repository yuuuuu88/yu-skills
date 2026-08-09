from __future__ import annotations

FILES: dict[str, str] = {
    "pyproject.toml": r'''[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "nous-pilot"
version = "0.1.0"
description = "Research-first, subagent-first software development control plane built on the Nous Loop."
requires-python = ">=3.11"
dependencies = []

[project.optional-dependencies]
test = ["pytest>=7,<9"]

[project.scripts]
nous-pilot = "nous_pilot.cli:main"

[tool.setuptools]
package-dir = {"" = "src"}

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
addopts = "-q"
''',
    "scripts/validate_compile.py": r'''from __future__ import annotations

from pathlib import Path


errors: list[str] = []
for root_name in ("src", "tests"):
    root = Path(root_name)
    for path in sorted(root.rglob("*.py")):
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec", dont_inherit=True)
        except (OSError, SyntaxError) as exc:
            errors.append(f"{path}: {exc}")

if errors:
    print("\n".join(errors))
    raise SystemExit(1)

print("syntax-ok")
''',
    "src/nous_pilot/__init__.py": r'''"""Nous Pilot: a research-first software development control plane."""

from .models import (
    AgentResult,
    DevelopmentRequest,
    Finding,
    RunReport,
    VerificationEvidence,
)
from .orchestrator import PilotEngine, PilotEngineConfig
from .control_plane import PilotControlPlane, PilotRun
from .runtime import NousRuntimeClient, RuntimeCommandError, RuntimeConfig

__all__ = [
    "AgentResult",
    "DevelopmentRequest",
    "Finding",
    "PilotEngine",
    "PilotEngineConfig",
    "PilotControlPlane",
    "PilotRun",
    "RunReport",
    "NousRuntimeClient",
    "RuntimeCommandError",
    "RuntimeConfig",
    "VerificationEvidence",
]
''',
    "src/nous_pilot/models.py": r'''from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping

Phase = Literal["research", "plan", "implement", "test", "review", "judge"]
RiskClass = Literal["green", "yellow", "red"]


class ContractError(ValueError):
    """Raised when an external or internal contract is invalid."""


def safe_identifier(value: str, label: str = "identifier") -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} must be a non-empty string")
    if any(char in value for char in "/\\\x00\r\n"):
        raise ContractError(f"{label} contains an unsafe character")
    return value.strip()


def string_tuple(value: Any, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ContractError(f"{label} must be a list of non-empty strings")
    return tuple(item.strip() for item in value)


@dataclass(frozen=True)
class DevelopmentRequest:
    request_id: str
    objective: str
    repository_root: Path
    constraints: tuple[str, ...] = ()
    non_goals: tuple[str, ...] = ()
    risk_class: RiskClass = "green"
    validation_profile: str = "unit"
    base_revision: str | None = None

    def __post_init__(self) -> None:
        safe_identifier(self.request_id, "request_id")
        if not self.objective.strip():
            raise ContractError("objective must be non-empty")
        if self.risk_class not in {"green", "yellow", "red"}:
            raise ContractError("risk_class must be green, yellow, or red")
        if not self.repository_root.is_dir():
            raise ContractError("repository_root must be an existing directory")
        if not self.validation_profile.strip():
            raise ContractError("validation_profile must be non-empty")

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "objective": self.objective,
            "repository_root": str(self.repository_root),
            "constraints": list(self.constraints),
            "non_goals": list(self.non_goals),
            "risk_class": self.risk_class,
            "validation_profile": self.validation_profile,
            "base_revision": self.base_revision,
        }


@dataclass(frozen=True)
class AgentResult:
    role: Phase
    status: Literal["success", "blocked", "failed"]
    summary: str
    outputs: dict[str, Any] = field(default_factory=dict)
    provider_id: str = "unknown"
    run_id: str = ""

    def __post_init__(self) -> None:
        if self.role not in {"research", "plan", "implement", "test", "review", "judge"}:
            raise ContractError(f"unsupported agent role: {self.role}")
        if self.status not in {"success", "blocked", "failed"}:
            raise ContractError(f"unsupported agent status: {self.status}")
        if not self.summary.strip():
            raise ContractError("agent summary must be non-empty")


@dataclass(frozen=True)
class Finding:
    finding_id: str
    severity: Literal["blocker", "major", "minor", "advisory"]
    summary: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        safe_identifier(self.finding_id, "finding_id")
        if self.severity not in {"blocker", "major", "minor", "advisory"}:
            raise ContractError("invalid finding severity")
        if not self.summary.strip():
            raise ContractError("finding summary must be non-empty")

    @property
    def blocking(self) -> bool:
        return self.severity in {"blocker", "major"}


@dataclass(frozen=True)
class VerificationEvidence:
    verification_id: str
    command: tuple[str, ...]
    returncode: int
    status: Literal["passed", "failed", "blocked"]
    stdout_sha256: str
    stderr_sha256: str
    summary: str

    def __post_init__(self) -> None:
        safe_identifier(self.verification_id, "verification_id")
        if not self.command:
            raise ContractError("verification command must be non-empty")
        if self.status not in {"passed", "failed", "blocked"}:
            raise ContractError("invalid verification status")


@dataclass(frozen=True)
class RunReport:
    request_id: str
    outcome: Literal["completed", "needs_plan", "blocked"]
    cycles: int
    agent_calls: tuple[dict[str, Any], ...]
    findings: tuple[Finding, ...]
    evidence: tuple[VerificationEvidence, ...]
    staged_change_paths: tuple[str, ...]
    diagnostics: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "outcome": self.outcome,
            "cycles": self.cycles,
            "agent_calls": [dict(item) for item in self.agent_calls],
            "findings": [
                {"finding_id": item.finding_id, "severity": item.severity, "summary": item.summary, "evidence_refs": list(item.evidence_refs)}
                for item in self.findings
            ],
            "evidence": [
                {
                    "verification_id": item.verification_id,
                    "command": list(item.command),
                    "returncode": item.returncode,
                    "status": item.status,
                    "stdout_sha256": item.stdout_sha256,
                    "stderr_sha256": item.stderr_sha256,
                    "summary": item.summary,
                }
                for item in self.evidence
            ],
            "staged_change_paths": list(self.staged_change_paths),
            "diagnostics": list(self.diagnostics),
        }
''',
    "src/nous_pilot/intake.py": r'''from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .models import ContractError, DevelopmentRequest, RiskClass, safe_identifier, string_tuple


def normalize_request(value: Mapping[str, Any]) -> DevelopmentRequest:
    """Turn a request or Issue-shaped mapping into a validated local request."""
    if not isinstance(value, Mapping):
        raise ContractError("request must be an object")
    request_id = safe_identifier(str(value.get("request_id", "")), "request_id")
    objective = value.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        raise ContractError("objective must be a non-empty string")
    repository = value.get("repository_root", value.get("target_repository"))
    if not isinstance(repository, str) or not repository.strip():
        raise ContractError("repository_root is required")
    root = Path(repository).expanduser().resolve()
    risk = value.get("risk_class", "green")
    if not isinstance(risk, str) or risk not in {"green", "yellow", "red"}:
        raise ContractError("risk_class must be green, yellow, or red")
    validation_profile = value.get("validation_profile", "unit")
    if not isinstance(validation_profile, str) or not validation_profile.strip():
        raise ContractError("validation_profile must be a non-empty string")
    base_revision = value.get("base_revision")
    if base_revision is not None and (not isinstance(base_revision, str) or not base_revision.strip()):
        raise ContractError("base_revision must be a non-empty string or null")
    return DevelopmentRequest(
        request_id=request_id,
        objective=objective.strip(),
        repository_root=root,
        constraints=string_tuple(value.get("constraints"), "constraints"),
        non_goals=string_tuple(value.get("non_goals"), "non_goals"),
        risk_class=risk,  # type: ignore[arg-type]
        validation_profile=validation_profile.strip(),
        base_revision=base_revision.strip() if isinstance(base_revision, str) else None,
    )
''',
    "src/nous_pilot/control_plane.py": r'''from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .intake import normalize_request
from .models import DevelopmentRequest
from .runtime import NousRuntimeClient, RuntimeCommandError, RuntimeResponse


@dataclass(frozen=True)
class PilotRun:
    request: DevelopmentRequest
    task_id: str
    created: RuntimeResponse
    execution: RuntimeResponse | None = None


class PilotControlPlane:
    """Coordinate intake and the durable local agent-nous Root Task boundary."""

    def __init__(self, runtime: NousRuntimeClient) -> None:
        self.runtime = runtime

    @staticmethod
    def _task_id(response: RuntimeResponse) -> str:
        result = response.payload.get("result")
        candidate = result.get("task_id") if isinstance(result, dict) else response.payload.get("task_id")
        if not isinstance(candidate, str) or not candidate:
            raise RuntimeCommandError("agent-nous create response did not contain a task_id")
        return candidate

    def create(self, request_value: Mapping[str, Any], *, run_id: str) -> PilotRun:
        request = normalize_request(request_value)
        created = self.runtime.create_root_task(request, run_id=run_id)
        return PilotRun(request, self._task_id(created), created)

    def run(self, request_value: Mapping[str, Any], *, run_id: str, engine_config: Path) -> PilotRun:
        created = self.create(request_value, run_id=run_id)
        execution = self.runtime.engine_run(created.task_id, engine_config=engine_config)
        return PilotRun(created.request, created.task_id, created.created, execution)
''',
    "src/nous_pilot/context.py": r'''from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

_EXCLUDED = {".git", ".nous", "__pycache__", ".pytest_cache", ".venv", "node_modules"}
_ALLOWED_TOP_LEVEL = {"README.md", "AGENTS.md", ".codex", "pyproject.toml", "package.json", "docs", "src", "tests", "scripts", "examples"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class FileObservation:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class RepositoryContext:
    repository_root: Path
    files: tuple[FileObservation, ...]
    source_of_truth_paths: tuple[str, ...]
    validation_hints: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "repository_root": str(self.repository_root),
            "files": [item.__dict__ for item in self.files],
            "source_of_truth_paths": list(self.source_of_truth_paths),
            "validation_hints": list(self.validation_hints),
        }


def build_repository_context(root: Path, *, max_files: int = 500) -> RepositoryContext:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("repository root does not exist")
    observations: list[FileObservation] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in _EXCLUDED for part in relative.parts):
            continue
        if not path.is_file() or path.is_symlink():
            continue
        if relative.parts[0] not in _ALLOWED_TOP_LEVEL:
            continue
        observations.append(FileObservation(str(relative), path.stat().st_size, _sha256(path)))
        if len(observations) >= max_files:
            break
    source_paths = tuple(
        item.path for item in observations
        if item.path in {"README.md", "AGENTS.md", ".codex"} or item.path.startswith("docs/")
    )
    validation_hints = tuple(
        command for command in (
            "python3 -B -m pytest -q -p no:cacheprovider",
            "python3 -B scripts/validate_compile.py",
        )
        if any(item.path == "pyproject.toml" for item in observations)
    )
    return RepositoryContext(root, tuple(observations), source_paths, validation_hints)
''',
    "src/nous_pilot/runtime.py": r'''from __future__ import annotations

import json
import os
import subprocess
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
        environment = {"PATH": "/usr/bin:/bin", "NOUS_HOME": str(self.config.nous_home)}
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
''',
    "src/nous_pilot/provider.py": r'''from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .models import AgentResult, ContractError, Phase


class AgentProvider(Protocol):
    provider_id: str

    def invoke(self, role: Phase, payload: dict[str, Any]) -> AgentResult:
        ...


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


def result_from_mapping(role: Phase, value: dict[str, Any], provider_id: str = "external") -> AgentResult:
    status = value.get("status", "success")
    if status not in {"success", "blocked", "failed"}:
        raise ContractError("provider status is invalid")
    summary = value.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ContractError("provider summary is required")
    outputs = value.get("outputs", {})
    if not isinstance(outputs, dict):
        raise ContractError("provider outputs must be an object")
    return AgentResult(role, status, summary, outputs, provider_id, str(value.get("run_id", "")))
''',
    "src/nous_pilot/workspace.py": r'''from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .models import ContractError, safe_identifier


@dataclass(frozen=True)
class DraftChange:
    path: str
    content: str

    def __post_init__(self) -> None:
        path = Path(self.path)
        if not self.path.strip() or path.is_absolute() or ".." in path.parts:
            raise ContractError("change path must be a relative path inside the workspace")
        if ".git" in path.parts:
            raise ContractError("change cannot target Git metadata")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StagedWorkspace:
    root: Path
    changes: tuple[DraftChange, ...]

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(item.path for item in self.changes)


class WorkspaceManager:
    """Stage proposed changes outside the target repository by default."""

    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = runtime_root.resolve()
        self.runtime_root.mkdir(parents=True, exist_ok=True)

    def stage(self, workspace_id: str, changes: list[DraftChange]) -> StagedWorkspace:
        safe_identifier(workspace_id, "workspace_id")
        root = (self.runtime_root / workspace_id).resolve()
        if self.runtime_root not in root.parents:
            raise ContractError("workspace escaped runtime root")
        root.mkdir(parents=True, exist_ok=False)
        for change in changes:
            target = (root / change.path).resolve()
            if root not in target.parents:
                raise ContractError("change escaped staged workspace")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(change.content, encoding="utf-8")
        return StagedWorkspace(root, tuple(changes))
''',
    "src/nous_pilot/verification.py": r'''from __future__ import annotations

import hashlib
import subprocess
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
                env={"PATH": "/usr/bin:/bin", "PYTHONPATH": "src"},
            )
            status = "passed" if result.returncode == 0 else "failed"
            summary = f"returncode={result.returncode}; elapsed_ms={int((time.monotonic() - started) * 1000)}"
            return VerificationEvidence(verification_id, command, result.returncode, status, _digest(result.stdout), _digest(result.stderr), summary)
        except subprocess.TimeoutExpired:
            return VerificationEvidence(verification_id, command, 124, "failed", _digest(""), _digest("timeout"), "verification timed out")
        except OSError as exc:
            return VerificationEvidence(verification_id, command, 127, "failed", _digest(""), _digest(type(exc).__name__), f"verification failed to start: {type(exc).__name__}")
''',
    "src/nous_pilot/orchestrator.py": r'''from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .context import RepositoryContext, build_repository_context
from .intake import normalize_request
from .models import AgentResult, ContractError, DevelopmentRequest, Finding, RunReport, VerificationEvidence
from .provider import AgentProvider
from .verification import VerificationRunner
from .workspace import DraftChange, WorkspaceManager


@dataclass(frozen=True)
class PilotEngineConfig:
    min_cycles: int = 1
    max_cycles: int = 3
    review_quorum: int = 2
    runtime_root: Path = Path(".nous-pilot-runtime")

    def __post_init__(self) -> None:
        if self.min_cycles < 1 or self.max_cycles < self.min_cycles:
            raise ContractError("cycle bounds are invalid")
        if self.review_quorum < 2:
            raise ContractError("review_quorum must be at least two")


class PilotEngine:
    """Execute a development workflow with explicit role separation."""

    def __init__(self, provider: AgentProvider, verifier: VerificationRunner, config: PilotEngineConfig | None = None) -> None:
        self.provider = provider
        self.verifier = verifier
        self.config = config or PilotEngineConfig()

    @staticmethod
    def _record(calls: list[dict[str, Any]], result: AgentResult, cycle: int, reviewer_id: str | None = None) -> None:
        calls.append({
            "role": result.role,
            "cycle": cycle,
            "reviewer_id": reviewer_id,
            "provider_id": result.provider_id,
            "run_id": result.run_id,
            "status": result.status,
            "summary": result.summary,
        })

    @staticmethod
    def _parse_changes(value: Any) -> list[DraftChange]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ContractError("implement changes must be a list")
        changes: list[DraftChange] = []
        for item in value:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("content"), str):
                raise ContractError("each change requires path and content")
            changes.append(DraftChange(item["path"], item["content"]))
        return changes

    @staticmethod
    def _parse_findings(value: Any, reviewer_id: str) -> list[Finding]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ContractError(f"{reviewer_id} findings must be a list")
        findings: list[Finding] = []
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise ContractError(f"{reviewer_id} finding {index} must be an object")
            findings.append(Finding(
                str(item.get("finding_id", f"{reviewer_id}-{index}")),
                item.get("severity", "major"),
                str(item.get("summary", "")),
                tuple(str(ref) for ref in item.get("evidence_refs", [])),
            ))
        return findings

    def run(self, request_value: dict[str, Any]) -> RunReport:
        request = normalize_request(request_value)
        context = build_repository_context(request.repository_root)
        calls: list[dict[str, Any]] = []
        all_findings: list[Finding] = []
        all_evidence: list[VerificationEvidence] = []
        staged_paths: list[str] = []
        diagnostics: list[str] = []
        workspace_manager = WorkspaceManager(self.config.runtime_root)

        for cycle in range(1, self.config.max_cycles + 1):
            research = self.provider.invoke("research", {"request": request.as_dict(), "context": context.as_dict(), "cycle": cycle})
            self._record(calls, research, cycle)
            if research.status != "success":
                diagnostics.append(f"research blocked in cycle {cycle}")
                return RunReport(request.request_id, "blocked", cycle, tuple(calls), tuple(all_findings), tuple(all_evidence), tuple(staged_paths), tuple(diagnostics))

            plan = self.provider.invoke("plan", {"request": request.as_dict(), "context": context.as_dict(), "research": research.outputs, "cycle": cycle})
            self._record(calls, plan, cycle)
            if plan.status != "success":
                diagnostics.append(f"plan blocked in cycle {cycle}")
                return RunReport(request.request_id, "blocked", cycle, tuple(calls), tuple(all_findings), tuple(all_evidence), tuple(staged_paths), tuple(diagnostics))

            implementation = self.provider.invoke("implement", {"request": request.as_dict(), "context": context.as_dict(), "plan": plan.outputs, "cycle": cycle})
            self._record(calls, implementation, cycle)
            if implementation.status != "success":
                diagnostics.append(f"implementation blocked in cycle {cycle}")
                return RunReport(request.request_id, "blocked", cycle, tuple(calls), tuple(all_findings), tuple(all_evidence), tuple(staged_paths), tuple(diagnostics))
            changes = self._parse_changes(implementation.outputs.get("changes"))
            if changes:
                staged = workspace_manager.stage(f"{request.request_id}-cycle-{cycle}", changes)
                staged_paths.extend(staged.paths)

            test = self.provider.invoke("test", {"request": request.as_dict(), "context": context.as_dict(), "plan": plan.outputs, "implementation": implementation.outputs, "cycle": cycle})
            self._record(calls, test, cycle)
            if test.status != "success":
                diagnostics.append(f"test provider blocked in cycle {cycle}")
                return RunReport(request.request_id, "blocked", cycle, tuple(calls), tuple(all_findings), tuple(all_evidence), tuple(staged_paths), tuple(diagnostics))
            commands = test.outputs.get("commands", plan.outputs.get("validation_commands", []))
            if commands is None:
                commands = []
            if not isinstance(commands, list):
                raise ContractError("test commands must be a list")
            for index, raw_command in enumerate(commands):
                if not isinstance(raw_command, list) or any(not isinstance(item, str) for item in raw_command):
                    raise ContractError("test command must be a list of strings")
                evidence = self.verifier.run(f"cycle-{cycle}-test-{index}", tuple(raw_command), request.repository_root)
                all_evidence.append(evidence)

            cycle_findings: list[Finding] = []
            for reviewer_index in range(self.config.review_quorum):
                reviewer_id = f"reviewer-{reviewer_index + 1}"
                review = self.provider.invoke("review", {
                    "request": request.as_dict(),
                    "context": context.as_dict(),
                    "plan": plan.outputs,
                    "implementation": implementation.outputs,
                    "test": test.outputs,
                    "evidence": [item.__dict__ for item in all_evidence if item.verification_id.startswith(f"cycle-{cycle}-")],
                    "reviewer_id": reviewer_id,
                    "cycle": cycle,
                })
                self._record(calls, review, cycle, reviewer_id)
                if review.status != "success":
                    cycle_findings.append(Finding(f"{reviewer_id}-provider", "major", "review provider failed"))
                    continue
                cycle_findings.extend(self._parse_findings(review.outputs.get("findings"), reviewer_id))
            all_findings.extend(cycle_findings)
            evidence_failed = any(item.status != "passed" for item in all_evidence if item.verification_id.startswith(f"cycle-{cycle}-"))
            blocking = any(item.blocking for item in cycle_findings) or evidence_failed
            if cycle >= self.config.min_cycles and not blocking:
                return RunReport(request.request_id, "completed", cycle, tuple(calls), tuple(all_findings), tuple(all_evidence), tuple(staged_paths), tuple(diagnostics))
            if blocking:
                diagnostics.append(f"cycle {cycle} requested re-plan")
            else:
                diagnostics.append(f"cycle {cycle} satisfied gates; minimum cycle count requires another loop")

        diagnostics.append("max_cycles reached before acceptance")
        return RunReport(request.request_id, "needs_plan", self.config.max_cycles, tuple(calls), tuple(all_findings), tuple(all_evidence), tuple(staged_paths), tuple(diagnostics))
''',
    "src/nous_pilot/cli.py": r'''from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

from .context import build_repository_context
from .control_plane import PilotControlPlane
from .runtime import NousRuntimeClient, RuntimeConfig


def _runtime(args: argparse.Namespace) -> NousRuntimeClient:
    binary = Path(args.nous_binary)
    if not binary.is_file():
        located = shutil.which(args.nous_binary)
        if located is None:
            raise ValueError("agent-nous binary was not found")
        binary = Path(located)
    return NousRuntimeClient(RuntimeConfig(binary, Path(args.nous_home), args.timeout_seconds))


def _request(path: str) -> dict[str, object]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("request file must contain a JSON object")
    return value


def _add_runtime_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--nous-binary", default=os.environ.get("NOUS_BINARY", "nous"))
    parser.add_argument("--nous-home", default=os.environ.get("NOUS_HOME", "~/.local/share/nous"))
    parser.add_argument("--timeout-seconds", type=float, default=300.0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nous-pilot")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="build a safe repository context summary")
    inspect.add_argument("repository", nargs="?", default=".")
    doctor = sub.add_parser("doctor", help="check the local agent-nous runtime")
    _add_runtime_options(doctor)
    create = sub.add_parser("create", help="create a durable local Root Task")
    create.add_argument("request_file")
    create.add_argument("--run-id", required=True)
    _add_runtime_options(create)
    run = sub.add_parser("run", help="create and execute a durable local Root Task")
    run.add_argument("request_file")
    run.add_argument("--run-id", required=True)
    run.add_argument("--engine-config", required=True)
    _add_runtime_options(run)
    args = parser.parse_args(argv)
    if args.command == "inspect":
        context = build_repository_context(Path(args.repository))
        print(json.dumps(context.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "doctor":
        print(json.dumps(_runtime(args).doctor().payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command in {"create", "run"}:
        control_plane = PilotControlPlane(_runtime(args))
        request = _request(args.request_file)
        result = control_plane.create(request, run_id=args.run_id)
        output: dict[str, object] = {"task_id": result.task_id, "request": result.request.as_dict(), "created": dict(result.created.payload)}
        if args.command == "run":
            executed = control_plane.runtime.engine_run(result.task_id, engine_config=Path(args.engine_config))
            output["execution"] = dict(executed.payload)
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
''',
    "tests/test_intake_context.py": r'''from pathlib import Path

import pytest

from nous_pilot.context import build_repository_context
from nous_pilot.intake import normalize_request
from nous_pilot.models import ContractError


def test_normalize_request_requires_existing_repository(tmp_path: Path) -> None:
    with pytest.raises(ContractError):
        normalize_request({"request_id": "r1", "objective": "x", "repository_root": str(tmp_path / "missing")})


def test_context_hashes_allowed_files_and_excludes_runtime(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    (tmp_path / ".nous").mkdir()
    (tmp_path / ".nous" / "secret.json").write_text("runtime", encoding="utf-8")
    context = build_repository_context(tmp_path)
    assert [item.path for item in context.files] == ["README.md"]
    assert context.source_of_truth_paths == ("README.md",)
''',
    "tests/test_workspace_verification.py": r'''from pathlib import Path

import pytest

from nous_pilot.models import ContractError
from nous_pilot.verification import VerificationPolicy, VerificationRunner
from nous_pilot.workspace import DraftChange, WorkspaceManager


def test_workspace_rejects_path_escape(tmp_path: Path) -> None:
    with pytest.raises(ContractError):
        DraftChange("../escape.txt", "bad")
    manager = WorkspaceManager(tmp_path / "runtime")
    staged = manager.stage("w1", [DraftChange("src/result.txt", "ok")])
    assert staged.paths == ("src/result.txt",)
    assert (staged.root / "src/result.txt").read_text(encoding="utf-8") == "ok"
    with pytest.raises(ContractError):
        DraftChange("nested/.git/config", "bad")


def test_verification_requires_allowlisted_argv(tmp_path: Path) -> None:
    runner = VerificationRunner(VerificationPolicy((("/usr/bin/python3", "-c"),)))
    blocked = runner.run("blocked", ("/bin/sh", "-c", "echo bad"), tmp_path)
    assert blocked.status == "blocked"
    passed = runner.run("passed", ("/usr/bin/python3", "-c", "print('ok')"), tmp_path)
    assert passed.status == "passed"
''',
    "tests/test_runtime_adapter.py": r'''import json
import os
from pathlib import Path

from nous_pilot.control_plane import PilotControlPlane
from nous_pilot.models import DevelopmentRequest
from nous_pilot.runtime import NousRuntimeClient, RuntimeConfig


def test_runtime_adapter_uses_explicit_home_and_extracts_task_id(tmp_path: Path) -> None:
    shim = tmp_path / "nous-shim"
    shim.write_text(
        "#!/usr/bin/python3\n"
        "import json, sys\n"
        "payload = json.load(sys.stdin) if '--input' in sys.argv else {}\n"
        "print(json.dumps({'ok': True, 'result': {'task_id': 'task-local-1'}, 'echo': {'argv': sys.argv, 'payload': payload}}))\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    client = NousRuntimeClient(RuntimeConfig(shim, tmp_path / "runtime"))
    request = {"request_id": "req-runtime", "objective": "start", "repository_root": str(tmp_path)}
    run = PilotControlPlane(client).create(request, run_id="run-1")
    assert run.task_id == "task-local-1"


def test_runtime_config_rejects_missing_binary(tmp_path: Path) -> None:
    from nous_pilot.models import ContractError
    import pytest

    with pytest.raises(ContractError):
        RuntimeConfig(tmp_path / "missing", tmp_path / "runtime")
''',
    "tests/test_provider_contract.py": r'''from pathlib import Path

from nous_pilot.models import AgentResult
from nous_pilot.orchestrator import PilotEngine, PilotEngineConfig
from nous_pilot.provider import RecordingProvider
from nous_pilot.verification import VerificationPolicy, VerificationRunner


def test_engine_runs_research_implementation_test_and_two_independent_reviews(tmp_path: Path) -> None:
    provider = RecordingProvider()
    request = {"request_id": "req-1", "objective": "inspect safely", "repository_root": str(tmp_path)}
    engine = PilotEngine(
        provider,
        VerificationRunner(VerificationPolicy((("/usr/bin/python3", "-c"),))),
        PilotEngineConfig(min_cycles=1, max_cycles=1, runtime_root=tmp_path / "runtime"),
    )
    report = engine.run(request)
    assert report.outcome == "completed"
    assert [call[0] for call in provider.calls] == ["research", "plan", "implement", "test", "review", "review"]
    assert {call[1]["reviewer_id"] for call in provider.calls if call[0] == "review"} == {"reviewer-1", "reviewer-2"}


def test_engine_replans_after_blocking_review(tmp_path: Path) -> None:
    provider = RecordingProvider(responses={
        "review": [
            AgentResult("review", "success", "blocking", {"findings": [{"finding_id": "f1", "severity": "major", "summary": "missing evidence"}]}),
            AgentResult("review", "success", "blocking", {"findings": [{"finding_id": "f2", "severity": "major", "summary": "missing evidence"}]}),
            AgentResult("review", "success", "pass", {"findings": []}),
            AgentResult("review", "success", "pass", {"findings": []}),
        ]
    })
    engine = PilotEngine(
        provider,
        VerificationRunner(VerificationPolicy((("/usr/bin/python3", "-c"),))),
        PilotEngineConfig(min_cycles=2, max_cycles=2, runtime_root=tmp_path / "runtime"),
    )
    report = engine.run({"request_id": "req-2", "objective": "replan", "repository_root": str(tmp_path)})
    assert report.outcome == "completed"
    assert report.cycles == 2
    assert report.diagnostics[0] == "cycle 1 requested re-plan"


def test_engine_blocks_when_test_agent_fails(tmp_path: Path) -> None:
    provider = RecordingProvider(responses={
        "test": [AgentResult("test", "failed", "test agent unavailable", {})],
    })
    engine = PilotEngine(
        provider,
        VerificationRunner(VerificationPolicy((('/usr/bin/python3', '-c'),))),
        PilotEngineConfig(min_cycles=1, max_cycles=1, runtime_root=tmp_path / "runtime"),
    )
    report = engine.run({"request_id": "req-test-failure", "objective": "block", "repository_root": str(tmp_path)})
    assert report.outcome == "blocked"
    assert "test provider blocked" in report.diagnostics[0]
''',
}
