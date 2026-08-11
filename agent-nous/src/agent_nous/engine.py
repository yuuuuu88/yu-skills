from __future__ import annotations

"""The phase-agent execution engine.

The original runtime persisted phase drafts but deliberately did not decide
who should produce them.  This module adds that missing boundary.  An
``AgentBackend`` starts one typed Plan, Implement, or Review agent; the engine
owns the lease, turns agent proposals into managed actions, persists the
result, and continues to the next phase.  Agent output is never accepted as
completion evidence until the corresponding managed action or validation has
been executed and observed by the parent process.
"""

import hashlib
import json
import copy
import re
import queue
import shutil
import threading
import time
import uuid
from contextlib import nullcontext
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Protocol

from .diagnostics import (
    REVIEW_CLASSIFICATIONS,
    ProviderDiagnostic,
    make_provider_diagnostic,
)
from .domain import ActionDescriptor, PolicyGateway, secret_summary
from .errors import ConflictError, ProviderFailureError, RecoveryRequiredError, UnsupportedActionError, ValidationError
from .executor.managed import ExecutionResult, ManagedExecutor
from .executor.sandbox import CodexSandbox
from .executor.validation import ValidationCommandPolicy, ValidationSpec
from .io import digest_json, read_json
from .observability import TraceExporter, export_execution_event
from .resources import ResourceBudget
from .subskills import SubskillRuntime
from .taskctl import TaskStore

Phase = Literal["plan", "implement", "review"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(char in value for char in "/\\\x00\r\n"):
        raise ValidationError(f"{label} must be a safe nonempty identifier")
    return value


def _artifact_decision(path: Path, decision: str) -> bool:
    try:
        value = read_json(path)
    except Exception:
        return False
    return isinstance(value, Mapping) and value.get("decision") == decision


def _trace_id_for_task(task_id: str) -> str:
    """Return a stable W3C-compatible trace ID without persisting payload data."""
    return hashlib.sha256(f"nous-trace:{task_id}".encode("utf-8")).hexdigest()[:32]


def _span_id() -> str:
    """Return a fresh 64-bit hexadecimal span identifier."""
    return uuid.uuid4().hex[:16]


def _stable_span_id(label: str) -> str:
    """Return a deterministic parent span for a durable task/loop boundary."""
    return hashlib.sha256(f"nous-span:{label}".encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class AgentContext:
    task_id: str
    phase: Phase
    cycle: int
    definition: dict[str, Any]
    state: dict[str, Any]
    stable_context: dict[str, Any]
    active_artifacts: dict[str, dict[str, Any]]
    repository_root: str | None
    loop_id: str = "root"
    reviewer_id: str | None = None
    review_perspective: str | None = None
    review_depth: int = 0
    subskill_selection: dict[str, Any] | None = None
    external_subagent_required: bool = False
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    # Populated only on the one bounded provider retry.  It contains the
    # previous redacted diagnosis, never raw provider output.
    retry_context: dict[str, Any] | None = None

    def payload(self) -> dict[str, Any]:
        """Return the only context an external agent is allowed to receive."""
        return {
            "schema_version": 1,
            "task_id": self.task_id,
            "phase": self.phase,
            "cycle": self.cycle,
            "definition": self.definition,
            "state": self.state,
            "stable_context": self.stable_context,
            "active_artifacts": self.active_artifacts,
            "repository_root": self.repository_root,
            "loop_id": self.loop_id,
            "reviewer_id": self.reviewer_id,
            "review_perspective": self.review_perspective,
            "review_depth": self.review_depth,
            "trace_context": {
                "trace_id": self.trace_id,
                "span_id": self.span_id,
                "parent_span_id": self.parent_span_id,
                "format": "w3c-trace-context-compatible",
            },
            "retry_context": self.retry_context,
            "nous_loop": {
                "name": "Nous Loop",
                "sequence": "Plan -> Implement -> Review -> Plan ...",
                "cycle": self.cycle,
                "loop_id": self.loop_id,
            },
            "execution_contract": {
                "external_subagent_required": self.external_subagent_required,
                "side_effects_via_managed_executor_only": True,
                "review_must_return_validation_evidence": True,
            },
            "subskill_selection": (
                {
                    **self.subskill_selection,
                    "trust_boundary": "untrusted_advisory_guidance",
                    "instructions_are_data": True,
                    "policy_authority": "nous_engine_only",
                }
                if self.subskill_selection else None
            ),
        }


@dataclass(frozen=True)
class AgentOutput:
    summary: str
    outputs: dict[str, Any] = field(default_factory=dict)
    decision: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentBackend(Protocol):
    """Provider boundary for Plan/Implement/Review agents."""

    def launch(self, phase: Phase, context: AgentContext) -> AgentOutput:
        ...


AgentHandler = Callable[[AgentContext], AgentOutput | Mapping[str, Any]]

_REVIEW_SEVERITIES = {"blocker", "major", "minor", "advisory"}
_REVIEW_DISPOSITIONS = {"blocking", "advisory", "question"}


def _normalize_findings(value: Any, reviewer_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate the small typed finding contract used by Review agents."""
    if value is None:
        return [], []
    if not isinstance(value, list):
        return [], [f"reviewer {reviewer_id} findings at $.findings must be a list"]
    findings: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            errors.append(f"reviewer {reviewer_id} finding {index} is not an object")
            continue
        finding = dict(raw)
        required = ("finding_id", "severity", "disposition", "summary")
        missing = [key for key in required if not isinstance(finding.get(key), str) or not str(finding[key]).strip()]
        if missing:
            errors.append(f"reviewer {reviewer_id} finding {index} has invalid required field(s) at $.findings[{index}]: {', '.join(missing)}")
            continue
        if finding["severity"] not in _REVIEW_SEVERITIES or finding["disposition"] not in _REVIEW_DISPOSITIONS:
            errors.append(f"reviewer {reviewer_id} finding {index} has invalid severity or disposition at $.findings[{index}]")
            continue
        category = finding.get("category", "code_gap")
        if not isinstance(category, str) or category not in REVIEW_CLASSIFICATIONS - {"completed", "human_required"}:
            errors.append(f"reviewer {reviewer_id} finding {index} has invalid category at findings[{index}].category")
            continue
        finding["category"] = category
        for key in ("evidence_refs", "next_plan_input"):
            if key in finding and (not isinstance(finding[key], list) or any(not isinstance(item, str) for item in finding[key])):
                errors.append(f"reviewer {reviewer_id} finding {index} has invalid {key} at $.findings[{index}].{key}")
                break
        else:
            findings.append(finding)
    return findings, errors


def _finding_schema_errors(errors: list[str]) -> list[dict[str, str]]:
    """Turn human-readable validation messages into bounded typed evidence."""
    records: list[dict[str, str]] = []
    for message in errors[:32]:
        match = re.search(r"\$\.findings\[(\d+)\](?:\.([A-Za-z_][A-Za-z0-9_]*))?", message)
        path = f"$.findings[{match.group(1)}]" if match else "$.findings"
        if match and match.group(2):
            path += f".{match.group(2)}"
        records.append({"path": path, "message": message})
    return records


def _finding_is_blocking(finding: Mapping[str, Any]) -> bool:
    # Disposition is the reviewer's explicit routing decision.  Severity is
    # still used as a conservative default for blocker/major findings so a
    # malformed producer cannot accidentally turn a concrete release issue
    # into an advisory merely by omitting disposition.
    return finding.get("disposition") == "blocking" or finding.get("severity") in {"blocker", "major"}


def _plan_granularity(context: AgentContext, values: Mapping[str, Any]) -> dict[str, Any]:
    """Assess plan size without imposing a fixed file-count ceiling.

    The result is advisory input for the next Plan/Review handoff.  Related
    files may remain one atomic change set; only a combination of large
    output, many dependencies, and many acceptance conditions recommends a
    split.  The engine never silently executes a split that the Plan did not
    describe.
    """
    file_values: list[str] = []
    for key in ("planned_files", "affected_files", "files", "targets"):
        raw = values.get(key)
        if isinstance(raw, list):
            file_values.extend(item for item in raw if isinstance(item, str) and item.strip())
    unique_files = list(dict.fromkeys(file_values))
    raw_change_sets = values.get("change_sets", values.get("atomic_change_sets", []))
    change_sets: list[dict[str, Any]] = []
    change_set_errors: list[str] = []
    if raw_change_sets is not None:
        if not isinstance(raw_change_sets, list):
            change_set_errors.append("change_sets must be an array")
        else:
            for index, raw in enumerate(raw_change_sets):
                if not isinstance(raw, Mapping):
                    change_set_errors.append(f"change_sets[{index}] must be an object")
                    continue
                item = dict(raw)
                files = item.get("files", item.get("targets", []))
                done = item.get("definition_of_done", item.get("done", []))
                depends_on = item.get("depends_on", [])
                if not isinstance(files, list) or any(not isinstance(path, str) or not path.strip() for path in files):
                    change_set_errors.append(f"change_sets[{index}].files must be a list of nonempty strings")
                    continue
                if not isinstance(done, list) or any(not isinstance(note, str) or not note.strip() for note in done):
                    change_set_errors.append(f"change_sets[{index}].definition_of_done must be a list of nonempty strings")
                    continue
                if not isinstance(depends_on, list) or any(not isinstance(ref, str) or not ref.strip() for ref in depends_on):
                    change_set_errors.append(f"change_sets[{index}].depends_on must be a list of nonempty strings")
                    continue
                change_sets.append({
                    "id": item.get("id", f"change-set-{index + 1}"),
                    "files": list(dict.fromkeys(files)),
                    "depends_on": list(dict.fromkeys(depends_on)),
                    "definition_of_done": list(done),
                })
    estimated_bytes = values.get("estimated_output_bytes", values.get("estimated_change_bytes", 0))
    if not isinstance(estimated_bytes, int) or isinstance(estimated_bytes, bool) or estimated_bytes < 0:
        estimated_bytes = sum(len(str(values.get(key, "")).encode("utf-8", errors="replace")) for key in ("plan", "planned_content", "acceptance_notes"))
    dependency_values = values.get("dependencies", values.get("dependency_graph", []))
    if isinstance(dependency_values, Mapping):
        dependency_count = len(dependency_values) + sum(len(item) for item in dependency_values.values() if isinstance(item, list))
    elif isinstance(dependency_values, list):
        dependency_count = len(dependency_values)
    else:
        dependency_count = 0
    acceptance_count = len(context.definition.get("acceptance_criteria", [])) if isinstance(context.definition.get("acceptance_criteria"), list) else 0
    complexity_score = round(
        len(unique_files) + estimated_bytes / 20_000 + dependency_count / 3 + acceptance_count / 2,
        2,
    )
    repeated_files: list[str] = []
    # Prior plan artifacts are read by the parent engine in normal operation;
    # this local signal is populated later by ``_plan_granularity_history``.
    prior_files = values.get("prior_planned_files", [])
    if isinstance(prior_files, list):
        repeated_files = sorted(set(unique_files) & {item for item in prior_files if isinstance(item, str)})
    split_reasons: list[str] = []
    if len(unique_files) >= 9:
        split_reasons.append("many_files")
    if estimated_bytes > 120_000:
        split_reasons.append("large_estimated_output")
    if dependency_count >= 16:
        split_reasons.append("many_dependencies")
    if complexity_score >= 16:
        split_reasons.append("high_composite_complexity")
    return {
        "model": "complexity_based_no_fixed_file_limit",
        "file_count": len(unique_files),
        "files": unique_files,
        "estimated_output_bytes": estimated_bytes,
        "dependency_count": dependency_count,
        "acceptance_criteria_count": acceptance_count,
        "complexity_score": complexity_score,
        "atomic_change_sets": change_sets,
        "change_set_errors": change_set_errors,
        "repeated_files": repeated_files,
        "same_file_rewrite_risk": bool(repeated_files),
        "requires_split": bool(split_reasons),
        "split_reasons": split_reasons,
        "definition_of_done_required": bool(split_reasons),
        "recommendation": "split_with_dependency_dag" if split_reasons else "keep_related_changes_atomic",
    }


def _review_input_digest(context: AgentContext) -> str:
    """Digest stable review inputs, excluding lease and revision churn."""
    stable_state = {
        key: value
        for key, value in context.state.items()
        if key not in {"revision", "execution_status", "active_lease", "run_id", "lease_expires_at"}
    }
    return digest_json({
        "task_id": context.task_id,
        "cycle": context.cycle,
        "definition": context.definition,
        "state": stable_state,
        "stable_context": context.stable_context,
        "active_artifacts": context.active_artifacts,
        "subskill_selection": (
            {
                key: context.subskill_selection.get(key)
                for key in ("selection_id", "task_id", "loop_id", "cycle", "phase", "selected", "registry_revision", "input_snapshot_digest", "pinned")
            }
            if context.subskill_selection else None
        ),
    })


def _provider_error(
    category: str,
    *,
    phase: Phase | None = None,
    cycle: int | None = None,
    provider: str | None = None,
    action_id: str | None = None,
    error_path: str = "$",
    expected: str | None = None,
    actual: Any = None,
    stderr: str | None = None,
    retryable: bool = False,
    message: str = "provider output is invalid",
    details: Mapping[str, Any] | None = None,
) -> ProviderFailureError:
    diagnostic = make_provider_diagnostic(
        category,
        phase=phase,
        cycle=cycle,
        provider=provider,
        action_id=action_id,
        error_path=error_path,
        expected=expected,
        actual=actual,
        stderr=stderr,
        retryable=retryable,
        message=message,
        details=details,
    )
    return ProviderFailureError(diagnostic)


class CallableAgentBackend:
    """Small in-process provider used by integrations and deterministic tests.

    The engine still launches a distinct role for every phase.  Side effects
    are not available through this callback; they must be returned as typed
    proposals and are executed by the engine's managed adapter.
    """

    def __init__(self, handlers: Mapping[Phase, AgentHandler], provider: str = "callable") -> None:
        self.handlers = dict(handlers)
        self.provider = provider
        self.external_subagent = False

    def launch(self, phase: Phase, context: AgentContext) -> AgentOutput:
        handler = self.handlers.get(phase)
        if handler is None:
            raise ValidationError(f"no agent backend is registered for phase={phase}")
        started = _now()
        value = handler(context)
        output = _coerce_agent_output(value, allow_reject=phase == "review")
        metadata = {
            **output.metadata,
            "provider": self.provider,
            "agent_role": phase,
            "agent_run_id": f"agent-{phase}-{uuid.uuid4().hex}",
            "started_at": started,
            "finished_at": _now(),
            "execution_mode": "in_process_provider",
        }
        return AgentOutput(output.summary, output.outputs, output.decision, metadata)


class JsonCommandAgentBackend:
    """Launch external phase agents in a read-only repository sandbox.

    The command receives the JSON ``AgentContext`` at the path in
    ``NOUS_AGENT_CONTEXT_FILE`` and must emit one JSON object containing
    ``summary``, ``outputs`` and optional ``decision``.
    It cannot write the target repository.  It may only return proposals; the
    parent engine executes those proposals through ``ManagedExecutor``.
    """

    def __init__(
        self,
        commands: Mapping[Phase, tuple[str, ...] | list[str]],
        *,
        runtime_root: Path,
        source_root: Path | None = None,
        timeout_seconds: float = 180,
        max_output_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        self.external_subagent = True
        self.commands = {phase: tuple(command) for phase, command in commands.items()}
        self.runtime_root = runtime_root.resolve()
        self.source_root = (source_root or Path(__file__).resolve().parents[1]).resolve()
        self.timeout_seconds = timeout_seconds
        if not isinstance(max_output_bytes, int) or not 1_024 <= max_output_bytes <= 16 * 1024 * 1024:
            raise ValidationError("agent max output must be between 1024 and 16777216 bytes")
        self.max_output_bytes = max_output_bytes
        if timeout_seconds <= 0:
            raise ValidationError("agent timeout must be positive")
        if not self.source_root.is_dir():
            raise ValidationError("agent backend source root is not a directory")
        for phase, command in self.commands.items():
            if phase not in {"plan", "implement", "review"} or not command or any(not isinstance(item, str) or not item for item in command):
                raise ValidationError(f"invalid agent command for phase={phase}")

    def launch(self, phase: Phase, context: AgentContext) -> AgentOutput:
        command = self.commands.get(phase)
        if command is None:
            raise ValidationError(f"no external agent command is registered for phase={phase}")
        run_id = f"agent-{phase}-{uuid.uuid4().hex}"
        scratch = self.runtime_root / "agent-runs" / run_id
        scratch.mkdir(parents=True, exist_ok=False)
        agent_span_id = _span_id()
        context_payload = context.payload()
        context_payload["trace_context"] = {
            "trace_id": context.trace_id,
            "span_id": agent_span_id,
            "parent_span_id": context.span_id or context.parent_span_id,
            "format": "w3c-trace-context-compatible",
        }
        context_json = json.dumps(context_payload, ensure_ascii=False, sort_keys=True)
        context_path = scratch / "context.json"
        context_path.write_text(context_json, encoding="utf-8")
        sandbox = CodexSandbox(self.runtime_root / "agent-evidence")
        read_roots = [self.source_root]
        if context.repository_root:
            read_roots.append(Path(context.repository_root).resolve())
        for part in command:
            candidate = Path(part)
            if candidate.is_absolute() and candidate.is_file():
                read_roots.append(candidate.parent.resolve())
        started = _now()
        try:
            result = sandbox.run(
                command,
                writable_root=scratch,
                read_roots=tuple(read_roots),
                timeout_seconds=self.timeout_seconds,
                extra_env={
                    "PYTHONPATH": str(self.source_root),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "NOUS_AGENT_CONTEXT_FILE": str(context_path),
                    "NOUS_TRACE_ID": context.trace_id or "",
                    "NOUS_SPAN_ID": agent_span_id,
                    "NOUS_PARENT_SPAN_ID": context.span_id or context.parent_span_id or "",
                },
            )
        finally:
            # The returned AgentOutput is the durable boundary.  Scratch is
            # not trusted as Task evidence and is removed after the process.
            shutil.rmtree(scratch, ignore_errors=True)
        if secret_summary({"stdout": result.stdout, "stderr": result.stderr}):
            raise _provider_error(
                "invalid_schema",
                phase=phase,
                provider="json-command",
                stderr=result.stderr,
                retryable=False,
                message=f"{phase} agent emitted secret-like output",
            )
        output_bytes = len(result.stdout.encode("utf-8", errors="replace")) + len(result.stderr.encode("utf-8", errors="replace"))
        if output_bytes > self.max_output_bytes:
            raise _provider_error(
                "output_too_large",
                phase=phase,
                provider="json-command",
                stderr=result.stderr,
                expected=f"output <= {self.max_output_bytes} bytes",
                actual=output_bytes,
                retryable=False,
                message=f"{phase} agent output exceeded the configured bound",
            )
        if result.returncode == 124 or "executor timeout" in result.stderr.lower():
            raise _provider_error(
                "timeout",
                phase=phase,
                provider="json-command",
                stderr=result.stderr,
                expected=f"complete within {self.timeout_seconds:g}s",
                actual=result.returncode,
                retryable=True,
                message=f"{phase} agent timed out",
            )
        if result.returncode != 0:
            stderr_digest = hashlib.sha256(result.stderr.encode("utf-8")).hexdigest()
            raise _provider_error(
                "provider_process_failure",
                phase=phase,
                provider="json-command",
                stderr=result.stderr,
                expected="process returncode 0",
                actual=result.returncode,
                retryable=result.returncode in {75, 126, 127},
                message=f"{phase} agent exited with returncode={result.returncode}; stderr_sha256={stderr_digest}",
            )
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise _provider_error(
                "invalid_json",
                phase=phase,
                provider="json-command",
                error_path="$",
                expected="one JSON object",
                actual=result.stdout,
                stderr=result.stderr,
                retryable=True,
                message=f"{phase} agent must emit one JSON object",
                details={"json_error": str(exc)},
            ) from exc
        try:
            output = _coerce_agent_output(value, allow_reject=phase == "review")
        except ProviderFailureError as exc:
            raise exc
        except ValidationError as exc:
            raise _provider_error(
                "invalid_envelope",
                phase=phase,
                provider="json-command",
                expected="summary, outputs, optional decision and metadata",
                actual=value,
                stderr=result.stderr,
                retryable=False,
                message=str(exc),
            ) from exc
        metadata = {
            **output.metadata,
            "provider": "json-command",
            "agent_role": phase,
            "agent_run_id": run_id,
            "started_at": started,
            "finished_at": _now(),
            "execution_mode": "codex_sandbox_subprocess",
            "process_returncode": result.returncode,
            "stdout_sha256": hashlib.sha256(result.stdout.encode("utf-8")).hexdigest(),
            "stderr_sha256": hashlib.sha256(result.stderr.encode("utf-8")).hexdigest(),
            "trace_id": context.trace_id,
            "span_id": agent_span_id,
            "parent_span_id": context.span_id or context.parent_span_id,
        }
        return AgentOutput(output.summary, output.outputs, output.decision, metadata)


class CrossReviewBackend:
    """Run a small set of independent reviewers and merge their evidence.

    Cross-review deliberately has no extra ``review-the-review`` phase.  Each
    reviewer sees the same implementation context plus one short perspective,
    validation proposals are deduplicated, and the parent engine applies the
    normal managed validation boundary once.  A single reviewer can block a
    completion, while successful reviewers cannot override a concrete finding.
    """

    def __init__(
        self,
        base: AgentBackend,
        reviewers: Mapping[str, AgentBackend],
        *,
        perspectives: Mapping[str, str] | None = None,
        max_parallel: int = 3,
        fallback: str = "single",
        timeout_seconds: float = 180,
        primary_reviewer_id: str | None = None,
        secondary_policy: str = "parallel",
    ) -> None:
        if len(reviewers) < 2:
            raise ValidationError("cross-review requires at least two reviewers")
        if len(reviewers) > 2:
            raise ValidationError("cross-review supports at most two reviewers")
        if max_parallel < 1:
            raise ValidationError("cross-review max_parallel must be positive")
        if fallback not in {"single", "human", "none"}:
            raise ValidationError("cross-review fallback must be single, human, or none")
        if timeout_seconds <= 0:
            raise ValidationError("cross-review timeout must be positive")
        if not isinstance(secondary_policy, str) or secondary_policy not in {"parallel", "after_primary_success"}:
            raise ValidationError("cross-review secondary_policy must be parallel or after_primary_success")
        self.base = base
        self.reviewers = tuple(reviewers.items())
        self.perspectives = dict(perspectives or {})
        self.max_parallel = min(max_parallel, len(self.reviewers))
        self.fallback = fallback
        self.timeout_seconds = timeout_seconds
        self.primary_reviewer_id = primary_reviewer_id or self.reviewers[0][0]
        if not isinstance(self.primary_reviewer_id, str) or self.primary_reviewer_id not in dict(self.reviewers):
            raise ValidationError("cross-review primary reviewer is not registered")
        self.secondary_policy = secondary_policy
        self.external_subagent = bool(
            getattr(base, "external_subagent", False)
            and all(getattr(backend, "external_subagent", False) for _, backend in self.reviewers)
        )

    def launch(self, phase: Phase, context: AgentContext) -> AgentOutput:
        if phase != "review":
            return self.base.launch(phase, context)

        return self.launch_review(context)

    def launch_review(
        self,
        context: AgentContext,
        *,
        observer: Callable[[str, AgentOutput | None, str | None], None] | None = None,
        cached: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> AgentOutput:
        cached = cached or {}

        def run_one(item: tuple[str, AgentBackend]) -> tuple[str, AgentOutput | None, str | None]:
            reviewer_id, backend = item
            cached_observation = cached.get(reviewer_id)
            if isinstance(cached_observation, Mapping):
                if cached_observation.get("status") == "failed":
                    return reviewer_id, None, str(cached_observation.get("error", "cached reviewer failure"))
                cached_output = cached_observation.get("output")
                if isinstance(cached_output, Mapping):
                    return reviewer_id, AgentOutput(
                        str(cached_output.get("summary", "cached reviewer result")),
                        dict(cached_output.get("outputs", {})) if isinstance(cached_output.get("outputs"), Mapping) else {},
                        cached_output.get("decision") if isinstance(cached_output.get("decision"), str) else None,
                        dict(cached_output.get("metadata", {})) if isinstance(cached_output.get("metadata"), Mapping) else {},
                    ), None
            reviewer_context = replace(
                context,
                definition=copy.deepcopy(context.definition),
                state=copy.deepcopy(context.state),
                stable_context=copy.deepcopy(context.stable_context),
                active_artifacts=copy.deepcopy(context.active_artifacts),
                subskill_selection=copy.deepcopy(context.subskill_selection),
                reviewer_id=reviewer_id,
                review_perspective=self.perspectives.get(reviewer_id, reviewer_id),
                review_depth=0,
            )
            try:
                return reviewer_id, backend.launch("review", reviewer_context), None
            except ProviderFailureError as exc:
                return reviewer_id, None, exc.category
            except Exception as exc:  # provider failure becomes a bounded finding
                return reviewer_id, None, type(exc).__name__

        results_by_id: dict[str, tuple[str, AgentOutput | None, str | None]] = {}
        # Reviewer providers are read-only by contract.  Use daemon worker
        # threads rather than ThreadPoolExecutor here: an in-process provider
        # cannot be force-killed safely, but a timed-out read-only worker must
        # never keep the parent process (or its lease) alive at interpreter
        # shutdown. External JsonCommand providers also enforce their own
        # subprocess timeout inside the worker.
        result_queue: queue.Queue[tuple[str, AgentOutput | None, str | None]] = queue.Queue()
        pending_items = [item for item in self.reviewers if item[0] not in cached]
        active: dict[str, threading.Thread] = {}

        def start_next() -> None:
            while pending_items and len(active) < self.max_parallel:
                item = pending_items.pop(0)
                thread = threading.Thread(
                    target=lambda current=item: result_queue.put(run_one(current)),
                    name=f"nous-review-{item[0]}",
                    daemon=True,
                )
                active[item[0]] = thread
                thread.start()

        if self.secondary_policy == "after_primary_success":
            primary_item = next(item for item in self.reviewers if item[0] == self.primary_reviewer_id)
            primary_queue: queue.Queue[tuple[str, AgentOutput | None, str | None]] = queue.Queue()
            primary_thread = threading.Thread(
                target=lambda: primary_queue.put(run_one(primary_item)),
                name=f"nous-review-primary-{self.primary_reviewer_id}",
                daemon=True,
            )
            primary_thread.start()
            try:
                primary_result = primary_queue.get(timeout=self.timeout_seconds)
            except queue.Empty:
                primary_result = (self.primary_reviewer_id, None, "TimeoutError")
            results_by_id[primary_result[0]] = primary_result
            if observer is not None:
                observer(*primary_result)
            if primary_result[2] is not None:
                for reviewer_id, _backend in self.reviewers:
                    if reviewer_id == self.primary_reviewer_id:
                        continue
                    skipped = (reviewer_id, None, f"skipped_after_primary_failure:{primary_result[2]}")
                    results_by_id[reviewer_id] = skipped
                    if observer is not None:
                        observer(*skipped)
                results = [results_by_id[reviewer_id] for reviewer_id, _ in self.reviewers]
                return self._merge_review_results(context, results)
            pending_items = [item for item in self.reviewers if item[0] != self.primary_reviewer_id]
        start_next()
        deadline = time.monotonic() + self.timeout_seconds
        while active:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                result = result_queue.get(timeout=remaining)
            except queue.Empty:
                break
            reviewer_id = result[0]
            active.pop(reviewer_id, None)
            results_by_id[reviewer_id] = result
            if observer is not None:
                observer(*result)
            start_next()
        if active:
            for reviewer_id in list(active):
                result = (reviewer_id, None, "TimeoutError")
                results_by_id[reviewer_id] = result
                if observer is not None:
                    observer(*result)
            active.clear()
        if pending_items:
            for reviewer_id, _backend in pending_items:
                result = (reviewer_id, None, "TimeoutError")
                results_by_id[reviewer_id] = result
                if observer is not None:
                    observer(*result)
            pending_items.clear()
        for reviewer_id, _ in self.reviewers:
            if reviewer_id in cached and reviewer_id not in results_by_id:
                results_by_id[reviewer_id] = run_one((reviewer_id, dict(self.reviewers)[reviewer_id]))
        results = [results_by_id[reviewer_id] for reviewer_id, _ in self.reviewers]

        return self._merge_review_results(context, results)

    def _merge_review_results(
        self,
        context: AgentContext,
        results: list[tuple[str, AgentOutput | None, str | None]],
    ) -> AgentOutput:
        proposals: dict[str, dict[str, Any]] = {}
        proposal_fingerprints: dict[str, str] = {}
        semantic_proposals: dict[str, str] = {}
        conflicts: list[str] = []
        blocking: list[str] = []
        findings: list[dict[str, Any]] = []
        finding_schema_errors: list[dict[str, str]] = []
        records: list[dict[str, Any]] = []
        questions: list[Mapping[str, Any]] = []
        decisions: list[str] = []
        input_snapshot_digest = _review_input_digest(context)
        for reviewer_id, output, failure in results:
            if failure is not None:
                decisions.append("needs_plan")
                blocking.append(f"cross-reviewer {reviewer_id} failed: {failure}")
                records.append({"reviewer_id": reviewer_id, "decision": "needs_plan", "status": "failed", "error": failure, "input_snapshot_digest": input_snapshot_digest})
                continue
            assert output is not None
            reviewer_outputs = dict(output.outputs)
            decision = "needs_plan" if output.decision == "reject" else (output.decision or "needs_plan")
            nested_keys = [key for key in ("parallelization_plan", "child_contracts", "reviewers", "review_round") if key in reviewer_outputs]
            if nested_keys:
                blocking.append(f"reviewer {reviewer_id} attempted nested review orchestration: {', '.join(nested_keys)}")
                decision = "needs_plan"
                for key in nested_keys:
                    reviewer_outputs.pop(key, None)
            reviewer_findings, finding_errors = _normalize_findings(reviewer_outputs.get("findings"), reviewer_id)
            findings.extend(reviewer_findings)
            if finding_errors:
                blocking.extend(finding_errors)
                finding_schema_errors.extend(_finding_schema_errors(finding_errors))
                decision = "needs_plan"
            for finding in reviewer_findings:
                if _finding_is_blocking(finding):
                    blocking.append(f"{reviewer_id}: {finding['summary']}")
            raw_proposals = reviewer_outputs.get("validation_proposals", [])
            if not isinstance(raw_proposals, list) or any(not isinstance(item, dict) for item in raw_proposals):
                raw_proposals = []
                blocking.append(f"cross-reviewer {reviewer_id} returned invalid validation proposals")
                decision = "needs_plan"
            for proposal in raw_proposals:
                action_id = proposal.get("action_id")
                if not isinstance(action_id, str) or not action_id.strip():
                    blocking.append(f"cross-reviewer {reviewer_id} returned a proposal without action_id")
                    decision = "needs_plan"
                    continue
                fingerprint = digest_json({key: value for key, value in proposal.items() if key != "action_id"})
                prior_fingerprint = proposal_fingerprints.get(action_id)
                if prior_fingerprint is not None and prior_fingerprint != fingerprint:
                    conflicts.append(f"action_id conflict: {action_id}")
                    proposals.pop(action_id, None)
                    continue
                prior_action_id = semantic_proposals.get(fingerprint)
                if prior_action_id is not None:
                    # Different reviewers may choose different local IDs for
                    # the same managed validation.  Execute the semantic
                    # action once; the reviewer records retain both sources.
                    proposal_fingerprints[action_id] = fingerprint
                    continue
                semantic_proposals[fingerprint] = action_id
                proposal_fingerprints[action_id] = fingerprint
                proposals.setdefault(action_id, dict(proposal))
            raw_blocking = reviewer_outputs.get("blocking_issues", [])
            if isinstance(raw_blocking, list):
                for index, item in enumerate(raw_blocking):
                    if isinstance(item, str) and item.strip():
                        legacy = {
                            "finding_id": f"legacy-{reviewer_id}-{index}",
                            "severity": "blocker",
                            "disposition": "blocking",
                            "summary": item,
                            "evidence_refs": [],
                            "next_plan_input": [],
                        }
                        findings.append(legacy)
                        blocking.append(f"{reviewer_id}: {item}")
            question = reviewer_outputs.get("human_question")
            if decision == "needs_human_input" and isinstance(question, Mapping):
                questions.append(question)
            if decision == "completed" and not raw_proposals:
                blocking.append(f"cross-reviewer {reviewer_id} returned no validation proposals")
                decision = "needs_plan"
            decisions.append(decision)
            records.append({
                "reviewer_id": reviewer_id,
                "perspective": self.perspectives.get(reviewer_id, reviewer_id),
                "decision": decision,
                "status": "completed",
                "summary": output.summary,
                "validation_proposal_count": len(raw_proposals),
                "finding_ids": [str(item["finding_id"]) for item in reviewer_findings],
                "provider": output.metadata.get("provider"),
                "agent_run_id": output.metadata.get("agent_run_id"),
                "output_digest": digest_json({"summary": output.summary, "outputs": output.outputs, "decision": output.decision}),
                "input_snapshot_digest": input_snapshot_digest,
            })

        failed_reviewers = [item for item in records if item.get("status") == "failed"]
        successful_outputs = [output for _, output, failure in results if output is not None and failure is None]
        if failed_reviewers and self.fallback == "single" and successful_outputs:
            fallback_output = successful_outputs[0]
            fallback_outputs = dict(fallback_output.outputs)
            fallback_outputs["cross_review"] = {
                "mode": "degraded_single",
                "decision_policy": "single_successful_reviewer_after_provider_failure",
                "fallback_used": True,
                "fallback_reason": "provider_failure",
                "reviewers": records,
                "conflicts": conflicts,
            }
            fallback_outputs["review_provenance"] = {
                "review_pass_id": f"review-pass-{uuid.uuid4().hex}",
                "review_depth": 0,
                "parent_review_id": None,
                "input_state_revision": context.state.get("revision"),
                "input_snapshot_digest": input_snapshot_digest,
                "reviewer_ids": [item[0] for item in self.reviewers],
            }
            fallback_metadata = {
                **fallback_output.metadata,
                "provider": "cross-review",
                "execution_mode": "degraded_single_review",
                "fallback_used": True,
                "reviewer_count": len(self.reviewers),
            }
            fallback_decision = "needs_plan" if fallback_output.decision == "reject" else (fallback_output.decision or "needs_plan")
            fallback_proposals = fallback_outputs.get("validation_proposals")
            fallback_findings, fallback_finding_errors = _normalize_findings(fallback_outputs.get("findings"), "degraded-single")
            if (
                not isinstance(fallback_proposals, list)
                or any(not isinstance(item, Mapping) for item in fallback_proposals)
                or not fallback_proposals
                or fallback_finding_errors
                or any(_finding_is_blocking(item) for item in fallback_findings)
                or bool(fallback_outputs.get("blocking_issues"))
            ):
                fallback_decision = "needs_plan"
            elif fallback_decision == "needs_human_input" and not isinstance(fallback_outputs.get("human_question"), Mapping):
                fallback_decision = "needs_plan"
            return _coerce_agent_output(AgentOutput("cross-review degraded to one reviewer", fallback_outputs, fallback_decision, fallback_metadata))

        fallback_human = bool(failed_reviewers and self.fallback == "human")
        if fallback_human:
            questions.append({
                "question": "Cross-reviewerの一部が利用できません。Reviewを停止しますか？",
                "why_now": "複数視点のReviewを完了できないため、安全側で停止します。",
                "options": [
                    {"option_id": "stop", "label": "停止", "impact": "Reviewer復旧後に再開します。"},
                ],
                "recommended_option_id": "stop",
                "free_text_allowed": False,
                "blocking_scope": "root_task",
                "blocked_refs": [],
                "affected_loop_ids": [context.loop_id],
                "resume_target": {"resume_phase": "review", "resume_transition": "answer_received", "blocked_action_ref": None, "blocked_artifact_ref": None},
                "approval_request_ref": None,
            })

        if conflicts:
            blocking.extend(f"cross-review conflict: {item}" for item in conflicts)
            for action_id in conflicts:
                proposals.pop(action_id.split(": ", 1)[-1], None)
        if fallback_human:
            decision = "needs_human_input"
        elif any(item == "needs_plan" for item in decisions) or blocking:
            decision = "needs_plan"
        elif questions:
            decision = "needs_human_input"
        elif any(item == "needs_human_input" for item in decisions):
            blocking.append("a cross-reviewer requested Human input without a valid question")
            decision = "needs_plan"
        else:
            decision = "completed"
        merged_outputs: dict[str, Any] = {
            "validation_proposals": list(proposals.values()),
            "blocking_issues": list(dict.fromkeys(blocking)),
            "cross_review": {
                "mode": "parallel_independent",
                "decision_policy": "any_needs_plan_blocks_completion",
                "reviewers": records,
                "conflicts": conflicts,
            },
            "findings": findings,
            "finding_schema_errors": finding_schema_errors,
            "review_provenance": {
                "review_pass_id": f"review-pass-{uuid.uuid4().hex}",
                "review_depth": 0,
                "parent_review_id": None,
                "input_state_revision": context.state.get("revision"),
                "input_snapshot_digest": input_snapshot_digest,
                "reviewer_ids": [reviewer_id for reviewer_id, _ in self.reviewers],
            },
        }
        if questions:
            merged_outputs["human_question"] = dict(questions[0])
            if any(dict(question) != dict(questions[0]) for question in questions[1:]):
                merged_outputs["blocking_issues"].append("cross-reviewers requested different Human decisions")
        metadata = {
            "provider": "cross-review",
            "agent_role": "review",
            "execution_mode": "parallel_independent_review",
            "reviewer_count": len(self.reviewers),
            "reviewers": records,
        }
        return _coerce_agent_output(AgentOutput("cross-review completed", merged_outputs, decision, metadata))


def _coerce_agent_output(value: AgentOutput | Mapping[str, Any], *, allow_reject: bool = False) -> AgentOutput:
    if isinstance(value, AgentOutput):
        result = value
    elif isinstance(value, Mapping):
        unknown = sorted(set(value) - {"summary", "outputs", "decision", "metadata"})
        if unknown:
            raise _provider_error(
                "invalid_envelope",
                error_path="$",
                expected="only summary, outputs, decision, and metadata",
                actual=value,
                message=f"agent output has unknown top-level fields: {', '.join(str(item) for item in unknown)}",
            )
        summary = value.get("summary")
        outputs = value.get("outputs", {})
        decision = value.get("decision")
        metadata = value.get("metadata", {})
        if "summary" not in value:
            raise _provider_error("missing_required_field", error_path="$.summary", expected="nonempty string", actual=None, message="agent output requires summary")
        if "outputs" not in value:
            raise _provider_error("missing_required_field", error_path="$.outputs", expected="object", actual=None, message="agent output requires outputs")
        if not isinstance(summary, str) or not summary.strip():
            raise _provider_error("invalid_schema", error_path="$.summary", expected="nonempty string", actual=summary, message="agent output summary must be a nonempty string")
        if not isinstance(outputs, Mapping):
            raise _provider_error("invalid_schema", error_path="$.outputs", expected="object", actual=outputs, message="agent output outputs must be an object")
        if not isinstance(metadata, Mapping):
            raise _provider_error("invalid_schema", error_path="$.metadata", expected="object", actual=metadata, message="agent output metadata must be an object")
        allowed_decisions = {"completed", "needs_plan", "needs_human_input"}
        if allow_reject:
            allowed_decisions.add("reject")
        if decision is not None and decision not in allowed_decisions:
            raise _provider_error("invalid_schema", error_path="$.decision", expected="completed, needs_plan, needs_human_input, or null", actual=decision, message="agent output decision is invalid")
        result = AgentOutput(summary, dict(outputs), decision, dict(metadata))
    else:
        raise _provider_error("invalid_envelope", error_path="$", expected="object", actual=value, message="agent output must be an object")
    if not isinstance(result.summary, str) or not result.summary.strip():
        raise _provider_error("missing_required_field", error_path="$.summary", expected="nonempty string", actual=result.summary, message="agent output summary must be nonempty")
    if not isinstance(result.outputs, Mapping):
        raise _provider_error("invalid_schema", error_path="$.outputs", expected="object", actual=result.outputs, message="agent output outputs must be an object")
    if not isinstance(result.metadata, Mapping):
        raise _provider_error("invalid_schema", error_path="$.metadata", expected="object", actual=result.metadata, message="agent output metadata must be an object")
    allowed_decisions = {"completed", "needs_plan", "needs_human_input"}
    if allow_reject:
        allowed_decisions.add("reject")
    if result.decision is not None and result.decision not in allowed_decisions:
        raise _provider_error("invalid_schema", error_path="$.decision", expected="known decision or null", actual=result.decision, message="agent output decision is invalid")
    if secret_summary({"summary": result.summary, "outputs": result.outputs, "metadata": result.metadata}):
        raise _provider_error("invalid_schema", error_path="$", expected="secret-free provider output", actual=result.outputs, message="agent output contains a high-confidence secret")
    return result


@dataclass(frozen=True)
class _ProposalResult:
    action_id: str
    returncode: int
    evidence_ref: str | None
    target: str | None
    failure_category: str | None = None
    duration_ms: float | None = None


class AutonomousEngine:
    """Run Plan → Implement → Review until completion or a safe stop."""

    def __init__(
        self,
        store: TaskStore,
        backend: AgentBackend,
        *,
        actor: str = "nous-engine",
        gateway: PolicyGateway | None = None,
        executor: ManagedExecutor | None = None,
        allowed_processes: tuple[tuple[str, ...], ...] = (),
        validation_policies: tuple[ValidationCommandPolicy, ...] = (),
        min_cycles: int = 1,
        max_cycles: int = 3,
        max_steps: int = 12,
        lease_seconds: int = 300,
        provider_retry_limit: int = 1,
        no_progress_limit: int = 2,
        subskill_runtime: SubskillRuntime | None = None,
        require_external_subagents: bool = True,
        trace_exporter: TraceExporter | None = None,
        resource_budget: ResourceBudget | None = None,
    ) -> None:
        if (
            not actor.strip()
            or not isinstance(min_cycles, int)
            or isinstance(min_cycles, bool)
            or not isinstance(max_cycles, int)
            or isinstance(max_cycles, bool)
            or min_cycles < 1
            or max_cycles < 1
            or min_cycles > max_cycles
            or max_steps < 1
            or (min_cycles > 1 and max_steps < min_cycles * 3)
            or not isinstance(provider_retry_limit, int)
            or isinstance(provider_retry_limit, bool)
            or provider_retry_limit != 1
            or not isinstance(no_progress_limit, int)
            or isinstance(no_progress_limit, bool)
            or no_progress_limit < 2
            or no_progress_limit > 3
        ):
            raise ValidationError("invalid autonomous engine configuration")
        if lease_seconds < 30:
            raise ValidationError("autonomous engine lease must be at least 30 seconds")
        backend_timeout = getattr(backend, "timeout_seconds", None)
        if isinstance(backend_timeout, (int, float)) and backend_timeout >= lease_seconds - 5:
            raise ValidationError("external agent timeout must leave a lease renewal safety margin")
        if require_external_subagents:
            self._validate_external_backend_contract(backend)
        self.store = store
        self.backend = backend
        self.actor = actor
        self.min_cycles = min_cycles
        self.max_cycles = max_cycles
        self.max_steps = max_steps
        self.lease_seconds = lease_seconds
        self.provider_retry_limit = provider_retry_limit
        self.no_progress_limit = no_progress_limit
        self.subskill_runtime = subskill_runtime
        self.require_external_subagents = require_external_subagents
        self.trace_exporter = trace_exporter
        self.resource_budget = resource_budget
        self._execution_trace: list[dict[str, Any]] = []
        self._phase_quality: list[dict[str, Any]] = []
        self.validation_policies = tuple(validation_policies) or tuple(
            ValidationCommandPolicy(command) for command in allowed_processes
        )
        self.gateway = gateway or PolicyGateway(allowed_processes=tuple(policy.command for policy in self.validation_policies))
        self.executor = executor or ManagedExecutor(store, self.gateway, store.home / "runtime", actor=actor)
        # Child Review phases may observe their two reviewers concurrently.
        # Serialize the read-revision/write-observation pair so independent
        # workers do not fence one another with a stale root revision.
        self._review_observation_lock = threading.Lock()
        self._task_started_at: str | None = None
        self._task_started_monotonic: float | None = None
        self._cycle_started: dict[int, float] = {}
        self._provider_retry_count = 0
        self._validation_count = 0
        self._validation_environment_failures = 0
        self._code_failures = 0
        self._no_progress_count = 0
        self._last_progress_signature: str | None = None
        self._last_actual_change_at: str | None = None
        self._current_bottleneck: str | None = None
        self._active_task_id: str | None = None

    def run(self, task_id: str, *, _reset_diagnostics: bool = True) -> dict[str, Any]:
        """Continue one task from canonical state, never from chat history."""
        self._active_task_id = task_id
        if _reset_diagnostics:
            self._execution_trace = []
            self._phase_quality = []
            self._task_started_at = _now()
            self._task_started_monotonic = time.monotonic()
            self._cycle_started = {}
            self._provider_retry_count = 0
            self._validation_count = 0
            self._validation_environment_failures = 0
            self._code_failures = 0
            self._no_progress_count = 0
            self._last_progress_signature = None
            self._last_actual_change_at = None
            self._current_bottleneck = None
        self.store.init()
        self._recover_for_resume(task_id)
        self._seed_progress_from_durable_artifacts(task_id)
        # A process may disappear after the root Plan committed its child
        # graph.  Resume from the durable graph, not from the root phase
        # cursor (which is intentionally still the ordinary Implement phase).
        _, resumed_state, _ = self.store.read_task(task_id)
        if resumed_state["lifecycle_status"] == "active":
            has_parallel_ref = resumed_state.get("active_parallelization_plan_ref") is not None
            has_children = bool(resumed_state.get("child_loops"))
            pending_parallel_outputs = self._read_pending_parallel_outputs(task_id, resumed_state)
            if pending_parallel_outputs is not None and not has_parallel_ref and not has_children:
                # The root Plan artifact and graph materialization are two
                # durable boundaries.  If a process dies between them, the
                # canonical Plan output is the recovery intent and is
                # materialized before ordinary Implement can be acquired.
                resumed_state = self._materialize_parallel_plan(task_id, resumed_state, pending_parallel_outputs)
                has_parallel_ref = True
                has_children = True
            if has_parallel_ref or has_children:
                if not (has_parallel_ref and has_children):
                    raise RecoveryRequiredError("parallel materialization is incomplete; canonical child graph must be reconciled")
                parallel_plan = self._read_parallel_plan(task_id, resumed_state)
                effective_concurrency = 1 if parallel_plan["mode"] == "sequential" else int(parallel_plan["max_concurrency"])
                return ParallelExecutionEngine(
                    self,
                    max_concurrency=effective_concurrency,
                    lease_seconds=self.lease_seconds,
                ).run(task_id)
        steps = 0
        while steps < self.max_steps:
            definition, state, stable_context = self.store.read_task(task_id)
            lifecycle = state["lifecycle_status"]
            if lifecycle in {"completed", "cancelled"}:
                return self._result(task_id, state, steps, "completed" if lifecycle == "completed" else "cancelled")
            if lifecycle == "waiting_human":
                return self._result(task_id, state, steps, "waiting_human")
            if lifecycle == "blocked":
                return self._result(task_id, state, steps, "blocked")
            if state["execution_status"] in {"paused", "interrupted"}:
                self._recover_for_resume(task_id)
                continue
            phase: Phase = state["phase"]
            self._cycle_started.setdefault(int(state["cycle"]), time.monotonic())
            lease = self.store.acquire_lease(task_id, state["revision"], self.actor, seconds=self.lease_seconds)
            lease_info = lease["active_lease"]
            assert isinstance(lease_info, dict)
            try:
                context = self._context(definition, lease, stable_context, phase)
                context = self._prepare_subskill_context(context, str(lease_info["token"]))
                try:
                    output = self._launch_phase_with_retry(context, str(lease_info["token"]))
                except Exception as exc:
                    if phase == "plan" or isinstance(exc, (RecoveryRequiredError, ConflictError)):
                        raise
                    # A failed Implement/Review provider is itself a typed
                    # reason to re-plan.  Do not pretend that the phase
                    # succeeded and do not leave the lease live forever.
                    output = AgentOutput(
                        f"{phase} agent failed; re-plan is required",
                        {"agent_failure": type(exc).__name__, "blocking_issues": [f"{phase} agent failure ({type(exc).__name__})"], "provider_diagnostic": self._diagnostic_public(exc)},
                        "needs_plan" if phase == "review" else None,
                        {"provider_failure": type(exc).__name__, "provider_diagnostic": self._diagnostic_public(exc)},
                    )
                current_revision = self.store.read_task(task_id)[1]["revision"]
                try:
                    normalized, decision = self._materialize_output(context, output, current_revision, str(lease_info["token"]))
                except ProviderFailureError as exc:
                    diagnostic = exc.diagnostic.with_context(phase=phase, cycle=context.cycle, provider=exc.diagnostic.provider or "provider")
                    self._record_provider_diagnostic(diagnostic, task_id=context.task_id)
                    if phase == "plan":
                        raise
                    fallback_output = AgentOutput(
                        f"{phase} provider output was invalid; re-plan is required",
                        {
                            "agent_failure": type(exc).__name__,
                            "provider_diagnostic": diagnostic.public(),
                            "blocking_issues": [f"{phase} provider output invalid ({diagnostic.category})"],
                        },
                        "needs_plan" if phase == "review" else None,
                        {"provider_failure": diagnostic.category, "provider_diagnostic": diagnostic.public()},
                    )
                    normalized, decision = self._materialize_output(context, fallback_output, current_revision, str(lease_info["token"]))
                if phase == "review" and decision == "completed" and self._implement_has_failures(context):
                    decision = "needs_plan"
                    normalized.setdefault("blocking_issues", []).append("previous implement phase contains unresolved managed failures")
                if phase == "review" and decision == "completed" and context.cycle < self.min_cycles:
                    decision = "needs_plan"
                    normalized.setdefault("blocking_issues", []).append(
                        f"minimum Nous Loop cycles not reached ({context.cycle}/{self.min_cycles})"
                    )
                    normalized["nous_loop_control"] = {
                        "minimum_cycles": self.min_cycles,
                        "current_cycle": context.cycle,
                        "forced_replan": True,
                    }
                current_revision = self.store.read_task(task_id)[1]["revision"]
                recorded = self.store.record_phase(
                    task_id,
                    current_revision,
                    self.actor,
                    str(lease_info["token"]),
                    phase,
                    output.summary,
                    normalized,
                    decision,
                )
            except Exception:
                # A phase agent failure is not silently converted to success.
                # No action may remain live when releasing the lease.
                latest = self.store.read_task(task_id)[1]
                if latest.get("current_action") is None and (latest.get("active_lease") or {}).get("token") == lease_info["token"]:
                    self.store.release_lease(task_id, latest["revision"], self.actor, str(lease_info["token"]))
                raise
            steps += 1
            state = recorded["state"]
            if phase == "review" and decision == "needs_plan":
                if self._should_stop_for_no_progress(task_id, normalized, state):
                    return self._result(task_id, self.store.read_task(task_id)[1], steps, "human_required")
            if phase == "plan" and output.outputs.get("parallelization_plan") is not None:
                # The root Plan is the only phase allowed to materialize a
                # child graph.  The graph is created only after the Plan
                # artifact has crossed the normal canonical boundary.
                state = self._materialize_parallel_plan(task_id, state, output.outputs)
                parallel_plan = self._read_parallel_plan(task_id, state)
                effective_concurrency = 1 if parallel_plan["mode"] == "sequential" else int(parallel_plan["max_concurrency"])
                parallel = ParallelExecutionEngine(
                    self,
                    max_concurrency=effective_concurrency,
                    lease_seconds=self.lease_seconds,
                )
                return parallel.run(task_id)
            if state["lifecycle_status"] == "completed":
                return self._result(task_id, state, steps, "completed")
            if state["lifecycle_status"] == "waiting_human":
                return self._result(task_id, state, steps, "waiting_human")
            if state["lifecycle_status"] == "blocked":
                return self._result(task_id, state, steps, "blocked")
            if state["cycle"] > self.max_cycles:
                self._persist_cycle_limit_question(task_id, state)
                return self._result(task_id, self.store.read_task(task_id)[1], steps, "max_cycles_reached")
        return self._result(task_id, self.store.read_task(task_id)[1], steps, "max_steps_reached")

    @staticmethod
    def _subskill_snapshot(context: AgentContext) -> dict[str, Any]:
        state = {
            key: value
            for key, value in context.state.items()
            if key not in {"revision", "execution_status", "active_lease", "run_id", "lease_expires_at"}
        }
        return {
            "task_id": context.task_id,
            "loop_id": context.loop_id,
            "phase": context.phase,
            "cycle": context.cycle,
            "definition": context.definition,
            "state": state,
            "stable_context": context.stable_context,
            "active_artifacts": context.active_artifacts,
        }

    def _prepare_subskill_context(self, context: AgentContext, lease_token: str) -> AgentContext:
        if self.subskill_runtime is None:
            return context
        snapshot = self._subskill_snapshot(context)
        existing = self.store.read_subskill_selection(context.task_id, context.cycle, context.phase, context.loop_id)
        root_revision: Any = context.state.get("revision")
        if existing is None or existing.get("schema_version") == 1:
            selection = self.subskill_runtime.select(
                task_id=context.task_id,
                phase=context.phase,
                cycle=context.cycle,
                loop_id=context.loop_id,
                input_snapshot=snapshot,
            )
            last_conflict: ConflictError | None = None
            recorded = None
            for _attempt in range(3 if context.loop_id != "root" else 1):
                current_revision = self.store.read_task(context.task_id)[1]["revision"]
                try:
                    recorded = self.store.record_subskill_selection(
                        context.task_id,
                        current_revision,
                        self.actor,
                        lease_token,
                        selection=selection,
                        loop_id=context.loop_id,
                    )
                    break
                except ConflictError as exc:
                    # Another process may have committed an unrelated child
                    # checkpoint between the read and the CAS.  Retry against
                    # the new canonical revision before treating the child as
                    # failed; the TaskStore lock still serializes the write.
                    last_conflict = exc
            if recorded is None:
                raced = self.store.read_subskill_selection(context.task_id, context.cycle, context.phase, context.loop_id)
                if raced is None:
                    if last_conflict is not None:
                        raise last_conflict
                    raise RecoveryRequiredError("subskill selection was not recorded")
                existing = raced
            if recorded is not None:
                existing = recorded["selection"]
                root_revision = recorded["state"]["revision"]
        else:
            root_revision = context.state.get("revision")
        materialized = self.subskill_runtime.validate_persisted(
            existing,
            input_snapshot=snapshot,
            task_id=context.task_id,
            phase=context.phase,
            cycle=context.cycle,
            loop_id=context.loop_id,
        )
        state = dict(context.state)
        # Root mutations advance the parent revision.  Child selection is a
        # parent transaction too, but the child context keeps its own revision.
        if context.loop_id == "root" and isinstance(root_revision, int):
            state["revision"] = root_revision
        return replace(context, state=state, subskill_selection=materialized)

    def _recover_for_resume(self, task_id: str) -> None:
        _, state, _ = self.store.read_task(task_id)
        if state["execution_status"] in {"running", "pausing", "interrupted"}:
            self.store.recover_expired_lease(task_id, actor="nous-engine-recovery")
            _, state, _ = self.store.read_task(task_id)
        if state["execution_status"] in {"paused", "interrupted"}:
            self.store.resume_prepare(task_id, state["revision"], self.actor)

    def _seed_progress_from_durable_artifacts(self, task_id: str) -> None:
        """Restore the trailing no-progress streak after a fresh process."""
        self._no_progress_count = 0
        self._last_progress_signature = None
        task_root = self.store._path(task_id)
        reviews: list[dict[str, Any]] = []
        for path in sorted(task_root.glob("cycles/*/03-review-result.json")):
            try:
                value = read_json(path)
            except Exception:
                continue
            outputs = value.get("outputs") if isinstance(value, Mapping) else None
            progress = outputs.get("progress") if isinstance(outputs, Mapping) else None
            if isinstance(progress, Mapping):
                reviews.append(dict(progress))
        for progress in reversed(reviews):
            signature = progress.get("failure_signature")
            if not isinstance(signature, str) or progress.get("has_effects") is True:
                break
            if self._last_progress_signature is None:
                self._last_progress_signature = signature
            if signature != self._last_progress_signature:
                break
            self._no_progress_count += 1

    def _prior_planned_files(self, task_id: str) -> list[str]:
        """Collect prior plan targets for same-file rewrite diagnostics."""
        files: list[str] = []
        task_root = self.store._path(task_id)
        for path in sorted(task_root.glob("cycles/*/01-plan-result.json")):
            try:
                artifact = read_json(path)
            except Exception:
                continue
            outputs = artifact.get("outputs") if isinstance(artifact, Mapping) else None
            if not isinstance(outputs, Mapping):
                continue
            for key in ("planned_files", "affected_files", "files", "targets"):
                raw = outputs.get(key)
                if isinstance(raw, list):
                    files.extend(item for item in raw if isinstance(item, str) and item.strip())
            granularity = outputs.get("plan_granularity")
            if isinstance(granularity, Mapping) and isinstance(granularity.get("files"), list):
                files.extend(item for item in granularity["files"] if isinstance(item, str) and item.strip())
        return list(dict.fromkeys(files))

    @staticmethod
    def _normalise_progress_text(value: Any) -> str:
        if not isinstance(value, str):
            return str(value)
        # Cycle/action IDs are execution identity, not failure identity.
        normalized = re.sub(r"(?:[-_]c|cycle[-_]|[-_])\d+\b", "", value, flags=re.IGNORECASE)
        return re.sub(r"\b[a-f0-9]{16,}\b", "<id>", normalized, flags=re.IGNORECASE)

    def _should_stop_for_no_progress(self, task_id: str, outputs: Mapping[str, Any], state: Mapping[str, Any]) -> bool:
        progress = outputs.get("progress")
        if not isinstance(progress, Mapping):
            return False
        signature = progress.get("failure_signature")
        if not isinstance(signature, str):
            return False
        has_effects = bool(progress.get("has_effects"))
        if has_effects or signature != self._last_progress_signature:
            self._last_progress_signature = signature
            self._no_progress_count = 0
            return False
        self._no_progress_count += 1
        self._current_bottleneck = str(progress.get("classification") or "provider_gap")
        if self._no_progress_count < self.no_progress_limit:
            return False
        latest = self.store.read_task(task_id)[1]
        if latest.get("lifecycle_status") != "active" or latest.get("execution_status") != "idle":
            return False
        question = {
            "question": "同じ失敗が続き、これ以上の自動再計画では進展しないため確認が必要です。",
            "why_now": f"同一の失敗分類 ({self._current_bottleneck}) が {self._no_progress_count} 回続き、変更または検証可能な進展を確認できませんでした。",
            "options": [
                {"option_id": "replan", "label": "前提を見直して再計画", "impact": "失敗原因を踏まえた新しいPlanを作成します。"},
                {"option_id": "stop", "label": "停止", "impact": "現在の成果物と診断を確認して手動対応します。"},
            ],
            "recommended_option_id": "stop",
            "free_text_allowed": False,
            "blocking_scope": "root_task",
            "blocked_refs": [],
            "affected_loop_ids": ["root"],
            "resume_target": {"resume_phase": "plan", "resume_transition": "answer_received", "blocked_action_ref": None, "blocked_artifact_ref": None},
            "approval_request_ref": None,
        }
        self.store.record_question(task_id, int(latest["revision"]), self.actor, question)
        return True

    def _record_trace_event(self, event: Mapping[str, Any]) -> None:
        """Keep the durable in-memory trace and optional export in lockstep."""
        value = dict(event)
        self._execution_trace.append(value)
        export_execution_event(self.trace_exporter, value)

    @staticmethod
    def _diagnostic_public(exc: Exception) -> dict[str, Any] | None:
        if isinstance(exc, ProviderFailureError):
            return exc.diagnostic.public()
        return None

    def _record_provider_diagnostic(self, diagnostic: ProviderDiagnostic, *, task_id: str | None = None) -> None:
        public = diagnostic.public()
        # Bind the diagnostic to the current Task without changing the
        # provider-facing redacted payload shape.
        bound_task_id = task_id or getattr(self, "_active_task_id", None)
        if isinstance(bound_task_id, str):
            public["task_id"] = bound_task_id
        try:
            self.store.record_runtime_diagnostic(public)
        except Exception:
            # Diagnostics must never replace the original provider outcome or
            # turn a safe stop into an unbounded retry.  The in-memory trace
            # still keeps a bounded category marker for the final report.
            pass
        self._record_trace_event(
            {
                "phase": diagnostic.phase,
                "cycle": diagnostic.cycle,
                "status": "provider_failure",
                "provider": diagnostic.provider,
                "error": diagnostic.category,
                "duration_ms": None,
            }
        )

    def _launch_phase_with_retry(self, context: AgentContext, lease_token: str) -> AgentOutput:
        """Launch one phase with exactly one bounded retry for transient errors."""
        retry_context: dict[str, Any] | None = None
        for attempt in range(self.provider_retry_limit + 1):
            launch_context = replace(context, retry_context=retry_context)
            try:
                output = self._launch_phase(launch_context, lease_token)
                if attempt:
                    output = AgentOutput(
                        output.summary,
                        output.outputs,
                        output.decision,
                        {**output.metadata, "provider_retries": attempt, "retry_context": retry_context},
                    )
                return output
            except ProviderFailureError as exc:
                diagnostic = exc.diagnostic.with_context(
                    phase=context.phase,
                    cycle=context.cycle,
                    provider=exc.diagnostic.provider or "provider",
                )
                self._record_provider_diagnostic(diagnostic, task_id=context.task_id)
                if not exc.retryable or attempt >= self.provider_retry_limit:
                    raise ProviderFailureError(diagnostic, str(exc)) from exc
                self._provider_retry_count += 1
                retry_context = {
                    "attempt": attempt + 1,
                    "max_attempts": self.provider_retry_limit + 1,
                    "failure": diagnostic.public(),
                    "scope": "active_artifacts_and_current_acceptance_criteria",
                }
        raise AssertionError("provider retry loop exhausted unexpectedly")

    def _launch_phase(self, context: AgentContext, lease_token: str) -> AgentOutput:
        """Launch a phase and durably observe each root cross-reviewer."""
        self._enforce_subagent_policy(context.phase)
        started_at = _now()
        started_monotonic = time.monotonic()
        phase_span_id = _span_id()
        launch_context = replace(context, span_id=phase_span_id)
        try:
            budget_context = self.resource_budget.agent() if self.resource_budget is not None else nullcontext()
            with budget_context:
                if not isinstance(self.backend, CrossReviewBackend) or context.phase != "review":
                    output = self.backend.launch(context.phase, launch_context)
                else:
                    output = self._launch_cross_review(launch_context, lease_token)
            if self.resource_budget is not None:
                encoded_output = json.dumps(
                    {"summary": output.summary, "outputs": output.outputs},
                    ensure_ascii=False,
                    default=str,
                ).encode("utf-8")
                self.resource_budget.record_output(len(encoded_output))
        except Exception as exc:
            self._record_trace_event({
                "phase": context.phase,
                "cycle": context.cycle,
                "loop_id": context.loop_id,
                "status": "failed",
                "external_subagent": self._backend_is_external(context.phase),
                "started_at": started_at,
                "finished_at": _now(),
                "duration_ms": round((time.monotonic() - started_monotonic) * 1000, 3),
                "error": type(exc).__name__,
                "trace_id": context.trace_id,
                "phase_span_id": phase_span_id,
                "parent_span_id": context.parent_span_id,
            })
            raise
        finished_at = _now()
        duration_ms = round((time.monotonic() - started_monotonic) * 1000, 3)
        metadata = {
            **output.metadata,
            "phase_invocation_id": f"phase-{context.phase}-{uuid.uuid4().hex}",
            "phase_started_at": started_at,
            "phase_finished_at": finished_at,
            "phase_duration_ms": duration_ms,
            "external_subagent": self._backend_is_external(context.phase),
            "nous_loop": f"{context.loop_id}/cycle-{context.cycle}/{context.phase}",
            "trace_id": context.trace_id,
            "phase_span_id": phase_span_id,
            "parent_span_id": context.parent_span_id,
        }
        self._record_trace_event({
            "phase": context.phase,
            "cycle": context.cycle,
            "loop_id": context.loop_id,
            "status": "completed",
            "external_subagent": self._backend_is_external(context.phase),
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": duration_ms,
            "provider": metadata.get("provider"),
            "agent_run_id": metadata.get("agent_run_id"),
            "trace_id": context.trace_id,
            "phase_span_id": phase_span_id,
            "agent_span_id": metadata.get("span_id"),
            "parent_span_id": context.parent_span_id,
            "reviewer_count": metadata.get("reviewer_count", 1),
            "subagent_count": int(metadata.get("reviewer_count", 1) or 1),
        })
        return AgentOutput(output.summary, output.outputs, output.decision, metadata)

    def _launch_cross_review(self, context: AgentContext, lease_token: str) -> AgentOutput:
        """Launch cross-review while retaining its durable observer boundary."""
        cached = self.store.read_review_observations(context.task_id, context.cycle, context.loop_id)
        expected_digest = _review_input_digest(context)
        for reviewer_id, observation in cached.items():
            if observation.get("input_snapshot_digest") != expected_digest:
                raise RecoveryRequiredError(f"cross-review observation input changed for reviewer {reviewer_id}")
            if observation.get("status") == "completed":
                output_value = observation.get("output")
                if not isinstance(output_value, Mapping) or observation.get("output_digest") != digest_json(output_value):
                    raise RecoveryRequiredError(f"cross-review observation output hash mismatch for reviewer {reviewer_id}")

        def observe(reviewer_id: str, output: AgentOutput | None, failure: str | None) -> None:
            output_value = None
            output_digest = None
            status = "failed" if failure is not None else "completed"
            if output is not None:
                output_value = {
                    "summary": output.summary,
                    "outputs": output.outputs,
                    "decision": output.decision,
                    "metadata": output.metadata,
                }
                output_digest = digest_json(output_value)
            observation = {
                "schema_version": 1,
                "observation_id": f"review-observation-{reviewer_id}-{uuid.uuid4().hex[:8]}",
                "task_id": context.task_id,
                "loop_id": context.loop_id,
                "phase": "review",
                "cycle": context.cycle,
                "reviewer_id": reviewer_id,
                "status": status,
                "error": failure,
                "input_snapshot_digest": expected_digest,
                "output_digest": output_digest,
                "output": output_value,
                "created_at": _now(),
            }
            try:
                with self._review_observation_lock:
                    current_revision = self.store.read_task(context.task_id)[1]["revision"]
                    self.store.record_review_observation(
                        context.task_id,
                        current_revision,
                        self.actor,
                        lease_token,
                        reviewer_id=reviewer_id,
                        observation=observation,
                        loop_id=context.loop_id,
                    )
            except Exception as exc:
                raise RecoveryRequiredError(f"reviewer observation persistence failed for {reviewer_id}") from exc

        return self.backend.launch_review(context, observer=observe, cached=cached)

    def _backend_is_external(self, phase: Phase) -> bool:
        if isinstance(self.backend, CrossReviewBackend):
            if phase != "review":
                return type(self.backend.base) is JsonCommandAgentBackend
            return bool(
                type(self.backend.base) is JsonCommandAgentBackend
                and all(type(backend) is JsonCommandAgentBackend for _, backend in self.backend.reviewers)
            )
        return type(self.backend) is JsonCommandAgentBackend

    @staticmethod
    def _validate_external_backend_contract(backend: AgentBackend) -> None:
        if isinstance(backend, CrossReviewBackend):
            if type(backend.base) is not JsonCommandAgentBackend:
                raise ValidationError("Plan and Implement require JsonCommandAgentBackend when external sub-agents are required")
            if any(type(reviewer) is not JsonCommandAgentBackend for _, reviewer in backend.reviewers):
                raise ValidationError("every Cross Review reviewer requires JsonCommandAgentBackend when external sub-agents are required")
            return
        if type(backend) is not JsonCommandAgentBackend:
            raise ValidationError(
                "Nous requires external JsonCommandAgentBackend instances; "
                "pass require_external_subagents=False only for explicit tests or embedded integrations"
            )

    def _enforce_subagent_policy(self, phase: Phase) -> None:
        if self.require_external_subagents and not self._backend_is_external(phase):
            raise ValidationError(
                f"{phase} must run through an external sub-agent; "
                "in-process providers are allowed only for explicit test/integration runs"
            )

    def _read_parallel_plan(self, task_id: str, state: Mapping[str, Any]) -> dict[str, Any]:
        ref = state.get("active_parallelization_plan_ref")
        if not isinstance(ref, Mapping) or not isinstance(ref.get("path"), str):
            raise RecoveryRequiredError("parallel execution has no canonical plan reference")
        task_root = self.store._path(task_id).resolve()
        path = (task_root / str(ref["path"])).resolve()
        if task_root not in path.parents or not path.is_file():
            raise RecoveryRequiredError("canonical parallel plan is missing")
        plan = read_json(path)
        self.store.schemas.validate("parallel-plan", plan)
        if digest_json(plan) != ref.get("content_hash") or plan.get("task_id") != task_id or plan.get("cycle") != state.get("cycle"):
            raise RecoveryRequiredError("canonical parallel plan identity or hash mismatch")
        return plan

    def _read_pending_parallel_outputs(self, task_id: str, state: Mapping[str, Any]) -> dict[str, Any] | None:
        ref = state.get("active_plan_ref")
        if not isinstance(ref, Mapping) or not isinstance(ref.get("path"), str):
            return None
        task_root = self.store._path(task_id).resolve()
        path = (task_root / str(ref["path"])).resolve()
        if task_root not in path.parents or not path.is_file():
            raise RecoveryRequiredError("active Plan artifact is missing")
        artifact = read_json(path)
        self.store.schemas.validate("phase-result", artifact)
        if digest_json(artifact) != ref.get("content_hash") or artifact.get("task_id") != task_id or artifact.get("phase") != "plan":
            raise RecoveryRequiredError("active Plan artifact identity or hash mismatch")
        outputs = artifact.get("outputs")
        if not isinstance(outputs, Mapping) or outputs.get("parallelization_plan") is None:
            return None
        return dict(outputs)

    def _context(self, definition: dict[str, Any], state: dict[str, Any], stable_context: dict[str, Any], phase: Phase) -> AgentContext:
        artifacts: dict[str, dict[str, Any]] = {}
        for key in ("active_plan_ref", "active_implement_ref", "latest_review_result_ref"):
            ref = state.get(key)
            if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
                continue
            task_root = self.store._path(state["task_id"]).resolve()
            path = (task_root / ref["path"]).resolve()
            if task_root not in path.parents:
                raise RecoveryRequiredError(f"referenced phase artifact escapes task root: {key}")
            if path.is_file():
                value = read_json(path)
                if not isinstance(value, dict):
                    raise RecoveryRequiredError(f"referenced phase artifact is not an object: {key}")
                expected_hash = ref.get("content_hash")
                if not isinstance(expected_hash, str) or digest_json(value) != expected_hash:
                    raise RecoveryRequiredError(f"referenced phase artifact hash mismatch: {key}")
                expected_phase = {"active_plan_ref": "plan", "active_implement_ref": "implement", "latest_review_result_ref": "review"}[key]
                cycle_matches = value.get("cycle") == state["cycle"] if key != "latest_review_result_ref" else isinstance(value.get("cycle"), int) and 1 <= value["cycle"] <= state["cycle"]
                if value.get("task_id") != state["task_id"] or value.get("phase") != expected_phase or not cycle_matches or value.get("artifact_id") != ref.get("artifact_id"):
                    raise RecoveryRequiredError(f"referenced phase artifact identity mismatch: {key}")
                self.store.schemas.validate("phase-result", value)
                artifacts[key] = value
            elif key in {"active_plan_ref", "active_implement_ref"}:
                raise RecoveryRequiredError(f"referenced phase artifact is missing: {key}")
        # Lease tokens, grant IDs, and live action handles are control-plane
        # credentials.  A phase agent receives the state it needs to reason
        # about the task, never the capability that authorizes its next write.
        public_state = {
            key: state.get(key)
            for key in (
                "schema_version",
                "task_id",
                "root_task_id",
                "loop_id",
                "parent_loop_id",
                "revision",
                "lifecycle_status",
                "execution_status",
                "phase",
                "cycle",
                "phase_kind",
                "visibility",
                "last_error",
                "active_plan_ref",
                "active_parallelization_plan_ref",
                "active_implement_ref",
                "latest_review_result_ref",
                "latest_review_decision_ref",
                "blocking_reasons",
                "sync_status",
                "target_artifact_durability",
                "child_loops",
            )
        }
        return AgentContext(
            task_id=state["task_id"],
            phase=phase,
            cycle=int(state["cycle"]),
            definition=definition,
            state=public_state,
            stable_context=stable_context,
            active_artifacts=artifacts,
            repository_root=definition.get("target_repository"),
            loop_id=str(state.get("loop_id", "root")),
            external_subagent_required=self.require_external_subagents,
            trace_id=_trace_id_for_task(str(state["task_id"])),
            parent_span_id=_stable_span_id(f"loop:{state.get('loop_id', 'root')}"),
        )

    @staticmethod
    def _validation_environment_question(context: AgentContext, refs: list[str]) -> dict[str, Any]:
        return {
            "question": "検証環境または安全境界を確認してから、Reviewを再開しますか？",
            "why_now": (
                "検証をコード不備と断定できない失敗として分類しました。"
                "依存関係、cwd、timeout、side-effect policy、または書き込み範囲を確認してください。"
            ),
            "options": [
                {"option_id": "resume", "label": "環境を直して再開", "impact": "同じReview境界から検証を再実行します。"},
                {"option_id": "replan", "label": "前提を見直して再計画", "impact": "検証条件を含めて新しいPlanを作成します。"},
                {"option_id": "stop", "label": "停止", "impact": "実装と安全診断を確認して手動対応します。"},
            ],
            "recommended_option_id": "resume",
            "free_text_allowed": False,
            "blocking_scope": "root_task",
            "blocked_refs": refs,
            "affected_loop_ids": [context.loop_id],
            "resume_target": {
                "resume_phase": "review",
                "resume_transition": "answer_received",
                "blocked_action_ref": None,
                "blocked_artifact_ref": None,
            },
            "approval_request_ref": None,
        }

    def _set_review_diagnostics(self, values: dict[str, Any], context: AgentContext, classification: str) -> None:
        if classification not in REVIEW_CLASSIFICATIONS:
            classification = "provider_gap"
        values["review_classification"] = classification
        implement_outputs: Mapping[str, Any] = {}
        implement_artifact = context.active_artifacts.get("active_implement_ref")
        if isinstance(implement_artifact, Mapping) and isinstance(implement_artifact.get("outputs"), Mapping):
            implement_outputs = implement_artifact["outputs"]
        managed_refs = implement_outputs.get("managed_action_refs", [])
        changed_artifacts = implement_outputs.get("changed_artifacts", [])
        has_effects = bool(
            isinstance(managed_refs, list) and managed_refs
        ) or bool(isinstance(changed_artifacts, list) and changed_artifacts)
        failures = values.get("validation_failures", {})
        categories = [
            result.get("failure_category")
            for result in values.get("validation_evidence", [])
            if isinstance(result, Mapping) and isinstance(result.get("failure_category"), str)
        ]
        if isinstance(failures, Mapping):
            categories.extend(
                str(item.get("category"))
                for item in failures.values()
                if isinstance(item, Mapping) and item.get("category")
            )
        blocking = values.get("blocking_issues", [])
        normalized_blocking = [self._normalise_progress_text(item) for item in blocking] if isinstance(blocking, list) else []
        signature = digest_json(
            {
                "classification": classification,
                "categories": sorted(set(categories)),
                "blocking_issues": normalized_blocking,
                "finding_categories": sorted(
                    str(item.get("category"))
                    for item in values.get("findings", [])
                    if isinstance(item, Mapping) and item.get("category")
                ),
            }
        )
        values["progress"] = {
            "failure_signature": signature,
            "classification": classification,
            "has_effects": has_effects,
            "managed_action_count": len(managed_refs) if isinstance(managed_refs, list) else 0,
            "changed_artifact_count": len(changed_artifacts) if isinstance(changed_artifacts, list) else 0,
            "failure_categories": sorted(set(categories)),
            "no_progress_candidate": not has_effects,
        }

    def _materialize_output(self, context: AgentContext, output: AgentOutput, revision: int, lease_token: str) -> tuple[dict[str, Any], str | None]:
        values = dict(output.outputs)
        values["agent_execution"] = output.metadata
        review_decision = output.decision
        if context.phase == "review":
            if context.review_depth != 0:
                raise ValidationError("review depth must remain zero; nested review orchestration is forbidden")
            nested_keys = [key for key in ("parallelization_plan", "child_contracts", "reviewers", "review_round") if key in values]
            if nested_keys:
                for key in nested_keys:
                    values.pop(key, None)
                values.setdefault("blocking_issues", []).append("review output attempted nested orchestration")
                review_decision = "needs_plan"
            findings, finding_errors = _normalize_findings(values.get("findings"), context.reviewer_id or "single")
            values["findings"] = findings
            if finding_errors:
                values["finding_schema_errors"] = _finding_schema_errors(finding_errors)
                values.setdefault("blocking_issues", []).extend(finding_errors)
                self._record_provider_diagnostic(
                    make_provider_diagnostic(
                        "invalid_schema",
                        phase=context.phase,
                        cycle=context.cycle,
                        provider=output.metadata.get("provider") if isinstance(output.metadata.get("provider"), str) else "review-provider",
                        error_path="$.outputs.findings",
                        expected="typed finding objects",
                        actual=values.get("findings"),
                        retryable=False,
                        message="review finding schema is invalid",
                        details={"finding_schema_errors": _finding_schema_errors(finding_errors)},
                    ),
                    task_id=context.task_id,
                )
                review_decision = "needs_plan"
            for finding in findings:
                if _finding_is_blocking(finding):
                    values.setdefault("blocking_issues", []).append(str(finding["summary"]))
            values.setdefault("review_provenance", {
                "review_pass_id": f"review-pass-{uuid.uuid4().hex}",
                "review_depth": 0,
                "parent_review_id": None,
                "input_state_revision": context.state.get("revision"),
                "input_snapshot_digest": _review_input_digest(context),
                "reviewer_ids": [context.reviewer_id or "single"],
            })
            if review_decision == "reject":
                review_decision = "needs_plan"
        if context.phase == "plan":
            values.pop("action_proposals", None)
            values.setdefault("no_managed_effects", True)
            values["plan_granularity"] = _plan_granularity(
                context,
                {**values, "prior_planned_files": self._prior_planned_files(context.task_id)},
            )
            quality = {
                "phase": context.phase,
                "cycle": context.cycle,
                "loop_id": context.loop_id,
                "substantive": bool(values.get("acceptance_notes") or values.get("acceptance_criteria") or values.get("planned_content") or values.get("plan")),
                "evidence_count": 0,
                "reason": "plan_has_no_acceptance_or_plan_detail" if not (values.get("acceptance_notes") or values.get("acceptance_criteria") or values.get("planned_content") or values.get("plan")) else None,
            }
            values["phase_quality"] = quality
            self._phase_quality.append(quality)
            return values, None
        if context.phase == "implement":
            proposals = _proposal_list(values.pop("action_proposals", []), "action_proposals")
            read_only_child = context.definition.get("preferred_backend") == "read_only_worker" or (
                context.loop_id != "root" and context.definition.get("write_scope") == []
            )
            executed: list[_ProposalResult] = []
            failures: list[dict[str, str]] = []
            for item in proposals:
                try:
                    executed.append(self._execute_proposal(context, item, revision, lease_token))
                except Exception as exc:
                    if isinstance(exc, ProviderFailureError):
                        self._record_provider_diagnostic(
                            exc.diagnostic.with_context(
                                phase=context.phase,
                                cycle=context.cycle,
                                provider=exc.diagnostic.provider or "managed-action",
                                action_id=str(item.get("action_id")) if item.get("action_id") else None,
                            ),
                            task_id=context.task_id,
                        )
                    elif isinstance(exc, ConflictError):
                        self._record_provider_diagnostic(
                            make_provider_diagnostic(
                                "write_proposal_conflict",
                                phase=context.phase,
                                cycle=context.cycle,
                                provider="managed-action",
                                action_id=str(item.get("action_id")) if item.get("action_id") else None,
                                expected="one immutable action descriptor per action_id",
                                actual=exc,
                                retryable=False,
                                message="managed action proposal conflicts with durable action evidence",
                            ),
                            task_id=context.task_id,
                        )
                    failures.append({"action_id": str(item.get("action_id", "unknown")), "error": f"{type(exc).__name__}: {exc}"})
            failed = [item for item in executed if item.returncode != 0]
            values["managed_action_refs"] = [item.action_id for item in executed if item.returncode == 0]
            values["changed_artifacts"] = [item.target for item in executed if item.returncode == 0 and item.target]
            if not any(item.returncode == 0 for item in executed):
                values.setdefault("no_managed_effects", True)
            if output.metadata.get("provider_failure"):
                values["provider_failure"] = str(output.metadata["provider_failure"])
                values.setdefault("blocking_issues", []).append("implement provider failed before managed execution")
            if failed:
                failures.extend({"action_id": item.action_id, "error": f"managed action returned {item.returncode}"} for item in failed)
            if failures:
                values["action_failures"] = failures
                values["blocking_issues"] = [f"managed action failed: {item['action_id']}" for item in failures]
            quality = {
                "phase": context.phase,
                "cycle": context.cycle,
                "loop_id": context.loop_id,
                "substantive": bool(values["managed_action_refs"] or failures or read_only_child),
                "managed_action_count": len(values["managed_action_refs"]),
                "proposal_count": len(proposals),
                "reason": "read_only_validation_child" if read_only_child and not values["managed_action_refs"] and not failures else ("implement_returned_no_managed_effects" if not values["managed_action_refs"] and not failures else None),
            }
            values["phase_quality"] = quality
            self._phase_quality.append(quality)
            return values, None

        proposals = _proposal_list(values.pop("validation_proposals", []), "validation_proposals")
        if not proposals:
            if output.metadata.get("provider_failure"):
                values["managed_action_refs"] = []
                values["validation_evidence"] = []
                values["acceptance_criteria"] = [{**dict(item), "status": "unverified", "evidence_refs": []} for item in context.definition["acceptance_criteria"]]
                values.setdefault("blocking_issues", []).append("review provider failed before validation")
                self._set_review_diagnostics(values, context, "provider_gap")
                quality = {"phase": context.phase, "cycle": context.cycle, "loop_id": context.loop_id, "substantive": False, "evidence_count": 0, "reason": "review_provider_failure"}
                values["phase_quality"] = quality
                self._phase_quality.append(quality)
                return values, "needs_plan"
            if review_decision == "needs_human_input":
                if not isinstance(values.get("human_question"), Mapping):
                    raise ValidationError("review needs_human_input requires outputs.human_question")
                values["managed_action_refs"] = []
                values["validation_evidence"] = []
                values.setdefault("blocking_issues", [])
                self._set_review_diagnostics(values, context, "human_required")
                quality = {"phase": context.phase, "cycle": context.cycle, "loop_id": context.loop_id, "substantive": False, "evidence_count": 0, "reason": "review_waiting_for_human"}
                values["phase_quality"] = quality
                self._phase_quality.append(quality)
                return values, "needs_human_input"
            if review_decision == "needs_plan":
                values["managed_action_refs"] = []
                values["validation_evidence"] = []
                values["acceptance_criteria"] = [{**dict(item), "status": "unverified", "evidence_refs": []} for item in context.definition["acceptance_criteria"]]
                values.setdefault("blocking_issues", []).append("review requested a re-plan without executable validation")
                classification = "schema_gap" if finding_errors else "code_gap"
                self._set_review_diagnostics(values, context, classification)
                quality = {"phase": context.phase, "cycle": context.cycle, "loop_id": context.loop_id, "substantive": False, "evidence_count": 0, "reason": "review_returned_no_validation_proposals"}
                values["phase_quality"] = quality
                self._phase_quality.append(quality)
                return values, "needs_plan"
            if output.metadata.get("provider") == "cross-review":
                values["managed_action_refs"] = []
                values["validation_evidence"] = []
                values["acceptance_criteria"] = [{**dict(item), "status": "unverified", "evidence_refs": []} for item in context.definition["acceptance_criteria"]]
                values.setdefault("blocking_issues", []).append("cross-review produced no executable validation proposals")
                cross_review = values.get("cross_review")
                reviewer_records = cross_review.get("reviewers", []) if isinstance(cross_review, Mapping) else []
                failed_categories = {
                    str(item.get("error"))
                    for item in reviewer_records
                    if isinstance(item, Mapping) and isinstance(item.get("error"), str)
                }
                if any(category in {"validation_environment_failure", "timeout", "unsafe_path"} for category in failed_categories):
                    cross_classification = "validation_environment_gap"
                elif failed_categories or output.metadata.get("fallback_used"):
                    cross_classification = "provider_gap"
                else:
                    cross_classification = "schema_gap"
                self._set_review_diagnostics(values, context, cross_classification)
                if cross_classification == "validation_environment_gap":
                    values["human_question"] = self._validation_environment_question(context, [])
                    return values, "needs_human_input"
                quality = {"phase": context.phase, "cycle": context.cycle, "loop_id": context.loop_id, "substantive": False, "evidence_count": 0, "reason": "cross_review_returned_no_validation_proposals"}
                values["phase_quality"] = quality
                self._phase_quality.append(quality)
                return values, review_decision or "needs_plan"
            raise _provider_error("invalid_managed_action", phase=context.phase, cycle=context.cycle, error_path="$.outputs.validation_proposals", expected="array of validation proposal objects", actual=None, message="review agent must provide managed validation_proposals")
        # Validate the complete Review proposal batch before executing any
        # process. A malformed second proposal must not leave a partially
        # validated batch behind as durable evidence.
        for index, item in enumerate(proposals):
            raw_action_id = item.get("action_id")
            if not isinstance(raw_action_id, str) or not raw_action_id.strip():
                raise _provider_error(
                    "invalid_managed_action",
                    phase=context.phase,
                    cycle=context.cycle,
                    error_path=f"$.outputs.validation_proposals[{index}].action_id",
                    expected="safe nonempty action_id",
                    actual=raw_action_id,
                    message="validation proposal requires action_id before execution",
                )
            try:
                _safe_id(raw_action_id, f"validation_proposals[{index}].action_id")
            except ValidationError as exc:
                raise _provider_error(
                    "invalid_managed_action",
                    phase=context.phase,
                    cycle=context.cycle,
                    action_id=None,
                    error_path=f"$.outputs.validation_proposals[{index}].action_id",
                    expected="safe nonempty action_id",
                    actual=raw_action_id,
                    message=str(exc),
                ) from exc
            criterion_id = item.get("criterion_id")
            if not isinstance(criterion_id, str) or not criterion_id.strip():
                raise _provider_error(
                    "invalid_managed_action",
                    phase=context.phase,
                    cycle=context.cycle,
                    action_id=raw_action_id,
                    error_path=f"$.outputs.validation_proposals[{index}].criterion_id",
                    expected="nonempty acceptance criterion ID",
                    actual=criterion_id,
                    message="validation proposal requires criterion_id before execution",
                )
            if item.get("kind") != "process.run":
                raise _provider_error(
                    "invalid_managed_action",
                    phase=context.phase,
                    cycle=context.cycle,
                    action_id=raw_action_id,
                    error_path=f"$.outputs.validation_proposals[{index}].kind",
                    expected="process.run",
                    actual=item.get("kind"),
                    message="review validation proposals may only run process.run",
                )
            raw_targets = item.get("targets")
            if raw_targets is None and item.get("target") is not None:
                raw_targets = [item["target"]]
            if not isinstance(raw_targets, list) or not raw_targets or any(not isinstance(target, str) or not target for target in raw_targets):
                raise _provider_error(
                    "invalid_managed_action",
                    phase=context.phase,
                    cycle=context.cycle,
                    action_id=raw_action_id,
                    error_path=f"$.outputs.validation_proposals[{index}].targets",
                    expected="nonempty string array",
                    actual=raw_targets,
                    message="validation proposal requires nonempty targets",
                )
            command = item.get("command")
            if not isinstance(command, list) or not command or any(not isinstance(part, str) or not part for part in command):
                raise _provider_error(
                    "invalid_managed_action",
                    phase=context.phase,
                    cycle=context.cycle,
                    action_id=raw_action_id,
                    error_path=f"$.outputs.validation_proposals[{index}].command",
                    expected="nonempty string array",
                    actual=command,
                    message="validation proposal requires a nonempty process command",
                )
            if "validation" in item:
                try:
                    ValidationSpec.from_mapping(item.get("validation"), label=f"validation_proposals[{index}].validation")
                except ValidationError as exc:
                    raise _provider_error(
                        "invalid_managed_action",
                        phase=context.phase,
                        cycle=context.cycle,
                        action_id=raw_action_id,
                        error_path=f"$.outputs.validation_proposals[{index}].validation",
                        expected="typed disposable validation policy",
                        actual=item.get("validation"),
                        message=str(exc),
                    ) from exc
        executed: list[_ProposalResult] = []
        proposal_failures: dict[str, str] = {}
        for item in proposals:
            try:
                executed.append(self._execute_proposal(context, item, revision, lease_token))
            except Exception as exc:
                if isinstance(exc, ProviderFailureError):
                    self._record_provider_diagnostic(
                        exc.diagnostic.with_context(
                            phase=context.phase,
                            cycle=context.cycle,
                            provider=exc.diagnostic.provider or "managed-validation",
                            action_id=str(item.get("action_id")) if item.get("action_id") else None,
                        ),
                        task_id=context.task_id,
                    )
                elif isinstance(exc, ConflictError):
                    self._record_provider_diagnostic(
                        make_provider_diagnostic(
                            "write_proposal_conflict",
                            phase=context.phase,
                            cycle=context.cycle,
                            provider="managed-validation",
                            action_id=str(item.get("action_id")) if item.get("action_id") else None,
                            expected="one immutable action descriptor per action_id",
                            actual=exc,
                            retryable=False,
                            message="validation proposal conflicts with durable action evidence",
                        ),
                        task_id=context.task_id,
                    )
                proposal_failures[str(item.get("action_id", "unknown"))] = f"{type(exc).__name__}: {exc}"
                executed.append(_ProposalResult(str(item.get("action_id", "unknown")), 74, None, None))
        evidence: list[dict[str, Any]] = []
        for item, result in zip(proposals, executed):
            criterion_id = item.get("criterion_id")
            if not isinstance(criterion_id, str) or not criterion_id:
                raise ValidationError("validation proposal requires criterion_id")
            evidence_ref = result.evidence_ref or f"engine://validation-failure/{context.cycle}/{result.action_id}"
            evidence.append({"criterion_id": criterion_id, "kind": item.get("kind", "managed_validation"), "ref": evidence_ref, "outcome": "passed" if result.returncode == 0 else "failed", "failure_category": result.failure_category, "duration_ms": result.duration_ms})
        values["managed_action_refs"] = [item.action_id for item in executed if item.returncode == 0]
        values["validation_evidence"] = evidence
        values.setdefault("acceptance_criteria", [dict(item) for item in context.definition["acceptance_criteria"]])
        values.setdefault("blocking_issues", [])
        decision = review_decision or "needs_plan"
        criterion_ids = {
            item.get("criterion_id")
            for item in values["acceptance_criteria"]
            if isinstance(item, Mapping) and isinstance(item.get("criterion_id"), str)
        }
        unknown_criterion_ids = sorted(
            {item["criterion_id"] for item in evidence if item["criterion_id"] not in criterion_ids}
        )
        if unknown_criterion_ids:
            values["blocking_issues"] = [
                *values.get("blocking_issues", []),
                *[f"validation evidence references unknown acceptance criterion: {item}" for item in unknown_criterion_ids],
            ]
            values["validation_schema_errors"] = [
                {"path": "$.outputs.validation_proposals[].criterion_id", "message": f"unknown acceptance criterion: {item}"}
                for item in unknown_criterion_ids
            ]
            decision = "needs_plan"
        unverified_criteria: list[str] = []
        for criterion in values["acceptance_criteria"]:
            if not isinstance(criterion, dict):
                continue
            criterion_id = criterion.get("criterion_id")
            matching = [item for item in evidence if item["criterion_id"] == criterion_id]
            passed_refs = [item["ref"] for item in matching if item["outcome"] == "passed"]
            failed_refs = [item["ref"] for item in matching if item["outcome"] == "failed"]
            if passed_refs:
                criterion["status"] = "passed"
                criterion["evidence_refs"] = passed_refs
            elif failed_refs:
                criterion["status"] = "failed"
                criterion["evidence_refs"] = failed_refs
            else:
                criterion["status"] = "unverified"
                criterion["evidence_refs"] = []
                if isinstance(criterion_id, str):
                    unverified_criteria.append(criterion_id)
        if unverified_criteria:
            values["unverified_criteria"] = unverified_criteria
            if decision == "completed":
                decision = "needs_plan"
                values["blocking_issues"] = [
                    *values.get("blocking_issues", []),
                    *[f"acceptance criterion was not verified: {item}" for item in unverified_criteria],
                ]
        if decision == "completed" and self._implement_has_no_effects(context):
            decision = "needs_plan"
            values.setdefault("blocking_issues", []).append(
                "Implement produced no managed effect; a completion review cannot certify a change task"
            )
        if any(item.returncode != 0 for item in executed):
            decision = "needs_plan"
            values["blocking_issues"] = [*values.get("blocking_issues", []), "managed validation failed"]
        if proposal_failures:
            values["validation_failures"] = proposal_failures
            values["blocking_issues"] = [*values.get("blocking_issues", []), *[f"validation proposal failed: {key}" for key in proposal_failures]]
        if any(item.returncode != 0 for item in executed):
            categories = {item.failure_category for item in executed if item.failure_category}
            if "validation_environment_failure" in categories:
                classification = "validation_environment_gap"
            elif "safety_gap" in categories:
                classification = "safety_gap"
            elif "invalid_managed_action" in categories:
                classification = "schema_gap"
            else:
                classification = "test_gap"
        elif proposal_failures:
            classification = "provider_gap" if any("ProviderFailureError" in error for error in proposal_failures.values()) else "schema_gap"
        elif finding_errors or unknown_criterion_ids:
            classification = "schema_gap"
        elif unverified_criteria:
            classification = "test_gap"
        elif any(_finding_is_blocking(item) for item in values.get("findings", []) if isinstance(item, Mapping)):
            classification = "code_gap"
        else:
            classification = "completed" if decision == "completed" else "code_gap"
        environment_or_safety_failure = classification in {"validation_environment_gap", "safety_gap"}
        if environment_or_safety_failure:
            # A missing dependency, timeout, or scope escape is not evidence
            # that the implementation is wrong.  Stop at the Human boundary
            # instead of blindly creating another Plan that repeats the same
            # environment failure.  The redacted category and evidence refs
            # remain in this Review artifact for diagnosis/resume.
            decision = "needs_human_input"
            values["human_question"] = self._validation_environment_question(
                context,
                [str(item.get("ref")) for item in evidence if isinstance(item, Mapping) and isinstance(item.get("ref"), str)],
            )
        self._set_review_diagnostics(values, context, classification)
        quality = {
            "phase": context.phase,
            "cycle": context.cycle,
            "loop_id": context.loop_id,
            "substantive": bool(evidence),
            "evidence_count": len(evidence),
            "proposal_count": len(proposals),
            "reason": None if evidence else "review_returned_no_validation_evidence",
        }
        values["phase_quality"] = quality
        self._phase_quality.append(quality)
        return values, decision

    @staticmethod
    def _implement_has_failures(context: AgentContext) -> bool:
        artifact = context.active_artifacts.get("active_implement_ref")
        if not isinstance(artifact, dict):
            return False
        outputs = artifact.get("outputs")
        if not isinstance(outputs, Mapping):
            return True
        if outputs.get("agent_failure") or outputs.get("action_failures") or outputs.get("provider_failure"):
            return True
        execution = outputs.get("agent_execution")
        return isinstance(execution, Mapping) and bool(execution.get("provider_failure"))

    @staticmethod
    def _implement_has_no_effects(context: AgentContext) -> bool:
        if context.state.get("phase_kind") == "integration":
            return False
        artifact = context.active_artifacts.get("active_implement_ref")
        if not isinstance(artifact, Mapping):
            return True
        outputs = artifact.get("outputs")
        if not isinstance(outputs, Mapping):
            return True
        if context.definition.get("preferred_backend") == "read_only_worker":
            return False
        if context.loop_id != "root" and context.definition.get("write_scope") == []:
            return False
        refs = outputs.get("managed_action_refs")
        changed = outputs.get("changed_artifacts")
        return not (isinstance(refs, list) and refs) and not (isinstance(changed, list) and changed)

    @staticmethod
    def _implement_outputs_have_failures(outputs: Mapping[str, Any]) -> bool:
        if outputs.get("agent_failure") or outputs.get("action_failures") or outputs.get("provider_failure"):
            return True
        execution = outputs.get("agent_execution")
        return isinstance(execution, Mapping) and bool(execution.get("provider_failure"))

    def _validation_policy_for_command(self, command: tuple[str, ...]) -> ValidationCommandPolicy | None:
        matches = [policy for policy in self.validation_policies if policy.matches(command)]
        if not matches:
            return None
        # Prefer the most specific prefix.  PolicyGateway independently checks
        # the same prefix, so this cannot turn an unallowlisted command into a
        # permitted effect.
        return max(matches, key=lambda policy: len(policy.command))

    def _execute_proposal(self, context: AgentContext, proposal: dict[str, Any], _revision: int, lease_token: str) -> _ProposalResult:
        if self.resource_budget is not None:
            self.resource_budget.record_tool_call()
        kind = proposal.get("kind")
        if kind not in {"filesystem.write", "process.run", "git.local"}:
            raise _provider_error(
                "invalid_managed_action",
                phase=context.phase,
                cycle=context.cycle,
                action_id=str(proposal.get("action_id")) if proposal.get("action_id") else None,
                error_path="$.outputs.action_proposals[].kind",
                expected="filesystem.write, process.run, or git.local",
                actual=kind,
                message=f"agent proposal kind is unsupported: {kind}",
            )
        if context.phase == "review" and kind != "process.run":
            raise _provider_error(
                "invalid_managed_action",
                phase=context.phase,
                cycle=context.cycle,
                error_path="$.outputs.validation_proposals[].kind",
                expected="process.run",
                actual=kind,
                message="review validation may only run an allowlisted process; filesystem and Git writes are forbidden",
            )
        repository_root = context.repository_root
        if not isinstance(repository_root, str) or not repository_root:
            raise _provider_error("invalid_managed_action", phase=context.phase, cycle=context.cycle, error_path="$.repository_root", expected="nonempty repository path", actual=repository_root, message="managed agent action requires a target repository")
        raw_action_id = proposal.get("action_id")
        if not isinstance(raw_action_id, str) or not raw_action_id.strip():
            raise _provider_error(
                "invalid_managed_action",
                phase=context.phase,
                cycle=context.cycle,
                error_path="$.outputs.action_proposals[].action_id",
                expected="safe nonempty action_id",
                actual=raw_action_id,
                message="managed action proposal requires a nonempty action_id for durable idempotency",
            )
        action_id = _safe_id(raw_action_id, "action_id")
        if context.phase == "review":
            criterion_id = proposal.get("criterion_id")
            if not isinstance(criterion_id, str) or not criterion_id.strip():
                raise _provider_error(
                    "invalid_managed_action",
                    phase=context.phase,
                    cycle=context.cycle,
                    action_id=action_id,
                    error_path="$.outputs.validation_proposals[].criterion_id",
                    expected="nonempty acceptance criterion ID",
                    actual=criterion_id,
                    message="validation proposal requires criterion_id",
                )
        targets = proposal.get("targets")
        if targets is None and proposal.get("target") is not None:
            targets = [proposal["target"]]
        if not isinstance(targets, list) or not targets or any(not isinstance(item, str) or not item for item in targets):
            raise _provider_error("invalid_managed_action", phase=context.phase, cycle=context.cycle, action_id=action_id, error_path="$.outputs.action_proposals[].targets", expected="nonempty string array", actual=targets, message="agent proposal targets must be a nonempty string list")
        # Every managed action advances Task revision while retaining the
        # phase lease.  Re-read it for each proposal so a phase agent may
        # safely return more than one sequential action without reusing a
        # stale expected_state_revision.
        current_revision = self.store.read_task(context.task_id)[1]["revision"]
        command = proposal.get("command")
        if command is not None and (not isinstance(command, list) or any(not isinstance(item, str) for item in command)):
            raise _provider_error("invalid_managed_action", phase=context.phase, cycle=context.cycle, action_id=action_id, error_path="$.outputs.validation_proposals[].command", expected="string array", actual=command, message="agent proposal command must be a string list")
        command_tuple = tuple(command) if command is not None else None
        validation_policy = self._validation_policy_for_command(command_tuple or ()) if kind == "process.run" else None
        if kind == "process.run" and validation_policy is None:
            configured = [list(policy.command) for policy in self.validation_policies]
            raise _provider_error(
                "invalid_managed_action",
                phase=context.phase,
                cycle=context.cycle,
                action_id=action_id,
                error_path="$.outputs.validation_proposals[].command",
                expected="command matching a configured validation allowlist prefix",
                actual=command_tuple,
                message=f"validation command is not allowlisted; add a matching prefix to validation.allowed_processes (configured={len(configured)})",
                details={"configured_prefix_count": len(configured)},
            )
        raw_validation = proposal.get("validation")
        try:
            requested_spec = ValidationSpec.from_mapping(raw_validation, label="agent proposal validation") if raw_validation is not None else None
            if validation_policy is not None:
                # The allowlist entry is Human/operator policy. An agent may
                # request the same public settings for explanation, but may
                # not widen cwd, environment, timeout, output, or side-effect
                # scope inside its own proposal.
                if requested_spec is not None and requested_spec.public() != validation_policy.spec.public():
                    raise ValidationError("agent validation settings must match the configured validation policy")
                validation_spec = validation_policy.spec
            else:
                validation_spec = requested_spec or ValidationSpec()
        except ValidationError as exc:
            raise _provider_error(
                "invalid_managed_action",
                phase=context.phase,
                cycle=context.cycle,
                action_id=action_id,
                error_path="$.outputs.validation_proposals[].validation",
                expected="typed disposable validation policy",
                actual=raw_validation,
                message=str(exc),
            ) from exc
        validation_public = validation_spec.public() if kind == "process.run" else None
        action = ActionDescriptor(
            action_id=action_id,
            task_id=context.task_id,
            kind=kind,
            repository_root=repository_root,
            targets=tuple(targets),
            command=command_tuple,
            loop_id=context.loop_id,
            expected_state_revision=int(current_revision),
            request_id=self.store.request_id,
            idempotency_key=str(proposal.get("idempotency_key") or action_id),
            expected_effect=str(proposal.get("expected_effect") or f"agent {context.phase} action"),
            plan_ref=(context.state.get("active_plan_ref") or {}).get("path"),
            plan_content_hash=(context.state.get("active_plan_ref") or {}).get("content_hash"),
            resource_ids=tuple(item for item in proposal.get("resource_ids", []) if isinstance(item, str)),
            reservation_ids=tuple(item for item in proposal.get("reservation_ids", []) if isinstance(item, str)),
            lease_token=lease_token if context.loop_id != "root" else None,
            validation=validation_public,
        )
        reused = self._reuse_existing_action(action, targets[0])
        if reused is not None:
            return reused
        if kind == "filesystem.write":
            content = proposal.get("content")
            if not isinstance(content, str):
                raise _provider_error("invalid_managed_action", phase=context.phase, cycle=context.cycle, action_id=action_id, error_path="$.outputs.action_proposals[].content", expected="string", actual=content, message="filesystem.write proposal requires string content")
            if secret_summary({"content": content}):
                raise ValidationError("agent write content contains a high-confidence secret")
            result = self.executor.write_text(action, content)
        elif kind == "process.run":
            result = self.executor.run_process(action, validation=validation_spec)
        else:
            result = self.executor.run_local_git(action)
        record = read_json(self.store._path(context.task_id) / "actions" / f"{action_id}.json")
        evidence_ref = record.get("after_evidence_ref") if isinstance(record, dict) else None
        target = targets[0] if targets else None
        failure_category: str | None = None
        duration_ms: float | None = None
        if isinstance(evidence_ref, str):
            task_root = self.store._path(context.task_id).resolve()
            evidence_path = (task_root / evidence_ref).resolve()
            if task_root in evidence_path.parents and evidence_path.is_file():
                evidence = read_json(evidence_path)
                facts = evidence.get("facts") if isinstance(evidence, Mapping) else None
                if isinstance(facts, Mapping):
                    candidate = facts.get("failure_category")
                    if isinstance(candidate, str):
                        failure_category = candidate
                    started_at = facts.get("started_at")
                    completed_at = facts.get("completed_at")
                    if isinstance(started_at, str) and isinstance(completed_at, str):
                        try:
                            duration_ms = max(0.0, (datetime.fromisoformat(completed_at) - datetime.fromisoformat(started_at)).total_seconds() * 1000)
                        except ValueError:
                            duration_ms = None
        self._validation_count += 1 if kind == "process.run" else 0
        if failure_category == "validation_environment_failure":
            self._validation_environment_failures += 1
            self._record_provider_diagnostic(
                make_provider_diagnostic(
                    "validation_environment_failure",
                    phase=context.phase,
                    cycle=context.cycle,
                    provider="managed-validation",
                    action_id=action_id,
                    error_path="$.postflight.facts",
                    expected="validation command executes in the disposable workspace",
                    actual=result.returncode,
                    stderr=result.stderr,
                    retryable=False,
                    message="validation could not execute in its configured environment",
                    details={"validation_cwd": validation_spec.cwd, "duration_ms": duration_ms},
                ),
                task_id=context.task_id,
            )
        elif failure_category == "safety_gap":
            self._record_provider_diagnostic(
                make_provider_diagnostic(
                    "unsafe_path",
                    phase=context.phase,
                    cycle=context.cycle,
                    provider="managed-validation",
                    action_id=action_id,
                    error_path="$.postflight.facts.unexpected_changed_paths",
                    expected="only explicitly allowed disposable side effects",
                    actual=result.returncode,
                    stderr=result.stderr,
                    retryable=False,
                    message="validation changed a path outside its allowed side-effect set",
                ),
                task_id=context.task_id,
            )
        elif result.returncode != 0 and kind == "process.run":
            self._code_failures += 1
        if result.returncode == 0 and kind == "filesystem.write":
            self._last_actual_change_at = result.completed_at
        return _ProposalResult(action_id, result.returncode, evidence_ref if isinstance(evidence_ref, str) else None, target, failure_category, duration_ms)

    def _reuse_existing_action(self, action: ActionDescriptor, target: str | None) -> _ProposalResult | None:
        """Recover a durable action instead of replaying an uncertain effect."""
        action_path = self.store._path(action.task_id) / "actions" / f"{action.action_id}.json"
        if not action_path.exists():
            return None
        record = read_json(action_path)
        self.store.schemas.validate("action-record", record)
        prior = record["action"]
        if prior["digest"] != action.digest or prior.get("idempotency_key") != action.idempotency_key:
            raise ConflictError(f"durable action conflicts with proposal: {action.action_id}")
        status = record["status"]
        if status in {"started", "reconciliation_required"}:
            raise RecoveryRequiredError(f"durable action requires reconciliation before retry: {action.action_id}")
        evidence_ref = record.get("after_evidence_ref") or record.get("before_evidence_ref")
        returncode = 0 if status == "completed" else 74
        if isinstance(evidence_ref, str):
            task_root = self.store._path(action.task_id).resolve()
            evidence_path = (task_root / evidence_ref).resolve()
            if task_root not in evidence_path.parents or not evidence_path.is_file():
                raise RecoveryRequiredError(f"durable action evidence is missing: {action.action_id}")
            evidence = read_json(evidence_path)
            if isinstance(evidence, dict):
                facts = evidence.get("facts")
                if isinstance(facts, dict) and isinstance(facts.get("returncode"), int):
                    returncode = int(facts["returncode"])
        if status not in {"completed", "failed", "denied", "reconciled"}:
            raise RecoveryRequiredError(f"durable action has unknown status: {action.action_id}")
        return _ProposalResult(action.action_id, returncode, evidence_ref if isinstance(evidence_ref, str) else None, target)

    def _persist_cycle_limit_question(self, task_id: str, state: dict[str, Any]) -> None:
        if state["lifecycle_status"] != "active" or state["execution_status"] != "idle":
            return
        question = {
            "question": "自律ループの上限回数に達しました。追加サイクルを許可しますか？",
            "why_now": "Reviewの指摘を自動修正するサイクル上限に達したため、安全に停止しました。",
            "options": [
                {"option_id": "continue", "label": "追加サイクルを許可", "impact": "再Planから自律実装を続けます。"},
                {"option_id": "stop", "label": "停止", "impact": "現在の成果物を確認して手動で判断します。"},
            ],
            "recommended_option_id": "continue",
            "free_text_allowed": False,
            "blocking_scope": "root_task",
            "blocked_refs": [],
            "affected_loop_ids": ["root"],
            "resume_target": {"resume_phase": "plan", "resume_transition": "answer_received", "blocked_action_ref": None, "blocked_artifact_ref": None},
            "approval_request_ref": None,
        }
        self.store.record_question(task_id, state["revision"], self.actor, question)

    def _materialize_parallel_plan(self, task_id: str, state: dict[str, Any], outputs: Mapping[str, Any]) -> dict[str, Any]:
        """Turn a typed Plan proposal into canonical Child Loop records."""
        raw_plan = outputs.get("parallelization_plan")
        raw_contracts = outputs.get("child_contracts")
        if not isinstance(raw_plan, Mapping) or not isinstance(raw_contracts, list) or not raw_contracts:
            raise ValidationError("parallel plan requires parallelization_plan and child_contracts")
        normalized_contracts = [dict(raw_contract) for raw_contract in raw_contracts if isinstance(raw_contract, Mapping)]
        if len(normalized_contracts) != len(raw_contracts):
            raise ValidationError("child_contracts must contain objects")
        planned = self.store.materialize_parallel_plan(
            task_id,
            state["revision"],
            self.actor,
            child_contracts=normalized_contracts,
            mode=str(raw_plan.get("mode", "sequential")),
            rationale=str(raw_plan.get("rationale", "typed Plan selected independent child loops")),
            max_concurrency=int(raw_plan.get("max_concurrency", 1)),
            max_depth=int(raw_plan.get("max_depth", 1)),
            integration_strategy=str(raw_plan.get("integration_strategy", "parent Integration Review")),
            integration_validation=[str(item) for item in raw_plan.get("integration_validation", []) if isinstance(item, str)],
            knowledge_references=[dict(item) for item in raw_plan.get("knowledge_references", []) if isinstance(item, Mapping)],
        )
        return planned["state"]

    def _durable_diagnostics(self, task_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Reconstruct phase trace and quality signals after a fresh process."""
        trace: list[dict[str, Any]] = []
        quality: list[dict[str, Any]] = []
        task_root = self.store._path(task_id)
        if task_root.is_dir():
            for path in sorted(task_root.glob("**/*-result.json")):
                try:
                    artifact = read_json(path)
                except Exception:
                    continue
                if not isinstance(artifact, Mapping) or artifact.get("task_id") != task_id:
                    continue
                execution = artifact.get("outputs", {}).get("agent_execution") if isinstance(artifact.get("outputs"), Mapping) else None
                if isinstance(execution, Mapping):
                    trace.append({
                        "phase": artifact.get("phase"),
                        "cycle": artifact.get("cycle"),
                        "loop_id": artifact.get("loop_id", "root"),
                        "status": "completed",
                        "external_subagent": execution.get("external_subagent") is True,
                        "started_at": execution.get("phase_started_at", execution.get("started_at")),
                        "finished_at": execution.get("phase_finished_at", execution.get("finished_at")),
                        "duration_ms": execution.get("phase_duration_ms"),
                        "provider": execution.get("provider"),
                        "agent_run_id": execution.get("agent_run_id"),
                        "trace_id": execution.get("trace_id"),
                        "phase_span_id": execution.get("phase_span_id"),
                        "agent_span_id": execution.get("span_id"),
                        "parent_span_id": execution.get("parent_span_id"),
                        "subagent_count": int(execution.get("reviewer_count", 1) or 1),
                    })
                outputs = artifact.get("outputs")
                if isinstance(outputs, Mapping) and isinstance(outputs.get("phase_quality"), Mapping):
                    quality.append(dict(outputs["phase_quality"]))
        if trace:
            return trace, quality
        return list(self._execution_trace), list(self._phase_quality)

    def _runtime_diagnostics(self, task_id: str) -> list[dict[str, Any]]:
        """Read only the safe, schema-validated runtime diagnosis envelope."""
        path = self.store._path(task_id) / "diagnostics.jsonl"
        if not path.is_file():
            return []
        values: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                value = json.loads(line)
                self.store.schemas.validate("runtime-diagnostic", value)
                values.append(value)
        except (OSError, ValueError, ValidationError):
            # A malformed operational log must not make the task state
            # unreadable; TaskStore.diagnose remains the strict recovery path.
            return []
        return values

    def _result(self, task_id: str, state: dict[str, Any], steps: int, outcome: str) -> dict[str, Any]:
        trace, durable_quality = self._durable_diagnostics(task_id)
        runtime_diagnostics = self._runtime_diagnostics(task_id)
        phases = [f"{item['loop_id']}/cycle-{item['cycle']}/{item['phase']}" for item in trace]
        quality_items: list[dict[str, Any]] = []
        quality_seen: set[str] = set()
        for item in [*durable_quality, *self._phase_quality]:
            fingerprint = digest_json(item)
            if fingerprint in quality_seen:
                continue
            quality_seen.add(fingerprint)
            quality_items.append(item)
        quality_gaps = [item for item in quality_items if item.get("substantive") is False]
        subagent_count = sum(int(item.get("subagent_count", 1) or 1) for item in trace)
        external_count = sum(
            int(item.get("subagent_count", 1) or 1)
            for item in trace
            if item.get("external_subagent")
        )
        root_causes: list[str] = []
        if self.require_external_subagents and external_count < len(trace):
            root_causes.append("external_subagent_policy_not_satisfied")
        if quality_gaps:
            root_causes.append("agent_returned_typed_noop_or_missing_evidence")
        root_phase_sequence = [item for item in phases if item.startswith("root/")]
        parallelized = any(
            path.is_file()
            for path in (self.store._path(task_id) / "cycles").glob("**/01-parallelization-plan.json")
        )
        if root_phase_sequence and root_phase_sequence[:3] != ["root/cycle-1/plan", "root/cycle-1/implement", "root/cycle-1/review"] and not parallelized:
            root_causes.append("unexpected_phase_sequence")
        if not root_causes and len(trace) >= 3:
            root_causes.append("no_phase_skip_detected")
        elapsed_ms = None
        if self._task_started_monotonic is not None:
            elapsed_ms = round(max(0.0, (time.monotonic() - self._task_started_monotonic) * 1000), 3)
        if elapsed_ms is None and trace:
            started_values = [item.get("started_at") for item in trace if isinstance(item.get("started_at"), str)]
            finished_values = [item.get("finished_at") for item in trace if isinstance(item.get("finished_at"), str)]
            try:
                if started_values and finished_values:
                    elapsed_ms = round(max(0.0, (datetime.fromisoformat(max(finished_values)) - datetime.fromisoformat(min(started_values))).total_seconds() * 1000), 3)
            except ValueError:
                elapsed_ms = None
        implementation_slices = 0
        for path in sorted(self.store._path(task_id).glob("cycles/*/02-implement-result.json")):
            try:
                artifact = read_json(path)
            except Exception:
                continue
            outputs = artifact.get("outputs") if isinstance(artifact, Mapping) else None
            if isinstance(outputs, Mapping) and (outputs.get("managed_action_refs") or outputs.get("changed_artifacts")):
                implementation_slices += 1
        current_blocker = self._current_bottleneck
        latest_review_ref = state.get("latest_review_result_ref")
        if isinstance(latest_review_ref, Mapping) and isinstance(latest_review_ref.get("path"), str):
            try:
                latest_review = read_json(self.store._path(task_id) / latest_review_ref["path"])
                latest_outputs = latest_review.get("outputs") if isinstance(latest_review, Mapping) else None
                if isinstance(latest_outputs, Mapping):
                    current_blocker = str(latest_outputs.get("review_classification") or current_blocker) if latest_outputs.get("review_classification") else current_blocker
            except Exception:
                pass
        cycle_timings: list[dict[str, Any]] = []
        for cycle in sorted({int(item.get("cycle", 0)) for item in trace if isinstance(item.get("cycle"), int) and int(item.get("cycle", 0)) > 0}):
            cycle_trace = [item for item in trace if item.get("cycle") == cycle]
            starts = [item.get("started_at") for item in cycle_trace if isinstance(item.get("started_at"), str)]
            finishes = [item.get("finished_at") for item in cycle_trace if isinstance(item.get("finished_at"), str)]
            duration = round(sum(float(item.get("duration_ms", 0) or 0) for item in cycle_trace), 3)
            cycle_timings.append({"cycle": cycle, "started_at": min(starts) if starts else None, "finished_at": max(finishes) if finishes else None, "duration_ms": duration})
        phase_duration_ms = round(sum(float(item.get("duration_ms", 0) or 0) for item in trace), 3)
        validation_duration_ms = 0.0
        changed_artifacts: list[str] = []
        managed_action_count = 0
        category_counts: dict[str, int] = {}
        durable_last_change_at = self._last_actual_change_at
        for path in sorted(self.store._path(task_id).glob("cycles/*/*-result.json")):
            try:
                artifact = read_json(path)
            except Exception:
                continue
            outputs = artifact.get("outputs") if isinstance(artifact, Mapping) else None
            if not isinstance(outputs, Mapping):
                continue
            evidence = outputs.get("validation_evidence")
            if isinstance(evidence, list):
                validation_duration_ms += sum(float(item.get("duration_ms", 0) or 0) for item in evidence if isinstance(item, Mapping))
                for item in evidence:
                    category = item.get("failure_category") if isinstance(item, Mapping) else None
                    if isinstance(category, str):
                        category_counts[category] = category_counts.get(category, 0) + 1
            refs = outputs.get("managed_action_refs")
            if isinstance(refs, list):
                managed_action_count += len(refs)
            changed = outputs.get("changed_artifacts")
            if isinstance(changed, list):
                changed_artifacts.extend(item for item in changed if isinstance(item, str))
        for action_path in sorted((self.store._path(task_id) / "actions").glob("*.json")):
            try:
                action_record = read_json(action_path)
            except Exception:
                continue
            action_descriptor = action_record.get("action") if isinstance(action_record, Mapping) else None
            if (
                isinstance(action_descriptor, Mapping)
                and action_descriptor.get("kind") == "filesystem.write"
                and action_record.get("status") == "completed"
                and isinstance(action_record.get("updated_at"), str)
                and (durable_last_change_at is None or action_record["updated_at"] > durable_last_change_at)
            ):
                durable_last_change_at = action_record["updated_at"]
        for diagnostic in runtime_diagnostics:
            category = diagnostic.get("category")
            if isinstance(category, str):
                category_counts[category] = category_counts.get(category, 0) + 1
        report = {
            "task_duration_ms": elapsed_ms,
            "task_started_at": min((item.get("started_at") for item in trace if isinstance(item.get("started_at"), str)), default=self._task_started_at),
            "task_finished_at": max((item.get("finished_at") for item in trace if isinstance(item.get("finished_at"), str)), default=_now() if elapsed_ms is not None else None),
            "cycles": max((int(item.get("cycle", 0) or 0) for item in trace), default=int(state.get("cycle", 1))),
            "cycle_timings": cycle_timings,
            "provider_duration_ms": phase_duration_ms,
            "validation_duration_ms": round(validation_duration_ms, 3),
            "completed_implementation_slices": implementation_slices,
            "managed_action_count": managed_action_count,
            "changed_artifacts": list(dict.fromkeys(changed_artifacts)),
            "provider_retries": self._provider_retry_count,
            "validation_environment_failures": max(self._validation_environment_failures, category_counts.get("validation_environment_failure", 0)),
            "code_failures": max(self._code_failures, category_counts.get("test_gap", 0)),
            "no_progress_cycles": self._no_progress_count,
            "current_blocker": current_blocker,
            "last_actual_change_at": durable_last_change_at,
            "needs_plan_count": sum(1 for path in self.store._path(task_id).glob("cycles/*/03-review-result.json") if _artifact_decision(path, "needs_plan")),
            "completion_rate": 1.0 if outcome == "completed" else round(min(1.0, implementation_slices / max(1, int(state.get("cycle", 1)) * 1.0)), 3),
            "failure_category_counts": dict(sorted(category_counts.items())),
        }
        termination_summary = {
            "outcome": outcome,
            "reason": state.get("last_error") or current_blocker or outcome,
            "final_error": current_blocker,
            "remaining_work": list(state.get("blocking_reasons", [])) if isinstance(state.get("blocking_reasons"), list) else [],
        }
        return {
            "task_id": task_id,
            "outcome": outcome,
            "steps": steps,
            "state": state,
            "diagnostics": {
                "nous_loop": "Plan -> Implement -> Review -> Plan ...",
                "minimum_cycles": self.min_cycles,
                "maximum_cycles": self.max_cycles,
                "phase_sequence": phases,
                "phase_invocations": len(trace),
                "subagent_invocations": subagent_count,
                "external_subagent_invocations": external_count,
                "quality_gaps": quality_gaps,
                "root_causes": root_causes,
                "trace": trace,
                "report": report,
                "termination_summary": termination_summary,
            },
        }


class ParallelExecutionEngine:
    """Run materialized Child Loops with bounded physical concurrency.

    The TaskStore remains the authority.  Threads only host backend calls;
    child leases, reservations, action grants, phase artifacts, and results
    cross the canonical taskctl boundary before and after every worker step.
    """

    def __init__(self, phase_engine: AutonomousEngine, *, max_concurrency: int = 3, lease_seconds: int = 300) -> None:
        if not 1 <= max_concurrency <= 3:
            raise ValidationError("parallel max_concurrency must be between 1 and 3")
        if lease_seconds < 30:
            raise ValidationError("parallel child lease must be at least 30 seconds")
        self.phase_engine = phase_engine
        self.store = phase_engine.store
        self.backend = phase_engine.backend
        self.actor = phase_engine.actor
        self.max_concurrency = max_concurrency
        self.lease_seconds = lease_seconds
        # TaskStore serializes canonical parent mutations.  Backend calls are
        # still physical and concurrent, but action start/finish plus child
        # checkpoint commits must occupy one short critical section because
        # the v1 root state has a single CAS revision/current-action slot.
        # Share the transition lock with cross-review observation persistence.
        # A child may finish a reviewer while another child is committing its
        # phase/result; both operations consume the single root CAS revision.
        self._canonical_lock = phase_engine._review_observation_lock

    def run(self, task_id: str) -> dict[str, Any]:
        self.store.init()
        self.store.recover_expired_reservations(task_id, actor="parallel-recovery")
        self.store.recover_expired_child_leases(task_id, actor="parallel-recovery")
        rounds = 0
        while rounds < self.phase_engine.max_steps:
            _, state, _ = self.store.read_task(task_id)
            if state["lifecycle_status"] in {"completed", "cancelled", "waiting_human", "blocked"}:
                return self.phase_engine._result(task_id, state, rounds, state["lifecycle_status"])
            if state["phase_kind"] == "integration" and state["phase"] == "review":
                integration_result = self._run_integration_review(task_id)
                if integration_result.get("outcome") == "needs_plan" and integration_result.get("minimum_cycle_replan") is True:
                    # Integration Review can be forced to re-plan by the
                    # minimum-cycle policy.  Continue through the ordinary
                    # root engine instead of returning early, while keeping
                    # one diagnostic trace for the whole Nous Loop run.
                    return self.phase_engine.run(task_id, _reset_diagnostics=False)
                return integration_result
            children = list(state.get("child_loops", []))
            if not children:
                raise ValidationError("parallel execution requires materialized child loops")
            sorted_children = sorted(children, key=lambda item: str(item["loop_id"]))
            exhausted_ready: list[dict[str, str]] = []
            for item in sorted_children:
                if item.get("status") != "ready":
                    continue
                child_state = self.store._read_child_state(task_id, str(item["loop_id"]))
                if int(child_state.get("cycle", 1)) > self.phase_engine.max_cycles:
                    exhausted_ready.append({"loop_id": str(item["loop_id"]), "error": "child_cycle_limit"})
            if exhausted_ready:
                result = self.phase_engine._result(task_id, state, rounds, "child_failed")
                result["parallel_failures"] = exhausted_ready
                return result
            ready = [item for item in sorted_children if item.get("status") == "ready"]
            if not ready:
                running = [item for item in children if item.get("status") == "running"]
                if running:
                    raise RecoveryRequiredError("parallel scheduler found running children without local workers")
                if all(item.get("status") == "integration_ready" for item in children):
                    raise RecoveryRequiredError("children are integration-ready but parent integration phase is missing")
                raise ValidationError("parallel child graph is not runnable; inspect dependencies and reservations")
            started: list[tuple[str, str]] = []
            for entry in ready[: self.max_concurrency]:
                loop_id = str(entry["loop_id"])
                try:
                    started_entry = self._start_child(task_id, loop_id)
                except (ConflictError, ValidationError) as exc:
                    # A conflicting resource is not a permission to overlap;
                    # try the next independent child in the same wave.
                    if not started:
                        raise ValidationError(f"parallel child {loop_id} cannot start safely: {type(exc).__name__}") from exc
                    continue
                started.append((loop_id, str(started_entry["lease_token"])))
            if not started:
                raise RecoveryRequiredError("parallel scheduler could not acquire a child lease")
            rounds += len(started)
            failures: list[dict[str, str]] = []
            with ThreadPoolExecutor(max_workers=len(started), thread_name_prefix="nous-child") as pool:
                futures: dict[Future[dict[str, Any]], tuple[str, str]] = {
                    pool.submit(self._run_child, task_id, loop_id, token): (loop_id, token)
                    for loop_id, token in started
                }
                for future in as_completed(futures):
                    loop_id, _ = futures[future]
                    try:
                        result = future.result()
                    except Exception as exc:  # pragma: no cover - defensive worker boundary
                        failures.append({"loop_id": loop_id, "error": type(exc).__name__})
                    else:
                        if result.get("outcome") != "completed":
                            failures.append({"loop_id": loop_id, "error": str(result.get("error", result.get("outcome", "child_failed")))})
            if failures:
                _, latest, _ = self.store.read_task(task_id)
                exhausted = []
                for failure in failures:
                    loop_id = failure["loop_id"]
                    child_state = self.store._read_child_state(task_id, loop_id)
                    if int(child_state.get("cycle", 1)) > self.phase_engine.max_cycles:
                        exhausted.append(failure)
                if exhausted or latest["execution_status"] in {"paused", "interrupted"}:
                    result = self.phase_engine._result(task_id, latest, rounds, "child_failed")
                    result["parallel_failures"] = exhausted or failures
                    return result
                # A failed child is durably left at its current phase and can
                # be started again.  The next worker resumes that phase; it
                # does not replay earlier completed effects.
                continue
        _, state, _ = self.store.read_task(task_id)
        return self.phase_engine._result(task_id, state, rounds, "max_steps_reached")

    def _start_child(self, task_id: str, loop_id: str) -> dict[str, Any]:
        _, state, _ = self.store.read_task(task_id)
        entry = next(item for item in state["child_loops"] if item.get("loop_id") == loop_id)
        _, contract = self.store.read_child_contract(task_id, loop_id, str(entry["contract_ref"]))
        # Read-only children need no reservation.  A writing child may only
        # run physically after a worktree and its declared reservations have
        # already been bound by the parent plan.
        if contract["write_scope"] and not entry.get("worktree_ref"):
            raise ValidationError("physical child writes require a canonical isolated worktree")
        return self.store.start_child_loop(task_id, state["revision"], self.actor, loop_id=loop_id, lease_seconds=self.lease_seconds)

    def _child_context(self, task_id: str, loop_id: str, phase: Phase) -> AgentContext:
        definition, root_state, stable_context = self.store.read_task(task_id)
        entry = next(item for item in root_state["child_loops"] if item.get("loop_id") == loop_id)
        _, contract = self.store.read_child_contract(task_id, loop_id, str(entry["contract_ref"]))
        child_state = self.store._read_child_state(task_id, loop_id)
        self.store.schemas.validate("task-state", child_state)
        artifacts: dict[str, dict[str, Any]] = {}
        for key in ("active_plan_ref", "active_implement_ref", "latest_review_result_ref"):
            ref = child_state.get(key)
            if not isinstance(ref, Mapping) or not isinstance(ref.get("path"), str):
                continue
            relative = str(ref["path"])
            path = self.store._safe_task_relative_path(task_id, relative)
            if path.is_file():
                value = self.store._read_task_json_nofollow(task_id, relative)
                self.store.schemas.validate("phase-result", value)
                if digest_json(value) != ref.get("content_hash"):
                    raise RecoveryRequiredError(f"child artifact hash mismatch: {key}")
                artifacts[key] = value
        public_state = {
            key: child_state.get(key)
            for key in (
                "schema_version", "task_id", "root_task_id", "loop_id", "parent_loop_id", "revision",
                "lifecycle_status", "execution_status", "phase", "cycle", "phase_kind", "visibility",
                "last_error", "active_contract_ref", "active_plan_ref", "active_implement_ref",
                "latest_review_result_ref", "latest_review_decision_ref", "blocking_reasons",
            )
        }
        repository_root = definition.get("target_repository")
        if entry.get("worktree_ref"):
            worktree = read_json(self.store._path(task_id) / str(entry["worktree_ref"]["path"]))
            self.store.schemas.validate("worktree", worktree)
            repository_root = str(worktree["worktree_path"])
        child_definition = {
            **dict(contract),
            "title": contract["objective"],
            "objective": contract["objective"],
            "target_repository": repository_root,
            "target_repository_identity": definition.get("target_repository_identity"),
            "acceptance_criteria": contract["acceptance_criteria"],
        }
        child_stable = {**stable_context, "child_loop_id": loop_id, "child_contract_ref": entry["contract_ref"]}
        return AgentContext(
            task_id=task_id,
            phase=phase,
            cycle=int(child_state["cycle"]),
            definition=child_definition,
            state=public_state,
            stable_context=child_stable,
            active_artifacts=artifacts,
            repository_root=repository_root,
            loop_id=loop_id,
            external_subagent_required=self.phase_engine.require_external_subagents,
            trace_id=_trace_id_for_task(task_id),
            parent_span_id=_stable_span_id(f"loop:{loop_id}"),
        )

    def _with_child_heartbeat(self, task_id: str, loop_id: str, lease_token: str, phase: Phase, operation: Callable[[], Any]) -> Any:
        """Run a complete phase, including managed validation, under a heartbeat."""
        stop = threading.Event()
        heartbeat_errors: list[Exception] = []

        def heartbeat() -> None:
            interval = max(0.25, min(5.0, self.lease_seconds / 3))
            while not stop.wait(interval):
                try:
                    with self._canonical_lock:
                        revision = self.store.read_task(task_id)[1]["revision"]
                        self.store.renew_child_lease(
                            task_id,
                            revision,
                            self.actor,
                            loop_id=loop_id,
                            lease_token=lease_token,
                            lease_seconds=self.lease_seconds,
                        )
                except Exception as exc:  # token fencing and expiry are terminal for this worker
                    heartbeat_errors.append(exc)
                    stop.set()
                    return

        worker = threading.Thread(target=heartbeat, name=f"nous-heartbeat-{loop_id}", daemon=True)
        worker.start()
        try:
            result = operation()
        finally:
            stop.set()
            worker.join(timeout=max(1.0, min(5.0, self.lease_seconds / 3)))
        if heartbeat_errors:
            raise RecoveryRequiredError(f"child {loop_id} lease fencing interrupted phase {phase}") from heartbeat_errors[0]
        return result

    def _record_child_phase_retry(
        self, task_id: str, loop_id: str, lease_token: str, phase: Phase, summary: str, outputs: dict[str, Any]
    ) -> dict[str, Any]:
        last: ConflictError | None = None
        for _attempt in range(3):
            revision = self.store.read_task(task_id)[1]["revision"]
            try:
                return self.store.record_child_phase(
                    task_id, revision, self.actor, loop_id=loop_id, lease_token=lease_token,
                    phase=phase, summary=summary, outputs=outputs,
                )
            except ConflictError as exc:
                last = exc
        assert last is not None
        raise last

    def _record_child_failure_retry(
        self, task_id: str, loop_id: str, lease_token: str, phase: Phase, error_code: str, resume_phase: Phase = "plan"
    ) -> dict[str, Any]:
        last: ConflictError | None = None
        for _attempt in range(3):
            revision = self.store.read_task(task_id)[1]["revision"]
            try:
                return self.store.record_child_failure(
                    task_id, revision, self.actor, loop_id=loop_id, lease_token=lease_token,
                    phase=phase, error_code=error_code, resume_phase=resume_phase,
                )
            except ConflictError as exc:
                last = exc
        assert last is not None
        raise last

    def _record_child_result_retry(self, task_id: str, loop_id: str, lease_token: str, outputs: dict[str, Any]) -> dict[str, Any]:
        last: ConflictError | None = None
        for _attempt in range(3):
            revision = self.store.read_task(task_id)[1]["revision"]
            try:
                return self.store.record_child_result(
                    task_id, revision, self.actor, loop_id=loop_id, lease_token=lease_token, outputs=outputs,
                )
            except ConflictError as exc:
                last = exc
        assert last is not None
        raise last

    def _run_child(self, task_id: str, loop_id: str, lease_token: str) -> dict[str, Any]:
        current_phase = "plan"
        try:
            child_state = self.store._read_child_state(task_id, loop_id)
            start_phase = child_state.get("phase")
            phases = ("plan", "implement", "review")
            if start_phase not in phases:
                raise RecoveryRequiredError("child state has an unsupported phase")
            for phase in phases[phases.index(start_phase) :]:
                current_phase = phase
                context = self._child_context(task_id, loop_id, phase)  # type: ignore[arg-type]
                # Selection is a short parent-CAS transaction.  Serialize it
                # with the other child canonical writes so two fresh children
                # cannot fence one another while pinning their first phase.
                with self._canonical_lock:
                    context = self.phase_engine._prepare_subskill_context(context, lease_token)
                def run_phase() -> bool:
                    output = self.phase_engine._launch_phase(context, lease_token)
                    with self._canonical_lock:
                        root_revision = self.store.read_task(task_id)[1]["revision"]
                        values, decision = self.phase_engine._materialize_output(context, output, root_revision, lease_token)
                        if phase == "plan":
                            self._record_child_phase_retry(task_id, loop_id, lease_token, phase, output.summary, values)
                            return True
                        if phase == "implement":
                            self._record_child_phase_retry(task_id, loop_id, lease_token, phase, output.summary, values)
                            if self.phase_engine._implement_outputs_have_failures(values):
                                self._record_child_failure_retry(task_id, loop_id, lease_token, phase, "implement-failed")
                                return False
                            return True
                        values["decision"] = decision or output.decision or "needs_plan"
                        if values["decision"] != "completed":
                            raise ValidationError("child review did not complete")
                        values.setdefault("summary", output.summary)
                        self._record_child_result_retry(task_id, loop_id, lease_token, values)
                        return True

                phase_completed = self._with_child_heartbeat(task_id, loop_id, lease_token, phase, run_phase)
                if not phase_completed:
                    return {"loop_id": loop_id, "outcome": "failed", "error": "implement-failed"}
            return {"loop_id": loop_id, "outcome": "completed"}
        except Exception as exc:
            try:
                with self._canonical_lock:
                    self._record_child_failure_retry(task_id, loop_id, lease_token, current_phase, type(exc).__name__)
            except Exception:
                self.store.recover_expired_child_leases(task_id, actor="parallel-worker-recovery")
            return {"loop_id": loop_id, "outcome": "failed", "error": type(exc).__name__}

    def _run_integration_review(self, task_id: str) -> dict[str, Any]:
        definition, state, stable_context = self.store.read_task(task_id)
        lease = self.store.acquire_lease(task_id, state["revision"], self.actor, seconds=self.lease_seconds)
        token = str(lease["active_lease"]["token"])
        try:
            context = self.phase_engine._context(definition, lease, stable_context, "review")
            child_artifacts: dict[str, dict[str, Any]] = {}
            for child in state["child_loops"]:
                result_ref = child.get("result_ref")
                if not isinstance(result_ref, Mapping) or not isinstance(result_ref.get("path"), str):
                    raise RecoveryRequiredError("integration child result reference is missing")
                result_path = (self.store._path(task_id) / str(result_ref["path"])).resolve()
                if self.store._path(task_id).resolve() not in result_path.parents or not result_path.is_file():
                    raise RecoveryRequiredError("integration child result is missing")
                artifact = read_json(result_path)
                self.store.schemas.validate("phase-result", artifact)
                if digest_json(artifact) != result_ref.get("content_hash"):
                    raise RecoveryRequiredError("integration child result hash mismatch")
                child_artifacts[f"child:{child['loop_id']}"] = artifact
            context = replace(
                context,
                active_artifacts={**context.active_artifacts, **child_artifacts},
            )
            context = self.phase_engine._prepare_subskill_context(context, token)
            try:
                output = self.phase_engine._launch_phase(context, token)
                current_revision = self.store.read_task(task_id)[1]["revision"]
                values, decision = self.phase_engine._materialize_output(context, output, current_revision, token)
                decision = decision or output.decision or "needs_plan"
            except Exception as exc:
                # Provider and validation failures are themselves durable
                # Integration Review outcomes.  The next process must see a
                # re-plan checkpoint, not an apparently untouched graph.
                output = AgentOutput(
                    "integration review failed; re-plan is required",
                    {},
                    "needs_plan",
                    {"provider_failure": type(exc).__name__},
                )
                values = {
                    "managed_action_refs": [],
                    "validation_evidence": [],
                    "acceptance_criteria": [dict(item) for item in context.definition["acceptance_criteria"]],
                    "blocking_issues": [f"integration review failure ({type(exc).__name__})"],
                    "agent_execution": output.metadata,
                }
                decision = "needs_plan"
            values.update({
                "summary": output.summary,
                "accepted_children": [str(item["loop_id"]) for item in state["child_loops"]],
                "integration_validation": list(values.get("integration_validation", ["all child results accepted"])),
                "integrated_outputs": list(values.get("integrated_outputs", [])),
                "cleanup_actions": list(values.get("cleanup_actions", [])),
            })
            if decision == "completed" and int(state["cycle"]) < self.phase_engine.min_cycles:
                decision = "needs_plan"
                values.setdefault("blocking_issues", []).append(
                    f"minimum Nous Loop cycles not reached ({state['cycle']}/{self.phase_engine.min_cycles})"
                )
                values["nous_loop_control"] = {
                    "minimum_cycles": self.phase_engine.min_cycles,
                    "current_cycle": int(state["cycle"]),
                    "forced_replan": True,
                }
            if decision != "completed":
                values["decision"] = decision
            latest = self.store.read_task(task_id)[1]
            if latest.get("current_action") is not None:
                raise ConflictError("integration review cannot release a live root action")
            released = self.store.release_lease(task_id, latest["revision"], self.actor, token)
            if decision == "completed":
                final = self.store.record_integration_review(task_id, released["revision"], self.actor, outputs=values)
                return self.phase_engine._result(task_id, final["state"], 1, "completed")
            final = self.store.record_integration_outcome(
                task_id,
                released["revision"],
                self.actor,
                outputs=values,
                decision=decision,
            )
            outcome = "waiting_human" if decision == "needs_human_input" else "needs_plan"
            result = self.phase_engine._result(task_id, final["state"], 1, outcome)
            if values.get("nous_loop_control", {}).get("forced_replan") is True:
                result["minimum_cycle_replan"] = True
            return result
        except Exception:
            latest = self.store.read_task(task_id)[1]
            if latest.get("current_action") is None and (latest.get("active_lease") or {}).get("token") == token:
                self.store.release_lease(task_id, latest["revision"], self.actor, token)
            raise


def _proposal_list(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise _provider_error(
            "invalid_managed_action",
            error_path=f"$.outputs.{label}",
            expected="array of objects",
            actual=value,
            message=f"{label} must be a list of objects",
        )
    return [dict(item) for item in value]
