from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_nous.cli import main
from agent_nous.errors import ValidationError
from agent_nous.eval.environment import TrialEnvironment
from agent_nous.eval.graders import grade_case
from agent_nous.eval.manifest import load_manifest
from agent_nous.eval.runner import EvalRunner, _capture_canonical_evidence, _tree_snapshot


def test_manifest_registers_every_v1_case_at_stage_one() -> None:
    manifest = load_manifest()
    assert len(manifest.cases) == 17
    assert {case["registration_required_by_stage"] for case in manifest.cases} == {1}
    selected = manifest.select(release="v1.0")
    assert len(selected) == 17
    assert {case["selection_status"] for case in selected} == {"runnable_required"}
    early = manifest.select(release="v0.1")
    assert sum(case["selection_status"] == "runnable_required" for case in early) == 4
    assert sum(case["selection_status"] == "registered_not_runnable" for case in early) == 13


def _manifest_copy(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    source = Path(__file__).parents[1] / "src" / "agent_nous" / "eval"
    for name in ("manifest.json", "manifest.schema.json", "fixtures.json"):
        shutil.copy2(source / name, tmp_path / name)
    value = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    return tmp_path / "manifest.json", value


def test_manifest_rejects_unknown_fixture_reference(tmp_path: Path) -> None:
    manifest_path, value = _manifest_copy(tmp_path)
    value["cases"][0]["task"]["fixture_refs"] = ["fixture://missing"]
    manifest_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValidationError, match="unknown fixture"):
        load_manifest(manifest_path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value["cases"][0]["trace"].update({"capture": ["forged-worker-claim"]}), "Eval manifest invalid"),
        (lambda value: value["cases"][0]["outcome"].update({"pass_predicate": "grader:unrelated_handler"}), "pass condition/predicate is not bound"),
    ],
)
def test_manifest_rejects_unregistered_trace_or_grader_predicate(tmp_path: Path, mutate: object, message: str) -> None:
    manifest_path, value = _manifest_copy(tmp_path)
    mutate(value)  # type: ignore[operator]
    manifest_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValidationError, match=message):
        load_manifest(manifest_path)


def test_manifest_stage_selection_is_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="unknown Eval stage"):
        load_manifest().select(stage=0)
    with pytest.raises(ValidationError, match="unknown Eval stage"):
        EvalRunner(root=tmp_path).validate_manifest(stage=0)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value["cases"][0].update({"handler": "unknown_handler"}), "unknown Eval handler"),
        (lambda value: value["cases"][0]["trial"].update({"interruption_ref": "fixture://missing-interrupt"}), "unknown fixture"),
        (lambda value: value["cases"][0]["grader"].pop("allowed_write_resources"), "Eval manifest invalid"),
    ],
)
def test_manifest_rejects_handler_interrupt_and_malformed_grader(tmp_path: Path, mutate: object, message: str) -> None:
    manifest_path, value = _manifest_copy(tmp_path)
    mutate(value)  # type: ignore[operator]
    manifest_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValidationError, match=message):
        load_manifest(manifest_path)


def test_one_off_grader_rejects_durable_task_evidence(tmp_path: Path) -> None:
    case = next(item for item in load_manifest().cases if item["eval_id"] == "trigger_one_off_negative")
    task = tmp_path / "nous-home" / "tasks" / "fake-task"
    task.mkdir(parents=True)
    (task / "task-definition.json").write_text("{}", encoding="utf-8")
    (tmp_path / "evidence").mkdir()
    result = grade_case(
        case=case,
        observed={"events": [], "policy_decisions": [], "action_kinds": [], "changed_paths": [], "sandbox_enforced": True, "trace": {}},
        trial_root=tmp_path,
    )
    assert "one_off_created_managed_task" in result["failure_codes"]
    assert "parent_evidence_attestation_missing" in result["failure_codes"]


def _environment(tmp_path: Path, eval_id: str) -> TrialEnvironment:
    run_root = tmp_path / "run"
    run_root.mkdir(parents=True)
    environment = TrialEnvironment.allocate(run_root, eval_id, 1)
    (environment.evidence / "sandbox-execution.json").write_text(
        json.dumps(
            {
                "backend": "codex_sandbox",
                "network_requested_disabled": True,
                "credential_environment_replaced": True,
                "allowed_write_roots": ["trial-root"],
            }
        ),
        encoding="utf-8",
    )
    return environment


def _capture(environment: TrialEnvironment, handler: str, *, stdout: str = "") -> dict[str, object]:
    runner = EvalRunner()
    runner._prepare_fixture({"eval_id": environment.eval_id}, environment)
    before = _tree_snapshot(environment.root)
    runner._handlers()[handler](environment)
    captured = _capture_canonical_evidence(
        environment,
        before=before,
        worker=subprocess.CompletedProcess(("fixture-worker",), 0, stdout, ""),
    )
    captured["sandbox_enforced"] = True
    return captured


def test_grader_rejects_forged_observed_and_scope_outside_write(tmp_path: Path) -> None:
    case = next(item for item in load_manifest().cases if item["eval_id"] == "small_change_sequential")
    environment = _environment(tmp_path, "small_change_sequential")
    (environment.worker_evidence / "worker-observed.json").write_text(
        json.dumps({"task_id": "forged", "changed_paths": ["repository/outside.txt"]}), encoding="utf-8"
    )
    # A worker-written observation is not a grader input.  Without a
    # parent-produced canonical capture, the release gate fails closed.
    result = grade_case(case=case, observed={}, trial_root=environment.root)
    assert "canonical_validation_evidence_missing" in result["failure_codes"]
    assert "invalid_observed_task_id" in result["failure_codes"]

    captured = _capture(environment, "small_change_sequential")
    captured["diff"] = {"changed_paths": ["repository/outside.txt"]}
    result = grade_case(case=case, observed=captured, trial_root=environment.root)
    assert "repository_write_scope_mismatch" in result["failure_codes"]


def test_grader_rejects_secret_and_missing_sandbox_probe(tmp_path: Path) -> None:
    case = next(item for item in load_manifest().cases if item["eval_id"] == "trigger_explicit_new")
    environment = _environment(tmp_path, "trigger_explicit_new")
    # A forged worker probe is non-authoritative.  The parent process capture
    # remains authoritative for isolation and output leak checks.
    (environment.worker_evidence / "sandbox-probe.json").write_text(
        json.dumps({"network_namespace_isolated": False}), encoding="utf-8"
    )
    captured = _capture(environment, "trigger_explicit_new", stdout="api_key=very-secret-value")
    result = grade_case(case=case, observed=captured, trial_root=environment.root)
    assert "secret_like_value_in_trace_or_artifact" in result["failure_codes"]
    assert "sandbox_network_or_credential_boundary_missing" not in result["failure_codes"]


def test_grader_rejects_malformed_approval_and_duplicate_idempotency(tmp_path: Path) -> None:
    approval_case = next(item for item in load_manifest().cases if item["eval_id"] == "approval_exact_red_action")
    approval_environment = _environment(tmp_path / "approval", "approval_exact_red_action")
    runner = EvalRunner()
    before = _tree_snapshot(approval_environment.root)
    runner._approval_exact_red_action(approval_environment)
    question = next((approval_environment.home / "tasks").glob("*/human-input/questions/*.json"))
    forged = json.loads(question.read_text(encoding="utf-8"))
    forged["question"] = "x"
    forged["why_now"] = "x"
    for option in forged["options"]:
        option["label"] = "x"
        option["impact"] = "x"
    question.write_text(json.dumps(forged), encoding="utf-8")
    approval_capture = _capture_canonical_evidence(
        approval_environment,
        before=before,
        worker=subprocess.CompletedProcess(("fixture-worker",), 0, "", ""),
    )
    approval_capture["sandbox_enforced"] = True
    approval = grade_case(case=approval_case, observed=approval_capture, trial_root=approval_environment.root)
    assert any(code.startswith("canonical_validation_failed:") for code in approval["failure_codes"])

    action_case = next(item for item in load_manifest().cases if item["eval_id"] == "small_change_interrupt_resume")
    action_environment = _environment(tmp_path / "idempotency", "small_change_interrupt_resume")
    before = _tree_snapshot(action_environment.root)
    runner._small_change_interrupt_resume(action_environment)
    action = next((action_environment.home / "tasks").glob("*/actions/*.json"))
    (action.parent / "forged-duplicate.json").write_text(action.read_text(encoding="utf-8"), encoding="utf-8")
    duplicate_capture = _capture_canonical_evidence(
        action_environment,
        before=before,
        worker=subprocess.CompletedProcess(("fixture-worker",), 0, "", ""),
    )
    duplicate_capture["sandbox_enforced"] = True
    duplicate = grade_case(case=action_case, observed=duplicate_capture, trial_root=action_environment.root)
    assert any(code.startswith("canonical_validation_failed:") for code in duplicate["failure_codes"])


def test_v1_release_gate_runs_every_required_case_without_skip(tmp_path: Path) -> None:
    result = EvalRunner(root=tmp_path).run(release="v1.0")
    assert result["outcome"] == "passed"
    assert result["required_case_count"] == 17
    assert result["required_failures"] == []
    assert all(case["selection_status"] == "runnable_required" for case in result["cases"])
    assert all(case["outcome"] == "passed" for case in result["cases"])
    run_root = Path(result["run_path"])
    report = EvalRunner(root=tmp_path).report(result["run_id"])
    assert isinstance(report, dict) and report["outcome"] == "passed"
    cleanup = EvalRunner(root=tmp_path).cleanup(result["run_id"])
    assert cleanup["evidence_retained"] is True
    assert (run_root / "report.json").is_file()
    assert list((run_root / "evidence").glob("*/outcome.json"))
    assert not list((run_root / "trials").glob("*/scratch/*"))
    assert not list((run_root / "trials").glob("*/worker-evidence/*"))


def test_eval_cli_list_run_report_and_cleanup(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["eval", "validate-manifest", "--stage", "5"]) == 0
    validate = json.loads(capsys.readouterr().out)
    assert validate["result"]["valid"] is True
    assert main(["eval", "list", "--release", "v0.1"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["result"]["cases"][0]["selection_status"] == "runnable_required"
    assert main(["eval", "run", "--release", "v0.1", "--root", str(tmp_path)]) == 0
    run = json.loads(capsys.readouterr().out)["result"]
    assert run["outcome"] == "passed"
    assert main(["eval", "report", "--root", str(tmp_path), "--run", run["run_id"], "--format", "markdown"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert "Outcome: **passed**" in report["result"]
    assert main(["eval", "cleanup", "--root", str(tmp_path), "--run", run["run_id"]]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["evidence_retained"] is True


def test_cleanup_rejects_run_outside_configured_root(tmp_path: Path) -> None:
    runner = EvalRunner(root=tmp_path / "owned")
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "run.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="Eval run"):
        runner.cleanup(str(foreign))


def test_cleanup_rejects_tampered_owned_marker(tmp_path: Path) -> None:
    runner = EvalRunner(root=tmp_path)
    result = runner.run(release="v0.1", eval_ids=("trigger_explicit_new",))
    owner = Path(result["run_path"]) / "owner.json"
    value = json.loads(owner.read_text(encoding="utf-8"))
    value["ownership_token"] = "tampered"
    owner.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="ownership marker"):
        runner.cleanup(result["run_id"])


def test_cleanup_rejects_hand_made_owner_marker_without_deleting_trials(tmp_path: Path) -> None:
    runner = EvalRunner(root=tmp_path)
    run_root = tmp_path / "runs" / "hand-made"
    (run_root / "trials" / "foreign-trial" / "scratch").mkdir(parents=True)
    (run_root / "trials" / "foreign-trial" / "scratch" / "keep.txt").write_text("keep", encoding="utf-8")
    (run_root / "run.json").write_text(
        json.dumps({"schema_version": 1, "run_id": "hand-made", "manifest_digest": load_manifest().digest}),
        encoding="utf-8",
    )
    (run_root / "owner.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "hand-made",
                "manifest_digest": load_manifest().digest,
                "ownership_token": "forged",
                "ownership_key_id": "forged-key",
                "owner_signature": "forged-signature",
                "owner_digest": "forged-digest",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="ownership marker"):
        runner.cleanup("hand-made")
    assert (run_root / "trials" / "foreign-trial" / "scratch" / "keep.txt").is_file()
