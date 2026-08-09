from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path

import pytest

from agent_nous.domain import ActionDescriptor, PolicyGateway
from agent_nous.errors import ConflictError, PolicyDeniedError
from agent_nous.executor import ManagedExecutor
from agent_nous.taskctl import TaskStore


pytestmark = pytest.mark.skipif(shutil.which("codex") is None, reason="codex sandbox unavailable")


def gateway() -> PolicyGateway:
    return PolicyGateway(
        allowed_processes=(("/usr/bin/python3", "-m", "unittest"),)
    )


def runtime(tmp_path: Path, repo: Path) -> tuple[TaskStore, str, int]:
    store = TaskStore(tmp_path / "home")
    task = store.create(title="Managed action", objective="exercise action journal", target_repository=str(repo), actor="test")["task_id"]
    leased = store.acquire_lease(task, 1, "worker")
    return store, task, leased["revision"]


def test_managed_write_is_contained(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store, task, revision = runtime(tmp_path, repo)
    executor = ManagedExecutor(store, gateway(), tmp_path / "runtime", actor="worker")
    action = ActionDescriptor(
        action_id=uuid.uuid4().hex,
        task_id=task,
        kind="filesystem.write",
        repository_root=str(repo),
        targets=(str(repo / "nested" / "result.txt"),),
        expected_state_revision=revision,
        idempotency_key="write-1",
    )
    result = executor.write_text(action, "ok\n")
    assert result.returncode == 0
    assert (repo / "nested" / "result.txt").read_text() == "ok\n"
    action = next((tmp_path / "home" / "tasks" / task / "actions").glob("*.json"))
    assert '"status": "completed"' in action.read_text()
    assert '"action_completed"' in (tmp_path / "home" / "tasks" / task / "events.jsonl").read_text()


def test_scope_escape_is_denied_before_execution(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store, task, revision = runtime(tmp_path, repo)
    executor = ManagedExecutor(store, gateway(), tmp_path / "runtime", actor="worker")
    action = ActionDescriptor(
        action_id=uuid.uuid4().hex,
        task_id=task,
        kind="filesystem.write",
        repository_root=str(repo),
        targets=(str(tmp_path / "escape.txt"),),
        expected_state_revision=revision,
    )
    with pytest.raises(PolicyDeniedError):
        executor.write_text(action, "no")
    assert not (tmp_path / "escape.txt").exists()


def test_write_fails_closed_when_parent_is_swapped_to_a_symlink_after_grant(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    nested = repo / "nested"
    nested.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    store, task, revision = runtime(tmp_path, repo)
    executor = ManagedExecutor(store, gateway(), tmp_path / "runtime", actor="worker")
    original_verify = store.verify_execution_grant

    def swap_after_verify(*args: object, **kwargs: object) -> dict[str, object]:
        verified = original_verify(*args, **kwargs)
        nested.rename(repo / "nested-real")
        nested.symlink_to(outside, target_is_directory=True)
        return verified

    store.verify_execution_grant = swap_after_verify  # type: ignore[method-assign]
    action = ActionDescriptor(action_id=uuid.uuid4().hex, task_id=task, kind="filesystem.write", repository_root=str(repo), targets=(str(nested / "payload.txt"),), expected_state_revision=revision)
    result = executor.write_text(action, "must not escape\n")
    assert result.returncode == 74
    assert not (outside / "payload.txt").exists()
    assert '"status": "failed"' in next((tmp_path / "home" / "tasks" / task / "actions").glob("*.json")).read_text()


def test_write_fails_closed_when_repository_root_path_is_replaced_after_grant(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    original_repo = tmp_path / "original-repo"
    store, task, revision = runtime(tmp_path, repo)
    executor = ManagedExecutor(store, gateway(), tmp_path / "runtime", actor="worker")
    original_verify = store.verify_execution_grant

    def replace_root_after_verify(*args: object, **kwargs: object) -> dict[str, object]:
        verified = original_verify(*args, **kwargs)
        repo.rename(original_repo)
        repo.mkdir()
        return verified

    store.verify_execution_grant = replace_root_after_verify  # type: ignore[method-assign]
    action = ActionDescriptor(action_id=uuid.uuid4().hex, task_id=task, kind="filesystem.write", repository_root=str(repo), targets=(str(repo / "result.txt"),), expected_state_revision=revision)
    result = executor.write_text(action, "must bind original root\n")
    assert result.returncode == 74
    assert not (repo / "result.txt").exists()
    assert not (original_repo / "result.txt").exists()


def test_process_fails_before_sandbox_when_repository_root_path_is_replaced_after_grant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    original_repo = tmp_path / "original-repo"
    store, task, revision = runtime(tmp_path, repo)
    executor = ManagedExecutor(store, gateway(), tmp_path / "runtime", actor="worker")
    original_verify = store.verify_execution_grant

    def replace_root_after_verify(*args: object, **kwargs: object) -> dict[str, object]:
        verified = original_verify(*args, **kwargs)
        if repo.exists():
            repo.rename(original_repo)
            repo.mkdir()
        return verified

    store.verify_execution_grant = replace_root_after_verify  # type: ignore[method-assign]
    monkeypatch.setattr(executor.sandbox, "run", lambda *args, **kwargs: pytest.fail("sandbox must not start with a replaced root"))
    action = ActionDescriptor(action_id=uuid.uuid4().hex, task_id=task, kind="process.run", repository_root=str(repo), targets=(str(repo / "declared.txt"),), command=("/usr/bin/python3", "-m", "unittest"), expected_state_revision=revision)
    result = executor.run_process(action)
    assert result.returncode == 74
    assert original_repo.exists()
    assert repo.exists()


def test_unallowlisted_process_is_denied(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store, task, revision = runtime(tmp_path, repo)
    executor = ManagedExecutor(store, gateway(), tmp_path / "runtime", actor="worker")
    action = ActionDescriptor(
        action_id=uuid.uuid4().hex,
        task_id=task,
        kind="process.run",
        repository_root=str(repo),
        command=("/usr/bin/git", "push"),
        targets=(str(repo),),
        expected_state_revision=revision,
    )
    with pytest.raises(PolicyDeniedError):
        executor.run_process(action)


def test_process_workspace_rejects_undeclared_write_without_touching_target_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_escape.py").write_text("from pathlib import Path\nimport unittest\nclass Escape(unittest.TestCase):\n    def test_escape(self):\n        Path('undeclared.txt').write_text('no')\n")
    store, task, revision = runtime(tmp_path, repo)
    executor = ManagedExecutor(store, gateway(), tmp_path / "runtime", actor="worker")
    action = ActionDescriptor(action_id=uuid.uuid4().hex, task_id=task, kind="process.run", repository_root=str(repo), targets=(str(repo / "declared.txt"),), command=("/usr/bin/python3", "-m", "unittest", "test_escape"), expected_state_revision=revision)
    result = executor.run_process(action)
    assert result.returncode == 73
    assert not (repo / "undeclared.txt").exists()
    assert '"status": "failed"' in next((tmp_path / "home" / "tasks" / task / "actions").glob("*.json")).read_text()


def test_process_source_symlink_is_rejected_before_copy_or_execution(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside-only\n")
    (repo / "outside-link").symlink_to(outside)
    store, task, revision = runtime(tmp_path, repo)
    executor = ManagedExecutor(store, gateway(), tmp_path / "runtime", actor="worker")
    action = ActionDescriptor(action_id=uuid.uuid4().hex, task_id=task, kind="process.run", repository_root=str(repo), targets=(str(repo / "declared.txt"),), command=("/usr/bin/python3", "-m", "unittest"), expected_state_revision=revision)
    with pytest.raises(PolicyDeniedError):
        executor.run_process(action)
    assert outside.read_text() == "outside-only\n"


def test_process_snapshot_rejects_directory_to_symlink_swap_without_reading_outside(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    child = repo / "child"
    child.mkdir(parents=True)
    (child / "inside.txt").write_text("inside\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("outside-data\n")
    store, task, revision = runtime(tmp_path, repo)
    executor = ManagedExecutor(store, gateway(), tmp_path / "runtime", actor="worker")
    from agent_nous.executor import managed
    original_open = managed.os.open
    swapped = False

    def swap_before_child_open(path: object, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        nonlocal swapped
        if path == "child" and dir_fd is not None and not swapped:
            swapped = True
            child.rename(repo / "child-real")
            child.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(managed.os, "open", swap_before_child_open)
    monkeypatch.setattr(executor.sandbox, "run", lambda *args, **kwargs: pytest.fail("sandbox must not run after source swap"))
    action = ActionDescriptor(action_id=uuid.uuid4().hex, task_id=task, kind="process.run", repository_root=str(repo), targets=(str(repo / "declared.txt"),), command=("/usr/bin/python3", "-m", "unittest"), expected_state_revision=revision)
    result = executor.run_process(action)
    assert swapped
    assert result.returncode == 74
    assert (outside / "private.txt").read_text() == "outside-data\n"


def test_process_rechecks_expired_grant_after_slow_preparation_before_sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store = TaskStore(tmp_path / "home")
    task = store.create(title="slow process", objective="recheck grant", target_repository=str(repo), actor="test")["task_id"]
    lease = store.acquire_lease(task, 1, "worker", seconds=1)
    executor = ManagedExecutor(store, gateway(), tmp_path / "runtime", actor="worker")
    action = ActionDescriptor(action_id=uuid.uuid4().hex, task_id=task, kind="process.run", repository_root=str(repo), targets=(str(repo / "declared.txt"),), command=("/usr/bin/python3", "-m", "unittest"), expected_state_revision=lease["revision"])
    from agent_nous.executor.managed import _copy_source_snapshot_no_follow

    def slow_snapshot(*args: object, **kwargs: object) -> object:
        result = _copy_source_snapshot_no_follow(*args, **kwargs)
        time.sleep(2.0)
        return result

    monkeypatch.setattr("agent_nous.executor.managed._copy_source_snapshot_no_follow", slow_snapshot)
    monkeypatch.setattr(executor.sandbox, "run", lambda *args, **kwargs: pytest.fail("sandbox must not start after grant expiry"))
    with pytest.raises(ConflictError):
        executor.run_process(action)


def test_unbrokerable_remote_surface_stays_unsupported_with_approval() -> None:
    from agent_nous.domain import Approval
    action = ActionDescriptor(
        action_id=uuid.uuid4().hex,
        task_id="20260719-1200-test",
        kind="network",
        repository_root=None,
        network_destinations=("https://example.com",),
        approval_id="approval-1",
    )
    approval = Approval("approval-1", action.task_id, action.digest, "active")
    assert gateway().evaluate(action, (approval,)).outcome == "unsupported"


def test_rootless_process_is_denied() -> None:
    action = ActionDescriptor(
        action_id=uuid.uuid4().hex,
        task_id="20260719-1200-test",
        kind="process.run",
        command=("/usr/bin/python3", "-c", "print('no')"),
    )
    assert gateway().evaluate(action).outcome == "deny"
