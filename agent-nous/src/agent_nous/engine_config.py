from __future__ import annotations

"""Human-friendly configuration for the autonomous engine.

The runtime request remains the canonical transport contract.  This module
only provides a safe, reusable way to fill that request from one project
configuration file so a Human does not have to repeat long command arrays and
loop limits for every run.
"""

import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .errors import ValidationError
from .executor.validation import ValidationCommandPolicy
from .ids import validate_runtime_id
from .research import ResearchPolicy
from .subskills import SubskillRuntimeConfig

_ENGINE_KEYS = {
    "agents",
    "review",
    "validation",
    "allowed_processes",
    "min_cycles",
    "max_cycles",
    "max_steps",
    "no_progress_limit",
    "lease_seconds",
    "agent_timeout_seconds",
    "subskills",
    "subagents",
    "resources",
    "durability",
    "observability",
    "research",
}
_AGENT_KEYS = {"plan", "implement", "review"}
_REVIEW_KEYS = {"mode", "reviewers", "max_parallel", "fallback", "primary_reviewer_id", "secondary_policy"}
_REVIEWER_KEYS = {"id", "perspective", "command"}
_SUBSKILL_KEYS = {"enabled", "roots", "limit", "role", "phase_capabilities"}
_SUBAGENT_KEYS = {"required", "mode"}


@dataclass(frozen=True)
class EngineConfig:
    agent_commands: dict[str, tuple[str, ...]]
    allowed_processes: tuple[tuple[str, ...], ...] = ()
    validation_policies: tuple[ValidationCommandPolicy, ...] = ()
    review_mode: str = "single"
    reviewer_commands: tuple[tuple[str, tuple[str, ...]], ...] = ()
    reviewer_perspectives: tuple[tuple[str, str], ...] = ()
    review_max_parallel: int = 3
    review_fallback: str = "single"
    review_primary_reviewer_id: str | None = None
    review_secondary_policy: str = "parallel"
    min_cycles: int = 1
    max_cycles: int = 3
    max_steps: int = 12
    no_progress_limit: int = 2
    lease_seconds: int = 300
    agent_timeout_seconds: float = 180
    subagents_required: bool = True
    subagents_mode: str = "external"
    resources: dict[str, Any] = field(default_factory=dict)
    durability_mode: str = "local"
    observability: dict[str, Any] = field(default_factory=dict)
    research: dict[str, Any] = field(default_factory=lambda: ResearchPolicy().as_dict())
    research: dict[str, Any] = field(default_factory=lambda: ResearchPolicy().as_dict())
    research: dict[str, Any] = field(default_factory=lambda: ResearchPolicy().as_dict())
    research: dict[str, Any] = field(default_factory=lambda: ResearchPolicy().as_dict())
    research: dict[str, Any] = field(default_factory=lambda: ResearchPolicy().as_dict())
    research: dict[str, Any] = field(default_factory=lambda: ResearchPolicy().as_dict())
    research: dict[str, Any] = field(default_factory=lambda: ResearchPolicy().as_dict())
    research: dict[str, Any] = field(default_factory=lambda: ResearchPolicy().as_dict())
    research: dict[str, Any] = field(default_factory=lambda: ResearchPolicy().as_dict())
    research: dict[str, Any] = field(default_factory=lambda: ResearchPolicy().as_dict())
    subskills_enabled: bool = True
    subskill_roots: tuple[str, ...] = ()
    subskill_limit: int = 1
    subskill_role: str = "primary"
    subskill_phase_capabilities: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("plan", ("plan.acceptance",)),
        ("implement", ("implement.incremental",)),
        ("review", ("review.independence",)),
    )

    def payload(self) -> dict[str, Any]:
        policies = self.validation_policies or tuple(
            ValidationCommandPolicy(command) for command in self.allowed_processes
        )
        return {
            "agent_commands": {key: list(value) for key, value in self.agent_commands.items()},
            "allowed_processes": [list(value) for value in self.allowed_processes],
            "validation": {
                "allowed_processes": [policy.public() for policy in policies]
            },
            "review_mode": self.review_mode,
            "reviewers": [
                {"id": reviewer_id, "perspective": perspective, "command": list(dict(self.reviewer_commands)[reviewer_id])}
                for reviewer_id, perspective in self.reviewer_perspectives
            ],
            "review_max_parallel": self.review_max_parallel,
            "review_fallback": self.review_fallback,
            "review_primary_reviewer_id": self.review_primary_reviewer_id,
            "review_secondary_policy": self.review_secondary_policy,
            "min_cycles": self.min_cycles,
            "max_cycles": self.max_cycles,
            "max_steps": self.max_steps,
            "no_progress_limit": self.no_progress_limit,
            "lease_seconds": self.lease_seconds,
            "agent_timeout_seconds": self.agent_timeout_seconds,
            "subagents": {
                "required": self.subagents_required,
                "mode": self.subagents_mode,
            },
            "resources": dict(self.resources),
            "durability": {"mode": self.durability_mode},
            "observability": dict(self.observability),
            "research": dict(self.research),
            "research": dict(self.research),
            "research": dict(self.research),
            "research": dict(self.research),
            "research": dict(self.research),
            "research": dict(self.research),
            "research": dict(self.research),
            "research": dict(self.research),
            "research": dict(self.research),
            "research": dict(self.research),
            "subskills": {
                "enabled": self.subskills_enabled,
                "roots": list(self.subskill_roots),
                "limit": self.subskill_limit,
                "role": self.subskill_role,
                "phase_capabilities": {
                    phase: list(capabilities)
                    for phase, capabilities in self.subskill_phase_capabilities
                },
            },
        }


def parse_subskill_runtime_config(
    raw: Any,
    *,
    base: Path,
    allow_relative_roots: bool,
    label: str = "subskills",
) -> tuple[bool, SubskillRuntimeConfig]:
    """Parse the subskill section identically for config files and CLI input."""
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ValidationError(f"{label} must be an object")
    unknown = sorted(set(raw) - _SUBSKILL_KEYS)
    if unknown:
        raise ValidationError(f"unknown {label} keys: {', '.join(unknown)}")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValidationError(f"{label}.enabled must be boolean")
    raw_roots = raw.get("roots", [])
    if not isinstance(raw_roots, list) or any(not isinstance(item, str) or not item.strip() for item in raw_roots):
        raise ValidationError(f"{label}.roots must be a list of nonempty paths")
    roots: list[Path] = []
    for item in raw_roots:
        candidate = Path(item).expanduser()
        if not candidate.is_absolute() and not allow_relative_roots:
            raise ValidationError(f"{label}.roots must contain absolute paths in direct input")
        roots.append((base / candidate).resolve() if not candidate.is_absolute() else candidate.resolve())
    limit = _integer(raw.get("limit", 1), f"{label}.limit", minimum=1, maximum=3)
    role = raw.get("role", "primary")
    if not isinstance(role, str) or role not in {"primary", "advisor", "validator"}:
        raise ValidationError(f"{label}.role must be primary, advisor, or validator")
    capabilities = dict(SubskillRuntimeConfig().phase_capabilities)
    raw_capabilities = raw.get("phase_capabilities", {})
    if not isinstance(raw_capabilities, Mapping):
        raise ValidationError(f"{label}.phase_capabilities must be an object")
    unknown_phases = sorted(set(raw_capabilities) - {"plan", "implement", "review"})
    if unknown_phases:
        raise ValidationError(f"unknown {label}.phase_capabilities phases: {', '.join(unknown_phases)}")
    for phase, values in raw_capabilities.items():
        if not isinstance(values, list) or any(not isinstance(item, str) or not item.strip() for item in values):
            raise ValidationError(f"{label}.phase_capabilities.{phase} must be a list of nonempty strings")
        if len(values) != len(set(values)):
            raise ValidationError(f"{label}.phase_capabilities.{phase} must not contain duplicates")
        capabilities[phase] = tuple(values)
    return enabled, SubskillRuntimeConfig(
        roots=tuple(roots),
        limit=limit,
        role=role,
        phase_capabilities=tuple(sorted(capabilities.items())),
    )


def discover_config_path(explicit: str | None = None, *, cwd: Path | None = None) -> Path | None:
    """Find the one project/user config used by the friendly CLI path."""
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise ValidationError(f"nous config not found: {path}")
        return path
    current = (cwd or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / ".nous" / "config.json"
        if candidate.is_file():
            return candidate
        candidate = directory / "nous.config.json"
        if candidate.is_file():
            return candidate
    user_config = Path.home() / ".config" / "nous" / "config.json"
    return user_config if user_config.is_file() else None


def load_engine_config(path: Path | None) -> EngineConfig:
    if path is None:
        raise ValidationError(
            "engine config is not found; create .nous/config.json or pass --engine-config"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"invalid nous config: {path}") from exc
    if not isinstance(raw, Mapping):
        raise ValidationError("nous config must be a JSON object")
    section = raw.get("engine", raw)
    if not isinstance(section, Mapping):
        raise ValidationError("nous config.engine must be an object")
    if "engine" in raw:
        unknown_root = sorted(set(raw) - {"nous_home", "engine"})
        if unknown_root:
            raise ValidationError(f"unknown nous config keys: {', '.join(unknown_root)}")
    unknown = sorted(set(section) - _ENGINE_KEYS)
    if unknown:
        raise ValidationError(f"unknown engine config keys: {', '.join(unknown)}")

    raw_agents = section.get("agents")
    if not isinstance(raw_agents, Mapping) or set(raw_agents) != _AGENT_KEYS:
        raise ValidationError("engine.agents must define exactly plan, implement, and review")
    commands = {phase: _command(raw_agents[phase], f"engine.agents.{phase}") for phase in sorted(_AGENT_KEYS)}

    review_mode = "single"
    reviewer_commands: tuple[tuple[str, tuple[str, ...]], ...] = ()
    reviewer_perspectives: tuple[tuple[str, str], ...] = ()
    review_max_parallel = 3
    review_fallback = "single"
    review_primary_reviewer_id: str | None = None
    review_secondary_policy = "parallel"
    raw_review = section.get("review")
    if raw_review is not None:
        if not isinstance(raw_review, Mapping):
            raise ValidationError("engine.review must be an object")
        unknown_review = sorted(set(raw_review) - _REVIEW_KEYS)
        if unknown_review:
            raise ValidationError(f"unknown engine.review keys: {', '.join(unknown_review)}")
        review_mode = raw_review.get("mode", "single")
        if review_mode not in {"single", "cross"}:
            raise ValidationError("engine.review.mode must be single or cross")
        review_fallback = raw_review.get("fallback", "single")
        if review_fallback not in {"single", "human", "none"}:
            raise ValidationError("engine.review.fallback must be single, human, or none")
        review_primary_reviewer_id = raw_review.get("primary_reviewer_id")
        if review_primary_reviewer_id is not None:
            if not isinstance(review_primary_reviewer_id, str):
                raise ValidationError("engine.review.primary_reviewer_id must be a string or null")
            validate_runtime_id(review_primary_reviewer_id, "engine.review.primary_reviewer_id")
        review_secondary_policy = raw_review.get("secondary_policy", "parallel")
        if review_secondary_policy not in {"parallel", "after_primary_success"}:
            raise ValidationError("engine.review.secondary_policy must be parallel or after_primary_success")
        raw_reviewers = raw_review.get("reviewers", [])
        if not isinstance(raw_reviewers, list):
            raise ValidationError("engine.review.reviewers must be a list")
        parsed_commands: list[tuple[str, tuple[str, ...]]] = []
        parsed_perspectives: list[tuple[str, str]] = []
        seen_ids: set[str] = set()
        for index, raw_reviewer in enumerate(raw_reviewers):
            if not isinstance(raw_reviewer, Mapping) or set(raw_reviewer) != _REVIEWER_KEYS:
                raise ValidationError(f"engine.review.reviewers[{index}] must contain exactly id, perspective, and command")
            reviewer_id = raw_reviewer.get("id")
            perspective = raw_reviewer.get("perspective")
            if not isinstance(reviewer_id, str):
                raise ValidationError(f"engine.review.reviewers[{index}].id must be a safe nonempty identifier")
            try:
                validate_runtime_id(reviewer_id, f"engine.review.reviewers[{index}].id")
            except ValidationError as exc:
                raise ValidationError(str(exc)) from exc
            if reviewer_id in seen_ids:
                raise ValidationError(f"duplicate cross-reviewer id: {reviewer_id}")
            if not isinstance(perspective, str) or not perspective.strip():
                raise ValidationError(f"engine.review.reviewers[{index}].perspective must be a nonempty string")
            seen_ids.add(reviewer_id)
            parsed_commands.append((reviewer_id, _command(raw_reviewer["command"], f"engine.review.reviewers[{index}].command")))
            parsed_perspectives.append((reviewer_id, perspective.strip()))
        if review_mode == "cross" and len(parsed_commands) < 2:
            raise ValidationError("cross-review requires at least two reviewers")
        if review_mode == "cross" and len(parsed_commands) > 2:
            raise ValidationError("cross-review supports at most two reviewers")
        if review_mode == "single" and parsed_commands:
            raise ValidationError("engine.review.reviewers require engine.review.mode=cross")
        if review_primary_reviewer_id is not None and review_primary_reviewer_id not in seen_ids:
            raise ValidationError("engine.review.primary_reviewer_id must name a configured reviewer")
        raw_max_parallel = raw_review.get("max_parallel", 3)
        review_max_parallel = _integer(raw_max_parallel, "engine.review.max_parallel", minimum=1, maximum=8)
        if parsed_commands:
            review_max_parallel = min(review_max_parallel, len(parsed_commands))
        reviewer_commands = tuple(parsed_commands)
        reviewer_perspectives = tuple(parsed_perspectives)

    raw_research = section.get("research")
    research_policy = ResearchPolicy.from_mapping(raw_research)

    raw_validation = section.get("validation")
    if raw_validation is not None:
        if not isinstance(raw_validation, Mapping) or set(raw_validation) != {"allowed_processes"}:
            raise ValidationError("engine.validation must contain only allowed_processes")
        if "allowed_processes" in section:
            raise ValidationError("configure validation.allowed_processes or allowed_processes, not both")
        raw_allowed = raw_validation["allowed_processes"]
    else:
        if "allowed_processes" not in section:
            raise ValidationError("engine.validation.allowed_processes is required")
        raw_allowed = section["allowed_processes"]
    if not isinstance(raw_allowed, list) or not raw_allowed:
        raise ValidationError("engine.validation.allowed_processes must be a nonempty list")
    policies: list[ValidationCommandPolicy] = []
    for index, item in enumerate(raw_allowed):
        label = f"engine.validation.allowed_processes[{index}]"
        if isinstance(item, Mapping):
            # Normalize human-friendly command strings before the typed
            # policy parser validates the runtime-only settings.
            normalized = dict(item)
            normalized["command"] = list(_command(item.get("command"), f"{label}.command"))
            policies.append(ValidationCommandPolicy.from_value(normalized, label=label))
        else:
            policies.append(ValidationCommandPolicy.from_value(item, label=label))
    if not policies:
        raise ValidationError("engine.validation.allowed_processes must be a nonempty list")
    allowed = tuple(policy.command for policy in policies)

    max_cycles = _integer(section.get("max_cycles", 3), "engine.max_cycles", minimum=1, maximum=100)
    min_cycles = _integer(section.get("min_cycles", 1), "engine.min_cycles", minimum=1, maximum=100)
    if min_cycles > max_cycles:
        raise ValidationError("engine.min_cycles must not exceed engine.max_cycles")
    max_steps = _integer(section.get("max_steps", max(12, min_cycles * 3)), "engine.max_steps", minimum=1, maximum=1000)
    if min_cycles > 1 and max_steps < min_cycles * 3:
        raise ValidationError("engine.max_steps must allow at least three phases per min_cycles")
    lease_seconds = _integer(section.get("lease_seconds", 300), "engine.lease_seconds", minimum=30, maximum=86400)
    no_progress_limit = _integer(section.get("no_progress_limit", 2), "engine.no_progress_limit", minimum=2, maximum=3)
    timeout = section.get("agent_timeout_seconds", 180)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise ValidationError("engine.agent_timeout_seconds must be a positive number")
    if timeout >= lease_seconds - 5:
        raise ValidationError("engine.agent_timeout_seconds must leave at least 5 seconds before lease expiry")
    config_base = path.parent.resolve() if path is not None else Path.cwd().resolve()
    raw_resources = section.get("resources", {})
    if not isinstance(raw_resources, Mapping):
        raise ValidationError("engine.resources must be an object")
    resource_keys = {"max_concurrent_agents", "max_output_bytes", "max_tool_calls", "max_wall_seconds"}
    unknown_resources = sorted(set(raw_resources) - resource_keys)
    if unknown_resources:
        raise ValidationError(f"unknown engine.resources keys: {', '.join(unknown_resources)}")
    from .resources import ExecutionResourcePolicy
    resource_values = {
        "max_concurrent_agents": raw_resources.get("max_concurrent_agents", 3),
        "max_output_bytes": raw_resources.get("max_output_bytes", 4 * 1024 * 1024),
        "max_tool_calls": raw_resources.get("max_tool_calls", 100),
        "max_wall_seconds": raw_resources.get("max_wall_seconds", 300.0),
    }
    try:
        ExecutionResourcePolicy(**resource_values).validate()
    except (TypeError, ValidationError) as exc:
        raise ValidationError(f"invalid engine.resources: {exc}") from exc
    raw_durability = section.get("durability", {})
    if not isinstance(raw_durability, Mapping) or set(raw_durability) - {"mode"}:
        raise ValidationError("engine.durability must contain only mode")
    durability_mode = raw_durability.get("mode", "local")
    from .durability import DurabilityProfile
    try:
        DurabilityProfile(mode=durability_mode).validate_home(config_base)
    except (TypeError, ValidationError) as exc:
        raise ValidationError(f"invalid engine.durability: {exc}") from exc
    raw_observability = section.get("observability", {})
    if not isinstance(raw_observability, Mapping):
        raise ValidationError("engine.observability must be an object")
    if set(raw_observability) - {"trace_export_path", "max_event_bytes"}:
        raise ValidationError("engine.observability supports only trace_export_path and max_event_bytes")
    trace_path = raw_observability.get("trace_export_path")
    if trace_path is not None and (not isinstance(trace_path, str) or not trace_path.strip()):
        raise ValidationError("engine.observability.trace_export_path must be a nonempty string or null")
    max_event_bytes = raw_observability.get("max_event_bytes", 256 * 1024)
    if not isinstance(max_event_bytes, int) or isinstance(max_event_bytes, bool) or not 1024 <= max_event_bytes <= 4 * 1024 * 1024:
        raise ValidationError("engine.observability.max_event_bytes must be between 1024 and 4194304")
    observability = {"trace_export_path": trace_path, "max_event_bytes": max_event_bytes}

    raw_subagents = section.get("subagents", {})
    if not isinstance(raw_subagents, Mapping):
        raise ValidationError("engine.subagents must be an object")
    unknown_subagents = sorted(set(raw_subagents) - _SUBAGENT_KEYS)
    if unknown_subagents:
        raise ValidationError(f"unknown engine.subagents keys: {', '.join(unknown_subagents)}")
    subagents_required = raw_subagents.get("required", True)
    if not isinstance(subagents_required, bool):
        raise ValidationError("engine.subagents.required must be boolean")
    subagents_mode = raw_subagents.get("mode", "external")
    if subagents_mode != "external":
        raise ValidationError("engine.subagents.mode must be external")
    config_base = path.parent.resolve() if path is not None else Path.cwd().resolve()
    subskills_enabled, subskill_config = parse_subskill_runtime_config(
        section.get("subskills"),
        base=config_base,
        allow_relative_roots=True,
        label="engine.subskills",
    )
    return EngineConfig(
        agent_commands=commands,
        allowed_processes=allowed,
        validation_policies=tuple(policies),
        review_mode=review_mode,
        reviewer_commands=reviewer_commands,
        reviewer_perspectives=reviewer_perspectives,
        review_max_parallel=review_max_parallel,
        review_fallback=review_fallback,
        review_primary_reviewer_id=review_primary_reviewer_id,
        review_secondary_policy=review_secondary_policy,
        min_cycles=min_cycles,
        max_cycles=max_cycles,
        max_steps=max_steps,
        no_progress_limit=no_progress_limit,
        lease_seconds=lease_seconds,
        agent_timeout_seconds=float(timeout),
        subagents_required=subagents_required,
        subagents_mode=subagents_mode,
        resources=resource_values,
        durability_mode=durability_mode,
        observability=observability,
        research=research_policy.as_dict(),
        subskills_enabled=subskills_enabled,
        subskill_roots=tuple(str(root) for root in subskill_config.roots),
        subskill_limit=subskill_config.limit,
        subskill_role=subskill_config.role,
        subskill_phase_capabilities=subskill_config.phase_capabilities,
    )


def _command(value: Any, label: str) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        value = value.get("command")
    if isinstance(value, str):
        try:
            parts = shlex.split(value)
        except ValueError as exc:
            raise ValidationError(f"{label} has invalid command quoting") from exc
    elif isinstance(value, list):
        parts = value
    else:
        raise ValidationError(f"{label} must be a command string or string list")
    if not parts or any(not isinstance(part, str) or not part for part in parts):
        raise ValidationError(f"{label} must contain nonempty command arguments")
    return tuple(parts)


def _integer(value: Any, label: str, *, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValidationError(f"{label} must be an integer between {minimum} and {maximum}")
    return value
