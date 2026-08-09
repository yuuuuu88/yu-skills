from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .config import resolve_nous_home
from .errors import NousError, UnsupportedActionError, ValidationError
from .engine_config import discover_config_path, load_engine_config, parse_subskill_runtime_config
from .ids import validate_runtime_id
from .eval.runner import EvalRunner
from .eval.capability_runner import CapabilityEvalRunner
from .eval.provider import ProviderEvalConfig, ProviderEvalRunner
from .domain import ActionDescriptor, PolicyDecision, PolicyGateway
from .parallel import ParallelScheduler
from .research import PublicWebResearchAdapter, ResearchPolicy
from .taskctl import RequestLedger, TaskStore
from .taskctl.request import validate_request_id


_MUTATING_COMMANDS = {
    "init-home",
    "create",
    "rename",
    "duplicate",
    "cancel",
    "reopen",
    "archive",
    "unarchive",
    "approval-request",
    "approval-grant",
    "approval-deny",
    "approval-revoke",
    "approval-expire",
    "action-start",
    "action-complete",
    "action-reconcile",
    "child-create",
    "child-revise",
    "parallel-plan",
    "worktree-bind",
    "reservation-request",
    "reservation-acquire",
    "reservation-release",
    "reservation-renew",
    "reservation-recover",
    "child-start",
    "child-renew",
    "child-release",
    "child-recover",
    "child-result",
    "integration-review",
    "git-durability-observe",
    "git-durability-reconcile",
    "repair-apply",
    "pause",
    "pause-ack",
    "resume-prepare",
    "lease-acquire",
    "lease-renew",
    "lease-release",
    "lease-recover",
    "checkpoint",
    "record-human-question",
    "record-human-answer",
    "record-knowledge",
    "record-phase-output",
    "record-review",
    "render",
    "engine-run",
}


def _input(path: str | None) -> dict[str, Any]:
    if not path: return {}
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    value = json.loads(raw)
    if not isinstance(value, dict): raise ValueError("input must be a JSON object")
    return value


def _response(command: str, result: Any, request_id: str | None = None, task_id: str | None = None) -> dict[str, Any]:
    return {"contract_version": 1, "ok": True, "command": command, "request_id": request_id,
            "task_id": task_id, "result": result, "warnings": []}


def _actor(value: dict[str, Any]) -> str:
    return str(value.get("actor", {}).get("actor_id") or "nous-cli")


def _request_id(args: argparse.Namespace, payload: dict[str, Any], *, required: bool) -> str | None:
    flag = getattr(args, "request_id", None)
    body = payload.get("request_id")
    if flag is not None and body is not None and flag != body:
        raise ValidationError("request_id in CLI and input envelope must match")
    value = flag if flag is not None else body
    if value is None:
        if required:
            raise ValidationError("mutating taskctl commands require request_id")
        return None
    return validate_request_id(value)


def _validate_actor(payload: dict[str, Any]) -> None:
    actor = payload.get("actor")
    if not isinstance(actor, dict) or any(not isinstance(actor.get(key), str) or not actor[key].strip() for key in ("type", "run_id", "actor_id")):
        raise ValidationError("mutating taskctl commands require actor.type, actor.run_id, and actor.actor_id")


def _validate_request_envelope(payload: dict[str, Any], *, mutating: bool) -> None:
    if not mutating:
        return
    if payload.get("contract_version") != 1:
        raise ValidationError("mutating taskctl commands require contract_version 1")
    loop_id = payload.get("loop_id", "root")
    if not isinstance(loop_id, str) or not loop_id.strip() or len(loop_id) > 128 or any(char in loop_id for char in "/\\"):
        raise ValidationError("loop_id must be a nonempty safe identifier")


def _engine_integer(payload: dict[str, Any], name: str, *, default: int, minimum: int, maximum: int) -> int:
    value = payload.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValidationError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


def _engine_timeout(payload: dict[str, Any], lease_seconds: int) -> float:
    value = payload.get("agent_timeout_seconds", 180)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ValidationError("agent_timeout_seconds must be a positive number")
    if value >= lease_seconds - 5:
        raise ValidationError("agent_timeout_seconds must leave at least 5 seconds before lease expiry")
    return float(value)


def _request_material(args: argparse.Namespace, payload: dict[str, Any], request_id: str) -> dict[str, Any]:
    flags = {
        key: value
        for key, value in vars(args).items()
        if key not in {"input", "request_id", "pretty", "config", "nous_home"}
    }
    return {"contract_version": 1, "request_id": request_id, "command": f"taskctl {args.command}", "flags": flags, "payload": payload}


def _prepare_engine_payload(args: argparse.Namespace, payload: dict[str, Any]) -> dict[str, Any]:
    """Fill the engine request from one friendly config file when needed."""
    config_path = getattr(args, "engine_config", None) or args.config or payload.get("engine_config_path")
    config = None
    if config_path or not payload.get("agent_commands"):
        config_path = discover_config_path(config_path)
        if config_path is not None:
            # Use this exact path for both Engine settings and NOUS_HOME
            # resolution. Explicit --nous-home / NOUS_HOME remain higher
            # priority overrides in the existing runtime contract.
            args.engine_config = str(config_path)
        config = load_engine_config(config_path)
    if config is not None:
        defaults = config.payload()
        if isinstance(defaults.get("subskills"), dict) and isinstance(payload.get("subskills"), dict):
            merged_subskills = {**defaults["subskills"], **payload["subskills"]}
            if isinstance(defaults["subskills"].get("phase_capabilities"), dict) and isinstance(payload["subskills"].get("phase_capabilities"), dict):
                merged_subskills["phase_capabilities"] = {
                    **defaults["subskills"]["phase_capabilities"],
                    **payload["subskills"]["phase_capabilities"],
                }
            defaults["subskills"] = merged_subskills
        defaults.update(payload)
        payload = defaults
    if payload.get("agent_commands"):
        return payload
    raise ValidationError("engine-run needs agent commands; configure .nous/config.json or pass --input")


def _flag_value(args: argparse.Namespace, payload: dict[str, Any], name: str) -> Any:
    flag = getattr(args, name, None)
    if flag is not None and name in payload and flag != payload[name]:
        raise ValidationError(f"{name} in CLI and input envelope must match")
    return flag if flag is not None else payload.get(name)


def _revision(args: argparse.Namespace, value: dict[str, Any]) -> int:
    flag = getattr(args, "expected_revision", None)
    if flag is not None and "expected_revision" in value and flag != value["expected_revision"]:
        raise ValidationError("expected_revision in CLI and input envelope must match")
    candidate = flag if flag is not None else value.get("expected_revision")
    if not isinstance(candidate, int): raise ValueError("expected_revision is required")
    return candidate


def _record_list(payload: dict[str, Any], name: str) -> list[dict[str, Any]]:
    value = payload.get(name, [])
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValidationError(f"{name} must be a list of JSON objects")
    return value


def _strings(payload: dict[str, Any], name: str, *, default: list[str] | None = None) -> list[str]:
    value = payload.get(name, [] if default is None else default)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValidationError(f"{name} must be a list of strings")
    return value


def _object(payload: dict[str, Any], name: str) -> dict[str, Any]:
    value = payload.get(name)
    if not isinstance(value, dict):
        raise ValidationError(f"{name} must be a JSON object")
    return value


def _string(payload: dict[str, Any], name: str, *, required: bool = True) -> str | None:
    value = payload.get(name)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} must be a nonempty string")
    return value


def _integer(payload: dict[str, Any], name: str, *, default: int | None = None) -> int:
    value = payload.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{name} must be an integer")
    return value


def _diagnosis(store: TaskStore, task_id: str) -> dict[str, Any]:
    """Return a read-only, canonical recovery summary for one task."""
    return store.diagnose(task_id)


def _action_descriptor(payload: dict[str, Any], task_id: str, expected_revision: int) -> ActionDescriptor:
    value = payload.get("action")
    if not isinstance(value, dict):
        raise ValidationError("action request requires an action object")
    def tuple_field(name: str) -> tuple[str, ...]:
        raw = value.get(name, [])
        if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
            raise ValidationError(f"action.{name} must be a list of strings")
        return tuple(raw)
    command = value.get("command")
    if command is not None and (not isinstance(command, list) or any(not isinstance(item, str) for item in command)):
        raise ValidationError("action.command must be a list of strings or null")
    action_task_id = str(value.get("task_id", task_id))
    if action_task_id != task_id:
        raise ValidationError("action.task_id must match the task argument")
    return ActionDescriptor(
        action_id=str(value.get("action_id", "")),
        task_id=action_task_id,
        kind=str(value.get("kind", "")),  # type: ignore[arg-type]
        repository_root=value.get("repository_root"),
        targets=tuple_field("targets"),
        command=tuple(command) if command is not None else None,
        network_destinations=tuple_field("network_destinations"),
        estimated_cost_usd=value.get("estimated_cost_usd"),
        approval_id=value.get("approval_id"),
        declared_risk=value.get("declared_risk", value.get("risk")),  # type: ignore[arg-type]
        loop_id=str(value.get("loop_id", "root")),
        expected_state_revision=value.get("expected_state_revision", expected_revision),
        request_id=value.get("request_id"),
        idempotency_key=value.get("idempotency_key"),
        expected_effect=value.get("expected_effect"),
        plan_ref=value.get("plan_ref"),
        plan_content_hash=value.get("plan_content_hash"),
        environment=str(value.get("environment", "local")),
        reservation_ids=tuple_field("reservation_ids"),
        resource_ids=tuple_field("resource_ids"),
    )


def _policy_decision_payload(decision: PolicyDecision) -> dict[str, Any]:
    return {
        "outcome": decision.outcome,
        "risk": decision.risk,
        "reason": decision.reason,
        "action_digest": decision.action_digest,
        "grant_id": decision.grant_id,
        "constraints": decision.constraints,
    }


def _stable_file_digests(root: Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    digests: dict[str, str] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root)
        if ".transactions" in relative.parts or path.name == ".lock":
            continue
        digests[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


def _event_summaries(root: Path) -> list[dict[str, Any]]:
    if not root.is_dir():
        return []
    summaries: list[dict[str, Any]] = []
    for path in sorted(root.glob("tasks/*/events.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            summaries.append(
                {
                    "event_id": event.get("event_id"),
                    "task_id": event.get("task_id"),
                    "revision": event.get("revision"),
                    "type": event.get("type"),
                    "request_id": payload.get("request_id"),
                }
            )
    return summaries


def _state_revision(root: Path, task_id: str | None) -> int | None:
    if not task_id:
        return None
    path = root / "tasks" / task_id / "state.json"
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    revision = value.get("revision")
    return revision if isinstance(revision, int) else None


def _dry_run(
    args: argparse.Namespace,
    payload: dict[str, Any],
    request_id: str | None,
    home: Path,
) -> dict[str, Any]:
    """Execute a mutation against a disposable copy and return its plan only."""
    before_files = _stable_file_digests(home)
    before_events = _event_summaries(home)
    before_event_ids = {event["event_id"] for event in before_events}
    with tempfile.TemporaryDirectory(prefix="nous-dry-run-") as temporary:
        sandbox = Path(temporary) / "home"
        if home.exists():
            shutil.copytree(home, sandbox, symlinks=True)
        else:
            sandbox.mkdir(parents=True)
        simulation_args = copy.copy(args)
        simulation_args.dry_run = False
        simulation_args.nous_home = str(sandbox)
        simulation_args.config = None
        simulated = run(simulation_args, payload, request_id=request_id)
        after_files = _stable_file_digests(sandbox)
        after_events = _event_summaries(sandbox)
        task_id = simulated.get("task_id") or getattr(args, "task_id", None)
        to_revision = _state_revision(sandbox, task_id)

    created = sorted(path for path in after_files if path not in before_files)
    updated = sorted(path for path in after_files if path in before_files and after_files[path] != before_files[path])
    deleted = sorted(path for path in before_files if path not in after_files)
    planned_events = [event for event in after_events if event["event_id"] not in before_event_ids]
    return _response(
        args.command,
        {
            "dry_run": True,
            "planned_files_created": created,
            "planned_files_updated": updated,
            "planned_files_deleted": deleted,
            "planned_events": planned_events,
            "from_revision": _state_revision(home, task_id),
            "to_revision": to_revision,
            "validation_warnings": [],
            "policy_outcome": None,
            "gateway_outcome": None,
            "required_preflight": [],
            "required_postcheck": [],
            "required_approval": [],
        },
        task_id=task_id,
    )


def run(
    args: argparse.Namespace,
    payload: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    if args.top == "eval":
        if args.eval_command.startswith("capability-"):
            capability_runner = CapabilityEvalRunner(
                manifest_path=Path(args.manifest) if args.manifest else None,
                root=Path(args.root) if args.root else None,
            )
            command = f"eval {args.eval_command}"
            if args.eval_command == "capability-validate":
                return _response(command, capability_runner.validate())
            if args.eval_command == "capability-list":
                return _response(command, capability_runner.list(args.profile))
            if args.eval_command in {"capability-run", "capability-smoke", "capability-full", "capability-holdout"}:
                profile = args.profile if args.eval_command == "capability-run" else args.eval_command.removeprefix("capability-")
                result = capability_runner.run(
                    profile=profile,
                    observations_path=Path(args.observations) if args.observations else None,
                    trials=args.trials,
                    baseline_path=Path(args.baseline) if args.baseline else None,
                )
                return _response(command, result)
            if args.eval_command == "capability-report":
                return _response(command, capability_runner.report(args.run_id, args.format))
            if args.eval_command == "capability-from-eval":
                result = capability_runner.run_from_eval_capture(
                    eval_root=Path(args.eval_root),
                    eval_run=args.eval_run,
                    profile=args.profile,
                    baseline_path=Path(args.baseline) if args.baseline else None,
                )
                return _response(command, result)
            if args.eval_command == "capability-provider-validate":
                provider_runner = ProviderEvalRunner(
                    manifest_path=Path(args.manifest) if args.manifest else None,
                    root=Path(args.root) if args.root else None,
                )
                return _response(command, provider_runner.validate_config(ProviderEvalConfig.from_mapping(_input(args.provider_config))))
            if args.eval_command == "capability-provider-run":
                provider_runner = ProviderEvalRunner(
                    manifest_path=Path(args.manifest) if args.manifest else None,
                    root=Path(args.root) if args.root else None,
                )
                result = provider_runner.run(
                    config=ProviderEvalConfig.from_mapping(_input(args.provider_config)),
                    profile=args.profile,
                    allow_host_provider=args.allow_host_provider,
                    baseline_path=Path(args.baseline) if args.baseline else None,
                )
                return _response(command, result)
            raise ValueError(f"unsupported capability command: {args.eval_command}")
        runner = EvalRunner(
            manifest_path=Path(args.manifest) if args.manifest else None,
            root=Path(args.root) if args.root else None,
        )
        command = f"eval {args.eval_command}"
        if args.eval_command == "validate-manifest":
            return _response(command, runner.validate_manifest(args.stage))
        if args.eval_command == "list":
            return _response(command, runner.list(stage=args.stage, release=args.release, eval_ids=tuple(args.eval_id or ())))
        if args.eval_command == "run":
            result = runner.run(
                stage=args.stage,
                release=args.release,
                eval_ids=tuple(args.eval_id or ()),
                trials=args.trials,
            )
            return _response(command, result)
        if args.eval_command == "report":
            return _response(command, runner.report(args.run_id, args.format))
        if args.eval_command == "cleanup":
            return _response(command, runner.cleanup(args.run_id))
        raise ValueError(f"unsupported Eval command: {args.eval_command}")
    payload = _input(args.input) if payload is None else payload
    command = args.command
    if command == "engine-run":
        payload = _prepare_engine_payload(args, payload)
    home = resolve_nous_home(args.nous_home, getattr(args, "engine_config", None) or args.config)
    if getattr(args, "dry_run", False) and command in _MUTATING_COMMANDS:
        return _dry_run(args, payload, request_id, home)
    store = TaskStore(home, request_id=request_id)
    if command == "init-home": store.init(); return _response(command, {"nous_home": str(home)})
    if command == "doctor":
        schema_errors = store.schemas.check_all()
        return _response(command, {"nous_home": str(home), "schemas": "ok" if not schema_errors else "invalid", "schema_errors": schema_errors, "store_ready": home.exists()})
    if command == "list": return _response(command, {"tasks": store.list(include_archived=bool(getattr(args, "include_archived", False)))})
    if command == "status":
        store.init(); store.render_all(); return _response(command, {"status_view": str(store.root / "STATUS.md"), "tasks": store.list()})
    if command == "engine-run":
        from .durability import DurabilityProfile
        from .engine import AutonomousEngine, CrossReviewBackend, JsonCommandAgentBackend
        from .observability import JsonlTraceExporter
        from .resources import ExecutionResourcePolicy, ResourceBudget
        from .subskills import SubskillRuntime

        raw_commands = payload.get("agent_commands")
        if not isinstance(raw_commands, dict):
            raise ValidationError("engine-run requires agent_commands for plan, implement, and review")
        commands: dict[str, tuple[str, ...]] = {}
        for phase in ("plan", "implement", "review"):
            command_value = raw_commands.get(phase)
            if not isinstance(command_value, list) or not command_value or any(not isinstance(item, str) or not item for item in command_value):
                raise ValidationError(f"agent_commands.{phase} must be a nonempty string list")
            commands[phase] = tuple(command_value)
        allowed_value = payload.get("allowed_processes", [])
        if not isinstance(allowed_value, list) or not allowed_value or any(not isinstance(item, list) or not item or any(not isinstance(part, str) or not part for part in item) for item in allowed_value):
            raise ValidationError("allowed_processes must be a nonempty list of string lists")
        max_cycles = _engine_integer(payload, "max_cycles", default=3, minimum=1, maximum=100)
        min_cycles = _engine_integer(payload, "min_cycles", default=1, minimum=1, maximum=100)
        if min_cycles > max_cycles:
            raise ValidationError("min_cycles must not exceed max_cycles")
        max_steps = _engine_integer(payload, "max_steps", default=max(12, min_cycles * 3), minimum=1, maximum=1000)
        if min_cycles > 1 and max_steps < min_cycles * 3:
            raise ValidationError("max_steps must allow at least three phases per min_cycles")
        lease_seconds = _engine_integer(payload, "lease_seconds", default=300, minimum=30, maximum=86400)
        agent_timeout_seconds = _engine_timeout(payload, lease_seconds)
        raw_subagents = payload.get("subagents", {})
        if not isinstance(raw_subagents, dict):
            raise ValidationError("subagents must be an object")
        unknown_subagents = sorted(set(raw_subagents) - {"required", "mode"})
        if unknown_subagents:
            raise ValidationError("unknown subagents keys: " + ", ".join(unknown_subagents))
        subagents_required = raw_subagents.get("required", True)
        if not isinstance(subagents_required, bool):
            raise ValidationError("subagents.required must be boolean")
        subagents_mode = raw_subagents.get("mode", "external")
        if subagents_mode != "external":
            raise ValidationError("subagents.mode must be external")
        raw_resources = payload.get("resources", {})
        if not isinstance(raw_resources, dict):
            raise ValidationError("resources must be an object")
        resource_keys = {"max_concurrent_agents", "max_output_bytes", "max_tool_calls", "max_wall_seconds"}
        unknown_resources = sorted(set(raw_resources) - resource_keys)
        if unknown_resources:
            raise ValidationError("unknown resources keys: " + ", ".join(unknown_resources))
        resource_policy = ExecutionResourcePolicy(
            max_concurrent_agents=raw_resources.get("max_concurrent_agents", 3),
            max_output_bytes=raw_resources.get("max_output_bytes", 4 * 1024 * 1024),
            max_tool_calls=raw_resources.get("max_tool_calls", 100),
            max_wall_seconds=raw_resources.get("max_wall_seconds", 300.0),
        )
        resource_policy.validate()
        resource_budget = ResourceBudget(resource_policy)
        raw_durability = payload.get("durability", {})
        if not isinstance(raw_durability, dict) or set(raw_durability) - {"mode"}:
            raise ValidationError("durability must contain only mode")
        durability = DurabilityProfile(mode=raw_durability.get("mode", "local"))
        durability.validate_home(home)
        raw_observability = payload.get("observability", {})
        if not isinstance(raw_observability, dict) or set(raw_observability) - {"trace_export_path", "max_event_bytes"}:
            raise ValidationError("observability must contain only trace_export_path and max_event_bytes")
        trace_exporter = None
        trace_path = raw_observability.get("trace_export_path")
        max_event_bytes = raw_observability.get("max_event_bytes", 256 * 1024)
        if not isinstance(max_event_bytes, int) or isinstance(max_event_bytes, bool) or not 1024 <= max_event_bytes <= 4 * 1024 * 1024:
            raise ValidationError("observability.max_event_bytes must be between 1024 and 4194304")
        if trace_path is not None:
            if not isinstance(trace_path, str) or not trace_path.strip():
                raise ValidationError("observability.trace_export_path must be a nonempty string or null")
            trace_file = Path(trace_path).expanduser()
            if not trace_file.is_absolute():
                trace_file = home / trace_file
            trace_exporter = JsonlTraceExporter(trace_file, max_event_bytes)

        backend = JsonCommandAgentBackend(
            commands,
            runtime_root=home / "runtime",
            source_root=Path(__file__).resolve().parents[1],
            timeout_seconds=agent_timeout_seconds,
        )
        review_mode = payload.get("review_mode", "single")
        if not isinstance(review_mode, str) or review_mode not in {"single", "cross"}:
            raise ValidationError("review_mode must be single or cross")
        if review_mode == "cross":
            raw_reviewers = payload.get("reviewers")
            if not isinstance(raw_reviewers, list) or len(raw_reviewers) < 2:
                raise ValidationError("cross-review requires at least two reviewers")
            if len(raw_reviewers) > 2:
                raise ValidationError("cross-review supports at most two reviewers")
            reviewer_backends: dict[str, JsonCommandAgentBackend] = {}
            perspectives: dict[str, str] = {}
            for index, reviewer in enumerate(raw_reviewers):
                if not isinstance(reviewer, dict) or set(reviewer) != {"id", "perspective", "command"}:
                    raise ValidationError(f"reviewers[{index}] must contain exactly id, perspective, and command")
                reviewer_id = reviewer.get("id")
                perspective = reviewer.get("perspective")
                command_value = reviewer.get("command")
                if not isinstance(reviewer_id, str):
                    raise ValidationError(f"reviewers[{index}].id must be a safe nonempty identifier")
                validate_runtime_id(reviewer_id, f"reviewers[{index}].id")
                if reviewer_id in reviewer_backends:
                    raise ValidationError(f"duplicate cross-reviewer id: {reviewer_id}")
                if not isinstance(perspective, str) or not perspective.strip():
                    raise ValidationError(f"reviewers[{index}].perspective must be nonempty")
                if not isinstance(command_value, list) or not command_value or any(not isinstance(item, str) or not item for item in command_value):
                    raise ValidationError(f"reviewers[{index}].command must be a nonempty string list")
                reviewer_backends[reviewer_id] = JsonCommandAgentBackend(
                    {"review": tuple(command_value)},
                    runtime_root=home / "runtime",
                    source_root=Path(__file__).resolve().parents[1],
                    timeout_seconds=agent_timeout_seconds,
                )
                perspectives[reviewer_id] = perspective
            fallback = payload.get("review_fallback", "single")
            if not isinstance(fallback, str) or fallback not in {"single", "human", "none"}:
                raise ValidationError("review_fallback must be single, human, or none")
            backend = CrossReviewBackend(
                backend,
                reviewer_backends,
                perspectives=perspectives,
                max_parallel=_engine_integer(payload, "review_max_parallel", default=3, minimum=1, maximum=8),
                fallback=fallback,
                timeout_seconds=agent_timeout_seconds,
            )
        raw_subskills = payload.get("subskills", {})
        subskill_runtime = None
        enabled, subskill_config = parse_subskill_runtime_config(
            raw_subskills,
            base=Path.cwd().resolve(),
            allow_relative_roots=False,
            label="subskills",
        )
        if enabled:
            subskill_runtime = SubskillRuntime(
                subskill_config
            )
        engine = AutonomousEngine(
            store,
            backend,
            actor=_actor(payload),
            allowed_processes=tuple(tuple(item) for item in allowed_value),
            min_cycles=min_cycles,
            max_cycles=max_cycles,
            max_steps=max_steps,
            lease_seconds=lease_seconds,
            subskill_runtime=subskill_runtime,
            require_external_subagents=subagents_required,
            trace_exporter=trace_exporter,
            resource_budget=resource_budget,
        )
        return _response(command, engine.run(args.task_id), task_id=args.task_id)
    if command == "show":
        definition, state, context = store.read_task(args.task_id); return _response(command, {"definition": definition, "state": state, "stable_context": context}, task_id=args.task_id)
    if command == "parallel-ready":
        scheduler = ParallelScheduler(store, max_concurrency=_integer(payload, "max_concurrency", default=3), max_depth=_integer(payload, "max_depth", default=2))
        return _response(command, {"ready": scheduler.ready(args.task_id), "diagnosis": _diagnosis(store, args.task_id)}, task_id=args.task_id)
    if command == "diagnose":
        return _response(command, _diagnosis(store, args.task_id), task_id=args.task_id)
    if command == "repair-plan":
        return _response(command, store.repair_plan(args.task_id), task_id=args.task_id)
    if command == "create":
        result = store.create(title=_flag_value(args, payload, "title") or "", objective=_flag_value(args, payload, "objective") or "", target_repository=_flag_value(args, payload, "target_repository"), actor=_actor(payload))
        return _response(command, result, task_id=result["task_id"])
    if command == "rename":
        state = store.rename(args.task_id, _revision(args, payload), _actor(payload), str(_flag_value(args, payload, "title") or ""))
        return _response(command, state, task_id=args.task_id)
    if command == "duplicate":
        result = store.duplicate(args.task_id, _actor(payload), title=_flag_value(args, payload, "title"), objective=_flag_value(args, payload, "objective"))
        return _response(command, result, task_id=result["task_id"])
    if command == "cancel":
        state = store.cancel(args.task_id, _revision(args, payload), _actor(payload), str(payload.get("reason", "")))
        return _response(command, {"state": state}, task_id=args.task_id)
    if command == "reopen":
        state = store.reopen(args.task_id, _revision(args, payload), _actor(payload), str(payload.get("reason", "")))
        return _response(command, {"state": state}, task_id=args.task_id)
    if command in {"archive", "unarchive"}:
        visibility = "archived" if command == "archive" else "visible"
        state = store.set_visibility(args.task_id, _revision(args, payload), _actor(payload), visibility)
        return _response(command, {"state": state}, task_id=args.task_id)
    if command == "approval-request":
        revision = _revision(args, payload)
        action = _action_descriptor(payload, args.task_id, revision)
        result = store.request_exact_approval(args.task_id, revision, _actor(payload), action, expires_at=payload.get("expires_at"))
        return _response(command, result, task_id=args.task_id)
    if command == "approval-grant":
        state = store.grant_exact_approval(args.task_id, _revision(args, payload), _actor(payload), str(payload["approval_id"]), str(payload["answer_id"]))
        return _response(command, state, task_id=args.task_id)
    if command in {"approval-deny", "approval-revoke"}:
        method = store.deny_exact_approval if command == "approval-deny" else store.revoke_exact_approval
        result = method(args.task_id, _revision(args, payload), _actor(payload), str(payload["approval_id"]), str(payload.get("reason", "approval state changed")))
        return _response(command, result, task_id=args.task_id)
    if command == "approval-expire":
        result = store.expire_approvals(args.task_id, _actor(payload))
        return _response(command, {"result": result}, task_id=args.task_id)
    if command == "approval-list":
        return _response(command, store.list_approvals(args.task_id), task_id=args.task_id)
    if command == "policy-evaluate":
        raw_action = payload.get("action")
        if not isinstance(raw_action, dict) or not isinstance(raw_action.get("task_id"), str):
            raise ValidationError("policy-evaluate requires action.task_id")
        action = _action_descriptor(payload, str(raw_action["task_id"]), int(raw_action.get("expected_state_revision", 0) or 0))
        decision = PolicyGateway().evaluate(action)
        return _response(command, {"outcome": decision.outcome, "risk": decision.risk, "reason": decision.reason, "action_digest": decision.action_digest, "grant_id": decision.grant_id, "constraints": decision.constraints}, task_id=action.task_id)
    if command == "action-start":
        revision = _revision(args, payload)
        action = _action_descriptor(payload, args.task_id, revision)
        preflight = payload.get("preflight")
        if not isinstance(preflight, dict):
            raise ValidationError("action-start requires a typed preflight object")
        result = store.begin_managed_action(action, _actor(payload), PolicyGateway(), preflight)
        if isinstance(result.get("decision"), PolicyDecision):
            result = {**result, "decision": _policy_decision_payload(result["decision"])}
        return _response(command, result, task_id=args.task_id)
    if command == "action-complete":
        postflight = payload.get("postflight")
        if not isinstance(postflight, dict):
            raise ValidationError("action-complete requires a typed postflight object")
        result = store.finish_managed_action(args.task_id, _revision(args, payload), _actor(payload), str(payload["lease_token"]), str(payload["action_id"]), str(payload["grant_id"]), int(payload["returncode"]), postflight)
        return _response(command, result, task_id=args.task_id)
    if command == "action-reconcile":
        reconciliation = payload.get("reconciliation")
        if not isinstance(reconciliation, dict):
            raise ValidationError("action-reconcile requires a typed reconciliation object")
        result = store.reconcile_managed_action(args.task_id, _revision(args, payload), _actor(payload), str(payload["action_id"]), reconciliation)
        return _response(command, result, task_id=args.task_id)
    if command == "child-create":
        result = store.create_child_loop(args.task_id, _revision(args, payload), _actor(payload), _object(payload, "contract"))
        return _response(command, result, task_id=args.task_id)
    if command == "child-revise":
        loop_id = _string(payload, "loop_id")
        assert loop_id is not None
        result = store.revise_child_contract(args.task_id, _revision(args, payload), _actor(payload), loop_id, _object(payload, "contract"))
        return _response(command, result, task_id=args.task_id)
    if command == "parallel-plan":
        plan = _object(payload, "plan")
        mode = _string(plan, "mode")
        rationale = _string(plan, "rationale")
        integration_strategy = _string(plan, "integration_strategy")
        max_concurrency = _integer(plan, "max_concurrency")
        max_depth = _integer(plan, "max_depth")
        assert mode is not None and rationale is not None and integration_strategy is not None
        result = store.record_parallel_plan(
            args.task_id,
            _revision(args, payload),
            _actor(payload),
            mode=mode,
            rationale=rationale,
            max_concurrency=max_concurrency,
            max_depth=max_depth,
            integration_strategy=integration_strategy,
            integration_validation=_strings(plan, "integration_validation"),
            knowledge_references=_record_list({"items": plan.get("knowledge_references", [])}, "items"),
        )
        return _response(command, result, task_id=args.task_id)
    if command == "worktree-bind":
        result = store.record_child_worktree(
            args.task_id,
            _revision(args, payload),
            _actor(payload),
            loop_id=str(_string(payload, "loop_id")),
            action_id=str(_string(payload, "action_id")),
            branch=str(_string(payload, "branch")),
            base_commit=str(_string(payload, "base_commit")),
            worktree_path=str(_string(payload, "worktree_path")),
        )
        return _response(command, result, task_id=args.task_id)
    if command == "reservation-request":
        result = store.request_reservation(
            args.task_id,
            _revision(args, payload),
            _actor(payload),
            loop_id=str(_string(payload, "loop_id")),
            resource=str(_string(payload, "resource")),
            mode=str(_string(payload, "mode")),
            input_version_ref=_string(payload, "input_version_ref", required=False),
        )
        return _response(command, result, task_id=args.task_id)
    if command == "reservation-acquire":
        result = store.acquire_reservation(args.task_id, _revision(args, payload), _actor(payload), reservation_id=str(_string(payload, "reservation_id")), lease_seconds=_integer(payload, "lease_seconds", default=300))
        return _response(command, result, task_id=args.task_id)
    if command == "reservation-release":
        result = store.release_reservation(args.task_id, _revision(args, payload), _actor(payload), reservation_id=str(_string(payload, "reservation_id")), lease_token=str(_string(payload, "lease_token")), reason=str(_string(payload, "reason")))
        return _response(command, result, task_id=args.task_id)
    if command == "reservation-renew":
        result = store.renew_reservation(args.task_id, _revision(args, payload), _actor(payload), reservation_id=str(_string(payload, "reservation_id")), lease_token=str(_string(payload, "lease_token")), lease_seconds=_integer(payload, "lease_seconds", default=300))
        return _response(command, result, task_id=args.task_id)
    if command == "reservation-recover":
        result = store.recover_expired_reservations(args.task_id, _actor(payload))
        return _response(command, {"result": result}, task_id=args.task_id)
    if command == "child-start":
        result = store.start_child_loop(args.task_id, _revision(args, payload), _actor(payload), loop_id=str(_string(payload, "loop_id")), lease_seconds=_integer(payload, "lease_seconds", default=300))
        return _response(command, result, task_id=args.task_id)
    if command == "child-renew":
        result = store.renew_child_lease(args.task_id, _revision(args, payload), _actor(payload), loop_id=str(_string(payload, "loop_id")), lease_token=str(_string(payload, "lease_token")), lease_seconds=_integer(payload, "lease_seconds", default=300))
        return _response(command, result, task_id=args.task_id)
    if command == "child-release":
        result = store.release_child_lease(args.task_id, _revision(args, payload), _actor(payload), loop_id=str(_string(payload, "loop_id")), lease_token=str(_string(payload, "lease_token")), reason=str(_string(payload, "reason")))
        return _response(command, result, task_id=args.task_id)
    if command == "child-recover":
        result = store.recover_expired_child_leases(args.task_id, _actor(payload))
        return _response(command, {"result": result}, task_id=args.task_id)
    if command == "child-result":
        result = store.record_child_result(args.task_id, _revision(args, payload), _actor(payload), loop_id=str(_string(payload, "loop_id")), lease_token=str(_string(payload, "lease_token")), outputs=_object(payload, "outputs"))
        return _response(command, result, task_id=args.task_id)
    if command == "integration-review":
        result = store.record_integration_review(args.task_id, _revision(args, payload), _actor(payload), outputs=_object(payload, "outputs"))
        return _response(command, result, task_id=args.task_id)
    if command == "git-durability-observe":
        result = store.record_git_durability_observation(args.task_id, _revision(args, payload), _actor(payload), _object(payload, "observation"))
        return _response(command, result, task_id=args.task_id)
    if command == "git-durability-reconcile":
        result = store.record_git_durability_reconciliation(args.task_id, _revision(args, payload), _actor(payload), outcome=str(_string(payload, "outcome")), rationale=str(_string(payload, "rationale")))
        return _response(command, result, task_id=args.task_id)
    if command == "repair-apply":
        raise UnsupportedActionError("generic repair application is intentionally unsupported; use repair-plan and an exact managed action or Human-approved replan")
    if command == "pause":
        state = store.request_pause(args.task_id, _revision(args,payload), _actor(payload)); return _response(command, {"state": state}, task_id=args.task_id)
    if command == "pause-ack":
        state = store.pause(args.task_id, _revision(args,payload), _actor(payload),str(payload["lease_token"])); return _response(command, {"state": state}, task_id=args.task_id)
    if command == "resume-prepare":
        result = store.resume_prepare(args.task_id, _revision(args,payload), _actor(payload)); return _response(command, result, task_id=args.task_id)
    if command == "lease-acquire":
        state=store.acquire_lease(args.task_id,_revision(args,payload),_actor(payload),int(payload.get("seconds",300))); return _response(command,{"state":state},task_id=args.task_id)
    if command == "lease-renew":
        state=store.renew_lease(args.task_id,_revision(args,payload),_actor(payload),str(payload["lease_token"]),int(payload.get("seconds",300))); return _response(command,{"state":state},task_id=args.task_id)
    if command == "lease-release":
        state=store.release_lease(args.task_id,_revision(args,payload),_actor(payload),str(payload["lease_token"])); return _response(command,{"state":state},task_id=args.task_id)
    if command == "lease-recover":
        state=store.recover_expired_lease(args.task_id,_actor(payload)); return _response(command,{"state":state},task_id=args.task_id)
    if command == "checkpoint":
        state=store.checkpoint(args.task_id,_revision(args,payload),_actor(payload),str(payload.get("reason","checkpoint")),str(payload.get("next_safe_action","resume-prepare"))); return _response(command,{"state":state},task_id=args.task_id)
    if command == "record-human-question":
        result=store.record_question(args.task_id,_revision(args,payload),_actor(payload),payload["question"]); return _response(command,{"question":result},task_id=args.task_id)
    if command == "record-human-answer":
        state=store.answer_question(args.task_id,_revision(args,payload),_actor(payload),str(payload["question_id"]),payload["answer"]); return _response(command,{"state":state},task_id=args.task_id)
    if command == "record-knowledge":
        result = store.record_knowledge(
            args.task_id,
            _revision(args, payload),
            _actor(payload),
            questions=_record_list(payload, "questions"),
            sources=_record_list(payload, "sources"),
            claims=_record_list(payload, "claims"),
            loop_id=str(payload.get("loop_id", "root")),
        )
        return _response(command, result, task_id=args.task_id)
    if command in {"record-phase-output", "record-review"}:
        phase = _flag_value(args, payload, "phase")
        result=store.record_phase(args.task_id,_revision(args,payload),_actor(payload),str(payload["lease_token"]),str(phase),str(payload["summary"]),dict(payload.get("outputs",{})),payload.get("decision")); return _response(command,result,task_id=args.task_id)
    if command == "render": store.render_all(); return _response(command,{"rendered":True},task_id=args.task_id)
    if command == "validate":
        definition,state,context=store.read_task(args.task_id)
        checkpoint=store._path(args.task_id)/"checkpoints/latest.json"
        store.schemas.validate("checkpoint", json.loads(checkpoint.read_text()))
        return _response(command,{"valid":True,"revision":state["revision"]},task_id=args.task_id)
    raise ValueError(f"unsupported command: {command}")


def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(prog="nous")
    p.add_argument("--nous-home"); p.add_argument("--config"); p.add_argument("--input"); p.add_argument("--request-id"); p.add_argument("--dry-run", action="store_true"); p.add_argument("--pretty",action="store_true")
    sub=p.add_subparsers(dest="top",required=True); taskctl=sub.add_parser("taskctl")
    commands=taskctl.add_subparsers(dest="command",required=True)
    for name in ("init-home","doctor","status"): commands.add_parser(name)
    list_command = commands.add_parser("list")
    list_command.add_argument("--include-archived", action="store_true")
    for name in ("show","parallel-ready","diagnose","repair-plan","rename","cancel","reopen","archive","unarchive","approval-request","approval-grant","approval-deny","approval-revoke","approval-expire","approval-list","action-start","action-complete","action-reconcile","child-create","child-revise","parallel-plan","worktree-bind","reservation-request","reservation-acquire","reservation-release","reservation-renew","reservation-recover","child-start","child-renew","child-release","child-recover","child-result","integration-review","git-durability-observe","git-durability-reconcile","repair-apply","pause","pause-ack","resume-prepare","lease-acquire","lease-renew","lease-release","lease-recover","checkpoint","record-human-question","record-human-answer","record-knowledge","record-phase-output","record-review","render","validate"):
        x=commands.add_parser(name); x.add_argument("task_id"); x.add_argument("--expected-revision",type=int)
        if name in {"record-phase-output","record-review"}: x.add_argument("--phase",choices=("plan","implement","review"))
    engine_run = commands.add_parser("engine-run")
    engine_run.add_argument("task_id")
    engine_run.add_argument("--expected-revision", type=int)
    engine_run.add_argument("--engine-config", help="friendly project config path; defaults to .nous/config.json")
    create=commands.add_parser("create"); create.add_argument("--title"); create.add_argument("--objective"); create.add_argument("--target-repository")
    duplicate=commands.add_parser("duplicate"); duplicate.add_argument("task_id"); duplicate.add_argument("--title"); duplicate.add_argument("--objective")
    commands.add_parser("policy-evaluate")
    research = sub.add_parser("research")
    research_commands = research.add_subparsers(dest="research_command", required=True)
    fetch = research_commands.add_parser("fetch")
    fetch.add_argument("--url", required=True)
    fetch.add_argument("--method", choices=("GET", "HEAD"), default="GET")
    search = research_commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--max-results", type=int)
    evaluation = sub.add_parser("eval")
    eval_commands = evaluation.add_subparsers(dest="eval_command", required=True)
    validate = eval_commands.add_parser("validate-manifest")
    validate.add_argument("--manifest")
    validate.add_argument("--root")
    validate.add_argument("--stage", type=int)
    listed = eval_commands.add_parser("list")
    listed.add_argument("--manifest")
    listed.add_argument("--root")
    listed.add_argument("--stage", type=int)
    listed.add_argument("--release")
    listed.add_argument("--eval-id", action="append")
    execute = eval_commands.add_parser("run")
    execute.add_argument("--manifest")
    execute.add_argument("--root")
    execute.add_argument("--stage", type=int)
    execute.add_argument("--release")
    execute.add_argument("--eval-id", action="append")
    execute.add_argument("--trials", type=int)
    report = eval_commands.add_parser("report")
    report.add_argument("--manifest")
    report.add_argument("--root")
    report.add_argument("--run", dest="run_id", required=True)
    report.add_argument("--format", choices=("json", "markdown"), default="json")
    cleanup = eval_commands.add_parser("cleanup")
    cleanup.add_argument("--manifest")
    cleanup.add_argument("--root")
    cleanup.add_argument("--run", dest="run_id", required=True)
    capability_validate = eval_commands.add_parser("capability-validate")
    capability_validate.add_argument("--manifest")
    capability_validate.add_argument("--root")
    capability_list = eval_commands.add_parser("capability-list")
    capability_list.add_argument("--manifest")
    capability_list.add_argument("--root")
    capability_list.add_argument("--profile", choices=("smoke", "full", "holdout"), default="full")
    for capability_command in ("capability-run", "capability-smoke", "capability-full", "capability-holdout"):
        command_parser = eval_commands.add_parser(capability_command)
        command_parser.add_argument("--manifest")
        command_parser.add_argument("--root")
        command_parser.add_argument("--profile", choices=("smoke", "full", "holdout"), default="smoke")
        command_parser.add_argument("--observations")
        command_parser.add_argument("--trials", type=int)
        command_parser.add_argument("--baseline")
    capability_report = eval_commands.add_parser("capability-report")
    capability_report.add_argument("--manifest")
    capability_report.add_argument("--root")
    capability_report.add_argument("--run", dest="run_id", required=True)
    capability_report.add_argument("--format", choices=("json", "markdown"), default="json")
    capability_from_eval = eval_commands.add_parser("capability-from-eval")
    capability_from_eval.add_argument("--manifest")
    capability_from_eval.add_argument("--root")
    capability_from_eval.add_argument("--eval-root", required=True)
    capability_from_eval.add_argument("--eval-run", required=True)
    capability_from_eval.add_argument("--profile", choices=("smoke", "full", "holdout"), default="full")
    capability_from_eval.add_argument("--baseline")
    provider_validate = eval_commands.add_parser("capability-provider-validate")
    provider_validate.add_argument("--provider-config", required=True)
    provider_validate.add_argument("--manifest")
    provider_validate.add_argument("--root")
    provider_run = eval_commands.add_parser("capability-provider-run")
    provider_run.add_argument("--provider-config", required=True)
    provider_run.add_argument("--manifest")
    provider_run.add_argument("--root")
    provider_run.add_argument("--profile", choices=("smoke", "full", "holdout"), default="smoke")
    provider_run.add_argument("--baseline")
    provider_run.add_argument("--allow-host-provider", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args=parser().parse_args(argv)
    request_id: str | None = None
    ledger: RequestLedger | None = None
    material: dict[str, Any] | None = None
    request_started = False
    command = getattr(args, "command", None) or (f"eval {args.eval_command}" if getattr(args, "top", None) == "eval" else None)
    try:
        payload: dict[str, Any] | None = None
        if args.top == "research":
            policy_value: dict[str, Any] = {}
            config_path = args.config
            if config_path:
                config = load_engine_config(Path(config_path).expanduser().resolve())
                policy_value = config.research
            policy_value.update(_input(args.input) if args.input else {})
            policy = ResearchPolicy.from_mapping(policy_value)
            adapter = PublicWebResearchAdapter(policy=policy)
            if args.research_command == "fetch":
                result = adapter.fetch(args.url, method=args.method)
                return _response("research fetch", result.as_dict(include_content=True))
            if args.research_command == "search":
                return _response("research search", adapter.search(args.query, max_results=args.max_results))
            raise ValueError(f"unsupported research command: {args.research_command}")
        if args.top == "taskctl":
            payload = _input(args.input)
            if args.command == "engine-run":
                payload = _prepare_engine_payload(args, payload)
            if args.command == "engine-run" and args.input is None:
                generated_request_id = f"engine-cli-{uuid.uuid4().hex}"
                payload.update(
                    {
                        "contract_version": 1,
                        "request_id": generated_request_id,
                        "actor": {"type": "human", "run_id": generated_request_id, "actor_id": "local-cli"},
                        "loop_id": "root",
                    }
                )
            mutating = args.command in _MUTATING_COMMANDS
            request_id = _request_id(args, payload, required=mutating)
            if mutating:
                _validate_request_envelope(payload, mutating=True)
                _validate_actor(payload)
                home = resolve_nous_home(args.nous_home, getattr(args, "engine_config", None) or args.config)
                if not args.dry_run:
                    material = _request_material(args, payload, request_id or "")
                    ledger = RequestLedger(home)
                    cached = ledger.begin(request_id or "", command or "taskctl", material)
                    if cached is not None:
                        print(json.dumps(cached, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
                        return 0 if cached.get("ok") else int(cached.get("error", {}).get("exit_code", 2))
                    request_started = True
        result=run(args, payload, request_id=request_id)
        if request_id is not None:
            result["request_id"] = request_id
            if ledger is not None and material is not None:
                ledger.complete(request_id, command or "taskctl", material, result)
        print(json.dumps(result,ensure_ascii=False,indent=2 if args.pretty else None,sort_keys=True)); return 0
    except NousError as exc:
        error = {"contract_version":1,"ok":False,"command":command,"request_id":request_id,"error":{"code":exc.code,"message":str(exc),"retryable":False,"details":{},"exit_code":exc.exit_code}}
        if request_started and request_id is not None and ledger is not None and material is not None:
            ledger.complete(request_id, command or "taskctl", material, error)
        print(json.dumps(error,ensure_ascii=False),file=sys.stdout); return exc.exit_code
    except (ValueError,KeyError,json.JSONDecodeError) as exc:
        error = {"contract_version":1,"ok":False,"command":command,"request_id":request_id,"error":{"code":"validation_error","message":str(exc),"retryable":False,"details":{},"exit_code":2}}
        if request_started and request_id is not None and ledger is not None and material is not None:
            ledger.complete(request_id, command or "taskctl", material, error)
        print(json.dumps(error,ensure_ascii=False),file=sys.stdout); return 2

if __name__ == "__main__": raise SystemExit(main())
