from __future__ import annotations

from agent_nous.eval.capability import (
    CAPABILITY_DIMENSIONS,
    aggregate_suite,
    aggregate_trials,
    compare_reports,
    grade_capability_trial,
    load_capability_manifest,
)
from agent_nous.errors import ValidationError
import pytest


def _observed(*, task_id: str, passed: bool = True, external: bool = True, research: bool = True) -> dict:
    return {
        "task_id": task_id,
        "provenance": {"source": "eval_runner_parent_capture", "evidence_digest": "a" * 64},
        "trace": {
            "phase_sequence": ["plan", "implement", "review"],
            "external_subagent_phases": ["plan", "implement", "review"] if external else [],
        },
        "hidden_tests": {"passed": passed},
        "safety": {"passed": True},
        "selected_capabilities": ["plan.acceptance", "implement.incremental", "review.independence", "knowledge.source.verify", "plan.architecture", "plan.parallel-decomposition", "review.security"],
        "research": {"claim_evidence_coverage": 1.0 if research else 0.0, "primary_source_count": 1 if research else 0},
        "recovery": {"success": True},
        "parallel": {"peak_children": 2},
        "review": {"reviewer_count": 2, "independent": True},
        "scores": {"planning": 84, "efficiency": 78},
    }


def test_manifest_has_ten_capability_tasks_and_contracts() -> None:
    manifest = load_capability_manifest()
    assert len(manifest.tasks) == 10
    assert manifest.value["trial_policy"] == {"repetitions": 3, "minimum_successes": 2, "isolation": "fresh_trial_home_and_repository"}
    assert set(manifest.value["dimensions"]["weights"]) == set(CAPABILITY_DIMENSIONS)
    assert {task["family"] for task in manifest.tasks} >= {"implementation", "research", "subskill", "parallel", "recovery", "safety"}


def test_required_external_subagents_and_research_are_hard_gates() -> None:
    manifest = load_capability_manifest()
    research_task = manifest.task("T06-research-evidence")
    passed = grade_capability_trial(research_task, _observed(task_id=research_task["task_id"]))
    assert passed["verdict"] == "passed"
    missing = grade_capability_trial(research_task, _observed(task_id=research_task["task_id"], external=False, research=False))
    assert missing["verdict"] == "failed"
    assert {"required_external_subagents", "research_evidence"}.issubset(missing["failure_reasons"])


def test_replan_and_parallel_contracts_are_evidence_gated() -> None:
    manifest = load_capability_manifest()
    replan = manifest.task("T05-review-replan")
    observation = _observed(task_id=replan["task_id"])
    observation["trace"]["phase_sequence"] = ["plan", "implement", "review", "plan", "implement", "review"]
    observation["replans"] = 1
    assert grade_capability_trial(replan, observation)["verdict"] == "passed"
    parallel = manifest.task("T08-parallel-decomposition")
    observation = _observed(task_id=parallel["task_id"])
    assert grade_capability_trial(parallel, observation)["verdict"] == "passed"
    observation["parallel"]["peak_children"] = 1
    assert "parallel" in grade_capability_trial(parallel, observation)["failure_reasons"]


def test_three_trials_aggregate_reliability_and_minimum_score() -> None:
    manifest = load_capability_manifest()
    task = manifest.task("T01-basic-implementation")
    results = [grade_capability_trial(task, _observed(task_id=task["task_id"])) for _ in range(2)]
    failed = grade_capability_trial(task, _observed(task_id=task["task_id"], passed=False))
    aggregate = aggregate_trials(task, [*results, failed])
    assert aggregate["trial_count"] == 3
    assert aggregate["passed_trials"] == 2
    assert aggregate["verdict"] == "passed"
    assert aggregate["reliability"] == pytest.approx(2 / 3, rel=1e-3)
    with pytest.raises(ValidationError):
        aggregate_trials(task, results)


def test_suite_report_and_baseline_comparison_are_safe_and_dimensioned() -> None:
    manifest = load_capability_manifest()
    aggregates = []
    for task in manifest.tasks[:2]:
        graded = [grade_capability_trial(task, _observed(task_id=task["task_id"])) for _ in range(3)]
        aggregates.append(aggregate_trials(task, graded))
    current = aggregate_suite(aggregates, suite_id="candidate")
    baseline = {**current, "suite_id": "baseline", "ncs": current["ncs"] - 5, "dimension_scores": {name: max(0, value - 5) for name, value in current["dimension_scores"].items()}}
    comparison = compare_reports(baseline, current)
    assert current["task_count"] == 2
    assert set(current["dimension_scores"]) == set(CAPABILITY_DIMENSIONS)
    assert comparison["ncs_delta"] == pytest.approx(5)
    assert comparison["regressions"] == []
    assert "prompt" not in str(current).lower()
