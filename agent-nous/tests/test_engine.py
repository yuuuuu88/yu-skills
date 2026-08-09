from __future__ import annotations

import time
import shutil
import json
import threading
from pathlib import Path

import pytest

from agent_nous.engine import AgentContext, AgentOutput, AutonomousEngine, CallableAgentBackend, CrossReviewBackend, JsonCommandAgentBackend
from agent_nous.domain import PolicyGateway
from agent_nous.errors import RecoveryRequiredError, ValidationError
from agent_nous.taskctl import TaskStore
from agent_nous.subskills import SubskillRuntime


def _repository(root: Path) -> Path:
    repository = root / "repository"
    (repository / "tests").mkdir(parents=True)
    (repository / "tests" / "test_result.py").write_text(
        "import unittest\nfrom pathlib import Path\n\nclass ResultTest(unittest.TestCase):\n    def test_result(self):\n        self.assertEqual(Path('result.txt').read_text(), 'ok\\n')\n\n",
        encoding="utf-8",
    )
    return repository


def _backend() -> CallableAgentBackend:
    def plan(context: AgentContext) -> AgentOutput:
        return AgentOutput(
            "plan the result file",
            {"planned_content": "ok\n", "acceptance_notes": ["result.txt must satisfy the repository test"]},
        )

    def implement(context: AgentContext) -> AgentOutput:
        plan_output = context.active_artifacts["active_plan_ref"]["outputs"]
        return AgentOutput(
            "implement the planned result",
            {
                "action_proposals": [
                    {
                        "action_id": f"write-result-c{context.cycle}",
                        "kind": "filesystem.write",
                        "targets": ["result.txt"],
                        "content": plan_output["planned_content"],
                        "expected_effect": "write the planned result file",
                    },
                    {
                        "action_id": f"write-marker-c{context.cycle}",
                        "kind": "filesystem.write",
                        "targets": ["engine-marker.txt"],
                        "content": f"cycle {context.cycle}\n",
                        "expected_effect": "write an execution marker",
                    },
                ],
                "changed_artifacts": ["result.txt"],
            },
        )

    def review(context: AgentContext) -> AgentOutput:
        return AgentOutput(
            "run the repository validation",
            {
                "validation_proposals": [
                    {
                        "action_id": f"validate-result-c{context.cycle}",
                        "kind": "process.run",
                        "targets": ["tests"],
                        "command": ["/usr/bin/python3", "-m", "unittest", "discover", "-s", "tests", "-v"],
                        "criterion_id": "AC-001",
                    }
                ]
            },
            "completed",
        )

    return CallableAgentBackend({"plan": plan, "implement": implement, "review": review})


def test_engine_starts_all_three_agents_and_completes_managed_repository_change(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store = TaskStore(tmp_path / "nous-home")
    task_id = store.create(title="engine e2e", objective="create a tested result file", target_repository=str(repository), actor="test")["task_id"]
    engine = AutonomousEngine(store, _backend(), allowed_processes=(("/usr/bin/python3", "-m", "unittest"),), require_external_subagents=False)

    result = engine.run(task_id)

    assert result["outcome"] == "completed"
    assert result["state"]["lifecycle_status"] == "completed"
    assert (repository / "result.txt").read_text(encoding="utf-8") == "ok\n"
    assert (repository / "engine-marker.txt").read_text(encoding="utf-8") == "cycle 1\n"
    _, state, _ = store.read_task(task_id)
    assert state["latest_review_decision_ref"] is not None
    review = (store._path(task_id) / "cycles/0001/03-review-result.json").read_text(encoding="utf-8")
    assert "agent_execution" in review


def test_engine_honors_minimum_nous_loop_cycles(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store = TaskStore(tmp_path / "nous-home")
    task_id = store.create(title="minimum loop", objective="run the configured minimum cycles", target_repository=str(repository), actor="test")["task_id"]
    engine = AutonomousEngine(
        store,
        _backend(),
        allowed_processes=(("/usr/bin/python3", "-m", "unittest"),),
        min_cycles=3,
        max_cycles=3,
        require_external_subagents=False,
    )

    result = engine.run(task_id)

    assert result["outcome"] == "completed"
    assert result["state"]["cycle"] == 3
    assert result["diagnostics"]["minimum_cycles"] == 3
    assert result["diagnostics"]["phase_invocations"] == 9
    for cycle in (1, 2):
        review = json.loads((store._path(task_id) / f"cycles/{cycle:04d}/03-review-result.json").read_text(encoding="utf-8"))
        assert review["decision"] == "needs_plan"
        assert review["outputs"]["nous_loop_control"] == {
            "minimum_cycles": 3,
            "current_cycle": cycle,
            "forced_replan": True,
        }
    final_review = json.loads((store._path(task_id) / "cycles/0003/03-review-decision.json").read_text(encoding="utf-8"))
    assert final_review["decision"] == "completed"


def test_engine_pins_subskills_per_phase_and_reselects_on_next_cycle(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store = TaskStore(tmp_path / "nous-home")
    task_id = store.create(title="subskill e2e", objective="create a tested result file", target_repository=str(repository), actor="test")["task_id"]
    engine = AutonomousEngine(
        store,
        _backend(),
        allowed_processes=(("/usr/bin/python3", "-m", "unittest"),),
        subskill_runtime=SubskillRuntime(), require_external_subagents=False
    )

    result = engine.run(task_id)

    assert result["outcome"] == "completed"
    for phase in ("plan", "implement", "review"):
        record = store.read_subskill_selection(task_id, 1, phase)
        assert record is not None
        assert record["pinned"] is True
        assert record["selected"]
    _, state, _ = store.read_task(task_id)
    # A completed run has only one cycle, so all three choices are exactly
    # stable pins.  A replan test below confirms cycle boundaries.


def test_engine_replans_after_failed_review_validation(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store = TaskStore(tmp_path / "nous-home")
    task_id = store.create(title="engine replan", objective="repair the tested result file", target_repository=str(repository), actor="test")["task_id"]

    def plan(context: AgentContext) -> AgentOutput:
        return AgentOutput("plan cycle", {"planned_content": "bad\n" if context.cycle == 1 else "ok\n"})

    def implement(context: AgentContext) -> AgentOutput:
        content = context.active_artifacts["active_plan_ref"]["outputs"]["planned_content"]
        return AgentOutput("write cycle", {"action_proposals": [{"action_id": f"write-c{context.cycle}", "kind": "filesystem.write", "targets": ["result.txt"], "content": content}]})

    def review(context: AgentContext) -> AgentOutput:
        return AgentOutput("validate cycle", {"validation_proposals": [{"action_id": f"test-c{context.cycle}", "kind": "process.run", "targets": ["tests"], "command": ["/usr/bin/python3", "-m", "unittest", "discover", "-s", "tests"], "criterion_id": "AC-001"}]}, "completed")

    engine = AutonomousEngine(
        store,
        CallableAgentBackend({"plan": plan, "implement": implement, "review": review}),
        allowed_processes=(("/usr/bin/python3", "-m", "unittest"),),
        max_cycles=3,
        subskill_runtime=SubskillRuntime(), require_external_subagents=False
    )
    result = engine.run(task_id)

    assert result["outcome"] == "completed"
    assert result["state"]["cycle"] == 2
    cycle_one = store.read_subskill_selection(task_id, 1, "plan")
    cycle_two = store.read_subskill_selection(task_id, 2, "plan")
    assert cycle_one is not None and cycle_two is not None
    assert cycle_one["selection_id"] != cycle_two["selection_id"]
    assert (repository / "result.txt").read_text(encoding="utf-8") == "ok\n"
    assert (store._path(task_id) / "cycles/0001/03-review-result.json").is_file()
    assert (store._path(task_id) / "cycles/0002/03-review-decision.json").is_file()


def test_engine_recovers_an_expired_lease_before_starting_agents(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store = TaskStore(tmp_path / "nous-home")
    task_id = store.create(title="engine recovery", objective="recover and finish", target_repository=str(repository), actor="test")["task_id"]
    lease = store.acquire_lease(task_id, 1, "stale-agent", seconds=1)
    time.sleep(2.0)

    result = AutonomousEngine(store, _backend(), allowed_processes=(("/usr/bin/python3", "-m", "unittest"),), require_external_subagents=False).run(task_id)

    assert result["outcome"] == "completed"
    events = (store._path(task_id) / "events.jsonl").read_text(encoding="utf-8")
    assert "lease_expired" in events
    assert "task_resumed" in events
    assert lease["execution_status"] == "running"


def test_review_agent_can_stop_for_human_input_without_fake_validation(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store = TaskStore(tmp_path / "nous-home")
    task_id = store.create(title="engine human gate", objective="ask before choosing a policy", target_repository=str(repository), actor="test")["task_id"]

    def plan(context: AgentContext) -> AgentOutput:
        return AgentOutput("plan requires a policy choice", {"planned_content": "ok\n"})

    def implement(context: AgentContext) -> AgentOutput:
        return AgentOutput("implementation is waiting for policy", {"action_proposals": []})

    def review(context: AgentContext) -> AgentOutput:
        return AgentOutput(
            "policy choice is required",
            {
                "human_question": {
                    "question": "どの運用方針を採用しますか？",
                    "why_now": "自動推測すると成果物の意味が変わるためです。",
                    "options": [
                        {"option_id": "recommended", "label": "推奨案", "impact": "安全側で進めます。"},
                        {"option_id": "alternative", "label": "代替案", "impact": "別の運用になります。"},
                    ],
                    "recommended_option_id": "recommended",
                    "free_text_allowed": False,
                    "blocking_scope": "root_task",
                    "blocked_refs": [],
                    "affected_loop_ids": ["root"],
                    "resume_target": {"resume_phase": "plan", "resume_transition": "answer_received", "blocked_action_ref": None, "blocked_artifact_ref": None},
                    "approval_request_ref": None,
                }
            },
            "needs_human_input",
        )

    result = AutonomousEngine(
        store,
        CallableAgentBackend({"plan": plan, "implement": implement, "review": review}),
        max_cycles=2, require_external_subagents=False
    ).run(task_id)

    assert result["outcome"] == "waiting_human"
    assert result["state"]["lifecycle_status"] == "waiting_human"
    assert result["state"]["phase"] == "review"


def test_implement_failure_cannot_be_overridden_by_a_completed_review(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store = TaskStore(tmp_path / "nous-home")
    task_id = store.create(title="engine fail closed", objective="do not complete after a rejected effect", target_repository=str(repository), actor="test")["task_id"]

    def plan(context: AgentContext) -> AgentOutput:
        return AgentOutput("plan", {})

    def implement(context: AgentContext) -> AgentOutput:
        return AgentOutput(
            "propose an unsupported effect",
            {"action_proposals": [{"action_id": "unsupported-effect", "kind": "network", "targets": ["."], "expected_effect": "must be rejected"}]},
        )

    def review(context: AgentContext) -> AgentOutput:
        return AgentOutput(
            "the validation itself passes, but implementation did not",
            {"validation_proposals": [{"action_id": "validate-after-failure", "kind": "process.run", "targets": ["tests"], "command": ["/usr/bin/python3", "-m", "unittest", "discover", "-s", "tests"], "criterion_id": "AC-001"}]},
            "completed",
        )

    result = AutonomousEngine(
        store,
        CallableAgentBackend({"plan": plan, "implement": implement, "review": review}),
        allowed_processes=(("/usr/bin/python3", "-m", "unittest"),),
        max_cycles=1, require_external_subagents=False
    ).run(task_id)

    assert result["outcome"] == "max_cycles_reached"
    assert result["state"]["lifecycle_status"] == "waiting_human"
    assert result["state"]["latest_review_decision_ref"] is None


def test_engine_reuses_completed_actions_after_a_phase_record_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _repository(tmp_path)
    store = TaskStore(tmp_path / "nous-home")
    task_id = store.create(title="engine action recovery", objective="reuse a durable action", target_repository=str(repository), actor="test")["task_id"]
    engine = AutonomousEngine(store, _backend(), allowed_processes=(("/usr/bin/python3", "-m", "unittest"),), require_external_subagents=False)
    original = store.record_phase
    failed_once = False

    def crash_after_effect(*args: object, **kwargs: object) -> object:
        nonlocal failed_once
        phase = args[4] if len(args) > 4 else kwargs.get("phase")
        if phase == "implement" and not failed_once:
            failed_once = True
            raise RuntimeError("simulated record crash")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "record_phase", crash_after_effect)
    with pytest.raises(RuntimeError, match="simulated record crash"):
        engine.run(task_id)
    monkeypatch.setattr(store, "record_phase", original)

    result = engine.run(task_id)

    assert result["outcome"] == "completed"
    assert (repository / "result.txt").read_text(encoding="utf-8") == "ok\n"


def test_cross_review_runs_independent_reviewers_and_deduplicates_validation(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store = TaskStore(tmp_path / "nous-home")
    task_id = store.create(title="cross review", objective="complete after independent reviews", target_repository=str(repository), actor="test")["task_id"]

    def reviewer(context: AgentContext) -> AgentOutput:
        assert context.reviewer_id in {"correctness", "security"}
        return AgentOutput(
            f"{context.reviewer_id} review",
            {
                "validation_proposals": [
                    {
                        "action_id": f"validate-result-{context.reviewer_id}",
                        "kind": "process.run",
                        "targets": ["tests"],
                        "command": ["/usr/bin/python3", "-m", "unittest", "discover", "-s", "tests"],
                        "criterion_id": "AC-001",
                    }
                ]
            },
            "completed",
        )

    reviewers = {
        "correctness": CallableAgentBackend({"review": reviewer}),
        "security": CallableAgentBackend({"review": reviewer}),
    }
    engine = AutonomousEngine(
        store,
        CrossReviewBackend(_backend(), reviewers, perspectives={"correctness": "behavior", "security": "security"}),
        allowed_processes=(("/usr/bin/python3", "-m", "unittest"),), require_external_subagents=False
    )

    result = engine.run(task_id)

    assert result["outcome"] == "completed"
    assert result["diagnostics"]["phase_invocations"] == 3
    assert result["diagnostics"]["subagent_invocations"] == 4
    review = json.loads((store._path(task_id) / "cycles/0001/03-review-result.json").read_text(encoding="utf-8"))
    assert review["outputs"]["cross_review"]["mode"] == "parallel_independent"
    assert {item["reviewer_id"] for item in review["outputs"]["cross_review"]["reviewers"]} == {"correctness", "security"}
    assert len(review["outputs"]["validation_evidence"]) == 1
    assert (store._path(task_id) / "cycles/0001/reviewers/correctness.json").is_file()
    assert (store._path(task_id) / "cycles/0001/reviewers/security.json").is_file()


def test_cross_reviewers_receive_independent_subskill_contexts(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    barrier = threading.Barrier(2)
    observed: dict[str, str] = {}

    def reviewer(context: AgentContext) -> AgentOutput:
        assert context.subskill_selection is not None
        guidance = context.subskill_selection["guidance"][0]["guidance"]
        observed[str(context.reviewer_id)] = guidance
        barrier.wait(timeout=2)
        context.subskill_selection["guidance"][0]["guidance"] = f"mutated by {context.reviewer_id}"
        return AgentOutput("independent review", {"validation_proposals": []}, "completed")

    context = AgentContext(
        "task-cross-subskills",
        "review",
        1,
        {"acceptance_criteria": []},
        {"revision": 1},
        {},
        {},
        str(repository),
        subskill_selection={"guidance": [{"guidance": "original guidance"}]},
    )
    backend = CrossReviewBackend(
        _backend(),
        {"correctness": CallableAgentBackend({"review": reviewer}), "security": CallableAgentBackend({"review": reviewer})},
        perspectives={"correctness": "behavior", "security": "security"},
    )

    output = backend.launch_review(context)

    assert output.metadata["execution_mode"] == "parallel_independent_review"
    assert observed == {"correctness": "original guidance", "security": "original guidance"}
    assert context.subskill_selection["guidance"][0]["guidance"] == "original guidance"


def test_cross_review_provider_failure_degrades_to_one_reviewer(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store = TaskStore(tmp_path / "nous-home")
    task_id = store.create(title="cross failure", objective="stop safely when one reviewer fails", target_repository=str(repository), actor="test")["task_id"]

    def good_reviewer(context: AgentContext) -> AgentOutput:
        return AgentOutput(
            "good review",
            {
                "validation_proposals": [
                    {
                        "action_id": "validate-result",
                        "kind": "process.run",
                        "targets": ["tests"],
                        "command": ["/usr/bin/python3", "-m", "unittest", "discover", "-s", "tests"],
                        "criterion_id": "AC-001",
                    }
                ]
            },
            "completed",
        )

    def failed_reviewer(context: AgentContext) -> AgentOutput:
        time.sleep(2.0)
        raise RuntimeError("reviewer unavailable")

    review_backend = CrossReviewBackend(
        _backend(),
        {"correctness": CallableAgentBackend({"review": good_reviewer}), "security": CallableAgentBackend({"review": failed_reviewer})},
        timeout_seconds=0.01,
    )
    review_context = AgentContext(
        task_id,
        "review",
        1,
        {"acceptance_criteria": []},
        {"revision": 1},
        {},
        {},
        str(repository),
    )
    started = time.monotonic()
    degraded = review_backend.launch_review(review_context)
    assert time.monotonic() - started < 1.0
    assert degraded.metadata["fallback_used"] is True

    engine = AutonomousEngine(
        store,
        review_backend,
        allowed_processes=(("/usr/bin/python3", "-m", "unittest"),),
        max_cycles=1, require_external_subagents=False
    )

    result = engine.run(task_id)

    assert result["outcome"] == "completed"
    assert result["state"]["lifecycle_status"] == "completed"
    review = json.loads((store._path(task_id) / "cycles/0001/03-review-result.json").read_text(encoding="utf-8"))
    assert review["outputs"]["cross_review"]["mode"] == "degraded_single"
    assert review["outputs"]["cross_review"]["fallback_used"] is True


def test_cross_review_fallback_normalizes_incomplete_successful_reviewer(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    def incomplete(context: AgentContext) -> AgentOutput:
        return AgentOutput("claimed success without validation", {}, "completed")

    def unavailable(context: AgentContext) -> AgentOutput:
        raise RuntimeError("unavailable")

    backend = CrossReviewBackend(
        _backend(),
        {"primary": CallableAgentBackend({"review": incomplete}), "secondary": CallableAgentBackend({"review": unavailable})},
        timeout_seconds=30,
    )
    context = AgentContext("task-fallback-normalize", "review", 1, {"acceptance_criteria": []}, {"revision": 1}, {}, {}, str(repository))

    output = backend.launch_review(context)

    assert output.decision == "needs_plan"


def test_cross_review_honors_explicit_blocking_finding_even_when_minor(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store = TaskStore(tmp_path / "nous-home")
    task_id = store.create(title="cross finding", objective="do not ignore explicit review blocker", target_repository=str(repository), actor="test")["task_id"]

    def review(context: AgentContext) -> AgentOutput:
        return AgentOutput(
            "minor issue is release-blocking by disposition",
            {
                "validation_proposals": [],
                "findings": [
                    {
                        "finding_id": f"finding-{context.reviewer_id}",
                        "severity": "minor",
                        "disposition": "blocking",
                        "summary": "the release checklist is incomplete",
                    }
                ],
            },
            "needs_plan",
        )

    engine = AutonomousEngine(
        store,
        CrossReviewBackend(
            _backend(),
            {"correctness": CallableAgentBackend({"review": review}), "security": CallableAgentBackend({"review": review})},
            timeout_seconds=30,
        ),
        max_cycles=1, require_external_subagents=False
    )

    result = engine.run(task_id)

    assert result["outcome"] == "max_cycles_reached"
    review = json.loads((store._path(task_id) / "cycles/0001/03-review-result.json").read_text(encoding="utf-8"))
    assert "the release checklist is incomplete" in review["outputs"]["blocking_issues"]


def test_review_proposals_cannot_write_files(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store = TaskStore(tmp_path / "nous-home")
    task_id = store.create(title="review boundary", objective="keep review read-only", target_repository=str(repository), actor="test")["task_id"]

    def review(context: AgentContext) -> AgentOutput:
        return AgentOutput(
            "attempted write from review",
            {
                "validation_proposals": [
                    {
                        "action_id": "review-write",
                        "kind": "filesystem.write",
                        "targets": ["must-not-exist.txt"],
                        "content": "must not be written",
                        "criterion_id": "AC-001",
                    }
                ]
            },
            "completed",
        )

    engine = AutonomousEngine(
        store,
        CallableAgentBackend({"plan": lambda context: AgentOutput("plan", {}), "implement": lambda context: AgentOutput("implement", {"action_proposals": []}), "review": review}),
        max_cycles=1, require_external_subagents=False
    )

    result = engine.run(task_id)

    assert result["outcome"] == "max_cycles_reached"
    assert not (repository / "must-not-exist.txt").exists()


def test_cross_review_resumes_from_durable_completed_reviewer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _repository(tmp_path)
    store = TaskStore(tmp_path / "nous-home")
    task_id = store.create(title="cross recovery", objective="resume one reviewer without replaying it", target_repository=str(repository), actor="test")["task_id"]
    calls = {"primary": 0, "secondary": 0}

    def reviewer(context: AgentContext) -> AgentOutput:
        assert context.reviewer_id is not None
        calls[context.reviewer_id] += 1
        if context.reviewer_id == "secondary":
            time.sleep(0.05)
        return AgentOutput(
            f"{context.reviewer_id} review",
            {
                "validation_proposals": [
                    {
                        "action_id": "validate-result",
                        "kind": "process.run",
                        "targets": ["tests"],
                        "command": ["/usr/bin/python3", "-m", "unittest", "discover", "-s", "tests"],
                        "criterion_id": "AC-001",
                    }
                ]
            },
            "completed",
        )

    backend = CrossReviewBackend(
        _backend(),
        {"primary": CallableAgentBackend({"review": reviewer}), "secondary": CallableAgentBackend({"review": reviewer})},
    )
    engine = AutonomousEngine(store, backend, allowed_processes=(("/usr/bin/python3", "-m", "unittest"),), require_external_subagents=False)
    original = store.record_review_observation
    crashed = False

    def crash_after_first(*args: object, **kwargs: object) -> object:
        nonlocal crashed
        result = original(*args, **kwargs)
        if not crashed:
            crashed = True
            raise RuntimeError("simulated process loss after first reviewer")
        return result

    monkeypatch.setattr(store, "record_review_observation", crash_after_first)
    with pytest.raises(RecoveryRequiredError, match="reviewer observation persistence failed"):
        engine.run(task_id)
    monkeypatch.setattr(store, "record_review_observation", original)

    result = engine.run(task_id)

    assert result["outcome"] == "completed"
    assert calls["primary"] == 1
    assert calls["secondary"] == 2


def test_empty_process_allowlist_prefix_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PolicyGateway(allowed_processes=((),))


def test_engine_requires_external_subagents_by_default(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="external JsonCommandAgentBackend"):
        AutonomousEngine(TaskStore(tmp_path / "nous-home"), _backend())


def test_engine_rejects_backend_that_only_impersonates_external_agent(tmp_path: Path) -> None:
    class ImpersonatingBackend(JsonCommandAgentBackend):
        pass

    fake = object.__new__(ImpersonatingBackend)
    with pytest.raises(ValidationError, match="external JsonCommandAgentBackend"):
        AutonomousEngine(TaskStore(tmp_path / "nous-home"), fake)


def test_noop_implementation_is_reported_and_cannot_complete_change_task(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store = TaskStore(tmp_path / "nous-home")
    task_id = store.create(title="no-op diagnostic", objective="create the result file", target_repository=str(repository), actor="test")["task_id"]

    backend = CallableAgentBackend({
        "plan": lambda context: AgentOutput("plan", {"planned_content": "ok\n", "acceptance_notes": ["result.txt exists"]}),
        "implement": lambda context: AgentOutput("nothing changed", {}),
        "review": lambda context: AgentOutput(
            "false proof",
            {"validation_proposals": [{
                "action_id": "fake-validation",
                "kind": "process.run",
                "targets": ["tests"],
                "command": ["/usr/bin/true"],
                "criterion_id": "AC-001",
            }]},
            "completed",
        ),
    })
    engine = AutonomousEngine(
        store,
        backend,
        allowed_processes=(("/usr/bin/true",),),
        max_cycles=1,
        require_external_subagents=False,
    )

    result = engine.run(task_id)

    assert result["outcome"] == "max_cycles_reached"
    assert "agent_returned_typed_noop_or_missing_evidence" in result["diagnostics"]["root_causes"]
    assert result["diagnostics"]["phase_sequence"][:3] == [
        "root/cycle-1/plan", "root/cycle-1/implement", "root/cycle-1/review"
    ]
    assert not (repository / "result.txt").exists()


def test_diagnostics_are_reconstructed_from_phase_artifacts(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store = TaskStore(tmp_path / "nous-home")
    task_id = store.create(title="durable diagnostics", objective="create a tested result file", target_repository=str(repository), actor="test")["task_id"]
    first = AutonomousEngine(
        store,
        _backend(),
        allowed_processes=(("/usr/bin/python3", "-m", "unittest"),),
        require_external_subagents=False,
    ).run(task_id)
    assert first["diagnostics"]["phase_invocations"] == 3

    second = AutonomousEngine(
        store,
        _backend(),
        allowed_processes=(("/usr/bin/python3", "-m", "unittest"),),
        require_external_subagents=False,
    ).run(task_id)

    assert second["outcome"] == "completed"
    assert second["diagnostics"]["phase_invocations"] == 3
    assert second["diagnostics"]["trace"][0]["phase"] == "plan"
    assert second["diagnostics"]["trace"][0]["duration_ms"] is not None


@pytest.mark.skipif(shutil.which("codex") is None, reason="Codex sandbox unavailable")
def test_json_command_backend_starts_an_isolated_external_agent(tmp_path: Path) -> None:
    script = tmp_path / "agent.py"
    script.write_text(
        "#!/usr/bin/python3\nimport json, os\ncontext=json.loads(open(os.environ['NOUS_AGENT_CONTEXT_FILE']).read())\nprint(json.dumps({'summary': context['phase'] + ' agent ran', 'outputs': {'no_managed_effects': True}}))\n",
        encoding="utf-8",
    )
    script.chmod(0o700)
    repository = _repository(tmp_path)
    context = AgentContext("task-1", "plan", 1, {"acceptance_criteria": []}, {"phase": "plan"}, {}, {}, str(repository))
    backend = JsonCommandAgentBackend({"plan": (str(script),)}, runtime_root=tmp_path / "runtime")

    output = backend.launch("plan", context)

    assert output.summary == "plan agent ran"
    assert output.metadata["execution_mode"] == "codex_sandbox_subprocess"
    assert output.metadata["process_returncode"] == 0
