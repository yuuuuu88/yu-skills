from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import base64
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent_nous.domain import ActionDescriptor, HumanApprovalAuthority, PolicyGateway, approval_answer_message
from agent_nous.errors import ConflictError, UnsupportedActionError, ValidationError
from agent_nous.executor.managed import ManagedExecutor
from agent_nous.git_adapter import LocalGitWorktreeManager, remote_state
from agent_nous.knowledge import KnowledgeStore
from agent_nous.parallel import ChildContract, ParallelScheduler
from agent_nous.taskctl import TaskStore


def _task(store: TaskStore, root: Path | None = None) -> str:
    return store.create(
        title="Parallel durable fixture",
        objective="prove durable child isolation",
        target_repository=str(root) if root else None,
        actor="test",
    )["task_id"]


def _contract(loop_id: str, *, depends_on: tuple[str, ...] = (), backend: str = "sequential") -> dict[str, object]:
    resource = f"repo://fixture/{loop_id}"
    return {
        "loop_id": loop_id,
        "objective": f"complete child {loop_id}",
        "scope": [resource],
        "dependencies": list(depends_on),
        "resources": [resource],
        "write_scope": [resource],
        "acceptance_criteria": [
            {"criterion_id": f"AC-{loop_id}", "statement": f"child {loop_id} accepted", "validation_method": "independent review"}
        ],
        "expected_outputs": [f"output-{loop_id}"],
        "validation_plan": ["fixture child validation"],
        "preferred_backend": backend,
    }


def _worktree_contract(loop_id: str) -> dict[str, object]:
    """A file-precise Child contract used for real managed Git effects."""
    resource = "repo://fixture/child-only.txt"
    contract = _contract(loop_id, backend="worktree")
    contract.update(
        {
            "scope": [resource],
            "resources": [resource],
            "write_scope": [resource],
            "resource_contracts": [
                {
                    "resource_id": resource,
                    "kind": "file",
                    "locator": "child-only.txt",
                    "semantic_scope": {"namespace": "repo", "entity": "fixture-child-output", "facet": "content"},
                    "parent_resource_ref": None,
                    "aliases": ["repo://fixture/child-output"],
                    "version_ref": None,
                    "conflict_class": "exclusive",
                    "merge_strategy": "cherry-pick",
                    "sensitivity": "normal",
                }
            ],
            "resource_requests": [
                {
                    "resource_ref": resource,
                    "requested_mode": "write",
                    "input_version_ref": None,
                    "required_reservation": "exclusive_write",
                    "expected_change": "write the isolated child output",
                    "integration_strategy": "managed cherry-pick",
                }
            ],
            "allowed_actions": ["filesystem.read", "filesystem.write", "git.local"],
        }
    )
    return contract


def _review_outputs(criterion_id: str) -> dict[str, object]:
    return {
        "decision": "completed",
        "summary": "independent child review passed",
        "acceptance_criteria": [{"criterion_id": criterion_id, "status": "passed", "evidence_refs": [f"evidence://{criterion_id}"]}],
        "validation_evidence": [{"criterion_id": criterion_id, "kind": "test", "ref": f"evidence://{criterion_id}", "outcome": "passed"}],
        "blocking_issues": [],
        "no_managed_effects": True,
    }


def _init_repo(path: Path) -> str:
    path.mkdir()
    for command in (
        ("git", "init", str(path)),
        ("git", "-C", str(path), "config", "user.email", "fixture@example.invalid"),
        ("git", "-C", str(path), "config", "user.name", "Fixture"),
    ):
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (path / "fixture.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(path), "add", "."), check=True)
    subprocess.run(("git", "-C", str(path), "commit", "-m", "base"), check=True, stdout=subprocess.DEVNULL)
    return subprocess.check_output(("git", "-C", str(path), "rev-parse", "HEAD"), text=True).strip()


def _run_exactly_approved_root_git(
    store: TaskStore,
    task_id: str,
    repo: Path,
    *,
    action_id: str,
    command: tuple[str, ...],
    expected_effect: str,
    expected_returncode: int = 0,
) -> tuple[object, dict[str, object]]:
    """Exercise the real signed-Human gate for a destructive local cleanup."""
    state = store.read_task(task_id)[1]
    proposed = ActionDescriptor(
        action_id=action_id,
        task_id=task_id,
        kind="git.local",
        repository_root=str(repo),
        targets=(".",),
        command=command,
        declared_risk="red",
        expected_state_revision=state["revision"],
        plan_ref="cycles/0001/01-parallelization-plan.json",
        plan_content_hash="a" * 64,
        expected_effect=expected_effect,
    )
    requested = store.request_exact_approval(task_id, state["revision"], "cleanup-requester", proposed)
    answer: dict[str, object] = {
        "answer_type": "selected_option",
        "selected_option_id": "approve",
        "answer_text": None,
        "applied_scope": "action",
        "answer_effect": "resume",
        "resume_target": requested["question"]["resume_target"],
    }
    private = Ed25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode("utf-8")
    signature = private.sign(approval_answer_message(task_id, requested["question"]["question_id"], "fixture-human", answer))
    question_id = requested["question"]["question_id"]
    answered = TaskStore(store.home, HumanApprovalAuthority(public_pem)).answer_question(
        task_id,
        requested["state"]["revision"],
        "fixture-human",
        question_id,
        {**answer, "human_attestation": base64.b64encode(signature).decode("ascii")},
    )
    answer_id = next(
        path.stem
        for path in (store._path(task_id) / "human-input" / "answers").glob("*.json")
        if json.loads(path.read_text(encoding="utf-8"))["question_id"] == question_id
    )
    granted = TaskStore(store.home).grant_exact_approval(task_id, answered["revision"], "grant-boundary", requested["approval"]["approval_id"], answer_id)
    lease = TaskStore(store.home).acquire_lease(task_id, granted["state"]["revision"], "integrator")
    authorized = ActionDescriptor(
        action_id=action_id,
        task_id=task_id,
        kind="git.local",
        repository_root=str(repo),
        targets=(".",),
        command=command,
        declared_risk="red",
        expected_state_revision=lease["revision"],
        approval_id=requested["approval"]["approval_id"],
        plan_ref="cycles/0001/01-parallelization-plan.json",
        plan_content_hash="a" * 64,
        expected_effect=expected_effect,
    )
    result = ManagedExecutor(TaskStore(store.home), PolicyGateway(), store.home / "runtime", actor="integrator").run_local_git(authorized)
    assert result.returncode == expected_returncode, result.stderr
    released = TaskStore(store.home).release_lease(task_id, result.action_state_revision, "integrator", lease["active_lease"]["token"])
    return result, released


def test_knowledge_is_transactional_immutable_and_canonical(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "home")
    task_id = _task(store)
    source = {"source_id": "S-001", "title": "fixture", "locator": "fixture://source", "source_type": "test_result", "retrieved_at": "2026-07-19T00:00:00+00:00", "content_hash": "a" * 64}
    claim = {"claim_id": "K-001", "text": "fixture claim", "source_ids": ["S-001"], "epistemic_status": "confirmed", "lifecycle_status": "active", "checked_at": "2026-07-19T00:00:00+00:00"}
    committed = KnowledgeStore(store).record(task_id, 1, [source], [claim])
    assert committed["state"]["revision"] == 2
    task = tmp_path / "home" / "tasks" / task_id
    assert (task / "knowledge/questions.json").is_file()
    assert (task / "knowledge/sources.json").is_file()
    assert (task / "knowledge/claims.json").is_file()
    assert KnowledgeStore(TaskStore(tmp_path / "home")).active_claims(task_id)[0]["content_hash"]
    with pytest.raises(ConflictError):
        KnowledgeStore(TaskStore(tmp_path / "home")).record(task_id, 1, [], [])
    changed = {**claim, "text": "silently changed claim"}
    with pytest.raises(ConflictError):
        KnowledgeStore(TaskStore(tmp_path / "home")).record(task_id, 2, [], [changed])


def test_knowledge_supersession_and_high_impact_gate_are_durable(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "home")
    task_id = _task(store)
    question = {"question_id": "Q-001", "question": "Which contract applies?", "reason": "implementation depends on it", "impact": "high", "required_before": "plan_complete", "status": "open"}
    recorded = KnowledgeStore(store).record(task_id, 1, [], [], questions=[question])
    lease = store.acquire_lease(task_id, recorded["state"]["revision"], "planner")
    with pytest.raises(ValidationError):
        store.record_phase(task_id, lease["revision"], "planner", lease["active_lease"]["token"], "plan", "blocked plan", {})
    released = store.release_lease(task_id, lease["revision"], "planner", lease["active_lease"]["token"])
    source = {"source_id": "S-001", "title": "fixture evidence", "locator": "fixture://knowledge-gate", "source_type": "test_result", "retrieved_at": "2026-07-19T00:00:00+00:00", "content_hash": "b" * 64}
    claim = {"claim_id": "K-001", "claim": "The fixture source resolves the contract.", "kind": "fact", "epistemic_status": "verified", "lifecycle_status": "active", "scope": {}, "as_of": "2026-07-19T00:00:00+00:00", "supports": ["S-001"], "contradicts": [], "derived_from": [], "confidence": "high", "owner": "plan", "revalidate_when": None, "notes": "fixture", "record_revision": 1, "supersedes": []}
    answered = {**question, "question_id": "Q-002", "status": "answered", "supersedes": ["Q-001"], "record_revision": 2, "answer_evidence": [{"record_type": "source", "record_id": "S-001", "content_hash": TaskStore._normalize_knowledge_source(source)["content_hash"]}, {"record_type": "claim", "record_id": "K-001", "content_hash": TaskStore._knowledge_content_hash(claim)}]}
    updated = KnowledgeStore(store).record(task_id, released["revision"], [source], [claim], questions=[answered])
    plan_lease = store.acquire_lease(task_id, updated["state"]["revision"], "planner")
    planned = store.record_phase(task_id, plan_lease["revision"], "planner", plan_lease["active_lease"]["token"], "plan", "unblocked plan", {})
    assert planned["state"]["phase"] == "implement"


def test_high_impact_knowledge_question_cannot_bypass_gate_with_defer_or_unproven_answer(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "home")
    task_id = _task(store)
    question = {"question_id": "Q-001", "question": "Which contract applies?", "reason": "implementation depends on it", "impact": "high", "required_before": "plan_complete", "status": "open"}
    recorded = KnowledgeStore(store).record(task_id, 1, [], [], questions=[question])
    deferred = {**question, "question_id": "Q-002", "status": "deferred", "supersedes": ["Q-001"], "record_revision": 2}
    deferred_recorded = KnowledgeStore(store).record(task_id, recorded["state"]["revision"], [], [], questions=[deferred])
    lease = store.acquire_lease(task_id, deferred_recorded["state"]["revision"], "planner")
    with pytest.raises(ValidationError):
        store.record_phase(task_id, lease["revision"], "planner", lease["active_lease"]["token"], "plan", "deferred is not evidence", {})
    released = store.release_lease(task_id, lease["revision"], "planner", lease["active_lease"]["token"])
    ungrounded = {**question, "question_id": "Q-003", "status": "answered", "supersedes": ["Q-002"], "record_revision": 3}
    with pytest.raises(ValidationError):
        KnowledgeStore(store).record(task_id, released["revision"], [], [], questions=[ungrounded])


def test_persistent_reservation_survives_fresh_process_and_conflicts(tmp_path: Path) -> None:
    home = tmp_path / "home"
    store = TaskStore(home)
    task_id = _task(store)
    one = store.create_child_loop(task_id, 1, "parent", _contract("one"))
    second_contract = _contract("two")
    second_contract["scope"] = ["repo://fixture/one"]
    second_contract["resources"] = ["repo://fixture/one"]
    second_contract["write_scope"] = ["repo://fixture/one"]
    two = store.create_child_loop(task_id, one["state"]["revision"], "parent", second_contract)
    plan = store.record_parallel_plan(task_id, two["state"]["revision"], "parent", mode="sequential", rationale="overlapping contracts require reservation serialization", max_concurrency=1, max_depth=1, integration_strategy="fixture", integration_validation=["child review"])
    first = store.request_reservation(task_id, plan["state"]["revision"], "scheduler", loop_id="one", resource="repo://fixture/one", mode="exclusive_write")
    second = store.request_reservation(task_id, first["state"]["revision"], "scheduler", loop_id="two", resource="repo://fixture/one", mode="exclusive_write")
    acquired = TaskStore(home).acquire_reservation(task_id, second["state"]["revision"], "scheduler", reservation_id=first["reservation"]["reservation_id"], lease_seconds=60)
    assert acquired["reservation"]["status"] == "held"
    conflicted = TaskStore(home).acquire_reservation(task_id, acquired["state"]["revision"], "scheduler", reservation_id=second["reservation"]["reservation_id"], lease_seconds=60)
    assert conflicted["outcome"] == "conflicted"
    script = """
import json, sys
from pathlib import Path
from agent_nous.taskctl import TaskStore
home=Path(sys.argv[1]); task=sys.argv[2]
store=TaskStore(home)
print(json.dumps(store.read_task(task)[1], sort_keys=True))
"""
    fresh = subprocess.check_output((sys.executable, "-c", script, str(home), task_id), text=True)
    assert json.loads(fresh)["revision"] == conflicted["state"]["revision"]
    with pytest.raises(ValidationError):
        TaskStore(home).release_reservation(task_id, conflicted["state"]["revision"], "scheduler", reservation_id=first["reservation"]["reservation_id"], lease_token="wrong", reason="wrong owner")


def test_semantic_resources_enforce_directory_containment_and_alias_conflicts(tmp_path: Path) -> None:
    directory = {
        "resource_id": "repo://fixture/allowed",
        "kind": "directory",
        "locator": "allowed",
        "semantic_scope": {"namespace": "repo", "entity": "allowed-area", "facet": "content"},
        "parent_resource_ref": None,
        "aliases": ["repo://semantic/shared"],
        "version_ref": None,
        "conflict_class": "exclusive",
        "merge_strategy": "sequential",
        "sensitivity": "normal",
    }
    assert TaskStore._resource_matches_child_target(directory, "allowed/nested.txt")
    assert not TaskStore._resource_matches_child_target(directory, "allowed-sibling.txt")
    store = TaskStore(tmp_path / "home")
    task_id = _task(store)
    def contract(loop_id: str, resource: dict[str, object]) -> dict[str, object]:
        value = _contract(loop_id)
        resource_id = str(resource["resource_id"])
        value.update(
            {
                "scope": [resource_id],
                "resources": [resource_id],
                "write_scope": [resource_id],
                "resource_contracts": [resource],
                "resource_requests": [{"resource_ref": resource_id, "requested_mode": "write", "input_version_ref": None, "required_reservation": "exclusive_write", "expected_change": "fixture write", "integration_strategy": "sequential"}],
            }
        )
        return value
    first = store.create_child_loop(task_id, 1, "parent", contract("one", directory))
    other = {**directory, "resource_id": "repo://fixture/other", "locator": "other", "semantic_scope": {"namespace": "repo", "entity": "other-area", "facet": "content"}}
    second = store.create_child_loop(task_id, first["state"]["revision"], "parent", contract("two", other))
    with pytest.raises(ValidationError):
        store.record_parallel_plan(task_id, second["state"]["revision"], "parent", mode="parallel", rationale="shared semantic alias is exclusive", max_concurrency=2, max_depth=1, integration_strategy="fixture", integration_validation=["review"])


def test_child_integration_is_the_only_parallel_root_completion_path(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "home")
    task_id = _task(store)
    first = store.create_child_loop(task_id, 1, "parent", _contract("a"))
    second = store.create_child_loop(task_id, first["state"]["revision"], "parent", _contract("b", depends_on=("a",)))
    planned = store.record_parallel_plan(task_id, second["state"]["revision"], "parent", mode="parallel", rationale="DAG fixture", max_concurrency=2, max_depth=1, integration_strategy="ordered patch", integration_validation=["combined validation"])
    scheduler = ParallelScheduler(store)
    assert [child["loop_id"] for child in scheduler.ready(task_id)] == ["a"]
    # These fixture children have no write scope after the immutable contract
    # is revised to read-only execution, so a child lease is sufficient.
    read_only_a = {**_contract("a"), "write_scope": []}
    revised_a = store.revise_child_contract(task_id, planned["state"]["revision"], "parent", "a", read_only_a)
    read_only_b = {**_contract("b", depends_on=("a",)), "write_scope": []}
    revised_b = store.revise_child_contract(task_id, revised_a["state"]["revision"], "parent", "b", read_only_b)
    # Revising active contracts invalidates the old plan; record an updated
    # DAG before a Child lease can be acquired.
    replanned = store.record_parallel_plan(task_id, revised_b["state"]["revision"], "parent", mode="parallel", rationale="read-only DAG fixture", max_concurrency=2, max_depth=1, integration_strategy="ordered patch", integration_validation=["combined validation"])
    child_a = store.start_child_loop(task_id, replanned["state"]["revision"], "child-a", loop_id="a")
    a = store.record_child_result(task_id, child_a["state"]["revision"], "child-a", loop_id="a", lease_token=child_a["lease_token"], outputs=_review_outputs("AC-a"))
    assert [child["loop_id"] for child in scheduler.ready(task_id)] == ["b"]
    child_b = store.start_child_loop(task_id, a["state"]["revision"], "child-b", loop_id="b")
    b = store.record_child_result(task_id, child_b["state"]["revision"], "child-b", loop_id="b", lease_token=child_b["lease_token"], outputs=_review_outputs("AC-b"))
    assert b["state"]["phase_kind"] == "integration"
    lease = store.acquire_lease(task_id, b["state"]["revision"], "wrong-completer")
    with pytest.raises(ValidationError):
        store.record_phase(task_id, lease["revision"], "wrong-completer", lease["active_lease"]["token"], "review", "ordinary review", {"acceptance_criteria": [{"criterion_id": "AC-001", "status": "passed", "evidence_refs": ["evidence://root"]}], "validation_evidence": [{"criterion_id": "AC-001", "kind": "test", "ref": "evidence://root", "outcome": "passed"}], "blocking_issues": []}, "completed")
    released = store.release_lease(task_id, lease["revision"], "wrong-completer", lease["active_lease"]["token"])
    final = store.record_integration_review(task_id, released["revision"], "parent", outputs={"accepted_children": ["a", "b"], "integrated_outputs": [], "cleanup_actions": [], "acceptance_criteria": [{"criterion_id": "AC-001", "status": "passed", "evidence_refs": ["evidence://root"]}], "validation_evidence": [{"criterion_id": "AC-001", "kind": "test", "ref": "evidence://root", "outcome": "passed"}], "integration_validation": ["combined validation passed"], "blocking_issues": []})
    assert final["state"]["lifecycle_status"] == "completed"


def test_child_lease_expiry_is_recovered_by_a_fresh_store_before_restart(tmp_path: Path) -> None:
    home = tmp_path / "home"
    store = TaskStore(home)
    task_id = _task(store)
    child = store.create_child_loop(task_id, 1, "parent", {**_contract("recover"), "write_scope": []})
    plan = store.record_parallel_plan(task_id, child["state"]["revision"], "parent", mode="sequential", rationale="child recovery fixture", max_concurrency=1, max_depth=0, integration_strategy="result", integration_validation=["child checkpoint"])
    started = store.start_child_loop(task_id, plan["state"]["revision"], "worker", loop_id="recover", lease_seconds=1)
    time.sleep(2.0)
    recovered = TaskStore(home).recover_expired_child_leases(task_id)
    assert recovered and recovered["expired_loop_ids"] == ["recover"]
    child_state = json.loads((home / "tasks" / task_id / "loops" / "recover" / "state.json").read_text(encoding="utf-8"))
    assert child_state["execution_status"] == "interrupted"
    restarted = TaskStore(home).start_child_loop(task_id, recovered["state"]["revision"], "fresh-worker", loop_id="recover")
    assert restarted["child_state"]["execution_status"] == "running"


def test_managed_worktree_is_isolated_and_bound_to_child_contract(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    base = _init_repo(repo)
    store = TaskStore(tmp_path / "home")
    task_id = _task(store, repo)
    child = store.create_child_loop(task_id, 1, "parent", _worktree_contract("write-child"))
    planned = store.record_parallel_plan(task_id, child["state"]["revision"], "parent", mode="parallel", rationale="isolated Git write", max_concurrency=1, max_depth=1, integration_strategy="patch", integration_validation=["Git diff"])
    lease = store.acquire_lease(task_id, planned["state"]["revision"], "worker")
    executor = ManagedExecutor(store, PolicyGateway(), store.home / "runtime", actor="worker")
    manager = LocalGitWorktreeManager(store, executor)
    created = manager.create_child_worktree(task_id, lease["revision"], "worker", lease["active_lease"]["token"], loop_id="write-child", repository_root=str(repo), branch="nous-child-write", base_commit=base, action_id="child-worktree-add")
    assert created["result"].returncode == 0
    worktree = Path(created["worktree"]["worktree"]["worktree_path"])
    assert worktree != repo and (worktree / "fixture.txt").read_text(encoding="utf-8") == "base\n"
    parent = store.release_lease(task_id, created["worktree"]["state"]["revision"], "worker", lease["active_lease"]["token"])
    requested = store.request_reservation(task_id, parent["revision"], "child-worker", loop_id="write-child", resource="repo://fixture/child-only.txt", mode="exclusive_write")
    held = store.acquire_reservation(task_id, requested["state"]["revision"], "child-worker", reservation_id=requested["reservation"]["reservation_id"])
    child = store.start_child_loop(task_id, held["state"]["revision"], "child-worker", loop_id="write-child")
    confused_root = ActionDescriptor(action_id="child-root-confusion", task_id=task_id, kind="filesystem.write", repository_root=str(repo), targets=("child-only.txt",), declared_risk="green", loop_id="write-child", expected_state_revision=child["state"]["revision"], reservation_ids=(requested["reservation"]["reservation_id"],), resource_ids=("repo://fixture/child-only.txt",))
    with pytest.raises(ValidationError):
        ManagedExecutor(store, PolicyGateway(), store.home / "runtime", actor="child-worker").write_text(confused_root, "must not write parent\n")
    missing_reservation = ActionDescriptor(action_id="child-missing-reservation", task_id=task_id, kind="filesystem.write", repository_root=str(worktree), targets=("child-only.txt",), declared_risk="green", loop_id="write-child", expected_state_revision=child["state"]["revision"], resource_ids=("repo://fixture/child-only.txt",))
    with pytest.raises(ValidationError):
        ManagedExecutor(store, PolicyGateway(), store.home / "runtime", actor="child-worker").write_text(missing_reservation, "must not bypass reservation\n")
    action = ActionDescriptor(
        action_id="child-managed-write",
        task_id=task_id,
        kind="filesystem.write",
        repository_root=str(worktree),
        targets=("child-only.txt",),
        declared_risk="green",
        loop_id="write-child",
        expected_state_revision=child["state"]["revision"],
        reservation_ids=(requested["reservation"]["reservation_id"],),
        resource_ids=("repo://fixture/child-only.txt",),
    )
    effect = ManagedExecutor(store, PolicyGateway(), store.home / "runtime", actor="child-worker").write_text(action, "isolated\n")
    assert effect.returncode == 0
    assert not (repo / "child-only.txt").exists()
    out_of_scope = ActionDescriptor(action_id="child-undeclared-target", task_id=task_id, kind="filesystem.write", repository_root=str(worktree), targets=("undeclared.txt",), declared_risk="green", loop_id="write-child", expected_state_revision=effect.action_state_revision, reservation_ids=(requested["reservation"]["reservation_id"],), resource_ids=("repo://fixture/child-only.txt",))
    with pytest.raises(ValidationError):
        ManagedExecutor(store, PolicyGateway(), store.home / "runtime", actor="child-worker").write_text(out_of_scope, "must not escape semantic resource\n")
    git = ManagedExecutor(store, PolicyGateway(), store.home / "runtime", actor="child-worker")
    with pytest.raises(UnsupportedActionError):
        git.run_local_git(ActionDescriptor(action_id="child-git-add-too-wide", task_id=task_id, kind="git.local", repository_root=str(worktree), targets=("child-only.txt",), command=("add", "--all"), declared_risk="yellow", loop_id="write-child", expected_state_revision=effect.action_state_revision, reservation_ids=(requested["reservation"]["reservation_id"],), resource_ids=("repo://fixture/child-only.txt",)))
    with pytest.raises(UnsupportedActionError):
        git.run_local_git(ActionDescriptor(action_id="child-git-commit-stages-too-wide", task_id=task_id, kind="git.local", repository_root=str(worktree), targets=("child-only.txt",), command=("commit", "--all", "-m", "must reject post-probe staging"), declared_risk="yellow", loop_id="write-child", expected_state_revision=effect.action_state_revision, reservation_ids=(requested["reservation"]["reservation_id"],), resource_ids=("repo://fixture/child-only.txt",)))
    # Simulate an attempted raw bypass already present in Git's index.  The
    # managed commit must inspect staged paths and refuse to commit it.
    (worktree / "undeclared-staged.txt").write_text("adversarial fixture\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(worktree), "add", "--", "undeclared-staged.txt"), check=True)
    blocked_commit = git.run_local_git(ActionDescriptor(action_id="child-git-commit-undeclared", task_id=task_id, kind="git.local", repository_root=str(worktree), targets=("child-only.txt",), command=("commit", "-m", "must reject undeclared staged file"), declared_risk="yellow", loop_id="write-child", expected_state_revision=effect.action_state_revision, reservation_ids=(requested["reservation"]["reservation_id"],), resource_ids=("repo://fixture/child-only.txt",)))
    assert blocked_commit.returncode == 73
    subprocess.run(("git", "-C", str(worktree), "reset", "HEAD", "--", "undeclared-staged.txt"), check=True, stdout=subprocess.DEVNULL)
    (worktree / "undeclared-staged.txt").unlink()
    add = git.run_local_git(ActionDescriptor(action_id="child-git-add", task_id=task_id, kind="git.local", repository_root=str(worktree), targets=("child-only.txt",), command=("add", "--", "child-only.txt"), declared_risk="yellow", loop_id="write-child", expected_state_revision=blocked_commit.action_state_revision, reservation_ids=(requested["reservation"]["reservation_id"],), resource_ids=("repo://fixture/child-only.txt",)))
    assert add.returncode == 0
    commit = git.run_local_git(ActionDescriptor(action_id="child-git-commit", task_id=task_id, kind="git.local", repository_root=str(worktree), targets=("child-only.txt",), command=("commit", "-m", "managed child change"), declared_risk="yellow", loop_id="write-child", expected_state_revision=add.action_state_revision, reservation_ids=(requested["reservation"]["reservation_id"],), resource_ids=("repo://fixture/child-only.txt",)))
    assert commit.returncode == 0
    commit_record = json.loads((store._path(task_id) / "actions" / "child-git-commit.json").read_text(encoding="utf-8"))
    commit_evidence = json.loads((store._path(task_id) / commit_record["after_evidence_ref"]).read_text(encoding="utf-8"))
    child_commit = commit_evidence["facts"]["after_head"]
    result_outputs = _review_outputs("AC-write-child")
    result_outputs.pop("no_managed_effects")
    result_outputs.update({"managed_action_refs": ["child-managed-write", "child-git-add", "child-git-commit"], "durability": {"kind": "local_commit", "commit": child_commit}})
    child_result = store.record_child_result(task_id, commit.action_state_revision, "child-worker", loop_id="write-child", lease_token=child["lease_token"], outputs=result_outputs)
    root_lease = store.acquire_lease(task_id, child_result["state"]["revision"], "integrator")
    integrated = ManagedExecutor(store, PolicyGateway(), store.home / "runtime", actor="integrator").run_local_git(ActionDescriptor(action_id="integration-cherry-pick", task_id=task_id, kind="git.local", repository_root=str(repo), targets=(".",), command=("cherry-pick", child_commit), declared_risk="yellow", expected_state_revision=root_lease["revision"]))
    assert integrated.returncode == 0
    root_idle = store.release_lease(task_id, integrated.action_state_revision, "integrator", root_lease["active_lease"]["token"])
    review_outputs = {"accepted_children": ["write-child"], "integrated_outputs": [{"loop_id": "write-child", "child_commit": child_commit, "integration_action_id": "integration-cherry-pick"}], "acceptance_criteria": [{"criterion_id": "AC-001", "status": "passed", "evidence_refs": ["evidence://root"]}], "validation_evidence": [{"criterion_id": "AC-001", "kind": "test", "ref": "evidence://root", "outcome": "passed"}], "integration_validation": ["managed cherry-pick and validation passed"], "blocking_issues": []}
    # A known-but-unremoved worktree is still an orphan from the completed
    # Root's point of view; canonical Integration Review fails closed until
    # managed cleanup evidence proves both worktree and branch are gone.
    with pytest.raises(ValidationError):
        store.record_integration_review(task_id, root_idle["revision"], "integrator", outputs={**review_outputs, "cleanup_actions": []})
    # An unintegrated late mutation makes the target worktree dirty.  Even a
    # signed exact cleanup request cannot turn force-removal into authority to
    # discard it; the managed preflight blocks the effect and root completion.
    (worktree / "late-dirty.txt").write_text("must block cleanup\n", encoding="utf-8")
    _blocked_cleanup, after_blocked_cleanup = _run_exactly_approved_root_git(
        store,
        task_id,
        repo,
        action_id="cleanup-dirty-worktree-rejected",
        command=("worktree", "remove", "--force", str(worktree)),
        expected_effect="attempt cleanup only if the child worktree is clean",
        expected_returncode=73,
    )
    assert worktree.is_dir()
    (worktree / "late-dirty.txt").unlink()
    _removed, after_remove = _run_exactly_approved_root_git(
        TaskStore(store.home),
        task_id,
        repo,
        action_id="cleanup-worktree-remove",
        command=("worktree", "remove", "--force", str(worktree)),
        expected_effect="remove the task-owned isolated child worktree",
    )
    _branch_deleted, root_idle = _run_exactly_approved_root_git(
        TaskStore(store.home),
        task_id,
        repo,
        action_id="cleanup-child-branch-delete",
        command=("branch", "-D", "nous-child-write"),
        expected_effect="delete the integrated task-owned child branch",
    )
    completed = TaskStore(store.home).record_integration_review(task_id, root_idle["revision"], "integrator", outputs={**review_outputs, "cleanup_actions": [{"loop_id": "write-child", "worktree_remove_action_id": "cleanup-worktree-remove", "branch_delete_action_id": "cleanup-child-branch-delete"}]})
    assert completed["state"]["lifecycle_status"] == "completed"
    assert (repo / "child-only.txt").read_text(encoding="utf-8") == "isolated\n"
    assert not worktree.exists()
    action_journal = (store._path(task_id) / "events.jsonl").read_text(encoding="utf-8")
    assert "action_started" in action_journal and "child_worktree_bound" in action_journal


def test_local_bare_fixture_proves_cross_clone_divergence_and_fresh_process_recovery(tmp_path: Path) -> None:
    """Git durability coverage is deliberately isolated from every real remote."""
    bare = tmp_path / "fixture.git"
    subprocess.run(("git", "init", "--bare", str(bare)), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    seed = tmp_path / "seed"
    _init_repo(seed)
    branch = subprocess.check_output(("git", "-C", str(seed), "branch", "--show-current"), text=True).strip()
    subprocess.run(("git", "-C", str(seed), "remote", "add", "origin", str(bare)), check=True)
    subprocess.run(("git", "-C", str(seed), "push", "-u", "origin", branch), check=True, stdout=subprocess.DEVNULL)
    first = tmp_path / "clone-first"
    second = tmp_path / "clone-second"
    subprocess.run(("git", "clone", str(bare), str(first)), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(("git", "clone", str(bare), str(second)), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for clone in (first, second):
        subprocess.run(("git", "-C", str(clone), "config", "user.email", "fixture@example.invalid"), check=True)
        subprocess.run(("git", "-C", str(clone), "config", "user.name", "Fixture"), check=True)
    home = tmp_path / "home"
    store = TaskStore(home)
    task_id = _task(store, first)
    lease = store.acquire_lease(task_id, 1, "durability-worker")
    executor = ManagedExecutor(store, PolicyGateway(), home / "runtime", actor="durability-worker")
    written = executor.write_text(
        ActionDescriptor(
            action_id="durability-managed-write",
            task_id=task_id,
            kind="filesystem.write",
            repository_root=str(first),
            targets=("first.txt",),
            declared_risk="green",
            expected_state_revision=lease["revision"],
        ),
        "managed local commit\n",
    )
    added = executor.run_local_git(
        ActionDescriptor(
            action_id="durability-managed-add",
            task_id=task_id,
            kind="git.local",
            repository_root=str(first),
            targets=("first.txt",),
            command=("add", "--", "first.txt"),
            declared_risk="yellow",
            expected_state_revision=written.action_state_revision,
        )
    )
    committed = executor.run_local_git(
        ActionDescriptor(
            action_id="durability-managed-commit",
            task_id=task_id,
            kind="git.local",
            repository_root=str(first),
            targets=("first.txt",),
            command=("commit", "-m", "managed local durability"),
            declared_risk="yellow",
            expected_state_revision=added.action_state_revision,
        )
    )
    assert committed.returncode == 0
    commit_record = json.loads((store._path(task_id) / "actions" / "durability-managed-commit.json").read_text(encoding="utf-8"))
    committed_head = json.loads((store._path(task_id) / commit_record["after_evidence_ref"]).read_text(encoding="utf-8"))["facts"]["after_head"]
    idle = store.release_lease(task_id, committed.action_state_revision, "durability-worker", lease["active_lease"]["token"])
    # The product path above never pushes or fetches.  This local-bare-only
    # fixture performs a read-only relation probe, then a separate Python
    # process recovers the completed managed commit into canonical durability
    # state with no in-memory executor/lease data.
    ahead = remote_state(task_id, first, "origin", branch)
    assert ahead["status"] == "ahead" and ahead["artifact_commit"] == committed_head
    ahead_path = tmp_path / "ahead.json"
    ahead_path.write_text(json.dumps(ahead), encoding="utf-8")
    recover = """
import json, sys
from pathlib import Path
from agent_nous.taskctl import TaskStore
home=Path(sys.argv[1]); task=sys.argv[2]; observation=json.loads(Path(sys.argv[3]).read_text())
store=TaskStore(home)
before=store.read_task(task)[1]
action=json.loads((home / 'tasks' / task / 'actions' / 'durability-managed-commit.json').read_text())
result=store.record_git_durability_observation(task, before['revision'], 'fresh-durability-recovery', observation)
print(json.dumps({'sync': result['state']['sync_status'], 'durability': result['state']['target_artifact_durability'], 'action_status': action['status'], 'head': action['action']['action_id']}, sort_keys=True))
"""
    recovered = json.loads(subprocess.check_output((sys.executable, "-c", recover, str(home), task_id, str(ahead_path)), text=True))
    assert recovered == {"action_status": "completed", "durability": "local_commit", "head": "durability-managed-commit", "sync": "push_pending"}
    assert idle["revision"] < TaskStore(home).read_task(task_id)[1]["revision"]
    (second / "second.txt").write_text("second\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(second), "add", "."), check=True)
    subprocess.run(("git", "-C", str(second), "commit", "-m", "second"), check=True, stdout=subprocess.DEVNULL)
    # This is fixture provisioning of an independent clone's external change,
    # never a nous managed product push.
    subprocess.run(("git", "-C", str(second), "push", "origin", branch), check=True, stdout=subprocess.DEVNULL)
    subprocess.run(("git", "-C", str(second), "fetch", "origin"), check=True, stdout=subprocess.DEVNULL)
    subprocess.run(("git", "-C", str(first), "fetch", "origin"), check=True, stdout=subprocess.DEVNULL)
    state = remote_state(task_id, first, "origin", branch)
    assert state["status"] == "diverged"
    observation = tmp_path / "divergence.json"
    observation.write_text(json.dumps(state), encoding="utf-8")
    mutation = """
import json, sys
from pathlib import Path
from agent_nous.taskctl import TaskStore
home=Path(sys.argv[1]); task=sys.argv[2]; observation=json.loads(Path(sys.argv[3]).read_text())
store=TaskStore(home)
result=store.record_git_durability_observation(task, store.read_task(task)[1]['revision'], 'fresh-durability-probe', observation)
print(json.dumps(result['state'], sort_keys=True))
"""
    durable = json.loads(subprocess.check_output((sys.executable, "-c", mutation, str(home), task_id, str(observation)), text=True))
    assert durable["sync_status"] == "conflicted"
    conflicted_state = TaskStore(home).read_task(task_id)[1]
    with pytest.raises(ConflictError):
        TaskStore(home).acquire_lease(task_id, conflicted_state["revision"], "must-not-continue")
    reconciled = TaskStore(home).record_git_durability_reconciliation(
        task_id,
        conflicted_state["revision"],
        "fresh-durability-reconciler",
        outcome="replan_required",
        rationale="isolated local-bare fixture diverged from the independently updated clone",
    )
    assert reconciled["state"]["lifecycle_status"] == "blocked"
    remote_action = ActionDescriptor(action_id="fixture-remote-denied", task_id=task_id, kind="git.remote", repository_root=str(first), targets=(".",), command=("push", "origin", branch), declared_risk="red")
    assert PolicyGateway().evaluate(remote_action).outcome == "unsupported"
    assert str(bare) not in os.environ.get("GIT_DIR", "")
