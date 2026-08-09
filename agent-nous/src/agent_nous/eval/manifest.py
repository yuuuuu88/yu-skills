from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator

from ..errors import ValidationError
from ..io import digest_json


RELEASE_STAGES = {"v0.1": 4, "v0.2": 5, "v0.3": 9, "v1.0": 10}
RELEASE_ORDER = tuple(RELEASE_STAGES)
TRACE_CAPTURE_NAMES = frozenset(
    {"actions", "approvals", "artifacts", "checkpoint", "children", "diff", "durability", "environment", "events", "knowledge", "policy", "process", "selection", "state"}
)
HANDLER_NAMES = frozenset(
    {
        "trigger_explicit_new",
        "trigger_one_off_negative",
        "pause_fresh_process_resume",
        "trial_isolation_basic",
        "trigger_implicit_managed_workflow",
        "approval_exact_red_action",
        "review_needs_plan_once",
        "managed_scope_violation",
        "unbrokerable_action_denied",
        "small_change_sequential",
        "small_change_interrupt_resume",
        "knowledge_gate",
        "parallel_isolation_integration",
        "parallel_interrupt_resume",
        "subskill_select_fallback",
        "local_bare_cross_clone_resume",
        "full_policy_surfaces",
    }
)


@dataclass(frozen=True)
class EvalManifest:
    path: Path
    value: dict[str, Any]
    fixtures: frozenset[str]

    @property
    def digest(self) -> str:
        return digest_json(self.value)

    @property
    def cases(self) -> tuple[dict[str, Any], ...]:
        return tuple(self.value["cases"])

    def select(
        self,
        *,
        stage: int | None = None,
        release: str | None = None,
        eval_ids: Iterable[str] = (),
    ) -> list[dict[str, Any]]:
        if (stage is None) == (release is None):
            raise ValidationError("select exactly one Eval stage or release")
        if stage is not None and (not isinstance(stage, int) or isinstance(stage, bool) or stage < 1 or stage > max(RELEASE_STAGES.values())):
            raise ValidationError("unknown Eval stage")
        target_stage = stage if stage is not None else RELEASE_STAGES.get(str(release))
        if target_stage is None:
            raise ValidationError("unknown Eval release")
        wanted = frozenset(eval_ids)
        known = {case["eval_id"] for case in self.cases}
        if wanted - known:
            raise ValidationError("unknown Eval ID: " + ", ".join(sorted(wanted - known)))
        result: list[dict[str, Any]] = []
        for case in self.cases:
            if wanted and case["eval_id"] not in wanted:
                continue
            runnable = int(case["runnable_from_stage"]) <= target_stage
            required = runnable and (
                (release is not None and release in case["required_for_releases"])
                or (stage is not None and any(RELEASE_STAGES[item] <= target_stage for item in case["required_for_releases"]))
            )
            result.append(
                {
                    **case,
                    "selection_status": (
                        "registered_not_runnable"
                        if not runnable
                        else "runnable_required"
                        if required
                        else "runnable_optional"
                    ),
                }
            )
        return result


def _default_manifest_path() -> Path:
    return Path(__file__).with_name("manifest.json")


def _validate_fixture_refs(value: dict[str, Any], fixtures: frozenset[str]) -> None:
    for case in value["cases"]:
        if int(case["registration_required_by_stage"]) != 1:
            raise ValidationError(f"{case['eval_id']}: every v1 case must be registered at Stage 1")
        if int(case["runnable_from_stage"]) < int(case["registration_required_by_stage"]):
            raise ValidationError(f"{case['eval_id']}: runnable stage precedes registration")
        if not case["required_for_releases"]:
            raise ValidationError(f"{case['eval_id']}: required release list is empty")
        for release in case["required_for_releases"]:
            if RELEASE_STAGES[release] < int(case["runnable_from_stage"]):
                raise ValidationError(f"{case['eval_id']}: required release precedes runnable stage")
        if case["handler"] not in HANDLER_NAMES:
            raise ValidationError(f"{case['eval_id']}: unknown Eval handler")
        trace_capture = case["trace"]["capture"]
        if any(item not in TRACE_CAPTURE_NAMES for item in trace_capture):
            raise ValidationError(f"{case['eval_id']}: unknown trace capture")
        expected_predicate = f"grader:{case['handler']}"
        if case["outcome"]["pass_condition"] != expected_predicate or case["outcome"]["pass_predicate"] != expected_predicate:
            raise ValidationError(f"{case['eval_id']}: pass condition/predicate is not bound to its grader")
        references = [
            case["task"]["request_ref"],
            *case["task"]["fixture_refs"],
            case["task"]["initial_state_ref"],
            *case["task"]["approval_refs"],
            case["trial"]["setup_ref"],
            case["trial"]["interruption_ref"],
            case["trial"]["resume_ref"],
        ]
        for reference in references:
            if reference is None:
                continue
            if not isinstance(reference, str) or not reference.startswith("fixture://"):
                raise ValidationError(f"{case['eval_id']}: fixture reference must use fixture://")
            name = reference.removeprefix("fixture://")
            if name not in fixtures:
                raise ValidationError(f"{case['eval_id']}: unknown fixture {reference}")


def load_manifest(path: Path | None = None) -> EvalManifest:
    manifest_path = (path or _default_manifest_path()).resolve()
    schema_path = manifest_path.with_name("manifest.schema.json")
    fixtures_path = manifest_path.with_name("fixtures.json")
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        fixture_value = json.loads(fixtures_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot load Eval manifest: {exc}") from exc
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda item: list(item.path))
    if errors:
        first = errors[0]
        location = ".".join(str(item) for item in first.absolute_path)
        raise ValidationError(f"Eval manifest invalid{f' at {location}' if location else ''}: {first.message}")
    names = fixture_value.get("fixtures")
    if fixture_value.get("schema_version") != 1 or not isinstance(names, list) or any(not isinstance(item, str) or not item for item in names):
        raise ValidationError("Eval fixture registry is invalid")
    fixtures = frozenset(names)
    _validate_fixture_refs(value, fixtures)
    identifiers = [case["eval_id"] for case in value["cases"]]
    if len(identifiers) != len(set(identifiers)):
        raise ValidationError("Eval manifest repeats eval_id")
    handlers = [case["handler"] for case in value["cases"]]
    if len(handlers) != len(set(handlers)):
        raise ValidationError("Eval manifest handler names must be unique")
    return EvalManifest(manifest_path, value, fixtures)
