from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from agent_nous.engine import AgentContext, AgentOutput, AutonomousEngine, CallableAgentBackend, CrossReviewBackend
from agent_nous.taskctl import TaskStore


def _read_only_contract(loop_id: str) -> dict[str, object]:
    resource = f"repo://fixture/{loop_id}/input"
    return {
        "loop_id": loop_id,
        "objective": f"independently validate {loop_id}",
        "scope": [resource],
        "resources": [resource],
        "write_scope": [],
        "dependencies": [],
        "acceptance_criteria": [
            {"criterion_id": f"AC-{loop_id}", "statement": f"{loop_id} validation passed", "validation_method": "managed process"}
        ],
        "expected_outputs": [f"{loop_id}-result"],
        "validation_plan": ["managed read-only validation"],
        "allowed_actions": ["filesystem.read", "process.run"],
        "preferred_backend": "read_only_worker",
    }


def test_typed_plan_materializes_and_runs_child_agents_physically_in_parallel(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "fixture.txt").write_text("parallel\n", encoding="utf-8")
    (repository / "tests").mkdir()
    (repository / "tests" / "test_fixture.py").write_text(
        "import unittest\nfrom pathlib import Path\n\nclass FixtureTest(unittest.TestCase):\n    def test_fixture(self):\n        self.assertEqual(Path('fixture.txt').read_text(), 'parallel\\n')\n",
        encoding="utf-8",
    )
    store = TaskStore(tmp_path / "nous-home")
    task_id = store.create(
        title="physical parallel engine",
        objective="run independent child agents and integrate their reviewed results",
        target_repository=str(repository),
        actor="test",
    )["task_id"]

    barrier = threading.Barrier(2)
    lock = threading.Lock()
    active = 0
    max_active = 0

    def plan(context: AgentContext) -> AgentOutput:
        nonlocal active, max_active
        if context.loop_id == "root":
            return AgentOutput(
                "materialize two independent read-only workers",
                {
                    "parallelization_plan": {
                        "mode": "parallel",
                        "rationale": "the two validations have no shared write scope",
                        "max_concurrency": 2,
                        "max_depth": 1,
                        "integration_strategy": "parent integration review",
                        "integration_validation": ["accept every child validation"],
                    },
                    "child_contracts": [_read_only_contract("a"), _read_only_contract("b")],
                },
            )
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            barrier.wait(timeout=5)
            time.sleep(0.02)
        finally:
            with lock:
                active -= 1
        return AgentOutput(f"plan {context.loop_id}", {})

    def implement(context: AgentContext) -> AgentOutput:
        return AgentOutput(f"no persistent changes for {context.loop_id}", {"action_proposals": []})

    def review(context: AgentContext) -> AgentOutput:
        criterion_id = "AC-001" if context.loop_id == "root" else f"AC-{context.loop_id}"
        return AgentOutput(
            f"review {context.loop_id}",
            {
                "validation_proposals": [
                    {
                        "action_id": f"validate-{context.loop_id}",
                        "kind": "process.run",
                        "targets": ["."],
                        "command": [
                            "/usr/bin/python3",
                            "-m",
                            "unittest",
                            "discover",
                            "-s",
                            "tests",
                        ],
                        "criterion_id": criterion_id,
                    }
                ]
            },
            "completed",
        )

    phase_backend = CallableAgentBackend({"plan": plan, "implement": implement, "review": review})
    engine = AutonomousEngine(
        store,
        CrossReviewBackend(
            phase_backend,
            {
                "correctness": CallableAgentBackend({"review": review}),
                "security": CallableAgentBackend({"review": review}),
            },
            perspectives={"correctness": "behavior", "security": "boundary safety"},
            timeout_seconds=30,
        ),
        allowed_processes=(("/usr/bin/python3", "-m", "unittest"),),
        max_steps=12,
        lease_seconds=60, require_external_subagents=False
    )

    result = engine.run(task_id)
    assert result["outcome"] == "completed"
    assert result["state"]["lifecycle_status"] == "completed"
    assert result["diagnostics"]["phase_invocations"] >= 8
    assert result["diagnostics"]["subagent_invocations"] > result["diagnostics"]["phase_invocations"]
    assert max_active == 2
    _, state, _ = store.read_task(task_id)
    assert {item["status"] for item in state["child_loops"]} == {"integration_ready"}
    events = [json.loads(line) for line in (store._path(task_id) / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(event.get("type") == "child_lease_acquired" for event in events)
    assert any(event.get("type") == "integration_review_recorded" for event in events)
    reviewer_files = list((store._path(task_id) / "loops").glob("*/cycles/*/reviewers/*.json"))
    assert len(reviewer_files) >= 4


def _parallel_backend(*, mode: str = "parallel", failing_integration: bool = False, failing_implement: bool = False, human_integration: bool = False) -> CallableAgentBackend:
    def plan(context: AgentContext) -> AgentOutput:
        if context.loop_id == "root":
            return AgentOutput(
                "materialize two children",
                {
                    "parallelization_plan": {
                        "mode": mode,
                        "rationale": "independent child validation",
                        "max_concurrency": 3,
                        "max_depth": 1,
                        "integration_strategy": "parent review",
                        "integration_validation": ["all child results are accepted"],
                    },
                    "child_contracts": [_read_only_contract("a"), _read_only_contract("b")],
                },
            )
        return AgentOutput(f"plan {context.loop_id}", {})

    def implement(context: AgentContext) -> AgentOutput:
        if failing_implement and context.loop_id != "root":
            return AgentOutput(
                "propose an unsupported child effect",
                {"action_proposals": [{"action_id": f"bad-{context.loop_id}", "kind": "network", "targets": ["."]}]},
            )
        return AgentOutput(f"implement {context.loop_id}", {"action_proposals": []})

    def review(context: AgentContext) -> AgentOutput:
        criterion_id = "AC-001" if context.loop_id == "root" else f"AC-{context.loop_id}"
        if human_integration and context.loop_id == "root":
            return AgentOutput(
                "human decision is required before integration",
                {
                    "human_question": {
                        "question": "integration policy",
                        "why_now": "the integration policy is not safe to infer",
                        "options": [{"option_id": "replan", "label": "replan", "impact": "start a fresh root plan"}],
                        "recommended_option_id": "replan",
                        "free_text_allowed": False,
                        "blocking_scope": "root_task",
                        "blocked_refs": [],
                        "affected_loop_ids": ["root"],
                        "resume_target": {"resume_phase": "review", "resume_transition": "answer_received", "blocked_action_ref": None, "blocked_artifact_ref": None},
                        "approval_request_ref": None,
                    }
                },
                "needs_human_input",
            )
        command = ["/usr/bin/python3", "-m", "unittest", "discover", "-s", "missing"] if failing_integration and context.loop_id == "root" else ["/usr/bin/python3", "-m", "unittest", "discover", "-s", "tests"]
        return AgentOutput(
            f"review {context.loop_id}",
            {
                "validation_proposals": [
                    {
                        "action_id": f"check-{context.loop_id}-{context.cycle}",
                        "kind": "process.run",
                        "targets": ["."],
                        "command": command,
                        "criterion_id": criterion_id,
                    }
                ]
            },
            "completed",
        )

    return CallableAgentBackend({"plan": plan, "implement": implement, "review": review})


def _parallel_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "parallel-repository"
    (repository / "tests").mkdir(parents=True)
    (repository / "tests" / "test_smoke.py").write_text("import unittest\n\nclass Smoke(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n", encoding="utf-8")
    return repository


def test_parallel_graph_resumes_from_fresh_engine_after_worker_checkpoint(tmp_path: Path) -> None:
    repository = _parallel_repository(tmp_path)
    store = TaskStore(tmp_path / "nous-home")
    task_id = store.create(title="resume parallel", objective="resume child graph", target_repository=str(repository), actor="test")["task_id"]
    first = AutonomousEngine(
        store,
        _parallel_backend(),
        allowed_processes=(("/usr/bin/python3", "-m", "unittest"),),
        max_steps=2,
        lease_seconds=60, require_external_subagents=False
    ).run(task_id)
    assert first["outcome"] == "max_steps_reached"
    _, checkpoint, _ = store.read_task(task_id)
    assert checkpoint["active_parallelization_plan_ref"] is not None
    assert checkpoint["phase_kind"] == "integration"

    second = AutonomousEngine(
        store,
        _parallel_backend(),
        allowed_processes=(("/usr/bin/python3", "-m", "unittest"),),
        max_steps=8,
        lease_seconds=60, require_external_subagents=False
    ).run(task_id)
    assert second["outcome"] == "completed"
    assert second["diagnostics"]["phase_invocations"] >= 8
    assert second["diagnostics"]["subagent_invocations"] >= second["diagnostics"]["phase_invocations"]


def test_parallel_root_honors_minimum_nous_loop_cycles(tmp_path: Path) -> None:
    repository = _parallel_repository(tmp_path)
    store = TaskStore(tmp_path / "nous-home")
    task_id = store.create(title="minimum parallel loop", objective="run two root cycles before integration completion", target_repository=str(repository), actor="test")["task_id"]

    result = AutonomousEngine(
        store,
        _parallel_backend(),
        allowed_processes=(("/usr/bin/python3", "-m", "unittest"),),
        min_cycles=2,
        max_cycles=2,
        max_steps=12,
        lease_seconds=60,
        require_external_subagents=False,
    ).run(task_id)

    assert result["outcome"] == "completed"
    assert result["state"]["cycle"] == 2
    assert result["diagnostics"]["minimum_cycles"] == 2
    assert result["diagnostics"]["phase_invocations"] >= 15
    forced = list((store._path(task_id) / "cycles/0001").glob("03-integration-review-*.json"))
    assert len(forced) == 1
    integration = json.loads(forced[0].read_text(encoding="utf-8"))
    assert integration["decision"] == "needs_plan"
    assert integration["outputs"]["nous_loop_control"]["forced_replan"] is True


def test_parallel_materialization_intent_recovers_after_boundary_failure(tmp_path: Path, monkeypatch) -> None:
    repository = _parallel_repository(tmp_path)
    store = TaskStore(tmp_path / "nous-home")
    task_id = store.create(title="materialization recovery", objective="do not bypass typed plan", target_repository=str(repository), actor="test")["task_id"]
    original = store.materialize_parallel_plan
    failed_once = False

    def fail_once(*args, **kwargs):
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise RuntimeError("simulated process loss before child materialization")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "materialize_parallel_plan", fail_once)
    try:
        AutonomousEngine(store, _parallel_backend(), allowed_processes=(("/usr/bin/python3", "-m", "unittest"),), max_steps=8, lease_seconds=60, require_external_subagents=False).run(task_id)
    except RuntimeError as exc:
        assert "simulated process loss" in str(exc)
    else:  # pragma: no cover - the first boundary must fail
        raise AssertionError("simulated materialization failure did not interrupt the run")
    monkeypatch.setattr(store, "materialize_parallel_plan", original)
    result = AutonomousEngine(store, _parallel_backend(), allowed_processes=(("/usr/bin/python3", "-m", "unittest"),), max_steps=12, lease_seconds=60, require_external_subagents=False).run(task_id)
    assert result["outcome"] == "completed"


def test_child_retry_budget_survives_a_fresh_engine(tmp_path: Path) -> None:
    repository = _parallel_repository(tmp_path)
    store = TaskStore(tmp_path / "nous-home")
    task_id = store.create(title="durable child retry", objective="bound retries across processes", target_repository=str(repository), actor="test")["task_id"]
    first = AutonomousEngine(
        store,
        _parallel_backend(failing_implement=True),
        allowed_processes=(("/usr/bin/python3", "-m", "unittest"),),
        max_cycles=2,
        max_steps=1,
        lease_seconds=60, require_external_subagents=False
    ).run(task_id)
    assert first["outcome"] == "max_steps_reached"
    second = AutonomousEngine(
        store,
        _parallel_backend(failing_implement=True),
        allowed_processes=(("/usr/bin/python3", "-m", "unittest"),),
        max_cycles=2,
        max_steps=4,
        lease_seconds=60, require_external_subagents=False
    ).run(task_id)
    assert second["outcome"] == "child_failed"


def test_sequential_parallel_plan_forces_one_physical_child_at_a_time(tmp_path: Path) -> None:
    repository = _parallel_repository(tmp_path)
    store = TaskStore(tmp_path / "nous-home")
    task_id = store.create(title="sequential mode", objective="honor sequential mode", target_repository=str(repository), actor="test")["task_id"]
    active = 0
    maximum = 0
    lock = threading.Lock()
    backend = _parallel_backend(mode="sequential")
    original = backend.handlers["plan"]

    def measured_plan(context: AgentContext) -> AgentOutput:
        nonlocal active, maximum
        if context.loop_id != "root":
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.02)
            with lock:
                active -= 1
        return original(context)

    backend.handlers["plan"] = measured_plan
    result = AutonomousEngine(
        store,
        backend,
        allowed_processes=(("/usr/bin/python3", "-m", "unittest"),),
        max_steps=12,
        lease_seconds=60, require_external_subagents=False
    ).run(task_id)
    assert result["outcome"] == "completed"
    assert maximum == 1


def test_child_implement_failure_never_reaches_integration_ready(tmp_path: Path) -> None:
    repository = _parallel_repository(tmp_path)
    store = TaskStore(tmp_path / "nous-home")
    task_id = store.create(title="child failure", objective="stop failed child", target_repository=str(repository), actor="test")["task_id"]
    result = AutonomousEngine(
        store,
        _parallel_backend(failing_implement=True),
        allowed_processes=(("/usr/bin/python3", "-m", "unittest"),),
        max_cycles=1,
        max_steps=12,
        lease_seconds=60, require_external_subagents=False
    ).run(task_id)
    assert result["outcome"] == "child_failed"
    _, state, _ = store.read_task(task_id)
    assert all(item["status"] != "integration_ready" for item in state["child_loops"])


def test_integration_failure_is_persisted_as_a_replan_checkpoint(tmp_path: Path) -> None:
    repository = _parallel_repository(tmp_path)
    store = TaskStore(tmp_path / "nous-home")
    task_id = store.create(title="integration failure", objective="persist integration failure", target_repository=str(repository), actor="test")["task_id"]
    result = AutonomousEngine(
        store,
        _parallel_backend(failing_integration=True),
        allowed_processes=(("/usr/bin/python3", "-m", "unittest"),),
        max_steps=12,
        lease_seconds=60, require_external_subagents=False
    ).run(task_id)
    assert result["outcome"] == "needs_plan"
    _, state, _ = store.read_task(task_id)
    assert state["phase"] == "plan"
    assert state["child_loops"] == []
    assert state["active_parallelization_plan_ref"] is None
    events = [json.loads(line) for line in (store._path(task_id) / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(event.get("type") == "integration_review_recorded" for event in events)


def test_human_replan_clears_the_parallel_graph_before_fresh_plan(tmp_path: Path) -> None:
    repository = _parallel_repository(tmp_path)
    store = TaskStore(tmp_path / "nous-home")
    task_id = store.create(title="human integration replan", objective="clear stale parallel graph", target_repository=str(repository), actor="test")["task_id"]
    waiting = AutonomousEngine(
        store,
        _parallel_backend(human_integration=True),
        allowed_processes=(("/usr/bin/python3", "-m", "unittest"),),
        max_steps=12,
        lease_seconds=60, require_external_subagents=False
    ).run(task_id)
    assert waiting["outcome"] == "waiting_human"
    _, state, _ = store.read_task(task_id)
    question_id = state["waiting_question_ids"][0]
    answered = store.answer_question(
        task_id,
        state["revision"],
        "human",
        question_id,
        {
            "answer_type": "selected_option",
            "selected_option_id": "replan",
            "answer_text": None,
            "applied_scope": "root_task",
            "answer_effect": "replan",
            "resume_target": None,
        },
    )
    assert answered["phase"] == "plan"
    assert answered["child_loops"] == []
    assert answered["active_parallelization_plan_ref"] is None
