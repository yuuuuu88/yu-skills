from __future__ import annotations

import json
import hashlib
import hmac
from pathlib import Path, PurePosixPath
from typing import Any

from ..domain import secret_summary
from ..io import digest_json


def events_for(task_root: Path) -> list[dict[str, Any]]:
    path = task_root / "events.jsonl"
    if not path.exists():
        return []
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError):
        return []


def grade_case(*, case: dict[str, Any], observed: dict[str, Any], trial_root: Path, attestation_key: bytes | None = None) -> dict[str, Any]:
    """Grade parent-retained evidence, never a handler's assertions alone.

    Worker stdout is never accepted as an observation.  Every assertion is
    rebuilt by the parent from canonical state, event, action, probe, and
    artifact files below the isolated trial home.
    """
    failures: list[str] = []
    grader = case["grader"]
    captured = observed
    _verify_parent_attestation(trial_root, captured, attestation_key, failures)
    validation_failures = captured.get("validation_failures")
    if validation_failures is None and isinstance(captured.get("state"), dict):
        validation_failures = captured["state"].get("validation_failures")
    if not isinstance(validation_failures, list):
        failures.append("canonical_validation_evidence_missing")
    elif validation_failures:
        failures.extend(f"canonical_validation_failed:{item}" for item in validation_failures)
    task_root = _canonical_task_root(trial_root, captured, failures)
    state = _canonical_state(captured, task_root.name if task_root else None)
    events = _canonical_events(captured, task_root.name if task_root else None)
    types = [str(event.get("type")) for event in events if isinstance(event, dict)]

    expected_state = grader["expected_state"]
    if expected_state:
        if not state:
            failures.append("canonical_state_missing")
        else:
            for key, value in expected_state.items():
                if state.get(key) != value:
                    failures.append(f"state.{key}_mismatch")
    _verify_event_order(types, grader["expected_events"], failures)
    for forbidden in grader["forbidden_events"]:
        if forbidden in types:
            failures.append(f"forbidden_event_present:{forbidden}")

    _verify_human_questions(bool(grader["expected_human_question"]), task_root, types, failures)
    _verify_artifacts(case, grader, trial_root, task_root, failures)

    action_records = _canonical_actions(captured, task_root.name if task_root else None)
    decisions = {str(item.get("policy_outcome")) for item in action_records}
    # Exact-approval requests are a canonical policy interruption rather than
    # an executable Action record.  Derive the decision from the journal, not
    # from a handler-provided label.
    if "approval_requested" in types:
        decisions.add("approval_required")
    kinds = {str(item.get("kind")) for item in action_records}
    # Pure Gateway fixtures have no durable Action by design.  Their parent
    # capture is still immutable after worker exit, unlike a direct argument
    # passed to this grader.  Never merge handler values for task cases.
    if not task_root:
        decisions |= {str(item) for item in captured.get("policy_decisions", [])}
        kinds |= {str(item) for item in captured.get("action_kinds", [])}
    for decision in grader["expected_policy_decisions"]:
        if decision not in decisions:
            failures.append(f"policy_decision_missing:{decision}")
    for kind in grader["expected_action_descriptors"]:
        if kind not in kinds:
            failures.append(f"action_kind_missing:{kind}")
    failures.extend(_verify_action_journal(action_records, events, task_root))

    changed_paths = captured.get("diff", {}).get("changed_paths") if isinstance(captured.get("diff"), dict) else captured.get("changed_paths")
    failures.extend(_verify_allowed_writes(grader["allowed_write_resources"], changed_paths))
    diff = captured.get("diff") if isinstance(captured.get("diff"), dict) else {}
    if diff.get("out_of_trial_paths"):
        failures.append("trial_write_boundary_violation")
    _verify_isolation(trial_root, captured, grader, failures)
    if grader["no_secret_leak"] and _has_secret_leak(trial_root):
        failures.append("secret_like_value_in_trace_or_artifact")
    if case["category"] == "parallel":
        failures.extend(_verify_parallel_evidence(task_root, captured))
    if case["eval_id"] == "remote_cross_machine_resume":
        failures.extend(_verify_cross_clone_evidence(task_root, captured, trial_root))
    if case["eval_id"] == "parallel_interrupt_resume":
        failures.extend(_verify_parallel_recovery_evidence(task_root, captured))
    return {"passed": not failures, "failure_codes": sorted(set(failures))}


def _verify_parent_attestation(trial_root: Path, captured: dict[str, Any], key: bytes | None, failures: list[str]) -> None:
    """Require a parent-only signed snapshot before grading any trial.

    The key is held by the parent runner outside the worker-writeable trial
    root.  Direct grader callers and forged worker snapshots therefore fail
    closed instead of being treated as release evidence.
    """
    if key is None:
        failures.append("parent_evidence_attestation_missing")
        return
    evidence_root = trial_root.parents[1] / "evidence" / trial_root.name
    attestation = _read_json(evidence_root / "parent-attestation.json")
    canonical = _read_json(evidence_root / "canonical.json")
    provenance = captured.get("provenance") if isinstance(captured.get("provenance"), dict) else {}
    if not attestation or not canonical or not provenance:
        failures.append("parent_evidence_attestation_missing")
        return
    unsigned = {name: value for name, value in attestation.items() if name != "signature"}
    signature = attestation.get("signature")
    expected = hmac.new(
        key,
        json.dumps(unsigned, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        failures.append("parent_evidence_attestation_invalid")
        return
    if (
        attestation.get("run_id") != trial_root.parents[1].name
        or attestation.get("trial_id") != trial_root.name
        or attestation.get("canonical_digest") != digest_json(canonical)
        or provenance.get("canonical_digest") != attestation.get("canonical_digest")
        or provenance.get("attestation_digest") != digest_json(attestation)
    ):
        failures.append("parent_evidence_attestation_binding_invalid")
        return
    digests = attestation.get("evidence_digests")
    if not isinstance(digests, dict):
        failures.append("parent_evidence_digest_manifest_missing")
        return
    for name, expected_digest in digests.items():
        path = evidence_root / str(name)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected_digest:
            failures.append(f"parent_evidence_digest_mismatch:{name}")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _canonical_task_root(trial_root: Path, captured: dict[str, Any], failures: list[str]) -> Path | None:
    tasks = trial_root / "nous-home" / "tasks"
    candidates = sorted(path for path in tasks.iterdir() if path.is_dir() and not path.is_symlink()) if tasks.is_dir() else []
    task_ids = captured.get("task_ids")
    if task_ids is None:
        # Legacy direct-grader tests deliberately model forged input.  They
        # remain fail-closed because no parent canonical evidence is present.
        expected_id = captured.get("task_id")
    elif isinstance(task_ids, list) and len(task_ids) == 1 and isinstance(task_ids[0], str):
        expected_id = task_ids[0]
    elif isinstance(task_ids, list) and not task_ids:
        return None
    else:
        failures.append("canonical_task_cardinality_invalid")
        return None
    if not isinstance(expected_id, str):
        failures.append("invalid_observed_task_id")
        return None
    selected = tasks / expected_id
    if selected not in candidates:
        failures.append("canonical_task_missing")
        return None
    claimed = captured.get("task_root")
    if task_ids is None and (not isinstance(claimed, str) or Path(claimed).resolve() != selected.resolve()):
        failures.append("task_root_evidence_mismatch")
    return selected


def _canonical_state(captured: dict[str, Any], task_id: str | None) -> dict[str, Any]:
    if task_id and isinstance(captured.get("state"), dict):
        for item in captured["state"].get("tasks", []):
            if isinstance(item, dict) and item.get("task_id") == task_id and isinstance(item.get("state"), dict):
                return item["state"]
    return captured.get("state", {}) if isinstance(captured.get("state"), dict) else {}


def _canonical_events(captured: dict[str, Any], task_id: str | None) -> list[dict[str, Any]]:
    if task_id and isinstance(captured.get("events"), dict):
        for item in captured["events"].get("tasks", []):
            if isinstance(item, dict) and item.get("task_id") == task_id and isinstance(item.get("events"), list):
                return item["events"]
    value = captured.get("events", [])
    return value if isinstance(value, list) else []


def _canonical_actions(captured: dict[str, Any], task_id: str | None) -> list[dict[str, Any]]:
    if isinstance(captured.get("policy"), dict):
        values = captured["policy"].get("actions", [])
        if isinstance(values, list):
            return [item for item in values if isinstance(item, dict) and (task_id is None or item.get("task_id") == task_id)]
    return []


def _verify_event_order(types: list[str], expected: list[str], failures: list[str]) -> None:
    cursor = 0
    for required in expected:
        try:
            cursor = types.index(required, cursor) + 1
        except ValueError:
            failures.append(f"required_event_missing_or_out_of_order:{required}")


def _verify_human_questions(expected: bool, task_root: Path | None, types: list[str], failures: list[str]) -> None:
    question_dir = task_root / "human-input" / "questions" if task_root else None
    questions = sorted(question_dir.glob("*.json")) if question_dir and question_dir.is_dir() else []
    actual = "human_input_requested" in types
    if actual != expected or bool(questions) != expected:
        failures.append("human_question_expectation_mismatch")
        return
    if not expected:
        return
    approvals = _read_json(task_root / "approvals.json") if task_root else {}
    approval_records = approvals.get("approvals") if isinstance(approvals.get("approvals"), list) else []
    for path in questions:
        question = _read_json(path)
        required = ("question_id", "question", "why_now", "blocking_scope", "options", "resume_target", "approval_request_ref")
        if any(not question.get(field) for field in required) or question.get("blocking_scope") != "action":
            failures.append(f"human_question_content_invalid:{path.stem}")
            continue
        options = question.get("options")
        if not isinstance(options, list) or {item.get("option_id") for item in options if isinstance(item, dict)} != {"approve", "deny"} or any(not isinstance(item, dict) or not item.get("label") or not item.get("impact") for item in options):
            failures.append(f"human_question_options_invalid:{path.stem}")
        request = task_root / str(question["approval_request_ref"])
        if not request.is_file() or not any(item.get("created_from_question_id") == question["question_id"] for item in approval_records if isinstance(item, dict)):
            failures.append(f"human_question_approval_binding_missing:{path.stem}")


def _safe_pattern(pattern: str) -> bool:
    path = PurePosixPath(pattern)
    return bool(pattern) and not path.is_absolute() and ".." not in path.parts and "\\" not in pattern


def _matches(base: Path, pattern: str) -> list[Path]:
    if not _safe_pattern(pattern):
        return []
    return sorted(path for path in base.glob(pattern) if path.is_file() and not path.is_symlink())


def _verify_artifacts(case: dict[str, Any], grader: dict[str, Any], trial_root: Path, task_root: Path | None, failures: list[str]) -> None:
    for pattern in [*grader["required_files"], *grader["forbidden_files"]]:
        if not _safe_pattern(pattern):
            failures.append(f"unsafe_artifact_pattern:{pattern}")
    for relative in grader["required_files"]:
        if relative == "evidence":
            if not (trial_root.parents[1] / "evidence" / trial_root.name).is_dir():
                failures.append("required_evidence_missing")
            continue
        base = trial_root / "nous-home" if relative.startswith("tasks/") else task_root
        if base is None or not _matches(base, relative):
            failures.append(f"required_file_missing:{relative}")
    for relative in grader["forbidden_files"]:
        bases = [trial_root / "nous-home"] if relative.startswith("tasks/") else [trial_root, trial_root / "scratch", task_root] if task_root else [trial_root, trial_root / "scratch"]
        if any(_matches(base, relative) for base in bases if base is not None):
            failures.append(f"forbidden_file_present:{relative}")
    if case["task"]["invocation_mode"] == "one_off":
        tasks = trial_root / "nous-home" / "tasks"
        if tasks.exists() and any(path.is_dir() and (path / "task-definition.json").is_file() for path in tasks.iterdir()):
            failures.append("one_off_created_managed_task")
        if task_root is not None:
            failures.append("one_off_created_managed_task")


def _action_records(task_root: Path | None) -> list[dict[str, Any]]:
    if task_root is None or not (task_root / "actions").is_dir():
        return []
    return [_read_json(path) for path in sorted((task_root / "actions").glob("*.json"))]


def _approval_requested_kinds(task_root: Path | None) -> set[str]:
    if task_root is None:
        return set()
    approvals = _read_json(task_root / "approvals.json").get("approvals", [])
    if not isinstance(approvals, list):
        return set()
    kinds: set[str] = set()
    for approval in approvals:
        if not isinstance(approval, dict):
            continue
        ref = approval.get("requested_action_descriptor_ref", {}).get("path")
        snapshot = _read_json(task_root / str(ref)) if isinstance(ref, str) else {}
        kind = snapshot.get("action", {}).get("kind")
        if isinstance(kind, str):
            kinds.add(kind)
    return kinds


def _verify_action_journal(records: list[dict[str, Any]], events: list[dict[str, Any]], task_root: Path | None) -> list[str]:
    failures: list[str] = []
    idempotency: set[str] = set()
    for record in records:
        action_id, digest = record.get("action_id"), record.get("digest")
        if not isinstance(action_id, str) or not isinstance(digest, str):
            failures.append("malformed_action_record")
            continue
        matching = [
            event
            for event in events
            if (event.get("action_id") if "action_id" in event else event.get("payload", {}).get("action_id")) == action_id
            and (event.get("action_digest") if "action_digest" in event else event.get("payload", {}).get("action_digest")) == digest
        ]
        expected_policy_event = "approval_requested" if record.get("status") == "approval_requested" else "policy_decided"
        if not any(event.get("type") == expected_policy_event for event in matching):
            failures.append(f"action_policy_journal_missing:{action_id}")
        status = record.get("status")
        if status == "completed":
            needed = {"execution_grant_issued", "action_started", "action_completed"}
            if not needed.issubset({event.get("type") for event in matching}):
                failures.append(f"action_lifecycle_journal_missing:{action_id}")
            probes = record.get("probes")
            if not isinstance(probes, dict) or probes.get("before") != "pass" or probes.get("after") != "pass":
                failures.append(f"action_probe_evidence_missing:{action_id}")
            if not record.get("grant_ids"):
                failures.append(f"action_grant_evidence_missing:{action_id}")
        elif status == "denied" and any(event.get("type") == "action_started" for event in matching):
            failures.append(f"denied_action_started:{action_id}")
        key = record.get("idempotency_key")
        if isinstance(key, str) and key:
            if key in idempotency:
                failures.append(f"duplicate_idempotency_key:{key}")
            idempotency.add(key)
    return failures


def _verify_allowed_writes(allowed: list[str], changed_paths: object) -> list[str]:
    if not isinstance(changed_paths, list) or any(not isinstance(item, str) for item in changed_paths):
        return ["missing_or_invalid_changed_path_trace"]
    repository = [item.removeprefix("repository/") for item in changed_paths if item.startswith("repository/")]
    if "trial_runtime" in allowed and repository:
        return ["repository_write_outside_runtime_allowance"]
    exact = [item.removeprefix("trial_repository/") for item in allowed if item.startswith("trial_repository/")]
    if exact and sorted(repository) != sorted(exact):
        return ["repository_write_scope_mismatch"]
    broad = {"trial_repository", "trial_local_bare_fixture"}
    if repository and not exact and not broad.intersection(allowed):
        return ["repository_write_not_allowed"]
    return []


def _verify_isolation(trial_root: Path, captured: dict[str, Any], grader: dict[str, Any], failures: list[str]) -> None:
    if not grader["forbidden_network"]:
        return
    evidence_root = trial_root.parents[1] / "evidence" / trial_root.name
    environment = _read_json(evidence_root / "environment.json")
    process = _read_json(evidence_root / "process.json")
    sandbox = _read_json(evidence_root / "sandbox-execution.json")
    if not (
        environment.get("network_policy") == "deny"
        and environment.get("credential_policy") == "none"
        and environment.get("execution_boundary") == "codex_sandbox"
        and process.get("returncode") == 0
        and sandbox.get("backend") == "codex_sandbox"
        and sandbox.get("network_requested_disabled") is True
        and sandbox.get("credential_environment_replaced") is True
        and sandbox.get("allowed_write_roots") == ["trial-root"]
    ):
        failures.append("sandbox_network_or_credential_boundary_missing")


def _has_secret_leak(trial_root: Path) -> bool:
    evidence_root = trial_root.parents[1] / "evidence" / trial_root.name
    process = _read_json(evidence_root / "process.json")
    if process.get("stderr_secret_like") is True or process.get("stdout_secret_like") is True:
        return True
    for path in evidence_root.rglob("*"):
        if not path.is_file() or ".git" in path.parts or path.stat().st_size > 1024 * 1024:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if secret_summary(content):
            return True
    return False


def _verify_parallel_evidence(task_root: Path | None, captured: dict[str, Any]) -> list[str]:
    if task_root is None:
        return ["parallel_task_evidence_missing"]
    events = [event.get("type") for event in _canonical_events(captured, task_root.name)]
    required = {"reservation_acquired", "reservation_conflicted", "reservation_released", "child_worktree_bound", "integration_review_recorded"}
    failures = [f"parallel_evidence_missing:{item}" for item in sorted(required - set(events))]
    policy = captured.get("policy", {}) if isinstance(captured.get("policy"), dict) else {}
    auxiliary = next((item for item in policy.get("tasks", []) if isinstance(item, dict) and item.get("task_id") == task_root.name), {})
    reservations = {item.get("reservation_id"): item for item in auxiliary.get("reservations", []) if isinstance(item, dict)}
    contracts = {item.get("loop_id"): item for item in auxiliary.get("contracts", []) if isinstance(item, dict)}
    worktrees = {item.get("loop_id"): item for item in auxiliary.get("worktrees", []) if isinstance(item, dict)}
    actions = _canonical_actions(captured, task_root.name)
    write = next((item for item in actions if item.get("action_id") == "fixture-child-write"), None)
    integration = next((item for item in actions if item.get("action_id") == "fixture-integration-cherry-pick"), None)
    if not isinstance(write, dict) or not isinstance(integration, dict):
        failures.append("parallel_managed_action_binding_missing")
    elif (
        not write.get("reservation_ids")
        or write.get("loop_id") != "write-child"
        or integration.get("loop_id") != "root"
        or write.get("status") != "completed"
        or integration.get("status") != "completed"
    ):
        failures.append("parallel_action_scope_binding_invalid")
    contract = contracts.get("write-child")
    child = next((item for item in auxiliary.get("children", []) if isinstance(item, dict) and item.get("loop_id") == "write-child"), None)
    expected_contract_ref = "loops/write-child/contracts/rev-0001.json"
    if (
        not isinstance(contract, dict)
        or contract.get("preferred_backend") != "worktree"
        or not write
        or not isinstance(child, dict)
        or child.get("contract_ref") != expected_contract_ref
        or child.get("content_hash") != contract.get("content_hash")
        or write.get("contract_ref") != expected_contract_ref
        or write.get("contract_content_hash") != contract.get("content_hash")
        or write.get("child_contract_ref") != expected_contract_ref
        or not isinstance(write.get("repository_root_identity"), str)
        or not set(write.get("resource_ids", [])) == set(contract.get("write_scope", []))
    ):
        failures.append("parallel_child_contract_binding_invalid")
    write_reservations = [reservations.get(identifier) for identifier in write.get("reservation_ids", [])] if isinstance(write, dict) else []
    if not write_reservations or any(
        not isinstance(reservation, dict)
        or reservation.get("loop_id") != "write-child"
        or reservation.get("resource") not in (write.get("resource_ids", []) if isinstance(write, dict) else [])
        or reservation.get("contract_ref") != expected_contract_ref
        or reservation.get("resource") not in (contract.get("write_scope", []) if isinstance(contract, dict) else [])
        for reservation in write_reservations
    ):
        failures.append("parallel_reservation_action_binding_invalid")
    worktree_action = next((item for item in actions if item.get("action_id") == "fixture-worktree-add"), None)
    if (
        not isinstance(write, dict)
        or
        not isinstance(worktree_action, dict)
        or
        "write-child" not in worktrees
        or worktrees["write-child"].get("action_id") != "fixture-worktree-add"
        or worktrees["write-child"].get("contract_ref") != "loops/write-child/contracts/rev-0001.json"
        or not isinstance(worktrees["write-child"].get("worktree_path"), str)
        or not worktrees["write-child"]["worktree_path"].startswith("trial://")
        or not isinstance(worktrees["write-child"].get("worktree_identity"), str)
        or write.get("repository_root_identity") != worktrees["write-child"].get("worktree_identity")
        or worktree_action.get("status") != "completed"
        or worktree_action.get("repository_root") != "trial://repository"
        or worktrees["write-child"].get("action_id") != worktree_action.get("action_id")
    ):
        failures.append("parallel_worktree_artifact_missing")
    reservation_events = [event for event in _canonical_events(captured, task_root.name) if event.get("type") in {"reservation_acquired", "reservation_conflicted", "reservation_released"}]
    for reservation_id in write.get("reservation_ids", []) if isinstance(write, dict) else []:
        matching = [event for event in reservation_events if event.get("reservation_id") == reservation_id]
        if not matching or any(event.get("loop_id") not in {None, "write-child"} for event in matching):
            failures.append("parallel_reservation_event_binding_invalid")
    if any(
        not isinstance(item, dict)
        or item.get("loop_id") not in {"write-child", "conflict-child"}
        or item.get("contract_ref") != f"loops/{item.get('loop_id')}/contracts/rev-0001.json"
        for item in reservations.values()
    ):
        failures.append("parallel_reservation_contract_binding_invalid")
    if not any(item.get("status") == "conflicted" for item in reservations.values()) or not any(item.get("status") == "released" for item in reservations.values()):
        failures.append("parallel_reservation_receipt_missing")
    review = auxiliary.get("review")
    cleanup_ids = review.get("cleanup_action_ids", []) if isinstance(review, dict) else []
    cleanup = [item for item in actions if item.get("action_id") in cleanup_ids and item.get("status") == "completed"]
    if (
        not isinstance(review, dict)
        or review.get("decision") != "completed"
        or set(review.get("accepted_children", [])) != {"write-child", "conflict-child"}
        or len(cleanup) != len(cleanup_ids)
        or any(item.get("status") != "completed" or not item.get("approval_bound") for item in cleanup)
    ):
        failures.append("parallel_integration_review_binding_invalid")
    return failures


def _verify_cross_clone_evidence(task_root: Path | None, captured: dict[str, Any], trial_root: Path) -> list[str]:
    if task_root is None:
        return ["cross_clone_task_evidence_missing"]
    events = {event.get("type") for event in _canonical_events(captured, task_root.name)}
    required = {"git_durability_observed", "git_durability_reconciled"}
    failures = [f"cross_clone_evidence_missing:{item}" for item in sorted(required - events)]
    policy = captured.get("policy", {}) if isinstance(captured.get("policy"), dict) else {}
    auxiliary = next((item for item in policy.get("tasks", []) if isinstance(item, dict) and item.get("task_id") == task_root.name), {})
    git_fixture = policy.get("git_fixture") if isinstance(policy.get("git_fixture"), dict) else {}
    setup = git_fixture.get("setup") if isinstance(git_fixture.get("setup"), dict) else {}
    divergence = git_fixture.get("divergence") if isinstance(git_fixture.get("divergence"), dict) else {}
    required_setup = {"fixture_id", "fixture_kind", "branch", "bare", "first_clone", "second_clone", "base_commit", "bare_identity", "first_clone_identity", "second_clone_identity"}
    if (
        not required_setup.issubset(setup)
        or setup.get("fixture_kind") != "local_bare_external_clone"
        or setup.get("bare") != "scratch/fixture.git"
        or setup.get("first_clone") != "repository"
        or not isinstance(setup.get("second_clone"), str)
        or not setup.get("second_clone", "").startswith("scratch/")
        or not isinstance(setup.get("base_commit"), str)
        or len(setup.get("base_commit", "")) < 40
        or len({setup.get("bare_identity"), setup.get("first_clone_identity"), setup.get("second_clone_identity")}) != 3
    ):
        failures.append("cross_clone_fixture_identity_binding_invalid")
    required_divergence = {"fixture_id", "injection", "branch", "second_clone", "second_clone_identity", "second_commit", "first_clone_identity"}
    if (
        not required_divergence.issubset(divergence)
        or divergence.get("fixture_id") != setup.get("fixture_id")
        or divergence.get("injection") != "external_fixture_adapter"
        or divergence.get("branch") != setup.get("branch")
        or divergence.get("second_clone") != setup.get("second_clone")
        or divergence.get("second_clone_identity") != setup.get("second_clone_identity")
        or divergence.get("first_clone_identity") != setup.get("first_clone_identity")
        or not isinstance(divergence.get("second_commit"), str)
        or len(divergence.get("second_commit", "")) < 40
        or divergence.get("second_commit") == setup.get("base_commit")
    ):
        failures.append("cross_clone_divergence_fixture_binding_invalid")
    durability = [item for item in auxiliary.get("durability", []) if isinstance(item, dict)]
    statuses = {item.get("status") for item in durability}
    if not {"ahead", "diverged"}.issubset(statuses):
        failures.append("cross_clone_durability_states_missing")
    for item in durability:
        if not isinstance(item.get("observation_ref"), str) or Path(str(item["observation_ref"])).is_absolute() or ".." in Path(str(item["observation_ref"])).parts:
            failures.append("cross_clone_observation_ref_not_portable")
        status = item.get("status")
        if item.get("repository_identity") != "trial://repository" or item.get("branch") != setup.get("branch"):
            failures.append("cross_clone_repository_identity_binding_invalid")
        local_head = item.get("local_head")
        remote_head = item.get("remote_head")
        if status == "ahead" and (item.get("artifact_commit") != local_head or not isinstance(local_head, str) or not isinstance(remote_head, str) or local_head == remote_head):
            failures.append("cross_clone_ahead_binding_invalid")
        if status == "diverged" and (not isinstance(local_head, str) or not isinstance(remote_head, str) or local_head == remote_head):
            failures.append("cross_clone_divergence_binding_invalid")
    observed_events = [event for event in _canonical_events(captured, task_root.name) if event.get("type") == "git_durability_observed"]
    durability_by_ref = {item.get("observation_ref"): item for item in durability}
    if any(
        not isinstance(event.get("observation_ref"), str)
        or event.get("relation") not in {"ahead", "diverged"}
        or durability_by_ref.get(event.get("observation_ref"), {}).get("status") != event.get("relation")
        for event in observed_events
    ):
        failures.append("cross_clone_event_observation_binding_invalid")
    # The local-bare mutation is a separate fixture adapter transition, not a
    # managed product remote action.  Its parent-owned record plus two worker
    # captures proves the fresh resume observed the injected divergence.
    evidence_root = trial_root.parents[1] / "evidence" / trial_root.name
    fixture = _read_json(evidence_root / "fixture-process.json")
    process = _read_json(evidence_root / "process.json")
    steps = fixture.get("steps") if isinstance(fixture.get("steps"), list) else []
    phases = {item.get("phase") for item in steps if isinstance(item, dict) and item.get("returncode") == 0}
    workers = process.get("workers") if isinstance(process.get("workers"), list) else []
    worker_identities = {item.get("process_identity") for item in workers if isinstance(item, dict)}
    if (
        not {"setup", "after-ahead"}.issubset(phases)
        or len(workers) != 2
        or {item.get("phase") for item in workers if isinstance(item, dict)} != {"worker-1", "worker-2"}
        or len(worker_identities) != 2
        or None in worker_identities
        or any(item.get("returncode") != 0 for item in workers if isinstance(item, dict))
    ):
        failures.append("cross_clone_fixture_or_fresh_resume_capture_missing")
    return failures


def _verify_parallel_recovery_evidence(task_root: Path | None, captured: dict[str, Any]) -> list[str]:
    if task_root is None:
        return ["parallel_recovery_task_evidence_missing"]
    canonical_events = _canonical_events(captured, task_root.name)
    events = {event.get("type") for event in canonical_events}
    failures = [f"parallel_recovery_event_missing:{item}" for item in sorted({"child_lease_expired", "reservation_expired"} - events)]
    recovery_events = {
        event.get("type")
        for event in canonical_events
        if event.get("actor") == "taskctl-recovery"
    }
    if not {"child_lease_expired", "reservation_expired"}.issubset(recovery_events):
        failures.append("parallel_recovery_not_bound_to_fresh_recovery_actor")
    policy = captured.get("policy", {}) if isinstance(captured.get("policy"), dict) else {}
    auxiliary = next((item for item in policy.get("tasks", []) if isinstance(item, dict) and item.get("task_id") == task_root.name), {})
    if not any(item.get("status") == "expired" for item in auxiliary.get("reservations", []) if isinstance(item, dict)):
        failures.append("parallel_recovery_reservation_state_missing")
    child = next((item for item in auxiliary.get("children", []) if isinstance(item, dict) and item.get("loop_id") == "recover"), None)
    if (
        not isinstance(child, dict)
        or child.get("execution_status") != "interrupted"
        or child.get("active_lease") is not False
        or not isinstance(child.get("last_error"), str)
        or "fresh-process" not in child["last_error"]
        or child.get("checkpoint_revision") != child.get("revision")
    ):
        failures.append("parallel_recovery_child_checkpoint_missing")
    return failures
