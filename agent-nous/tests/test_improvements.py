from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent_nous.diagnostics import make_provider_diagnostic
from agent_nous.domain import PolicyGateway
from agent_nous.engine import (
    AgentContext,
    AgentOutput,
    AutonomousEngine,
    CallableAgentBackend,
    CrossReviewBackend,
    JsonCommandAgentBackend,
    _ProposalResult,
    _coerce_agent_output,
    _normalize_findings,
    _plan_granularity,
)
from agent_nous.errors import ProviderFailureError, ValidationError
from agent_nous.executor.managed import ManagedExecutor, _changed_paths, _tree_snapshot
from agent_nous.executor.validation import ValidationCommandPolicy, ValidationSpec
from agent_nous.taskctl import TaskStore


def _context(tmp_path: Path, *, phase: str = "review") -> AgentContext:
    return AgentContext(
        task_id="task-improvements",
        phase=phase,  # type: ignore[arg-type]
        cycle=1,
        definition={"acceptance_criteria": [{"criterion_id": "AC-001", "statement": "run"}]},
        state={"revision": 1},
        stable_context={},
        active_artifacts={},
        repository_root=str(tmp_path),
    )


def _managed_action(store: TaskStore, repository: Path, action_id: str = "validation-action"):
    from agent_nous.domain import ActionDescriptor

    task_id = store.list()[0]["task_id"]
    revision = store.read_task(task_id)[1]["revision"]
    return ActionDescriptor(
        action_id=action_id,
        task_id=task_id,
        kind="process.run",
        repository_root=str(repository),
        targets=("tests",),
        command=("/usr/bin/python3", "-m", "pytest"),
        expected_state_revision=revision,
    )


def test_validation_policy_redacts_environment_and_allows_cache_patterns() -> None:
    spec = ValidationSpec.from_mapping(
        {
            "cwd": "tests",
            "environment": {"PRIVATE_VALUE": "secret-value"},
            "timeout_seconds": 12,
            "allowed_side_effects": [".pytest_cache", ".pytest_cache/**", "**/__pycache__", "*.pyc"],
        }
    )

    public = spec.public()

    assert public["environment_keys"] == ["PRIVATE_VALUE"]
    assert "secret-value" not in json.dumps(public)
    assert spec.is_allowed_side_effect(".pytest_cache")
    assert spec.is_allowed_side_effect(".pytest_cache/v/cache/nodeids")
    assert spec.is_allowed_side_effect("__pycache__/module.pyc")
    with pytest.raises(ValidationError, match="too broad"):
        ValidationSpec.from_mapping({"allowed_side_effects": ["**/*"]})
    with pytest.raises(ValidationError, match="below"):
        ValidationSpec.from_mapping({"cwd": "../outside"})


def test_validation_snapshot_distinguishes_cache_and_source_changes(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    (root / "src").mkdir(parents=True)
    (root / "src" / "module.py").write_text("pass\n", encoding="utf-8")
    before = _tree_snapshot(root)
    (root / ".pytest_cache").mkdir()
    (root / ".pytest_cache" / "nodeids").write_text("cache", encoding="utf-8")
    (root / "src" / "module.py").write_text("changed\n", encoding="utf-8")
    changed = _changed_paths(before, _tree_snapshot(root))

    assert ".pytest_cache" in changed
    assert ".pytest_cache/nodeids" in changed
    assert "src/module.py" in changed


def test_managed_validation_accepts_cache_side_effects_but_rejects_source_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = tmp_path / "repository"
    (repository / "tests").mkdir(parents=True)
    store = TaskStore(tmp_path / "nous-home")
    task_id = store.create(title="validation", objective="probe", target_repository=str(repository), actor="test")["task_id"]
    store.acquire_lease(task_id, 1, "managed-executor")
    executor = ManagedExecutor(store, PolicyGateway(allowed_processes=(("/usr/bin/python3", "-m", "pytest"),)), store.home / "runtime")

    def fake_run(argv, *, writable_root, **kwargs):
        (writable_root / ".pytest_cache").mkdir()
        (writable_root / ".pytest_cache" / "nodeids").write_text("cache", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(executor.sandbox, "run", fake_run)
    result = executor.run_process(_managed_action(store, repository), validation=ValidationSpec())
    assert result.returncode == 0
    evidence_ref = json.loads((store._path(task_id) / "actions" / "validation-action.json").read_text(encoding="utf-8"))["after_evidence_ref"]
    evidence = json.loads((store._path(task_id) / evidence_ref).read_text(encoding="utf-8"))
    assert evidence["facts"]["unexpected_changed_paths"] == []
    assert evidence["facts"]["actual_changed_paths"]
    assert "runtime_write_scope_escape" not in (store._path(task_id) / "events.jsonl").read_text(encoding="utf-8")

    def writes_source(argv, *, writable_root, **kwargs):
        (writable_root / "source.py").write_text("unsafe", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(executor.sandbox, "run", writes_source)
    unsafe = executor.run_process(
        _managed_action(store, repository, "unsafe-validation"),
        validation=ValidationSpec(),
    )
    assert unsafe.returncode == 73
    unsafe_record = json.loads((store._path(task_id) / "actions" / "unsafe-validation.json").read_text(encoding="utf-8"))
    unsafe_evidence = json.loads((store._path(task_id) / unsafe_record["after_evidence_ref"]).read_text(encoding="utf-8"))
    assert unsafe_evidence["facts"]["failure_category"] == "safety_gap"


def test_validation_timeout_and_output_bounds_are_classified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = tmp_path / "repository"
    (repository / "tests").mkdir(parents=True)
    store = TaskStore(tmp_path / "nous-home")
    task_id = store.create(title="validation bounds", objective="probe", target_repository=str(repository), actor="test")["task_id"]
    store.acquire_lease(task_id, 1, "managed-executor")
    executor = ManagedExecutor(store, PolicyGateway(allowed_processes=(("/usr/bin/python3", "-m", "pytest"),)), store.home / "runtime")

    def timed_out(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 124, "", "executor timeout")

    monkeypatch.setattr(executor.sandbox, "run", timed_out)
    timeout = executor.run_process(_managed_action(store, repository, "timeout-validation"), validation=ValidationSpec())
    assert timeout.returncode == 124
    timeout_record = json.loads((store._path(store.list()[0]["task_id"]) / "actions" / "timeout-validation.json").read_text(encoding="utf-8"))
    timeout_evidence = json.loads((store._path(store.list()[0]["task_id"]) / timeout_record["after_evidence_ref"]).read_text(encoding="utf-8"))
    assert timeout_evidence["facts"]["failure_category"] == "validation_environment_failure"

    def verbose(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "x" * 2_000, "")

    monkeypatch.setattr(executor.sandbox, "run", verbose)
    bounded = executor.run_process(
        _managed_action(store, repository, "large-validation"),
        validation=ValidationSpec(max_output_bytes=1_024),
    )
    assert bounded.returncode == 75


def test_allowlist_error_names_the_missing_configuration_without_command_text() -> None:
    gateway = PolicyGateway(allowed_processes=(("python3", "-m", "pytest"),))
    from agent_nous.domain import ActionDescriptor

    action = ActionDescriptor(
        action_id="not-allowed",
        task_id="task-id",
        kind="process.run",
        repository_root="/repo",
        targets=("tests",),
        command=("dangerous", "--secret-value"),
    )
    _risk, reason = gateway.assess_risk(action)
    assert "validation.allowed_processes" in reason
    assert "dangerous" not in reason
    assert "command_sha256=" in reason


@pytest.mark.parametrize(
    ("value", "category"),
    [
        ("not-json", "invalid_envelope"),
        ({"outputs": {}}, "missing_required_field"),
        ({"summary": "ok"}, "missing_required_field"),
        ({"summary": "ok", "outputs": [], "decision": None}, "invalid_schema"),
    ],
)
def test_provider_envelope_diagnostics_are_typed(value: object, category: str) -> None:
    with pytest.raises(ProviderFailureError) as raised:
        _coerce_agent_output(value)  # type: ignore[arg-type]
    assert raised.value.category == category
    assert raised.value.details["actual_type"] in {"string", "object", "array", "null"}


def test_finding_schema_error_keeps_specific_json_path() -> None:
    findings, errors = _normalize_findings(
        [{"finding_id": "f-1", "severity": "major", "disposition": "blocking"}],
        "primary",
    )
    assert findings == []
    assert errors
    assert "$.findings[0]" in errors[0]


def test_json_provider_classifies_invalid_json_and_output_too_large(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSandbox:
        output = subprocess.CompletedProcess([], 0, "not-json", "stderr secret-looking-but-safe")

        def __init__(self, _root):
            pass

        def run(self, argv, **kwargs):
            return self.output

    monkeypatch.setattr("agent_nous.engine.CodexSandbox", FakeSandbox)
    backend = JsonCommandAgentBackend(
        {"plan": ("provider",)},
        runtime_root=tmp_path / "runtime",
        source_root=tmp_path,
        max_output_bytes=1_024,
    )
    with pytest.raises(ProviderFailureError) as invalid:
        backend.launch("plan", _context(tmp_path, phase="plan"))
    assert invalid.value.category == "invalid_json"

    FakeSandbox.output = subprocess.CompletedProcess([], 0, "x" * 2_000, "")
    with pytest.raises(ProviderFailureError) as large:
        backend.launch("plan", _context(tmp_path, phase="plan"))
    assert large.value.category == "output_too_large"


def test_provider_retry_is_exactly_once_and_receives_redacted_context(tmp_path: Path) -> None:
    calls = 0
    retry_contexts: list[dict[str, object] | None] = []

    class FlakyBackend:
        external_subagent = False

        def launch(self, phase, context):
            nonlocal calls
            calls += 1
            retry_contexts.append(context.retry_context)
            if calls == 1:
                raise ProviderFailureError(
                    make_provider_diagnostic(
                        "timeout",
                        phase=phase,
                        cycle=context.cycle,
                        provider="flaky",
                        stderr="/private/path should be redacted",
                        retryable=True,
                        message="temporary timeout",
                    )
                )
            return AgentOutput("recovered", {}, "completed")

    engine = AutonomousEngine(TaskStore(tmp_path / "nous-home"), FlakyBackend(), require_external_subagents=False)
    output = engine._launch_phase_with_retry(_context(tmp_path, phase="plan"), "lease")
    assert output.summary == "recovered"
    assert calls == 2
    assert retry_contexts[0] is None
    assert retry_contexts[1] is not None
    assert "/private/path" not in json.dumps(retry_contexts[1])


def test_cross_review_can_gate_secondary_after_primary_failure(tmp_path: Path) -> None:
    calls = {"primary": 0, "secondary": 0}

    def primary(context: AgentContext) -> AgentOutput:
        calls["primary"] += 1
        raise ProviderFailureError(make_provider_diagnostic("validation_environment_failure", provider="primary", retryable=False))

    def secondary(context: AgentContext) -> AgentOutput:
        calls["secondary"] += 1
        return AgentOutput("secondary", {"validation_proposals": []}, "needs_plan")

    backend = CrossReviewBackend(
        CallableAgentBackend({"review": secondary}),
        {
            "primary": CallableAgentBackend({"review": primary}),
            "secondary": CallableAgentBackend({"review": secondary}),
        },
        primary_reviewer_id="primary",
        secondary_policy="after_primary_success",
    )
    output = backend.launch_review(_context(tmp_path))
    assert calls == {"primary": 1, "secondary": 0}
    assert output.decision == "needs_plan"
    assert output.outputs["cross_review"]["reviewers"][1]["status"] == "failed"


def test_plan_granularity_uses_complexity_and_atomic_sets(tmp_path: Path) -> None:
    context = _context(tmp_path, phase="plan")
    values = {
        "planned_files": [f"src/file-{index}.py" for index in range(10)],
        "estimated_output_bytes": 130_000,
        "dependencies": list(range(16)),
        "change_sets": [{"id": "atomic-core", "files": ["src/file-0.py"], "definition_of_done": ["tests pass"]}],
        "prior_planned_files": ["src/file-0.py"],
    }
    result = _plan_granularity(context, values)
    assert result["requires_split"] is True
    assert result["recommendation"] == "split_with_dependency_dag"
    assert result["same_file_rewrite_risk"] is True
    assert result["atomic_change_sets"][0]["id"] == "atomic-core"


def test_same_no_progress_streak_moves_task_to_human_required(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    store = TaskStore(tmp_path / "nous-home")
    task_id = store.create(title="no progress", objective="stop repeating", target_repository=str(repository), actor="test")["task_id"]
    backend = CallableAgentBackend(
        {
            "plan": lambda context: AgentOutput("plan", {}),
            "implement": lambda context: AgentOutput("noop", {"action_proposals": []}),
            "review": lambda context: AgentOutput("same failure", {}, "needs_plan"),
        }
    )
    result = AutonomousEngine(store, backend, max_cycles=5, no_progress_limit=2, require_external_subagents=False).run(task_id)
    assert result["outcome"] == "human_required"
    assert result["state"]["lifecycle_status"] == "waiting_human"
    assert result["diagnostics"]["report"]["no_progress_cycles"] >= 2
    assert store.diagnose(task_id)["pending_human_question_count"] == 1


def test_runtime_diagnostics_are_durable_and_visible_in_diagnose(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "nous-home")
    repository = tmp_path / "repository"
    repository.mkdir()
    task_id = store.create(title="diagnostics", objective="persist safe failures", target_repository=str(repository), actor="test")["task_id"]
    diagnostic = make_provider_diagnostic("invalid_json", phase="review", cycle=1, provider="test", stderr="/private/secret", retryable=True).public()
    diagnostic["task_id"] = task_id
    store.record_runtime_diagnostic(diagnostic)
    diagnosis = store.diagnose(task_id)
    assert diagnosis["runtime_diagnostic_count"] == 1
    assert diagnosis["runtime_diagnostic_category_counts"] == {"invalid_json": 1}
    assert "/private/secret" not in (store._path(task_id) / "diagnostics.jsonl").read_text(encoding="utf-8")


def test_validation_command_policy_accepts_mapping_and_keeps_prefix() -> None:
    policy = ValidationCommandPolicy.from_value(
        {"command": "python3 -m pytest", "timeout_seconds": 5, "allowed_side_effects": [".pytest_cache"]},
        label="validation",
    )
    assert policy.command == ("python3", "-m", "pytest")
    assert policy.matches(("python3", "-m", "pytest", "tests"))
    assert policy.spec.timeout_seconds == 5


def test_public_validation_policy_round_trips_without_runtime_secret() -> None:
    spec = ValidationSpec.from_mapping({"environment": {"TOKEN": "runtime-only"}})
    public = ValidationSpec.public_from_mapping(spec.public())
    assert public["environment_keys"] == ["TOKEN"]
    assert public["environment_value_sha256"] == spec.public()["environment_value_sha256"]
    assert "runtime-only" not in json.dumps(public)


def test_missing_managed_action_id_is_a_typed_provider_failure(tmp_path: Path) -> None:
    engine = AutonomousEngine(TaskStore(tmp_path / "nous-home"), CallableAgentBackend({}), require_external_subagents=False)
    with pytest.raises(ProviderFailureError) as raised:
        engine._execute_proposal(
            _context(tmp_path, phase="implement"),
            {"kind": "filesystem.write", "targets": ["result.txt"], "content": "ok"},
            1,
            "lease",
        )
    assert raised.value.category == "invalid_managed_action"
    assert raised.value.diagnostic.error_path.endswith("action_id")


def test_validation_environment_failure_stops_at_human_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = AutonomousEngine(TaskStore(tmp_path / "nous-home"), CallableAgentBackend({}), require_external_subagents=False)
    monkeypatch.setattr(
        engine,
        "_execute_proposal",
        lambda *_args, **_kwargs: _ProposalResult(
            "validation", 124, "evidence://validation", None, "validation_environment_failure", 12.0
        ),
    )
    normalized, decision = engine._materialize_output(
        _context(tmp_path),
        AgentOutput(
            "environment unavailable",
            {
                "validation_proposals": [
                    {"action_id": "validation", "kind": "process.run", "targets": ["tests"], "command": ["python3", "-m", "pytest"], "criterion_id": "AC-001"}
                ]
            },
            "completed",
        ),
        1,
        "lease",
    )
    assert decision == "needs_human_input"
    assert normalized["review_classification"] == "validation_environment_gap"
    assert normalized["human_question"]["recommended_option_id"] == "resume"
