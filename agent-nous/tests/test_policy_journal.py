from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent_nous.domain import ActionDescriptor, HumanApprovalAuthority, PolicyGateway, approval_answer_message
from agent_nous.errors import ConflictError, NotFoundError, ValidationError
from agent_nous.taskctl import TaskStore


def _leased(tmp_path: Path) -> tuple[TaskStore, str, Path, int]:
    repo = tmp_path / "repo"
    repo.mkdir()
    store = TaskStore(tmp_path / "home")
    task = store.create(title="Policy journal", objective="exercise the managed journal", target_repository=str(repo), actor="test")["task_id"]
    leased = store.acquire_lease(task, 1, "worker")
    return store, task, repo, leased["revision"]


def _fresh_python(source: str, *args: str) -> subprocess.CompletedProcess[str]:
    package_root = Path(__file__).resolve().parents[1] / "src"
    environment = {**os.environ, "PYTHONPATH": f"{package_root}{os.pathsep}{os.environ.get('PYTHONPATH', '')}"}
    return subprocess.run([sys.executable, "-c", source, *args], text=True, capture_output=True, env=environment, check=False)


def _trusted_approval_answer(task_id: str, question_id: str, actor: str, answer: dict[str, object]) -> tuple[dict[str, object], HumanApprovalAuthority]:
    private = Ed25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    signature = private.sign(approval_answer_message(task_id, question_id, actor, answer))
    import base64
    return {**answer, "human_attestation": base64.b64encode(signature).decode("ascii")}, HumanApprovalAuthority(public_pem)


def test_rootless_process_is_denied_and_recorded_without_action_start(tmp_path: Path) -> None:
    store, task, _, revision = _leased(tmp_path)
    action = ActionDescriptor(action_id=f"action-{uuid.uuid4().hex}", task_id=task, kind="process.run", command=("/usr/bin/python3", "-m", "unittest"), expected_state_revision=revision)
    with pytest.raises(ValidationError):
        store.begin_managed_action(action, "worker", PolicyGateway(allowed_processes=(("/usr/bin/python3", "-m", "unittest"),)), {"result": "pass", "facts": {}, "redaction_summary": {}})
    journal = (tmp_path / "home" / "tasks" / task / "events.jsonl").read_text()
    assert "action_started" not in journal


def test_unapproved_red_surface_is_recorded_and_never_started(tmp_path: Path) -> None:
    store, task, repo, revision = _leased(tmp_path)
    action = ActionDescriptor(action_id=f"action-{uuid.uuid4().hex}", task_id=task, kind="git.remote", repository_root=str(repo), targets=(str(repo),), command=("git", "push"), expected_state_revision=revision, idempotency_key="no-remote")
    result = store.begin_managed_action(action, "worker", PolicyGateway(), {"result": "pass", "facts": {}, "redaction_summary": {}})
    assert result["decision"].outcome in {"approval_required", "unsupported"}
    record = next((tmp_path / "home" / "tasks" / task / "actions").glob("*.json")).read_text()
    assert '"status": "denied"' in record


def test_duplicate_action_id_is_rejected_before_second_execution(tmp_path: Path) -> None:
    store, task, repo, revision = _leased(tmp_path)
    action = ActionDescriptor(action_id=f"action-{uuid.uuid4().hex}", task_id=task, kind="filesystem.write", repository_root=str(repo), targets=(str(repo / "one.txt"),), expected_state_revision=revision, idempotency_key="one")
    started = store.begin_managed_action(action, "worker", PolicyGateway(), {"result": "pass", "facts": {}, "redaction_summary": {}})
    assert started["decision"].outcome == "grant"
    with pytest.raises(ConflictError):
        store.begin_managed_action(action, "worker", PolicyGateway(), {"result": "pass", "facts": {}, "redaction_summary": {}})


def test_other_repository_root_and_failed_preflight_never_receive_grants(tmp_path: Path) -> None:
    store, task, repo, revision = _leased(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    wrong_root = ActionDescriptor(action_id=f"action-{uuid.uuid4().hex}", task_id=task, kind="filesystem.write", repository_root=str(other), targets=(str(other / "escape.txt"),), expected_state_revision=revision)
    with pytest.raises(ValidationError):
        store.begin_managed_action(wrong_root, "worker", PolicyGateway(), {"result": "pass", "facts": {}, "redaction_summary": {}})
    action = ActionDescriptor(action_id=f"action-{uuid.uuid4().hex}", task_id=task, kind="filesystem.write", repository_root=str(repo), targets=(str(repo / "result.txt"),), expected_state_revision=revision)
    denied = store.begin_managed_action(action, "worker", PolicyGateway(), {"result": "fail", "facts": {"reason": "probe failed"}, "redaction_summary": {}})
    assert denied["decision"].outcome == "deny"
    assert denied["action_record"]["status"] == "denied"
    assert denied["state"]["current_action"] is None


def test_wrong_actor_cannot_finish_a_live_grant(tmp_path: Path) -> None:
    store, task, repo, revision = _leased(tmp_path)
    action = ActionDescriptor(action_id=f"action-{uuid.uuid4().hex}", task_id=task, kind="filesystem.write", repository_root=str(repo), targets=(str(repo / "result.txt"),), expected_state_revision=revision)
    started = store.begin_managed_action(action, "worker", PolicyGateway(), {"result": "pass", "facts": {}, "redaction_summary": {}})
    with pytest.raises(ConflictError):
        store.finish_managed_action(task, started["state"]["revision"], "other", started["grant"]["lease_token"], action.action_id, started["grant"]["grant_id"], 0, {"result": "pass", "facts": {}, "redaction_summary": {}})


def test_live_managed_action_prevents_lease_release_and_orphaned_idle_state(tmp_path: Path) -> None:
    store, task, repo, revision = _leased(tmp_path)
    action = ActionDescriptor(action_id=f"action-{uuid.uuid4().hex}", task_id=task, kind="filesystem.write", repository_root=str(repo), targets=(str(repo / "result.txt"),), expected_state_revision=revision)
    started = store.begin_managed_action(action, "worker", PolicyGateway(), {"result": "pass", "facts": {}, "redaction_summary": {}})
    with pytest.raises(ConflictError):
        store.release_lease(task, started["state"]["revision"], "worker", started["grant"]["lease_token"])
    _, state, _ = TaskStore(tmp_path / "home").read_task(task)
    assert state["execution_status"] == "running"
    assert state["current_action"]["action_id"] == action.action_id


def test_fresh_os_process_cannot_release_a_live_managed_action(tmp_path: Path) -> None:
    store, task, repo, revision = _leased(tmp_path)
    action = ActionDescriptor(action_id=f"action-{uuid.uuid4().hex}", task_id=task, kind="filesystem.write", repository_root=str(repo), targets=(str(repo / "result.txt"),), expected_state_revision=revision)
    started = store.begin_managed_action(action, "worker", PolicyGateway(), {"result": "pass", "facts": {}, "redaction_summary": {}})
    child = _fresh_python(
        """
import sys
from pathlib import Path
from agent_nous.errors import ConflictError
from agent_nous.taskctl import TaskStore
store = TaskStore(Path(sys.argv[1]))
try:
    store.release_lease(sys.argv[2], int(sys.argv[3]), 'worker', sys.argv[4])
except ConflictError:
    print('blocked')
    raise SystemExit(0)
raise SystemExit(1)
""",
        str(tmp_path / "home"), task, str(started["state"]["revision"]), str(started["grant"]["lease_token"]),
    )
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == "blocked"
    _, state, _ = TaskStore(tmp_path / "home").read_task(task)
    assert state["execution_status"] == "running"


def test_exact_approval_is_immutable_consumed_once_and_journaled_in_one_transaction(tmp_path: Path) -> None:
    store, task, repo, revision = _leased(tmp_path)
    action_id = f"action-{uuid.uuid4().hex}"
    proposed = ActionDescriptor(
        action_id=action_id,
        task_id=task,
        kind="filesystem.write",
        repository_root=str(repo),
        targets=(str(repo / "approved.txt"),),
        expected_state_revision=revision + 1,
        plan_ref="cycles/0001/01-plan-result.json",
        plan_content_hash="a" * 64,
        expected_effect="write approved fixture",
    )
    released = store.release_lease(task, revision, "worker", store.read_task(task)[1]["active_lease"]["token"])
    assert released["revision"] == revision + 1
    requested = store.request_exact_approval(task, released["revision"], "requester", proposed)
    answer_payload: dict[str, object] = {
        "answer_type": "selected_option",
        "selected_option_id": "approve",
        "answer_text": None,
        "applied_scope": "action",
        "answer_effect": "resume",
        "resume_target": requested["question"]["resume_target"],
    }
    signed_answer, authority = _trusted_approval_answer(task, requested["question"]["question_id"], "human-approver", answer_payload)
    answered = TaskStore(tmp_path / "home", authority).answer_question(
        task,
        requested["state"]["revision"],
        "human-approver",
        requested["question"]["question_id"],
        signed_answer,
    )
    answer_id = next((tmp_path / "home" / "tasks" / task / "human-input" / "answers").glob("*.json")).stem
    # A fresh process sees the canonical request and grants it without an
    # in-memory Approval object crossing the boundary.
    granted = TaskStore(tmp_path / "home").grant_exact_approval(task, answered["revision"], "untrusted-caller-name", requested["approval"]["approval_id"], answer_id)
    assert granted["approval"]["granted_by"] == "human-approver"
    assert granted["approval"]["granted_answer_ref"].endswith(f"{answer_id}.json")
    lease = TaskStore(tmp_path / "home").acquire_lease(task, granted["state"]["revision"], "worker")
    authorized = ActionDescriptor(
        action_id=action_id,
        task_id=task,
        kind="filesystem.write",
        repository_root=str(repo),
        targets=(str(repo / "approved.txt"),),
        expected_state_revision=lease["revision"],
        approval_id=requested["approval"]["approval_id"],
        plan_ref="cycles/0001/01-plan-result.json",
        plan_content_hash="a" * 64,
        expected_effect="write approved fixture",
    )
    started = TaskStore(tmp_path / "home").begin_managed_action(authorized, "worker", PolicyGateway(), {"result": "pass", "facts": {}, "redaction_summary": {}})
    records = json.loads((tmp_path / "home" / "tasks" / task / "approvals.json").read_text())["approvals"]
    assert records[0]["status"] == "exhausted"
    assert records[0]["consumed_count"] == 1
    events = [json.loads(line) for line in (tmp_path / "home" / "tasks" / task / "events.jsonl").read_text().splitlines()]
    action_events = [event for event in events if event["payload"].get("action_id") == action_id]
    required = {"policy_decided", "execution_grant_issued", "approval_consumed", "action_started"}
    assert {event["type"] for event in action_events} >= required
    start_events = [event for event in action_events if event["type"] in required]
    assert len({event["transaction_id"] for event in start_events}) == 1
    with pytest.raises(ConflictError):
        TaskStore(tmp_path / "home").begin_managed_action(authorized, "worker", PolicyGateway(), {"result": "pass", "facts": {}, "redaction_summary": {}})
    finished = TaskStore(tmp_path / "home").finish_managed_action(task, started["state"]["revision"], "worker", started["grant"]["lease_token"], action_id, started["grant"]["grant_id"], 0, {"result": "pass", "facts": {}, "redaction_summary": {}})
    assert finished["action_record"]["status"] == "completed"


def test_approval_cannot_be_self_granted_without_matching_immutable_human_answer(tmp_path: Path) -> None:
    store, task, repo, revision = _leased(tmp_path)
    released = store.release_lease(task, revision, "worker", store.read_task(task)[1]["active_lease"]["token"])
    proposal = ActionDescriptor(action_id=f"action-{uuid.uuid4().hex}", task_id=task, kind="filesystem.write", repository_root=str(repo), targets=(str(repo / "requires-human.txt"),), expected_state_revision=released["revision"], plan_ref="cycles/0001/01-plan-result.json", plan_content_hash="c" * 64)
    requested = store.request_exact_approval(task, released["revision"], "phase-agent", proposal)
    with pytest.raises(ValidationError):
        TaskStore(tmp_path / "home").answer_question(
            task,
            requested["state"]["revision"],
            "phase-agent",
            requested["question"]["question_id"],
            {
                "answer_type": "selected_option",
                "selected_option_id": "approve",
                "answer_text": None,
                "applied_scope": "action",
                "answer_effect": "resume",
                "resume_target": requested["question"]["resume_target"],
            },
        )
    with pytest.raises(NotFoundError):
        TaskStore(tmp_path / "home").grant_exact_approval(task, requested["state"]["revision"], "phase-agent", requested["approval"]["approval_id"], "a-not-a-human-answer")


def test_default_authority_ignores_caller_environment_key_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    answer: dict[str, object] = {
        "answer_type": "selected_option",
        "selected_option_id": "approve",
        "answer_text": None,
        "applied_scope": "action",
        "answer_effect": "resume",
        "resume_target": {"resume_phase": "implement", "resume_transition": "approval_granted", "blocked_action_ref": "approval-requests/x.json", "blocked_artifact_ref": None},
    }
    signed, _authority = _trusted_approval_answer("20260719-1200-test", "q-test", "phase-agent", answer)
    # This variable was deliberately used by the rejected implementation.
    # A phase process can choose it, so the default verifier must ignore it.
    monkeypatch.setenv("NOUS_HUMAN_APPROVAL_PUBLIC_KEY_PEM", "phase-selected-key")
    assert not HumanApprovalAuthority().verify("20260719-1200-test", "q-test", "phase-agent", answer, str(signed["human_attestation"]))


def test_expired_approval_cannot_be_granted_or_started(tmp_path: Path) -> None:
    store, task, repo, revision = _leased(tmp_path)
    released = store.release_lease(task, revision, "worker", store.read_task(task)[1]["active_lease"]["token"])
    proposal = ActionDescriptor(action_id=f"action-{uuid.uuid4().hex}", task_id=task, kind="filesystem.write", repository_root=str(repo), targets=(str(repo / "one.txt"),), expected_state_revision=released["revision"], plan_ref="cycles/0001/01-plan-result.json", plan_content_hash="b" * 64)
    expiry = (datetime.now(timezone.utc) + timedelta(milliseconds=250)).isoformat()
    requested = store.request_exact_approval(task, released["revision"], "requester", proposal, expiry)
    time.sleep(0.3)
    expired = TaskStore(tmp_path / "home").expire_approvals(task)
    assert expired is not None
    assert "approval_expired" in (tmp_path / "home" / "tasks" / task / "events.jsonl").read_text()
    with pytest.raises(ConflictError):
        TaskStore(tmp_path / "home").grant_exact_approval(task, expired["revision"], "human", requested["approval"]["approval_id"], "a-missing")


def test_secret_gate_records_only_a_value_free_policy_violation(tmp_path: Path) -> None:
    store, task, repo, revision = _leased(tmp_path)
    secret = "fixture-api-key-placeholder"
    action = ActionDescriptor(action_id=f"action-{uuid.uuid4().hex}", task_id=task, kind="filesystem.write", repository_root=str(repo), targets=(str(repo / "safe.txt"),), expected_state_revision=revision, expected_effect=f"api_key={secret}")
    with pytest.raises(ValidationError):
        store.begin_managed_action(action, "worker", PolicyGateway(), {"result": "pass", "facts": {}, "redaction_summary": {}})
    task_root = tmp_path / "home" / "tasks" / task
    text = "\n".join(path.read_text(errors="ignore") for path in task_root.rglob("*") if path.is_file())
    assert secret not in text
    assert "policy_violation" in text


def test_fresh_process_reconciles_an_interrupted_action_without_rerunning_it(tmp_path: Path) -> None:
    store, task, repo, revision = _leased(tmp_path)
    # Use a short live lease, then start an action and let a fresh taskctl
    # instance reconcile the lease before observing its final state.
    store.release_lease(task, revision, "worker", store.read_task(task)[1]["active_lease"]["token"])
    short = store.acquire_lease(task, 3, "worker", seconds=1)
    action = ActionDescriptor(action_id=f"action-{uuid.uuid4().hex}", task_id=task, kind="filesystem.write", repository_root=str(repo), targets=(str(repo / "reconciled.txt"),), expected_state_revision=short["revision"])
    started = store.begin_managed_action(action, "worker", PolicyGateway(), {"result": "pass", "facts": {}, "redaction_summary": {}})
    time.sleep(2.0)
    recovered = TaskStore(tmp_path / "home").recover_expired_lease(task)
    assert recovered is not None and recovered["execution_status"] == "interrupted"
    with pytest.raises(ValidationError):
        TaskStore(tmp_path / "home").resume_prepare(task, recovered["revision"], "fresh-worker")
    reconciled = TaskStore(tmp_path / "home").reconcile_managed_action(task, recovered["revision"], "fresh-worker", action.action_id, {"result": "pass", "reconcile_outcome": "completed_verified", "facts": {"verification": "fixture"}, "artifact_refs": ["evidence://fixture"], "redaction_summary": {}})
    assert reconciled["action_record"]["status"] == "completed"
    assert reconciled["state"]["execution_status"] == "idle"
    journal = (tmp_path / "home" / "tasks" / task / "events.jsonl").read_text()
    assert "action_reconciled" in journal
    assert "execution_grant_revoked" in journal
