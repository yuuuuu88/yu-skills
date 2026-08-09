from __future__ import annotations

FILES: dict[str, str] = {
    "nous-pilot.toml": r'''[project]
mode = "personal"

[run]
cycles = 3
lease_seconds = 600
timeout_seconds = 300

[agents]
mode = "external"
reviewers = 2

[research]
web = true

[remote]
mode = "ask"
''',
    "src/nous_pilot/config.py": r'''from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .models import ContractError


class ConfigError(ContractError):
    """Raised when the small user-facing config is invalid."""


@dataclass(frozen=True)
class PilotConfig:
    path: Path
    mode: str = "personal"
    cycles: int = 3
    lease_seconds: int = 600
    timeout_seconds: int = 300
    agent_mode: str = "external"
    reviewers: int = 2
    web_research: bool = True
    remote_mode: str = "ask"

    def __post_init__(self) -> None:
        if self.mode not in {"personal"}:
            raise ConfigError("project.mode must be personal")
        if self.cycles < 1 or self.cycles > 10:
            raise ConfigError("run.cycles must be between 1 and 10")
        if self.lease_seconds < 60 or self.timeout_seconds < 1:
            raise ConfigError("run lease and timeout values are invalid")
        if self.agent_mode not in {"external"}:
            raise ConfigError("agents.mode must be external")
        if self.reviewers < 2 or self.reviewers > 5:
            raise ConfigError("agents.reviewers must be between 2 and 5")
        if self.remote_mode not in {"ask", "allow", "deny"}:
            raise ConfigError("remote.mode must be ask, allow, or deny")


def _section(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, Mapping):
        raise ConfigError(f"[{name}] must be a table")
    return value


def load_config(path: Path | None = None) -> PilotConfig:
    config_path = (path or Path.cwd() / "nous-pilot.toml").expanduser().resolve()
    data: dict[str, Any] = {}
    if config_path.is_file():
        try:
            data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"could not read {config_path.name}") from exc
    project = _section(data, "project")
    run = _section(data, "run")
    agents = _section(data, "agents")
    research = _section(data, "research")
    remote = _section(data, "remote")
    return PilotConfig(
        path=config_path,
        mode=str(project.get("mode", "personal")),
        cycles=int(run.get("cycles", 3)),
        lease_seconds=int(run.get("lease_seconds", 600)),
        timeout_seconds=int(run.get("timeout_seconds", 300)),
        agent_mode=str(agents.get("mode", "external")),
        reviewers=int(agents.get("reviewers", 2)),
        web_research=bool(research.get("web", True)),
        remote_mode=str(remote.get("mode", "ask")),
    )


def _absolute_tool(name: str) -> str:
    candidate = Path(name).expanduser()
    if candidate.is_file():
        return str(candidate.resolve())
    for directory in os.environ.get("PATH", "/usr/bin:/bin").split(os.pathsep):
        resolved = Path(directory) / name
        if resolved.is_file():
            return str(resolved.resolve())
    raise ConfigError(f"agent-nous binary was not found: {name}")


def write_internal_engine_config(config: PilotConfig, *, repository_root: Path, path: Path | None = None) -> Path:
    """Generate hidden runtime configuration from the single user config file."""
    root = repository_root.expanduser().resolve()
    target = (path or root / ".nous" / "config.json").expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    agent_dir = root / "scripts" / "agents"
    reviewer = ["/usr/bin/python3", str(agent_dir / "review.py")]
    reviewers = [{"id": f"reviewer-{index}", "perspective": "security and quality" if index == 1 else "requirements and regression", "command": reviewer} for index in range(1, config.reviewers + 1)]
    payload = {
        "nous_home": os.environ.get("NOUS_HOME", str(Path.home() / ".local" / "share" / "nous-pilot")),
        "engine": {
            "agents": {role: ["/usr/bin/python3", str(agent_dir / f"{role}.py")] for role in ("plan", "implement", "review")},
            "review": {"mode": "cross", "fallback": "none", "max_parallel": config.reviewers, "reviewers": reviewers},
            "validation": {"allowed_processes": [["/usr/bin/python3", "-B", "scripts/validate_compile.py"], ["/usr/bin/python3", "-B", "-m", "pytest"]]},
            "min_cycles": config.cycles,
            "max_cycles": 10,
            "max_steps": 80,
            "lease_seconds": config.lease_seconds,
            "agent_timeout_seconds": config.timeout_seconds,
            "resources": {"max_concurrent_agents": max(3, config.reviewers + 1), "max_output_bytes": 4194304, "max_tool_calls": 500, "max_wall_seconds": 900},
            "durability": {"mode": "local"},
            "subagents": {"required": True, "mode": config.agent_mode},
            "research": {"enabled": config.web_research, "mode": "open_web_read_only", "public_https": config.web_research, "allow_domains": ["*"], "blocked_private_networks": True, "allow_credentials": False, "allow_external_writes": False, "execute_downloads": False, "max_requests": 20, "max_response_bytes": 10485760, "max_total_bytes": 52428800, "max_concurrent_requests": 2, "timeout_seconds": 30, "max_redirects": 5, "max_results": 10, "search_endpoint_template": "https://html.duckduckgo.com/html/?q={query}", "user_agent": "nous-pilot-research/0.1"},
        },
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target
''',
    "src/nous_pilot/cli.py": r'''from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

from .config import PilotConfig, load_config, write_internal_engine_config
from .context import build_repository_context
from .control_plane import PilotControlPlane
from .runtime import NousRuntimeClient, RuntimeConfig


def _load_user_config(args: argparse.Namespace) -> PilotConfig:
    return load_config(Path(args.config))


def _runtime(args: argparse.Namespace, config: PilotConfig) -> NousRuntimeClient:
    binary_name = args.nous_binary or os.environ.get("NOUS_BINARY", "nous")
    binary = Path(binary_name).expanduser()
    if not binary.is_file():
        located = shutil.which(binary_name)
        if located is None:
            raise ValueError("agent-nous binary was not found; set NOUS_BINARY or use --nous-binary")
        binary = Path(located)
    home = Path(args.nous_home or os.environ.get("NOUS_HOME", Path.home() / ".local" / "share" / "nous-pilot"))
    timeout = args.timeout_seconds or config.timeout_seconds
    return NousRuntimeClient(RuntimeConfig(binary, home, timeout))


def _request(path: str) -> dict[str, object]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("request file must contain a JSON object")
    return value


def _add_config_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="nous-pilot.toml", help="optional single user config file")
    parser.add_argument("--nous-binary", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--nous-home", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--timeout-seconds", type=float, default=None, help=argparse.SUPPRESS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nous-pilot")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="build a safe repository context summary")
    inspect.add_argument("repository", nargs="?", default=".")
    doctor = sub.add_parser("doctor", help="check the local agent-nous runtime")
    _add_config_options(doctor)
    create = sub.add_parser("create", help="create a durable local Root Task")
    create.add_argument("request_file")
    create.add_argument("--run-id", required=True)
    _add_config_options(create)
    run = sub.add_parser("run", help="create and execute a durable local Root Task")
    run.add_argument("request_file")
    run.add_argument("--run-id", required=True)
    run.add_argument("--engine-config", default=None, help=argparse.SUPPRESS)
    _add_config_options(run)
    args = parser.parse_args(argv)
    if args.command == "inspect":
        context = build_repository_context(Path(args.repository))
        print(json.dumps(context.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    config = _load_user_config(args)
    if args.command == "doctor":
        print(json.dumps(_runtime(args, config).doctor().payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    control_plane = PilotControlPlane(_runtime(args, config))
    request = _request(args.request_file)
    result = control_plane.create(request, run_id=args.run_id)
    output: dict[str, object] = {"task_id": result.task_id, "request": result.request.as_dict(), "created": dict(result.created.payload), "config": str(config.path)}
    if args.command == "run":
        engine_config = Path(args.engine_config).expanduser() if args.engine_config else write_internal_engine_config(config, repository_root=config.path.parent)
        executed = control_plane.runtime.engine_run(result.task_id, engine_config=engine_config)
        output["execution"] = dict(executed.payload)
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
    "src/nous_pilot/__init__.py": r'''"""Nous Pilot: a research-first software development control plane."""

from .capabilities import (
    CapabilityNegotiator,
    CostLedger,
    ProviderCapabilities,
    ProviderPolicy,
    RetryController,
    RetryPolicy,
)
from .child_loops import ChildLoopDAG, IntegrationReview
from .config import ConfigError, PilotConfig, load_config, write_internal_engine_config
from .control_plane import PilotControlPlane, PilotRun
from .evaluation import QualityReport, SubagentUtilizationReport
from .models import AgentResult, DevelopmentRequest, Finding, RunReport, VerificationEvidence
from .mcp import MCPAuthContext, MCPHTTPGateway, MCPHostAdapter, MCPPolicy, MCPToolSpec
from .observability import OTLPExporter
from .orchestrator import PilotEngine, PilotEngineConfig
from .persistence import SQLiteTaskStore
from .runtime import NousRuntimeClient, RuntimeCommandError, RuntimeConfig

__all__ = [
    "AgentResult",
    "CapabilityNegotiator",
    "ConfigError",
    "ChildLoopDAG",
    "CostLedger",
    "DevelopmentRequest",
    "Finding",
    "IntegrationReview",
    "MCPAuthContext",
    "MCPHTTPGateway",
    "MCPHostAdapter",
    "MCPPolicy",
    "MCPToolSpec",
    "PilotControlPlane",
    "PilotConfig",
    "PilotEngine",
    "PilotEngineConfig",
    "PilotRun",
    "ProviderCapabilities",
    "ProviderPolicy",
    "QualityReport",
    "RetryController",
    "RetryPolicy",
    "RunReport",
    "RuntimeCommandError",
    "RuntimeConfig",
    "NousRuntimeClient",
    "OTLPExporter",
    "SQLiteTaskStore",
    "SubagentUtilizationReport",
    "VerificationEvidence",
    "load_config",
    "write_internal_engine_config",
]
''',
    "src/nous_pilot/capabilities.py": r'''from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, TypeVar

from .models import ContractError, Phase


class CapabilityError(ContractError):
    """Raised when a provider cannot satisfy a declared contract."""


class CostLimitError(ContractError):
    """Raised before a provider invocation would exceed its cost budget."""


class RetryableProviderError(RuntimeError):
    """Provider error that may be retried within the declared policy."""


@dataclass(frozen=True)
class ProviderCapabilities:
    provider_id: str
    supports_roles: frozenset[Phase]
    structured_output: bool = True
    tool_use: bool = False
    network: bool = False
    credentials: bool = False
    workspace: bool = False
    cancellation: bool = False

    def supports(self, role: Phase) -> bool:
        return role in self.supports_roles


@dataclass(frozen=True)
class ProviderPolicy:
    required_roles: frozenset[Phase] = frozenset()
    require_structured_output: bool = True
    require_workspace: bool = False
    require_cancellation: bool = False
    allow_network: bool = False
    max_cost_usd: float | None = None


@dataclass(frozen=True)
class NegotiationResult:
    provider_id: str
    accepted: bool
    missing_roles: tuple[Phase, ...] = ()
    missing_capabilities: tuple[str, ...] = ()


class CapabilityNegotiator:
    def negotiate(self, capabilities: ProviderCapabilities, policy: ProviderPolicy) -> NegotiationResult:
        missing_roles = tuple(sorted(policy.required_roles - capabilities.supports_roles))
        missing: list[str] = []
        if policy.require_structured_output and not capabilities.structured_output:
            missing.append("structured_output")
        if policy.require_workspace and not capabilities.workspace:
            missing.append("workspace")
        if policy.require_cancellation and not capabilities.cancellation:
            missing.append("cancellation")
        if not policy.allow_network and capabilities.network:
            missing.append("network must be disabled")
        result = NegotiationResult(capabilities.provider_id, not missing_roles and not missing, missing_roles, tuple(missing))
        if not result.accepted:
            raise CapabilityError(f"provider {capabilities.provider_id} does not satisfy the declared policy")
        return result


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    retryable_errors: frozenset[type[BaseException]] = frozenset({RetryableProviderError, TimeoutError})

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ContractError("max_attempts must be positive")


T = TypeVar("T")


class RetryController:
    def __init__(self, policy: RetryPolicy) -> None:
        self.policy = policy

    def run(self, operation: Callable[[], T]) -> T:
        last_error: BaseException | None = None
        for _ in range(self.policy.max_attempts):
            try:
                return operation()
            except tuple(self.policy.retryable_errors) as exc:
                last_error = exc
        assert last_error is not None
        raise last_error


@dataclass
class CostLedger:
    limit_usd: float | None = None
    spent_usd: float = 0.0
    entries: list[tuple[str, float]] = field(default_factory=list)

    def record(self, provider_id: str, amount_usd: float) -> float:
        if amount_usd < 0:
            raise ContractError("cost cannot be negative")
        projected = self.spent_usd + amount_usd
        if self.limit_usd is not None and projected > self.limit_usd:
            raise CostLimitError("provider cost limit would be exceeded")
        self.spent_usd = projected
        self.entries.append((provider_id, amount_usd))
        return self.spent_usd
''',
    "src/nous_pilot/child_loops.py": r'''from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from .models import ContractError, safe_identifier


@dataclass(frozen=True)
class ChildLoop:
    loop_id: str
    dependencies: tuple[str, ...] = ()
    read_scope: tuple[str, ...] = ()
    write_scope: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        safe_identifier(self.loop_id, "loop_id")
        if self.loop_id in self.dependencies:
            raise ContractError("child loop cannot depend on itself")


class ChildLoopDAG:
    def __init__(self) -> None:
        self._nodes: dict[str, ChildLoop] = {}

    def add(self, node: ChildLoop) -> None:
        if node.loop_id in self._nodes:
            raise ContractError(f"duplicate child loop: {node.loop_id}")
        self._nodes[node.loop_id] = node

    def waves(self) -> tuple[tuple[str, ...], ...]:
        remaining = dict(self._nodes)
        completed: set[str] = set()
        waves: list[tuple[str, ...]] = []
        while remaining:
            ready = tuple(sorted(loop_id for loop_id, node in remaining.items() if set(node.dependencies) <= completed))
            if not ready:
                raise ContractError("child loop dependencies contain a cycle or missing node")
            waves.append(ready)
            completed.update(ready)
            for loop_id in ready:
                remaining.pop(loop_id)
        return tuple(waves)


@dataclass(frozen=True)
class IntegrationReview:
    accepted: bool
    conflicts: tuple[tuple[str, str, str], ...] = ()

    @classmethod
    def from_write_sets(cls, write_sets: Mapping[str, Sequence[str]]) -> "IntegrationReview":
        conflicts: list[tuple[str, str, str]] = []
        items = sorted(write_sets.items())
        for index, (left_id, left_paths) in enumerate(items):
            for right_id, right_paths in items[index + 1:]:
                for path in sorted(set(left_paths) & set(right_paths)):
                    conflicts.append((left_id, right_id, path))
        return cls(not conflicts, tuple(conflicts))
''',
    "src/nous_pilot/verification_profiles.py": r'''from __future__ import annotations

from dataclasses import dataclass

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
    return VerificationProfileRegistry((
        VerificationProfile("static", (("python3", "-B", "scripts/validate_compile.py"),)),
        VerificationProfile("unit", (("python3", "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider"),)),
        VerificationProfile("security", (("python3", "-B", "scripts/validate_compile.py"),)),
    ))
''',
    "src/nous_pilot/handoff.py": r'''from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .models import ContractError, safe_identifier


_SECRET_WORDS = ("secret", "password", "token", "api_key", "private_key", "credential")


def _assert_safe(value: Any, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key).lower()
            if any(word in key_text for word in _SECRET_WORDS):
                raise ContractError(f"secret-like handoff field is not allowed: {path}.{key}")
            _assert_safe(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_safe(item, f"{path}[{index}]")


@dataclass(frozen=True)
class StructuredHandoff:
    handoff_id: str
    producer_role: str
    consumer_role: str
    input_digest: str
    scope: tuple[str, ...]
    outputs: Mapping[str, Any]
    assumptions: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        safe_identifier(self.handoff_id, "handoff_id")
        if not self.producer_role.strip() or not self.consumer_role.strip() or not self.input_digest.strip():
            raise ContractError("handoff roles and input digest are required")
        _assert_safe(self.outputs)

    def as_dict(self) -> dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "producer_role": self.producer_role,
            "consumer_role": self.consumer_role,
            "input_digest": self.input_digest,
            "scope": list(self.scope),
            "outputs": dict(self.outputs),
            "assumptions": list(self.assumptions),
            "unresolved": list(self.unresolved),
        }
''',
    "src/nous_pilot/evaluation.py": r'''from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class SubagentUtilizationReport:
    expected_roles: tuple[str, ...]
    invoked_roles: tuple[str, ...]
    missing_roles: tuple[str, ...]
    total_invocations: int
    independent_review_count: int
    fallback_count: int

    @property
    def utilization_rate(self) -> float:
        return len(set(self.invoked_roles)) / len(set(self.expected_roles)) if self.expected_roles else 1.0

    @classmethod
    def from_calls(cls, calls: Iterable[Mapping[str, Any]], expected_roles: Iterable[str], review_quorum: int = 2) -> "SubagentUtilizationReport":
        rows = list(calls)
        expected = tuple(dict.fromkeys(expected_roles))
        invoked = tuple(dict.fromkeys(str(row.get("role")) for row in rows))
        reviews = sum(1 for row in rows if row.get("role") == "review")
        return cls(expected, invoked, tuple(role for role in expected if role not in invoked), len(rows), min(reviews, review_quorum), sum(1 for row in rows if row.get("fallback")))


@dataclass(frozen=True)
class QualityReport:
    acceptance_rate: float
    blocking_findings: int
    passed_verifications: int
    failed_verifications: int

    @classmethod
    def from_evidence(cls, findings: Iterable[Mapping[str, Any]], evidence: Iterable[Mapping[str, Any]]) -> "QualityReport":
        finding_rows = list(findings)
        evidence_rows = list(evidence)
        passed = sum(1 for item in evidence_rows if item.get("status") == "passed")
        failed = sum(1 for item in evidence_rows if item.get("status") != "passed")
        blocking = sum(1 for item in finding_rows if item.get("severity") in {"blocker", "major"})
        return cls(1.0 if not blocking and not failed else 0.0, blocking, passed, failed)
''',
    "src/nous_pilot/adapters.py": r'''from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .models import ContractError, Finding, safe_identifier


class PolicyDenied(ContractError):
    """Raised when an external adapter crosses its explicit policy boundary."""


@dataclass(frozen=True)
class IssueRecord:
    issue_id: str
    title: str
    body: str
    labels: tuple[str, ...] = ()
    source: str = "local"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "IssueRecord":
        issue_id = safe_identifier(str(value.get("issue_id", "")), "issue_id")
        title = str(value.get("title", "")).strip()
        if not title:
            raise ContractError("issue title is required")
        return cls(issue_id, title, str(value.get("body", "")), tuple(str(label) for label in value.get("labels", ())), str(value.get("source", "local")))


class ReadOnlyIssueAdapter:
    def intake(self, value: Mapping[str, Any]) -> IssueRecord:
        return IssueRecord.from_mapping(value)


@dataclass(frozen=True)
class CIEvent:
    event_id: str
    status: str
    conclusion: str
    findings: tuple[Finding, ...] = ()


class CIEventAdapter:
    def from_mapping(self, value: Mapping[str, Any]) -> CIEvent:
        event_id = safe_identifier(str(value.get("event_id", "")), "event_id")
        status = str(value.get("status", "")).strip()
        conclusion = str(value.get("conclusion", "")).strip()
        findings: list[Finding] = []
        if conclusion.lower() in {"failure", "failed", "cancelled"}:
            findings.append(Finding(f"ci-{event_id}", "major", f"CI concluded with {conclusion}"))
        return CIEvent(event_id, status, conclusion, tuple(findings))


@dataclass(frozen=True)
class DraftPRProposal:
    task_id: str
    title: str
    body: str
    changed_paths: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    requires_human_approval: bool = True


class DraftPRAdapter:
    def build(self, task_id: str, title: str, body: str, changed_paths: Sequence[str], evidence_refs: Sequence[str]) -> DraftPRProposal:
        safe_identifier(task_id, "task_id")
        if not title.strip() or not body.strip():
            raise ContractError("draft PR title and body are required")
        return DraftPRProposal(task_id, title.strip(), body.strip(), tuple(changed_paths), tuple(evidence_refs))


@dataclass(frozen=True)
class RemoteGitPolicy:
    allowed_operations: frozenset[str] = frozenset()
    require_human_approval: bool = True
    network_allowed: bool = False
    credentials_allowed: bool = False

    def authorize(self, operation: str, *, human_approved: bool = False) -> None:
        if operation not in self.allowed_operations or not self.network_allowed or not self.credentials_allowed:
            raise PolicyDenied(f"remote Git operation is denied by policy: {operation}")
        if self.require_human_approval and not human_approved:
            raise PolicyDenied("remote Git operation requires human approval")


class RemoteGitAdapter:
    def __init__(self, policy: RemoteGitPolicy) -> None:
        self.policy = policy

    def execute(self, operation: str, *, human_approved: bool = False) -> None:
        self.policy.authorize(operation, human_approved=human_approved)
        raise NotImplementedError("remote Git execution is a separately deployed adapter")


@dataclass(frozen=True)
class BrowserVerificationResult:
    status: str
    summary: str
    evidence_refs: tuple[str, ...] = ()


class BrowserVerificationAdapter:
    def verify(self, url: str, *, host_capability_granted: bool = False) -> BrowserVerificationResult:
        if not host_capability_granted:
            return BrowserVerificationResult("blocked", "browser verification requires an explicit host capability")
        if not url.startswith(("http://", "https://")):
            raise ContractError("browser URL must use HTTP or HTTPS")
        return BrowserVerificationResult("not_configured", "browser provider is not configured in the local Phase 3 slice")
''',
    "src/nous_pilot/persistence.py": r'''from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .handoff import _assert_safe
from .models import ContractError, safe_identifier


class TaskStore(Protocol):
    def put_task(self, task_id: str, payload: Mapping[str, Any]) -> None: ...
    def get_task(self, task_id: str) -> dict[str, Any] | None: ...
    def append_event(self, task_id: str, event: Mapping[str, Any]) -> None: ...
    def list_events(self, task_id: str) -> tuple[dict[str, Any], ...]: ...


class SQLiteTaskStore:
    """Durable shared-store equivalent for local and single-host deployments."""

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS task_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, event TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            """)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def put_task(self, task_id: str, payload: Mapping[str, Any]) -> None:
        safe_identifier(task_id, "task_id")
        _assert_safe(payload)
        encoded = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            connection.execute("INSERT INTO tasks(task_id, payload) VALUES (?, ?) ON CONFLICT(task_id) DO UPDATE SET payload=excluded.payload, updated_at=CURRENT_TIMESTAMP", (task_id, encoded))

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def append_event(self, task_id: str, event: Mapping[str, Any]) -> None:
        safe_identifier(task_id, "task_id")
        _assert_safe(event)
        with self._connect() as connection:
            connection.execute("INSERT INTO task_events(task_id, event) VALUES (?, ?)", (task_id, json.dumps(dict(event), ensure_ascii=False, sort_keys=True)))

    def list_events(self, task_id: str) -> tuple[dict[str, Any], ...]:
        with self._connect() as connection:
            rows = connection.execute("SELECT event FROM task_events WHERE task_id = ? ORDER BY event_id", (task_id,)).fetchall()
        return tuple(json.loads(row[0]) for row in rows)
''',
    "src/nous_pilot/observability.py": r'''from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from .handoff import _assert_safe


@dataclass(frozen=True)
class ObservationEvent:
    event_type: str
    trace_id: str
    attributes: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        _assert_safe(self.attributes)
        return {"event_type": self.event_type, "trace_id": self.trace_id, "attributes": dict(self.attributes)}


class EventExporter(Protocol):
    def export(self, event: ObservationEvent) -> None: ...


class JSONLExporter:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, event: ObservationEvent) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.as_dict(), ensure_ascii=False, sort_keys=True) + "\n")


class RecordingExporter:
    def __init__(self) -> None:
        self.events: list[ObservationEvent] = []

    def export(self, event: ObservationEvent) -> None:
        self.events.append(event)


class OTLPExporter:
    """Vendor-neutral OTLP boundary; the host supplies the network sender."""

    def __init__(self, sender: Callable[[Mapping[str, Any]], None]) -> None:
        self.sender = sender

    def export(self, event: ObservationEvent) -> None:
        self.sender(event.as_dict())


def digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
''',
    "src/nous_pilot/mcp.py": r'''from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .adapters import PolicyDenied
from .models import ContractError, safe_identifier


@dataclass(frozen=True)
class MCPToolSpec:
    name: str
    handler: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    mutating: bool = False

    def __post_init__(self) -> None:
        safe_identifier(self.name, "tool_name")


@dataclass(frozen=True)
class MCPAuthContext:
    subject: str
    scopes: frozenset[str]
    oauth_verified: bool = False


@dataclass(frozen=True)
class MCPPolicy:
    allowed_tools: frozenset[str]
    required_scope: str = "nous:invoke"
    require_oauth_for_http: bool = True
    allow_mutations: bool = False


class MCPHostAdapter:
    """Policy host for local MCP-style tools; it does not open a network listener."""

    def __init__(self, policy: MCPPolicy) -> None:
        self.policy = policy
        self._tools: dict[str, MCPToolSpec] = {}

    def register(self, tool: MCPToolSpec) -> None:
        if tool.name in self._tools:
            raise ContractError(f"duplicate MCP tool: {tool.name}")
        self._tools[tool.name] = tool

    def invoke(self, name: str, arguments: Mapping[str, Any], auth: MCPAuthContext, *, transport: str = "local") -> Mapping[str, Any]:
        if name not in self.policy.allowed_tools or name not in self._tools:
            raise PolicyDenied(f"MCP tool is not allowed: {name}")
        if self.policy.required_scope not in auth.scopes:
            raise PolicyDenied("MCP scope is missing")
        if transport == "http" and self.policy.require_oauth_for_http and not auth.oauth_verified:
            raise PolicyDenied("HTTP MCP invocation requires verified OAuth")
        tool = self._tools[name]
        if tool.mutating and not self.policy.allow_mutations:
            raise PolicyDenied("MCP mutation is disabled by policy")
        return dict(tool.handler(arguments))


class MCPHTTPGateway:
    """Translate an already-authenticated HTTP request into the policy host.

    The gateway deliberately does not bind a socket; deployment owns the HTTP
    server, TLS, and OAuth verifier and passes the verified auth context here.
    """

    def __init__(self, host: MCPHostAdapter) -> None:
        self.host = host

    def handle(self, request: Mapping[str, Any], auth: MCPAuthContext) -> Mapping[str, Any]:
        name = request.get("tool")
        arguments = request.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, Mapping):
            raise ContractError("MCP HTTP request requires tool and object arguments")
        return self.host.invoke(name, arguments, auth, transport="http")
''',
    "src/nous_pilot/distribution.py": r'''from __future__ import annotations

import platform
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class CompatibilityReport:
    supported: bool
    python_version: str
    platform: str
    reasons: tuple[str, ...] = ()


def check_compatibility() -> CompatibilityReport:
    reasons: list[str] = []
    if sys.version_info < (3, 11):
        reasons.append("Python 3.11 or newer is required")
    return CompatibilityReport(not reasons, platform.python_version(), platform.platform(), tuple(reasons))


@dataclass(frozen=True)
class ReleaseManifest:
    version: str
    required_python: str
    migrations: tuple[str, ...]
    compatibility: CompatibilityReport

    def as_dict(self) -> dict[str, object]:
        return {"version": self.version, "required_python": self.required_python, "migrations": list(self.migrations), "compatibility": self.compatibility.__dict__}
''',
    "tests/test_phase2_control_plane.py": r'''from pathlib import Path

import pytest

from nous_pilot.capabilities import CapabilityError, CapabilityNegotiator, CostLedger, CostLimitError, ProviderCapabilities, ProviderPolicy, RetryController, RetryPolicy, RetryableProviderError
from nous_pilot.child_loops import ChildLoop, ChildLoopDAG, IntegrationReview
from nous_pilot.evaluation import QualityReport, SubagentUtilizationReport
from nous_pilot.handoff import StructuredHandoff
from nous_pilot.models import ContractError
from nous_pilot.verification_profiles import default_profiles


def test_capability_negotiation_retry_cost_and_profiles() -> None:
    capabilities = ProviderCapabilities("fixture", frozenset({"plan", "implement"}), workspace=True)
    assert CapabilityNegotiator().negotiate(capabilities, ProviderPolicy(required_roles=frozenset({"plan"}))).accepted
    with pytest.raises(CapabilityError):
        CapabilityNegotiator().negotiate(capabilities, ProviderPolicy(required_roles=frozenset({"review"})))
    state = {"attempts": 0}
    def operation() -> str:
        state["attempts"] += 1
        if state["attempts"] < 2:
            raise RetryableProviderError("temporary")
        return "ok"
    assert RetryController(RetryPolicy(max_attempts=2)).run(operation) == "ok"
    with pytest.raises(CostLimitError):
        CostLedger(limit_usd=1).record("fixture", 2)
    assert "unit" in default_profiles().names()


def test_child_loop_waves_and_integration_conflicts() -> None:
    dag = ChildLoopDAG()
    dag.add(ChildLoop("research"))
    dag.add(ChildLoop("implement", ("research",), write_scope=("src/a.py",)))
    dag.add(ChildLoop("docs", ("research",), write_scope=("docs/a.md",)))
    assert dag.waves() == (("research",), ("docs", "implement"))
    assert not IntegrationReview.from_write_sets({"a": ("x",), "b": ("x",)}).accepted


def test_handoff_and_metrics_reject_secret_and_measure_roles() -> None:
    with pytest.raises(ContractError):
        StructuredHandoff("h1", "plan", "implement", "digest", (), {"api_key": "never"})
    report = SubagentUtilizationReport.from_calls(({"role": "plan"}, {"role": "review"}, {"role": "review"}), ("plan", "implement", "review"))
    assert report.missing_roles == ("implement",)
    assert QualityReport.from_evidence((), ({"status": "passed"},)).acceptance_rate == 1.0
''',
    "tests/test_config.py": r'''import json
from pathlib import Path

from nous_pilot.config import load_config, write_internal_engine_config


def test_one_user_config_has_small_defaults_and_generates_internal_runtime(tmp_path: Path) -> None:
    config_file = tmp_path / "nous-pilot.toml"
    config_file.write_text("[run]\ncycles = 4\n\n[remote]\nmode = 'allow'\n", encoding="utf-8")
    config = load_config(config_file)
    assert config.cycles == 4
    assert config.lease_seconds == 600
    assert config.timeout_seconds == 300
    internal = write_internal_engine_config(config, repository_root=tmp_path)
    payload = json.loads(internal.read_text(encoding="utf-8"))
    assert payload["engine"]["min_cycles"] == 4
    assert payload["engine"]["lease_seconds"] == 600
    assert payload["engine"]["agent_timeout_seconds"] == 300


def test_missing_config_uses_personal_defaults(tmp_path: Path) -> None:
    config = load_config(tmp_path / "missing.toml")
    assert config.mode == "personal"
    assert config.cycles == 3
    assert config.remote_mode == "ask"
''',
    "tests/test_phase3_adapters.py": r'''import pytest

from nous_pilot.adapters import BrowserVerificationAdapter, CIEventAdapter, DraftPRAdapter, PolicyDenied, ReadOnlyIssueAdapter, RemoteGitAdapter, RemoteGitPolicy
from nous_pilot.mcp import MCPAuthContext, MCPHTTPGateway, MCPHostAdapter, MCPPolicy, MCPToolSpec


def test_issue_ci_pr_and_remote_policy_adapters() -> None:
    issue = ReadOnlyIssueAdapter().intake({"issue_id": "I-1", "title": "Improve", "body": "details"})
    assert issue.title == "Improve"
    event = CIEventAdapter().from_mapping({"event_id": "ci-1", "status": "completed", "conclusion": "failure"})
    assert event.findings[0].severity == "major"
    proposal = DraftPRAdapter().build("task-1", "Draft", "body", ("src/a.py",), ("e1",))
    assert proposal.requires_human_approval
    with pytest.raises(PolicyDenied):
        RemoteGitAdapter(RemoteGitPolicy()).execute("push")
    assert BrowserVerificationAdapter().verify("https://example.com").status == "blocked"


def test_mcp_policy_requires_scope_and_oauth_for_http() -> None:
    host = MCPHostAdapter(MCPPolicy(frozenset({"echo"})))
    host.register(MCPToolSpec("echo", lambda arguments: {"echo": arguments["value"]}))
    auth = MCPAuthContext("user-1", frozenset({"nous:invoke"}))
    assert host.invoke("echo", {"value": "ok"}, auth)["echo"] == "ok"
    gateway = MCPHTTPGateway(host)
    oauth_auth = MCPAuthContext("user-1", frozenset({"nous:invoke"}), oauth_verified=True)
    assert gateway.handle({"tool": "echo", "arguments": {"value": "http"}}, oauth_auth)["echo"] == "http"
    with pytest.raises(PolicyDenied):
        host.invoke("echo", {"value": "ok"}, auth, transport="http")
''',
    "tests/test_phase4_durable.py": r'''from pathlib import Path

import pytest

from nous_pilot.distribution import check_compatibility
from nous_pilot.models import ContractError
from nous_pilot.observability import JSONLExporter, ObservationEvent, RecordingExporter
from nous_pilot.persistence import SQLiteTaskStore


def test_sqlite_store_is_durable_and_secret_safe(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "tasks.db")
    store.put_task("task-1", {"objective": "test"})
    store.append_event("task-1", {"type": "created"})
    assert store.get_task("task-1")["objective"] == "test"
    assert store.list_events("task-1")[0]["type"] == "created"
    with pytest.raises(ContractError):
        store.put_task("task-2", {"token": "secret"})


def test_observability_and_compatibility_are_local() -> None:
    event = ObservationEvent("phase.completed", "trace-1", {"role": "review"})
    recorder = RecordingExporter()
    recorder.export(event)
    assert recorder.events[0].as_dict()["trace_id"] == "trace-1"
    assert check_compatibility().supported
''',
}
