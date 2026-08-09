from __future__ import annotations

"""Capability and execution-quality evaluation contracts for Nous.

This module deliberately grades a canonical evidence snapshot, not an Agent's
self-reported score.  The Eval Runner is responsible for rebuilding the
observation from durable state, events, actions, selections, Knowledge, and
diff evidence before calling these pure aggregation functions.
"""

import hashlib
import json
import os
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from jsonschema import Draft202012Validator

from ..domain import secret_summary
from ..errors import ValidationError
from ..io import digest_json

CAPABILITY_DIMENSIONS = (
    "outcome",
    "planning",
    "recovery",
    "research",
    "subagent",
    "subskill",
    "review",
    "efficiency",
)
DEFAULT_WEIGHTS = {
    "outcome": 30,
    "planning": 15,
    "recovery": 15,
    "research": 15,
    "subagent": 10,
    "subskill": 5,
    "review": 5,
    "efficiency": 5,
}


@dataclass(frozen=True)
class CapabilityManifest:
    path: Path
    value: dict[str, Any]

    @property
    def digest(self) -> str:
        return digest_json(self.value)

    @property
    def tasks(self) -> tuple[dict[str, Any], ...]:
        return tuple(self.value["tasks"])

    def task(self, task_id: str) -> dict[str, Any]:
        for item in self.tasks:
            if item["task_id"] == task_id:
                return item
        raise ValidationError(f"unknown capability task: {task_id}")

    def select_profile(self, profile: str) -> tuple[dict[str, Any], ...]:
        profiles = self.value.get("profiles", {})
        if not isinstance(profiles, Mapping) or profile not in profiles:
            raise ValidationError(f"unknown capability profile: {profile}")
        return tuple(self.task(task_id) for task_id in profiles[profile])


def _default_manifest_path() -> Path:
    return Path(__file__).with_name("capability_manifest.json")


def load_capability_manifest(path: Path | None = None) -> CapabilityManifest:
    manifest_path = (path or _default_manifest_path()).resolve()
    schema_path = manifest_path.with_name("capability_manifest.schema.json")
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot load capability manifest: {exc}") from exc
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda item: list(item.path))
    if errors:
        first = errors[0]
        location = ".".join(str(item) for item in first.absolute_path)
        raise ValidationError(f"capability manifest invalid{f' at {location}' if location else ''}: {first.message}")
    identifiers = [item["task_id"] for item in value["tasks"]]
    if len(identifiers) != len(set(identifiers)):
        raise ValidationError("capability manifest repeats task_id")
    expected_count = int(value.get("expected_task_count", len(identifiers)))
    if len(identifiers) != expected_count:
        raise ValidationError(f"capability manifest expected {expected_count} tasks, found {len(identifiers)}")
    weights = value["dimensions"]["weights"]
    if sum(int(weights[name]) for name in CAPABILITY_DIMENSIONS) != 100:
        raise ValidationError("capability dimension weights must sum to 100")
    return CapabilityManifest(manifest_path, value)


def _score(value: Any, *, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError("capability score must be numeric")
    return round(max(0.0, min(100.0, float(value))), 3)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"{label} must be an object")
    return value


def _phase_prefix(actual: Any, required: list[str], exact: bool) -> bool:
    if not isinstance(actual, list) or any(not isinstance(item, str) for item in actual):
        return False
    return actual == required if exact else actual[: len(required)] == required


def _weighted_score(task: Mapping[str, Any], scores: Mapping[str, Any]) -> tuple[float, dict[str, float]]:
    profile = _mapping(task.get("scoring_profile"), "scoring_profile")
    raw_weights = profile.get("weights", DEFAULT_WEIGHTS)
    weights = {name: int(raw_weights.get(name, 0)) for name in CAPABILITY_DIMENSIONS}
    active = {name: weight for name, weight in weights.items() if weight > 0}
    if not active:
        raise ValidationError("scoring_profile must enable at least one dimension")
    normalized = {name: _score(scores.get(name), default=0.0) for name in active}
    total = sum(normalized[name] * weight for name, weight in active.items()) / sum(active.values())
    return round(total, 3), normalized


def grade_capability_trial(task: Mapping[str, Any], observed: Mapping[str, Any]) -> dict[str, Any]:
    """Grade a parent-built canonical observation for one capability trial.

    ``observed`` must be assembled by the parent Eval Runner.  Agent output is
    not a trusted source for this function: callers should pass only durable
    state/event/action/selection/Knowledge/diff evidence after independent
    capture and redaction.
    """
    agent_policy = _mapping(task.get("agent_policy"), "agent_policy")
    skill_contract = _mapping(task.get("skill_contract"), "skill_contract")
    research_contract = _mapping(task.get("research_contract"), "research_contract")
    loop_contract = _mapping(task.get("loop_contract"), "loop_contract")
    trace = _mapping(observed.get("trace"), "observed.trace")
    hidden = _mapping(observed.get("hidden_tests"), "observed.hidden_tests")
    safety = _mapping(observed.get("safety"), "observed.safety")
    selected = set(item for item in observed.get("selected_capabilities", []) if isinstance(item, str))
    required_capabilities = {item for item in skill_contract.get("must_cover", []) if isinstance(item, str)}
    required_phases = [item for item in loop_contract.get("required_phase_sequence", []) if isinstance(item, str)]
    actual_phases = trace.get("phase_sequence", [])
    required_external = bool(agent_policy.get("external_required", False))
    required_external_phases = {item for item in agent_policy.get("required_external_phases", []) if isinstance(item, str)}
    actual_external_phases = {item for item in trace.get("external_subagent_phases", []) if isinstance(item, str)}
    research = _mapping(observed.get("research"), "observed.research")
    recovery = _mapping(observed.get("recovery"), "observed.recovery")
    parallel = _mapping(observed.get("parallel"), "observed.parallel")
    reviewers = _mapping(observed.get("review"), "observed.review")

    hard_gates = {
        "hidden_tests": hidden.get("passed") is True,
        "safety": safety.get("passed") is True,
        "loop_integrity": _phase_prefix(actual_phases, required_phases, bool(loop_contract.get("exact_phase_sequence", False))),
        "required_subskills": required_capabilities.issubset(selected),
    }
    if required_external:
        hard_gates["required_external_subagents"] = required_external_phases.issubset(actual_external_phases)
    if bool(research_contract.get("claim_evidence_required", False)):
        hard_gates["research_evidence"] = (
            float(research.get("claim_evidence_coverage", 0.0)) >= 1.0
            and int(research.get("primary_source_count", 0)) >= int(research_contract.get("min_primary_sources", 0))
        )
    if bool(loop_contract.get("requires_replan", False)):
        hard_gates["replan"] = int(observed.get("replans", 0)) >= 1
    if bool(loop_contract.get("requires_recovery", False)):
        hard_gates["recovery"] = recovery.get("success") is True
    if bool(loop_contract.get("requires_parallel", False)):
        hard_gates["parallel"] = int(parallel.get("peak_children", 0)) >= int(loop_contract.get("min_parallel_children", 1))
    min_reviewers = int(agent_policy.get("min_reviewers", 0))
    if min_reviewers:
        hard_gates["independent_review"] = int(reviewers.get("reviewer_count", 0)) >= min_reviewers and reviewers.get("independent") is True

    raw_scores = observed.get("scores", {})
    scores = _mapping(raw_scores, "observed.scores")
    derived = {
        "outcome": 100.0 if hard_gates["hidden_tests"] else 0.0,
        "planning": scores.get("planning", 0.0),
        "recovery": 100.0 if hard_gates.get("recovery", True) else 0.0,
        "research": 100.0 if hard_gates.get("research_evidence", True) else 0.0,
        "subagent": 100.0 if hard_gates.get("required_external_subagents", True) else 0.0,
        "subskill": 100.0 if hard_gates["required_subskills"] else 0.0,
        "review": 100.0 if hard_gates.get("independent_review", True) else 0.0,
        "efficiency": scores.get("efficiency", 0.0),
    }
    for name in CAPABILITY_DIMENSIONS:
        if name in scores:
            derived[name] = scores[name]
    score, normalized_scores = _weighted_score(task, derived)
    failures = [name for name, passed in hard_gates.items() if not passed]
    return {
        "task_id": task["task_id"],
        "verdict": "passed" if not failures else "failed",
        "score": score if not failures else 0.0,
        "dimension_scores": normalized_scores,
        "hard_gates": hard_gates,
        "failure_reasons": failures,
    }


def aggregate_trials(
    task: Mapping[str, Any],
    trial_results: Iterable[Mapping[str, Any]],
    *,
    expected_trials: int = 3,
    minimum_successes: int = 2,
) -> dict[str, Any]:
    results = [dict(item) for item in trial_results]
    if len(results) != expected_trials:
        raise ValidationError(f"expected exactly {expected_trials} capability trials, found {len(results)}")
    if not 1 <= minimum_successes <= expected_trials:
        raise ValidationError("minimum_successes must be within the trial count")
    scores = [float(item.get("score", 0.0)) for item in results]
    passed = sum(1 for item in results if item.get("verdict") == "passed")
    dimensions = {
        name: round(statistics.fmean(float(item.get("dimension_scores", {}).get(name, 0.0)) for item in results), 3)
        for name in CAPABILITY_DIMENSIONS
    }
    return {
        "task_id": task["task_id"],
        "trial_count": len(results),
        "passed_trials": passed,
        "reliability": round(passed / len(results), 3),
        "minimum_successes": minimum_successes,
        "verdict": "passed" if passed >= minimum_successes else "failed",
        "mean_score": round(statistics.fmean(scores), 3),
        "minimum_score": round(min(scores), 3),
        "dimension_scores": dimensions,
        "failure_reasons": sorted({reason for item in results for reason in item.get("failure_reasons", [])}),
    }


def aggregate_suite(task_results: Iterable[Mapping[str, Any]], *, suite_id: str = "nous-capability") -> dict[str, Any]:
    tasks = [dict(item) for item in task_results]
    if not tasks:
        raise ValidationError("capability suite requires at least one task result")
    task_scores = {str(item["task_id"]): round(float(item.get("mean_score", 0.0)), 3) for item in tasks}
    dimensions = {
        name: round(statistics.fmean(float(item.get("dimension_scores", {}).get(name, 0.0)) for item in tasks), 3)
        for name in CAPABILITY_DIMENSIONS
    }
    passed = sum(1 for item in tasks if item.get("verdict") == "passed")
    return {
        "schema_version": 1,
        "suite_id": suite_id,
        "task_count": len(tasks),
        "passed_tasks": passed,
        "task_reliability": round(passed / len(tasks), 3),
        "ncs": round(statistics.fmean(task_scores.values()), 3),
        "dimension_scores": dimensions,
        "task_scores": task_scores,
        "hard_gate_failures": sorted({reason for item in tasks for reason in item.get("failure_reasons", [])}),
    }


def compare_reports(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    baseline_dimensions = _mapping(baseline.get("dimension_scores"), "baseline.dimension_scores")
    candidate_dimensions = _mapping(candidate.get("dimension_scores"), "candidate.dimension_scores")
    dimension_deltas = {
        name: round(float(candidate_dimensions.get(name, 0.0)) - float(baseline_dimensions.get(name, 0.0)), 3)
        for name in CAPABILITY_DIMENSIONS
    }
    baseline_tasks = _mapping(baseline.get("task_scores"), "baseline.task_scores")
    candidate_tasks = _mapping(candidate.get("task_scores"), "candidate.task_scores")
    common = sorted(set(baseline_tasks) & set(candidate_tasks))
    task_deltas = {task_id: round(float(candidate_tasks[task_id]) - float(baseline_tasks[task_id]), 3) for task_id in common}
    return {
        "schema_version": 1,
        "baseline_suite_id": baseline.get("suite_id"),
        "candidate_suite_id": candidate.get("suite_id"),
        "ncs_delta": round(float(candidate.get("ncs", 0.0)) - float(baseline.get("ncs", 0.0)), 3),
        "reliability_delta": round(float(candidate.get("task_reliability", 0.0)) - float(baseline.get("task_reliability", 0.0)), 3),
        "dimension_deltas": dimension_deltas,
        "task_deltas": task_deltas,
        "regressions": sorted(name for name, delta in dimension_deltas.items() if delta < 0),
    }

_OBSERVATION_REQUIRED_KEYS = (
    "task_id",
    "provenance",
    "trace",
    "hidden_tests",
    "safety",
    "selected_capabilities",
    "research",
    "recovery",
    "parallel",
    "review",
    "scores",
)
_OBSERVATION_ALLOWED_KEYS = frozenset(_OBSERVATION_REQUIRED_KEYS) | {"replans", "metrics"}

_FORBIDDEN_OBSERVATION_KEYS = frozenset({
    "prompt", "raw_prompt", "raw_output", "stdout", "stderr", "secret", "credential",
    "absolute_path", "repository_path", "task_root", "worker_output",
})


def _validate_portable_observation(value: Any, *, key_path: str = "observation") -> None:
    if isinstance(value, str):
        if os.path.isabs(value) or value.startswith("~/"):
            raise ValidationError(f"{key_path} must not contain an absolute path")
        if secret_summary(value):
            raise ValidationError(f"{key_path} contains a secret-like value")
        return
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).lower() in _FORBIDDEN_OBSERVATION_KEYS:
                raise ValidationError(f"{key_path}.{key} is not portable evidence")
            _validate_portable_observation(nested, key_path=f"{key_path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _validate_portable_observation(nested, key_path=f"{key_path}[{index}]")
        return
    if value is None or isinstance(value, (bool, int, float)):
        return
    raise ValidationError(f"{key_path} contains an unsupported value")


def validate_capability_observation(observed: Mapping[str, Any], *, task_id: str | None = None) -> None:
    """Validate the portable, parent-owned observation boundary.

    This rejects worker self-reports, raw output, secrets, and machine-specific
    paths before the deterministic grader sees the observation.
    """
    if not isinstance(observed, Mapping):
        raise ValidationError("capability observation must be an object")
    unknown = sorted(set(observed) - _OBSERVATION_ALLOWED_KEYS)
    if unknown:
        raise ValidationError(f"capability observation contains unknown fields: {', '.join(str(item) for item in unknown)}")
    missing = [key for key in _OBSERVATION_REQUIRED_KEYS if key not in observed]
    if missing:
        raise ValidationError(f"capability observation missing: {', '.join(missing)}")
    if task_id is not None and observed.get("task_id") != task_id:
        raise ValidationError("capability observation task_id mismatch")
    provenance = observed.get("provenance")
    if not isinstance(provenance, Mapping) or provenance.get("source") != "eval_runner_parent_capture":
        raise ValidationError("capability observation requires parent EvalRunner provenance")
    evidence_digest = provenance.get("evidence_digest")
    if not isinstance(evidence_digest, str) or len(evidence_digest) != 64:
        raise ValidationError("capability observation requires a sha256 evidence digest")
    try:
        int(evidence_digest, 16)
    except ValueError as exc:
        raise ValidationError("capability observation evidence digest is invalid") from exc
    _validate_portable_observation(observed)


def capability_observation_from_eval_trial(
    task_id: str, trial: Mapping[str, Any], parent_evidence: Mapping[str, Any]
) -> dict[str, Any]:
    """Convert EvalRunner parent capture into a grader input.

    ``parent_evidence`` is the only accepted source.  Agent-provided claims
    and worker stdout are intentionally not accepted by this adapter.
    """
    if not isinstance(trial, Mapping) or trial.get("outcome") not in {"passed", "failed", "flake", "infrastructure_error"}:
        raise ValidationError("EvalRunner trial outcome is invalid")
    if not isinstance(parent_evidence, Mapping) or parent_evidence.get("source") != "eval_runner_parent_capture":
        raise ValidationError("capability observation source is not canonical parent capture")
    raw = parent_evidence.get("capability_observation")
    if not isinstance(raw, Mapping):
        raise ValidationError("parent capture has no capability observation")
    digest = parent_evidence.get("evidence_digest")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValidationError("parent capture evidence digest is missing")
    try:
        int(digest, 16)
    except ValueError as exc:
        raise ValidationError("parent capture evidence digest is invalid") from exc
    observation = dict(raw)
    observation["task_id"] = task_id
    observation["provenance"] = {
        "source": "eval_runner_parent_capture",
        "evidence_digest": digest,
        "trial_outcome": trial.get("outcome"),
    }
    validate_capability_observation(observation, task_id=task_id)
    return observation



def _capture_json(evidence_root: Path, name: str) -> tuple[Path, dict[str, Any]]:
    if not isinstance(evidence_root, Path):
        raise ValidationError("EvalRunner evidence root must be a Path")
    path = evidence_root / name
    if path.is_symlink() or not path.is_file():
        raise ValidationError(f"EvalRunner parent evidence is missing: {name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"EvalRunner parent evidence is invalid: {name}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"EvalRunner parent evidence must be an object: {name}")
    return path, value


def _capture_phase_signals(canonical: Mapping[str, Any]) -> dict[str, Any]:
    signals = canonical.get("capability_signals", {})
    if not isinstance(signals, Mapping):
        return {}
    return dict(signals)


def capability_observation_from_eval_runner_capture(
    evidence_root: Path,
    *,
    task_id: str,
    eval_id: str | None = None,
    trial_outcome: str | None = None,
) -> dict[str, Any]:
    """Convert an actual EvalRunner parent capture into a portable observation.

    Only evaluator-owned canonical.json, outcome.json, environment.json, and
    parent-attestation.json are read.  Worker stdout/stderr and paths never
    become observation fields.  The adapter intentionally leaves capability
    signals absent when the current EvalRunner has not captured them; a task
    then fails its corresponding hard gate instead of inventing evidence.
    """
    canonical_path, canonical = _capture_json(evidence_root, "canonical.json")
    _, attestation = _capture_json(evidence_root, "parent-attestation.json")
    _, outcome = _capture_json(evidence_root, "outcome.json")
    _, environment = _capture_json(evidence_root, "environment.json")
    if attestation.get("schema_version") != 1:
        raise ValidationError("EvalRunner parent attestation schema is invalid")
    canonical_digest = attestation.get("canonical_digest")
    if not isinstance(canonical_digest, str) or len(canonical_digest) != 64:
        raise ValidationError("EvalRunner parent attestation digest is missing")
    if digest_json(canonical) != canonical_digest:
        raise ValidationError("EvalRunner canonical evidence digest mismatch")
    evidence_digests = attestation.get("evidence_digests")
    if not isinstance(evidence_digests, Mapping) or evidence_digests.get("canonical.json") != hashlib.sha256(canonical_path.read_bytes()).hexdigest():
        raise ValidationError("EvalRunner evidence digest binding mismatch")
    trial_id = outcome.get("trial_id")
    if trial_id != environment.get("trial_id") or attestation.get("trial_id") != trial_id:
        raise ValidationError("EvalRunner parent evidence trial binding mismatch")
    captured_eval_id = outcome.get("eval_id") or environment.get("eval_id")
    if eval_id is not None and captured_eval_id != eval_id:
        raise ValidationError("EvalRunner parent evidence eval_id mismatch")
    observed_outcome = trial_outcome or outcome.get("outcome")
    if observed_outcome not in {"passed", "failed", "flake", "infrastructure_error"}:
        raise ValidationError("EvalRunner parent evidence outcome is invalid")
    trace = canonical.get("trace", {}) if isinstance(canonical.get("trace"), Mapping) else {}
    process = canonical.get("process", {}) if isinstance(canonical.get("process"), Mapping) else {}
    diff = canonical.get("diff", {}) if isinstance(canonical.get("diff"), Mapping) else {}
    validation_failures = canonical.get("validation_failures", [])
    if not isinstance(validation_failures, list):
        raise ValidationError("EvalRunner validation failures are invalid")
    signals = _capture_phase_signals(canonical)
    phase_sequence = signals.get("phase_sequence", [])
    external_phases = signals.get("external_subagent_phases", [])
    selected_capabilities = signals.get("selected_capabilities", [])
    research = signals.get("research", {})
    recovery = signals.get("recovery", {})
    parallel = signals.get("parallel", {})
    review = signals.get("review", {})
    scores = signals.get("scores", {})
    metrics = signals.get("metrics", {})
    for name, value in (("phase_sequence", phase_sequence), ("external_subagent_phases", external_phases), ("selected_capabilities", selected_capabilities)):
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValidationError(f"EvalRunner capability signal {name} is invalid")
    for name, value in (("research", research), ("recovery", recovery), ("parallel", parallel), ("review", review), ("scores", scores), ("metrics", metrics)):
        if not isinstance(value, Mapping):
            raise ValidationError(f"EvalRunner capability signal {name} is invalid")
    safety_passed = (
        trace.get("sandbox_enforced") is True
        and not validation_failures
        and not diff.get("out_of_trial_paths")
        and process.get("stdout_secret_like") is not True
        and process.get("stderr_secret_like") is not True
    )
    observation = {
        "task_id": task_id,
        "trace": {"phase_sequence": phase_sequence, "external_subagent_phases": external_phases},
        "hidden_tests": {"passed": observed_outcome == "passed"},
        "safety": {"passed": safety_passed},
        "selected_capabilities": selected_capabilities,
        "research": dict(research),
        "recovery": dict(recovery),
        "parallel": dict(parallel),
        "review": dict(review),
        "scores": dict(scores),
        "metrics": dict(metrics),
    }
    if isinstance(signals.get("replans"), int):
        observation["replans"] = signals["replans"]
    parent_evidence = {
        "source": "eval_runner_parent_capture",
        "evidence_digest": canonical_digest,
        "capability_observation": observation,
    }
    return capability_observation_from_eval_trial(
        task_id,
        {"outcome": observed_outcome},
        parent_evidence,
    )


def validate_capability_observation_bundle(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise ValidationError("capability observation bundle must be an object")
    schema_path = Path(__file__).with_name("capability_observation.schema.json")
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError("capability observation schema is unavailable") from exc
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda item: list(item.path))
    if errors:
        location = ".".join(str(item) for item in errors[0].absolute_path)
        raise ValidationError(f"capability observation bundle invalid{f' at {location}' if location else ''}: {errors[0].message}")
    observations = value.get("observations", {})
    for task_id, records in observations.items():
        for record in records:
            raw = dict(record.get("capability_observation", {}))
            raw["provenance"] = {"source": "eval_runner_parent_capture", "evidence_digest": record.get("evidence_digest")}
            validate_capability_observation(raw, task_id=str(task_id))
