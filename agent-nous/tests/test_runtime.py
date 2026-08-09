from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_nous.errors import ConflictError, ValidationError
from agent_nous.orchestrator import Orchestrator, PhaseResultDraft
from agent_nous.taskctl import TaskStore


def make(store: TaskStore) -> str:
    return store.create(title="Runtime task", objective="exercise durable runtime", target_repository=None, actor="test")["task_id"]


def completion_outputs() -> dict[str, object]:
    return {
        "acceptance_criteria": [{"criterion_id": "AC-001", "status": "passed", "evidence_refs": ["evidence://fixture/test"]}],
        "validation_evidence": [{"criterion_id": "AC-001", "kind": "test", "ref": "evidence://fixture/test", "outcome": "passed"}],
        "blocking_issues": [],
    }


def test_task_pause_and_fresh_process_resume_preserves_lifecycle(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    task = make(store)
    paused = store.pause(task, 1, "test")
    assert paused["lifecycle_status"] == "active"
    assert paused["execution_status"] == "paused"
    with pytest.raises(ConflictError):
        store.pause(task, 1, "test")
    resumed = TaskStore(tmp_path).resume_prepare(task, 2, "fresh")
    assert resumed["state"]["revision"] == 3
    assert resumed["state"]["lifecycle_status"] == "active"
    assert resumed["state"]["execution_status"] == "idle"
    assert (tmp_path / "tasks" / task / "checkpoints/latest.json").is_file()


def test_question_answer_is_immutable_and_fresh_process_resumes_exact_phase(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    task = make(store)
    target = {"resume_phase": "implement", "resume_transition": "answer_received", "blocked_action_ref": None, "blocked_artifact_ref": None}
    result = store.record_question(task, 1, "test", {"question": "Choose", "why_now": "A phase decision is required.", "options": [{"option_id": "safe", "label": "Safe", "impact": "Continue safely."}, {"option_id": "other", "label": "Other", "impact": "Use the alternative."}], "recommended_option_id": "safe", "free_text_allowed": False, "blocking_scope": "phase", "blocked_refs": [], "affected_loop_ids": ["root"], "resume_target": target, "approval_request_ref": None})
    question = result["question"]
    _, waiting, _ = TaskStore(tmp_path).read_task(task)
    assert waiting["lifecycle_status"] == "waiting_human"
    assert waiting["execution_status"] == "idle"
    with pytest.raises(ValidationError):
        TaskStore(tmp_path).resume_prepare(task, waiting["revision"], "fresh")
    answered = TaskStore(tmp_path).answer_question(task, waiting["revision"], "human", question["question_id"], {"answer_type": "selected_option", "selected_option_id": "safe", "answer_text": None, "applied_scope": "phase", "answer_effect": "resume", "resume_target": target})
    assert answered["lifecycle_status"] == "active"
    assert answered["phase"] == "implement"
    qpath = tmp_path / "tasks" / task / "human-input/questions" / f"{question['question_id']}.json"
    persisted_question = json.loads(qpath.read_text())
    assert persisted_question["question"] == "Choose"
    answer_files = list((tmp_path / "tasks" / task / "human-input/answers").glob("*.json"))
    assert len(answer_files) == 1
    with pytest.raises(ConflictError):
        TaskStore(tmp_path).answer_question(task, answered["revision"], "human", question["question_id"], {"answer_type": "selected_option", "selected_option_id": "other", "answer_text": None, "applied_scope": "phase", "answer_effect": "resume", "resume_target": target})


def test_lease_renew_release_and_expiry_recovery_are_durable(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    task = make(store)
    acquired = store.acquire_lease(task, 1, "worker", seconds=60)
    token = acquired["active_lease"]["token"]
    renewed = TaskStore(tmp_path).renew_lease(task, acquired["revision"], "worker", token, seconds=60)
    assert renewed["execution_status"] == "running"
    released = TaskStore(tmp_path).release_lease(task, renewed["revision"], "worker", token)
    assert released["execution_status"] == "idle"
    TaskStore(tmp_path).acquire_lease(task, released["revision"], "worker", seconds=1)
    # A separate process observes an actually expired lease.  The canonical
    # snapshot is never edited by the test.
    time.sleep(2.0)
    recovered = TaskStore(tmp_path).recover_expired_lease(task)
    assert recovered is not None
    assert recovered["execution_status"] == "interrupted"
    assert recovered["active_lease"] is None
    journal = (tmp_path / "tasks" / task / "events.jsonl").read_text()
    assert "lease_expired" in journal


def test_stale_same_actor_cannot_complete_without_exact_lease_token(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    task = make(store)
    lease = store.acquire_lease(task, 1, "worker")
    with pytest.raises(ValidationError):
        store.record_phase(task, lease["revision"], "worker", "lease-not-the-owner-token", "plan", "stale result", {})
    released = store.release_lease(task, lease["revision"], "worker", lease["active_lease"]["token"])
    fresh = TaskStore(tmp_path).acquire_lease(task, released["revision"], "worker")
    result = TaskStore(tmp_path).record_phase(task, fresh["revision"], "worker", fresh["active_lease"]["token"], "plan", "fresh result", {})
    assert result["state"]["phase"] == "implement"


def test_expired_lease_cannot_complete_a_phase_in_a_fresh_store(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    task = make(store)
    lease = store.acquire_lease(task, 1, "worker", seconds=1)
    time.sleep(2.0)
    with pytest.raises(ConflictError):
        TaskStore(tmp_path).record_phase(task, lease["revision"], "worker", lease["active_lease"]["token"], "plan", "late result", {})
    _, state, _ = TaskStore(tmp_path).read_task(task)
    assert state["execution_status"] == "interrupted"
    assert state["phase"] == "plan"


def test_running_pause_requires_worker_acknowledgement_at_safe_checkpoint(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    task = make(store)
    lease = store.acquire_lease(task, 1, "worker")
    with pytest.raises(ValidationError):
        store.pause(task, lease["revision"], "other")
    pausing = store.request_pause(task, lease["revision"], "control-plane")
    assert pausing["execution_status"] == "pausing"
    assert pausing["active_lease"]["token"] == lease["active_lease"]["token"]
    with pytest.raises(ConflictError):
        store.pause(task, pausing["revision"], "worker", "wrong-token")
    paused = store.pause(task, pausing["revision"], "worker", lease["active_lease"]["token"])
    assert paused["execution_status"] == "paused"
    assert paused["active_lease"] is None


def test_public_taskctl_pause_requests_then_acknowledges_in_fresh_process(tmp_path: Path) -> None:
    home = tmp_path / "cli-home"
    store = TaskStore(home)
    task = make(store)
    lease = store.acquire_lease(task, 1, "worker")
    request = tmp_path / "pause.json"
    request.write_text(json.dumps({"contract_version": 1, "request_id": "R-pause-request", "expected_revision": lease["revision"], "actor": {"type": "control-plane", "run_id": "RUN-pause", "actor_id": "control-plane"}}))
    command = [sys.executable, "-m", "agent_nous.cli", "--nous-home", str(home), "--input", str(request), "taskctl", "pause", task]
    paused = json.loads(subprocess.check_output(command, text=True))
    assert paused["result"]["state"]["execution_status"] == "pausing"
    ack = tmp_path / "ack.json"
    ack.write_text(json.dumps({"contract_version": 1, "request_id": "R-pause-ack", "expected_revision": paused["result"]["state"]["revision"], "lease_token": lease["active_lease"]["token"], "actor": {"type": "worker", "run_id": "RUN-pause", "actor_id": "worker"}}))
    acknowledged = json.loads(subprocess.check_output([sys.executable, "-m", "agent_nous.cli", "--nous-home", str(home), "--input", str(ack), "taskctl", "pause-ack", task], text=True))
    assert acknowledged["result"]["state"]["execution_status"] == "paused"


def test_public_taskctl_request_id_replays_and_rejects_payload_reuse(tmp_path: Path) -> None:
    home = tmp_path / "cli-home"
    request = tmp_path / "create.json"
    request.write_text(json.dumps({"contract_version": 1, "request_id": "R-create", "actor": {"type": "human", "run_id": "RUN-create", "actor_id": "human-1"}, "title": "request task", "objective": "exercise retry"}))
    command = [sys.executable, "-m", "agent_nous.cli", "--nous-home", str(home), "--input", str(request), "taskctl", "create"]
    first = json.loads(subprocess.check_output(command, text=True))
    second = json.loads(subprocess.check_output(command, text=True))
    assert first["request_id"] == second["request_id"] == "R-create"
    assert first["task_id"] == second["task_id"]
    assert len(list((home / "tasks").glob("20*/task-definition.json"))) == 1
    events = [json.loads(line) for line in (home / "tasks" / first["task_id"] / "events.jsonl").read_text().splitlines()]
    assert events[0]["type"] == "task_created"
    assert events[0]["payload"]["request_id"] == "R-create"

    request.write_text(json.dumps({"contract_version": 1, "request_id": "R-create", "actor": {"type": "human", "run_id": "RUN-create", "actor_id": "human-1"}, "title": "different", "objective": "different"}))
    conflict = subprocess.run(command, text=True, capture_output=True)
    assert conflict.returncode != 0
    assert json.loads(conflict.stdout)["error"]["code"] == "conflict"


def test_public_mutation_binds_request_id_to_canonical_event_and_transaction(tmp_path: Path) -> None:
    home = tmp_path / "cli-home"
    store = TaskStore(home)
    task = make(store)
    request = tmp_path / "checkpoint.json"
    request.write_text(json.dumps({"contract_version": 1, "request_id": "R-checkpoint", "expected_revision": 1, "reason": "save evidence", "actor": {"type": "worker", "run_id": "RUN-checkpoint", "actor_id": "worker"}}))
    command = [sys.executable, "-m", "agent_nous.cli", "--nous-home", str(home), "--input", str(request), "taskctl", "checkpoint", task]
    result = json.loads(subprocess.check_output(command, text=True))
    assert result["ok"] is True
    events = [json.loads(line) for line in (home / "tasks" / task / "events.jsonl").read_text().splitlines()]
    checkpoint_events = [event for event in events if event["type"] == "checkpoint_saved"]
    assert len(checkpoint_events) == 1
    assert checkpoint_events[0]["payload"]["request_id"] == "R-checkpoint"
    transaction_files = list((home / "tasks" / ".transactions").glob("*.json"))
    assert transaction_files == []


def test_public_taskctl_dry_run_plans_without_mutating_state_or_ledger(tmp_path: Path) -> None:
    home = tmp_path / "cli-home"
    store = TaskStore(home)
    task = make(store)
    original_state = (home / "tasks" / task / "state.json").read_text()
    original_events = (home / "tasks" / task / "events.jsonl").read_text()
    request = tmp_path / "dry-run.json"
    request.write_text(json.dumps({"contract_version": 1, "request_id": "R-dry-run", "expected_revision": 1, "reason": "preview checkpoint", "actor": {"type": "worker", "run_id": "RUN-dry-run", "actor_id": "worker"}}))
    command = [sys.executable, "-m", "agent_nous.cli", "--dry-run", "--nous-home", str(home), "--input", str(request), "taskctl", "checkpoint", task]
    result = json.loads(subprocess.check_output(command, text=True))
    plan = result["result"]
    assert plan["dry_run"] is True
    assert plan["from_revision"] == 1
    assert plan["to_revision"] == 2
    assert any(event["type"] == "checkpoint_saved" and event["request_id"] == "R-dry-run" for event in plan["planned_events"])
    assert (home / "tasks" / task / "state.json").read_text() == original_state
    assert (home / "tasks" / task / "events.jsonl").read_text() == original_events
    assert not (home / "requests").exists()


def test_public_taskctl_record_knowledge_uses_canonical_transaction(tmp_path: Path) -> None:
    home = tmp_path / "cli-home"
    task = make(TaskStore(home))
    request = tmp_path / "knowledge.json"
    request.write_text(json.dumps({
        "contract_version": 1,
        "request_id": "R-knowledge",
        "expected_revision": 1,
        "questions": [{"question_id": "Q-001", "question": "Which files are in scope?", "reason": "Define the implementation boundary.", "impact": "low", "required_before": "plan_complete"}],
        "sources": [],
        "claims": [],
        "actor": {"type": "orchestrator", "run_id": "RUN-knowledge", "actor_id": "codex"},
    }))
    command = [sys.executable, "-m", "agent_nous.cli", "--nous-home", str(home), "--input", str(request), "taskctl", "record-knowledge", task]
    result = json.loads(subprocess.check_output(command, text=True))
    assert result["ok"] is True
    assert result["result"]["knowledge"]["questions"][0]["question_id"] == "Q-001"
    assert (home / "tasks" / task / "knowledge/questions.json").is_file()
    events = [json.loads(line) for line in (home / "tasks" / task / "events.jsonl").read_text().splitlines()]
    assert events[-1]["type"] == "knowledge_recorded"
    assert events[-1]["payload"]["request_id"] == "R-knowledge"


def test_task_lifecycle_commands_preserve_history_and_identity(tmp_path: Path) -> None:
    home = tmp_path / "lifecycle"
    store = TaskStore(home, request_id="R-lifecycle-create")
    task = make(store)
    renamed = TaskStore(home, request_id="R-lifecycle-rename").rename(task, 1, "codex", "Renamed runtime task")
    assert renamed["state"]["revision"] == 2
    duplicate = TaskStore(home, request_id="R-lifecycle-duplicate").duplicate(task, "codex")
    duplicate_definition = TaskStore(home).read_task(duplicate["task_id"])[0]
    assert duplicate_definition["derived_from_task_id"] == task
    cancelled = TaskStore(home, request_id="R-lifecycle-cancel").cancel(task, 2, "codex", "scope is no longer needed")
    assert cancelled["lifecycle_status"] == "cancelled"
    reopened = TaskStore(home, request_id="R-lifecycle-reopen").reopen(task, 3, "codex", "scope is needed again")
    assert reopened["lifecycle_status"] == "active"
    assert reopened["cycle"] == 2
    archived = TaskStore(home, request_id="R-lifecycle-archive").set_visibility(task, 4, "codex", "archived")
    assert archived["visibility"] == "archived"
    visible = TaskStore(home, request_id="R-lifecycle-unarchive").set_visibility(task, 5, "codex", "visible")
    assert visible["visibility"] == "visible"


def test_archived_tasks_are_hidden_from_default_list(tmp_path: Path) -> None:
    store = TaskStore(tmp_path, request_id="R-list-create")
    task = make(store)
    TaskStore(tmp_path, request_id="R-list-archive").set_visibility(task, 1, "codex", "archived")
    assert TaskStore(tmp_path).list() == []
    assert TaskStore(tmp_path).list(include_archived=True)[0]["task_id"] == task


def test_public_policy_and_approval_commands_use_typed_action_descriptor(tmp_path: Path) -> None:
    home = tmp_path / "approval-home"
    repository = tmp_path / "repository"
    repository.mkdir()
    task = TaskStore(home).create(title="Approval task", objective="review an exact action", target_repository=str(repository), actor="test")["task_id"]
    action = {
        "action_id": "approval-action-001",
        "task_id": task,
        "kind": "git.local",
        "repository_root": str(repository),
        "targets": [str(repository)],
        "command": ["worktree", "remove", "--force"],
        "expected_state_revision": 1,
        "request_id": "ACTION-request-001",
        "idempotency_key": "ACTION-idempotency-001",
        "expected_effect": "remove a task-owned worktree after exact approval",
        "plan_ref": "cycles/0001/01-plan-result.json",
        "plan_content_hash": "a" * 64,
    }
    policy_request = tmp_path / "policy.json"
    policy_request.write_text(json.dumps({"action": action}))
    policy = json.loads(subprocess.check_output([sys.executable, "-m", "agent_nous.cli", "--nous-home", str(home), "--input", str(policy_request), "taskctl", "policy-evaluate"], text=True))
    assert policy["result"]["outcome"] == "approval_required"
    request = tmp_path / "approval.json"
    request.write_text(json.dumps({"contract_version": 1, "request_id": "R-approval-request", "expected_revision": 1, "action": action, "actor": {"type": "orchestrator", "run_id": "RUN-approval", "actor_id": "codex"}}))
    approval = json.loads(subprocess.check_output([sys.executable, "-m", "agent_nous.cli", "--nous-home", str(home), "--input", str(request), "taskctl", "approval-request", task], text=True))
    assert approval["ok"] is True
    assert approval["result"]["approval"]["status"] == "requested"
    listed = json.loads(subprocess.check_output([sys.executable, "-m", "agent_nous.cli", "--nous-home", str(home), "taskctl", "approval-list", task], text=True))
    assert listed["result"]["approvals"][0]["approval_id"] == approval["result"]["approval"]["approval_id"]


def test_public_action_start_serializes_policy_decision(tmp_path: Path) -> None:
    home = tmp_path / "action-home"
    repository = tmp_path / "repository"
    repository.mkdir()
    task = TaskStore(home).create(title="Action task", objective="exercise action start", target_repository=str(repository), actor="test")["task_id"]
    lease = TaskStore(home).acquire_lease(task, 1, "worker")
    request = tmp_path / "action.json"
    request.write_text(json.dumps({
        "contract_version": 1,
        "request_id": "R-action-start",
        "expected_revision": lease["revision"],
        "action": {"action_id": "cli-action-001", "task_id": task, "kind": "git.remote", "repository_root": str(repository), "targets": [str(repository)], "command": ["push"], "expected_state_revision": lease["revision"], "request_id": "action-request", "idempotency_key": "action-idem", "expected_effect": "remote push"},
        "preflight": {"result": "pass", "facts": {}, "artifact_refs": [], "redaction_summary": {}},
        "actor": {"type": "worker", "run_id": "RUN-action", "actor_id": "worker"},
    }))
    command = [sys.executable, "-m", "agent_nous.cli", "--nous-home", str(home), "--input", str(request), "taskctl", "action-start", task]
    result = json.loads(subprocess.check_output(command, text=True))
    assert result["result"]["decision"]["outcome"] == "unsupported"


def test_public_parallel_scheduler_and_diagnose_contract(tmp_path: Path) -> None:
    home = tmp_path / "parallel-cli-home"
    task = make(TaskStore(home))
    contract_request = tmp_path / "child-create.json"
    contract_request.write_text(json.dumps({
        "contract_version": 1,
        "request_id": "R-child-create",
        "expected_revision": 1,
        "contract": {
            "loop_id": "cli-child",
            "objective": "exercise the public child scheduler",
            "scope": ["workspace://cli-child"],
            "dependencies": [],
            "resources": ["workspace://cli-child"],
            "write_scope": [],
            "acceptance_criteria": [{"criterion_id": "AC-cli-child", "statement": "child is inspected", "validation_method": "CLI contract test"}],
            "expected_outputs": ["child-result"],
            "validation_plan": ["public scheduler contract"],
            "preferred_backend": "sequential",
        },
        "actor": {"type": "orchestrator", "run_id": "RUN-child-create", "actor_id": "scheduler"},
    }))
    create = json.loads(subprocess.check_output([
        sys.executable, "-m", "agent_nous.cli", "--nous-home", str(home), "--input", str(contract_request), "taskctl", "child-create", task
    ], text=True))
    assert create["ok"] is True
    plan_request = tmp_path / "parallel-plan.json"
    plan_request.write_text(json.dumps({
        "contract_version": 1,
        "request_id": "R-parallel-plan",
        "expected_revision": create["result"]["state"]["revision"],
        "plan": {
            "mode": "sequential",
            "rationale": "public scheduler contract",
            "max_concurrency": 1,
            "max_depth": 1,
            "integration_strategy": "child review",
            "integration_validation": ["diagnose canonical state"],
            "knowledge_references": [],
        },
        "actor": {"type": "orchestrator", "run_id": "RUN-parallel-plan", "actor_id": "scheduler"},
    }))
    planned = json.loads(subprocess.check_output([
        sys.executable, "-m", "agent_nous.cli", "--nous-home", str(home), "--input", str(plan_request), "taskctl", "parallel-plan", task
    ], text=True))
    ready = json.loads(subprocess.check_output([
        sys.executable, "-m", "agent_nous.cli", "--nous-home", str(home), "taskctl", "parallel-ready", task
    ], text=True))
    assert ready["result"]["ready"][0]["loop_id"] == "cli-child"
    assert ready["result"]["diagnosis"]["repair_required"] is False
    start_request = tmp_path / "child-start.json"
    start_request.write_text(json.dumps({
        "contract_version": 1,
        "request_id": "R-child-start",
        "expected_revision": planned["result"]["state"]["revision"],
        "loop_id": "cli-child",
        "actor": {"type": "worker", "run_id": "RUN-child", "actor_id": "worker"},
    }))
    started = json.loads(subprocess.check_output([
        sys.executable, "-m", "agent_nous.cli", "--nous-home", str(home), "--input", str(start_request), "taskctl", "child-start", task
    ], text=True))
    released_request = tmp_path / "child-release.json"
    released_request.write_text(json.dumps({
        "contract_version": 1,
        "request_id": "R-child-release",
        "expected_revision": started["result"]["state"]["revision"],
        "loop_id": "cli-child",
        "lease_token": started["result"]["lease_token"],
        "reason": "public contract test complete",
        "actor": {"type": "worker", "run_id": "RUN-child", "actor_id": "worker"},
    }))
    released = json.loads(subprocess.check_output([
        sys.executable, "-m", "agent_nous.cli", "--nous-home", str(home), "--input", str(released_request), "taskctl", "child-release", task
    ], text=True))
    diagnosis = json.loads(subprocess.check_output([
        sys.executable, "-m", "agent_nous.cli", "--nous-home", str(home), "taskctl", "diagnose", task
    ], text=True))
    repair = json.loads(subprocess.check_output([
        sys.executable, "-m", "agent_nous.cli", "--nous-home", str(home), "taskctl", "repair-plan", task
    ], text=True))
    assert released["result"]["child_state"]["execution_status"] == "idle"
    assert diagnosis["result"]["repair_required"] is False
    assert repair["result"]["outcome"] == "no_repair_required"


def test_public_repair_apply_is_explicitly_fail_closed(tmp_path: Path) -> None:
    home = tmp_path / "repair-home"
    task = make(TaskStore(home))
    request = tmp_path / "repair-apply.json"
    request.write_text(json.dumps({
        "contract_version": 1,
        "request_id": "R-repair-apply",
        "expected_revision": 1,
        "actor": {"type": "orchestrator", "run_id": "RUN-repair", "actor_id": "repairer"},
    }))
    result = subprocess.run([
        sys.executable, "-m", "agent_nous.cli", "--nous-home", str(home), "--input", str(request), "taskctl", "repair-apply", task
    ], text=True, capture_output=True)
    assert result.returncode != 0
    assert json.loads(result.stdout)["error"]["code"] == "unsupported_action"


def test_public_mutating_taskctl_requires_typed_request_envelope(tmp_path: Path) -> None:
    home = tmp_path / "cli-home"
    request = tmp_path / "invalid.json"
    request.write_text(json.dumps({"title": "missing envelope", "objective": "reject"}))
    command = [sys.executable, "-m", "agent_nous.cli", "--nous-home", str(home), "--input", str(request), "taskctl", "create"]
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode != 0
    assert json.loads(result.stdout)["error"]["code"] == "validation_error"


def test_public_mutating_taskctl_rejects_unknown_contract_version(tmp_path: Path) -> None:
    home = tmp_path / "cli-home"
    request = tmp_path / "invalid-version.json"
    request.write_text(json.dumps({"contract_version": 2, "request_id": "R-version", "actor": {"type": "human", "run_id": "RUN-version", "actor_id": "human"}, "title": "reject version", "objective": "reject"}))
    command = [sys.executable, "-m", "agent_nous.cli", "--nous-home", str(home), "--input", str(request), "taskctl", "create"]
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode != 0
    assert json.loads(result.stdout)["error"]["code"] == "validation_error"


def test_public_taskctl_rejects_conflicting_flag_and_envelope(tmp_path: Path) -> None:
    home = tmp_path / "cli-home"
    request = tmp_path / "conflict.json"
    request.write_text(json.dumps({"contract_version": 1, "request_id": "R-flag-conflict", "actor": {"type": "human", "run_id": "RUN-flag", "actor_id": "human-1"}, "title": "from-envelope", "objective": "reject conflict"}))
    command = [sys.executable, "-m", "agent_nous.cli", "--nous-home", str(home), "--input", str(request), "taskctl", "create", "--title", "from-flag"]
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode != 0
    assert "title in CLI and input envelope must match" in json.loads(result.stdout)["error"]["message"]


def test_review_completion_requires_acceptance_and_validation_evidence(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    task = make(store)
    lease = store.acquire_lease(task, 1, "worker")
    with pytest.raises(ValidationError):
        store.record_phase(task, lease["revision"], "worker", lease["active_lease"]["token"], "plan", "plan", {}, "completed")
    # Complete the ordinary Plan and Implement phases using valid leases.
    plan = store.record_phase(task, lease["revision"], "worker", lease["active_lease"]["token"], "plan", "plan", {})
    implement_lease = store.acquire_lease(task, plan["state"]["revision"], "worker")
    implement = store.record_phase(task, implement_lease["revision"], "worker", implement_lease["active_lease"]["token"], "implement", "implement", {"no_managed_effects": True})
    review_lease = store.acquire_lease(task, implement["state"]["revision"], "worker")
    with pytest.raises(ValidationError):
        store.record_phase(task, review_lease["revision"], "worker", review_lease["active_lease"]["token"], "review", "review", {}, "completed")
    with pytest.raises(ValidationError):
        store.record_phase(task, review_lease["revision"], "worker", review_lease["active_lease"]["token"], "review", "review", {"acceptance_criteria": [{"criterion_id": "invented", "status": "passed", "evidence_refs": ["evidence://fake"]}], "validation_evidence": [{"kind": "test", "ref": "evidence://fake"}], "blocking_issues": []}, "completed")
    with pytest.raises(ValidationError):
        store.record_phase(task, review_lease["revision"], "worker", review_lease["active_lease"]["token"], "review", "review", {"acceptance_criteria": [{"criterion_id": "AC-001", "status": "passed", "evidence_refs": ["evidence://fixture/test"]}], "validation_evidence": [{"criterion_id": "AC-001", "kind": "test", "ref": "evidence://fixture/test", "outcome": "failed"}], "blocking_issues": []}, "completed")
    # Failed validation leaves the lease live until explicitly reconciled; it
    # may be released then re-acquired by a fresh worker.
    released = store.release_lease(task, review_lease["revision"], "worker", review_lease["active_lease"]["token"])
    review_lease = store.acquire_lease(task, released["revision"], "worker")
    final = store.record_phase(task, review_lease["revision"], "worker", review_lease["active_lease"]["token"], "review", "review", completion_outputs(), "completed")
    assert final["state"]["lifecycle_status"] == "completed"
    assert (tmp_path / "tasks" / task / "cycles/0001/03-review-decision.json").is_file()


def test_orchestrator_completes_only_after_review_with_evidence(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    task = make(store)
    workers = {
        "plan": PhaseResultDraft("plan", {}),
        "implement": PhaseResultDraft("implement", {"no_managed_effects": True}),
        "review": PhaseResultDraft("review", completion_outputs(), "completed"),
    }
    state = Orchestrator(store).run_sequential(task, workers)["state"]
    assert state["lifecycle_status"] == "completed"
    assert (tmp_path / "tasks" / task / "cycles/0001/03-review-result.json").is_file()


def test_implement_phase_rejects_claimed_changes_without_completed_managed_action(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "managed-action-refs")
    task = make(store)
    plan_lease = store.acquire_lease(task, 1, "worker")
    planned = store.record_phase(task, plan_lease["revision"], "worker", plan_lease["active_lease"]["token"], "plan", "plan", {})
    implement_lease = store.acquire_lease(task, planned["state"]["revision"], "worker")
    with pytest.raises(ValidationError):
        store.record_phase(task, implement_lease["revision"], "worker", implement_lease["active_lease"]["token"], "implement", "claimed write", {"changed_artifacts": ["result.txt"]})
