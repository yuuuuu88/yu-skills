from __future__ import annotations
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .domain import ActionDescriptor, PolicyGateway
from .errors import ValidationError
from .executor.managed import ExecutionResult, ManagedExecutor
from .taskctl import TaskStore


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(("git", "-C", str(repo), *args), text=True, stderr=subprocess.DEVNULL).strip()


def _is_ancestor(repo: Path, older: str, newer: str) -> bool:
    result = subprocess.run(("git", "-C", str(repo), "merge-base", "--is-ancestor", older, newer), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0


def remote_state(task_id: str, repo: Path, remote_name: str = "origin", branch: str = "HEAD") -> dict[str, Any]:
    """Read-only local/remote relation probe; does not claim managed Git execution."""
    repo = repo.resolve()
    local = _git(repo, "rev-parse", branch)
    try:
        remote = _git(repo, "rev-parse", f"{remote_name}/{branch}")
        if local == remote:
            status = "synced"
        elif _is_ancestor(repo, remote, local):
            status = "ahead"
        elif _is_ancestor(repo, local, remote):
            status = "behind"
        else:
            status = "diverged"
    except Exception:
        remote, status = None, "unavailable"
    return {"schema_version": 1, "task_id": task_id, "repository_identity": str(repo), "remote_name": remote_name,
            "branch": branch, "local_head": local, "remote_head": remote, "status": status,
            "verified_at": datetime.now(timezone.utc).isoformat(), "artifact_commit": local}


def validate_remote_state(value: dict[str, Any], schemas: Any) -> None:
    schemas.validate("remote-state", value)


class LocalGitWorktreeManager:
    """Narrow, managed local-Git capability for parallel child isolation.

    It deliberately has no remote, fetch, pull, push, clone, or generic shell
    method.  The actual Git command is delegated to ``ManagedExecutor`` and
    the resulting worktree is only accepted when taskctl can bind it to an
    immutable child contract and a completed canonical action record.
    """

    def __init__(self, store: TaskStore, executor: ManagedExecutor, gateway: PolicyGateway | None = None) -> None:
        self.store = store
        self.executor = executor
        self.gateway = gateway
        if executor.store is not store:
            raise ValidationError("Git worktree manager and managed executor must share one TaskStore")
        if executor.runtime_root != (store.home / "runtime").resolve():
            raise ValidationError("managed Git runtime must be the task-owned nous runtime directory")

    def child_worktree_path(self, task_id: str, loop_id: str) -> Path:
        return self.store.home / "runtime" / "worktrees" / task_id / loop_id

    def create_child_worktree(
        self,
        task_id: str,
        expected_revision: int,
        actor: str,
        lease_token: str,
        *,
        loop_id: str,
        repository_root: str,
        branch: str,
        base_commit: str,
        action_id: str,
    ) -> dict[str, Any]:
        """Create and durably bind one child worktree with no remote effect."""
        state = self.store.read_task(task_id)[1]
        lease = state.get("active_lease")
        if state["revision"] != expected_revision or not lease or lease["owner"] != actor or lease["token"] != lease_token:
            raise ValidationError("worktree creation requires the caller's exact active parent lease")
        target = self.child_worktree_path(task_id, loop_id)
        action = ActionDescriptor(
            action_id=action_id,
            task_id=task_id,
            kind="git.local",
            repository_root=repository_root,
            targets=(".",),
            command=("worktree", "add", "-b", branch, str(target), base_commit),
            declared_risk="yellow",
            loop_id="root",
            expected_state_revision=expected_revision,
            expected_effect=f"create isolated child worktree for {loop_id}",
        )
        result: ExecutionResult = self.executor.create_worktree(
            action,
            worktree_path=target,
            branch=branch,
            base_ref=base_commit,
        )
        if result.returncode != 0:
            return {"result": result, "worktree": None}
        bound = self.store.record_child_worktree(
            task_id,
            result.action_state_revision,
            actor,
            loop_id=loop_id,
            action_id=action_id,
            branch=branch,
            base_commit=base_commit,
            worktree_path=str(target),
        )
        return {"result": result, "worktree": bound}
