from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..domain import ActionDescriptor, Approval, PolicyGateway
from ..diagnostics import safe_text_summary
from ..errors import PolicyDeniedError, UnsupportedActionError, ValidationError
from ..taskctl import TaskStore
from .sandbox import CodexSandbox
from .validation import ValidationSpec


# Local virtual environments are runtime dependencies, not source inputs.
# They commonly contain interpreter symlinks and must not be copied into the
# disposable validation snapshot.
_PROCESS_SNAPSHOT_EXCLUDED_DIRS = frozenset({".venv", "node_modules", ".tox", ".nox"})


@dataclass(frozen=True)
class ExecutionResult:
    action_id: str
    action_digest: str
    grant_id: str
    returncode: int
    stdout: str
    stderr: str
    started_at: str
    completed_at: str
    action_state_revision: int


def _hash_path(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_snapshot(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in root.rglob("*"):
        if ".git" in path.parts:
            continue
        relative = str(path.relative_to(root))
        if path.is_symlink():
            # A validation process creating a symlink is an observable change,
            # even when its target happens to be harmless.
            try:
                result[relative] = f"symlink:{path.readlink()}"
            except OSError:
                result[relative] = "symlink:<unreadable>"
        elif path.is_dir():
            try:
                result[relative] = f"directory:{stat.S_IMODE(path.stat().st_mode):o}"
            except OSError:
                result[relative] = "directory:<unreadable>"
        elif path.is_file():
            try:
                mode = stat.S_IMODE(path.stat().st_mode)
            except OSError:
                mode = 0
            result[relative] = f"file:{mode:o}:{_hash_path(path) or ''}"
        else:
            # FIFOs, sockets, device nodes, and other special files must not
            # disappear from the diff simply because they have no byte hash.
            try:
                metadata = path.lstat()
                result[relative] = f"special:{stat.S_IFMT(metadata.st_mode):o}:{stat.S_IMODE(metadata.st_mode):o}"
            except OSError:
                result[relative] = "special:<unreadable>"
    return result


def _changed_paths(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(
        path
        for path in {*before, *after}
        if before.get(path) != after.get(path)
    )


def _validation_failure_category(returncode: int, stderr: str) -> str | None:
    if returncode in {124, 126, 127}:
        return "validation_environment_failure"
    lowered = stderr.lower()
    if "executor timeout" in lowered or "command not found" in lowered:
        return "validation_environment_failure"
    # A traceback containing FileNotFoundError/ImportError is usually a test
    # or application failure, not proof that the validation runner itself is
    # unavailable.  Only classify import/missing-file markers as environment
    # failures when the process failed before it entered user code.
    has_traceback = "traceback (most recent call last)" in lowered
    early_environment_markers = (
        "can't open file",
        "no module named",
        "permission denied",
        "modulenotfounderror",
        "importerror",
        "cannot import",
        "sandbox",
        "no such file or directory",
    )
    if not has_traceback and any(marker in lowered for marker in early_environment_markers):
        return "validation_environment_failure"
    return None


def _write_text_dirfd(root_fd: int, relative: Path, content: str) -> None:
    """Atomically replace one file without following a swapped symlink.

    The managed write adapter never gets a repository-wide write capability.
    Every parent directory is opened relative to the canonical repository fd
    with ``O_NOFOLLOW``.  If a path component changes to a symlink after
    preflight, the effect fails closed before the target file is opened.
    """
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise OSError("managed write target is not a simple repository-relative path")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.dup(root_fd)
    try:
        parents = relative.parts[:-1]
        for component in parents:
            try:
                os.mkdir(component, mode=0o755, dir_fd=directory_fd)
            except FileExistsError:
                pass
            next_fd = os.open(component, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        filename = relative.name
        if not filename:
            raise OSError("managed write target has no filename")
        temporary = f".nous-write-{uuid.uuid4().hex}"
        file_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        try:
            encoded = content.encode("utf-8")
            offset = 0
            while offset < len(encoded):
                offset += os.write(file_fd, encoded[offset:])
            os.fsync(file_fd)
        finally:
            os.close(file_fd)
        # replace operates relative to the already-open non-symlink parent.
        os.replace(temporary, filename, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    except Exception:
        try:
            os.unlink(locals().get("temporary", ""), dir_fd=directory_fd)
        except (FileNotFoundError, OSError):
            pass
        raise
    finally:
        os.close(directory_fd)


def _assert_source_tree_has_no_symlinks(root: Path) -> None:
    if root.is_symlink():
        raise OSError("process source root is a symlink")
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in _PROCESS_SNAPSHOT_EXCLUDED_DIRS for part in relative.parts):
            continue
        if path.is_symlink():
            raise OSError("process source tree contains a symlink")


def _open_bound_repository_root(repository_root: str, expected_identity: str) -> int:
    """Open the exact Task-bound repository directory and retain its fd."""
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(repository_root, flags)
    actual = os.fstat(descriptor)
    if f"{actual.st_dev}:{actual.st_ino}" != expected_identity:
        os.close(descriptor)
        raise OSError("repository root identity changed after grant")
    return descriptor


def _copy_source_snapshot_no_follow(source_root_fd: int, destination: Path) -> None:
    """Copy a process source snapshot through directory descriptors only.

    ``shutil.copytree`` is not sufficient here: even ``symlinks=True`` can
    follow a directory replaced by a symlink after it has inspected a
    ``DirEntry``.  This copier holds the source root as an fd and opens every
    child relative to its already-open parent with ``O_NOFOLLOW``.  A swap to
    a symlink therefore fails before any out-of-root content is read.
    """
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.dup(source_root_fd)
    try:
        source_device = os.fstat(source_fd).st_dev
        destination.mkdir(mode=0o700)
        destination_fd = os.open(destination, directory_flags)
        try:
            def copy_directory(current_source_fd: int, current_destination_fd: int) -> None:
                with os.scandir(current_source_fd) as entries:
                    for entry in entries:
                        name = entry.name
                        if name == ".git" or name in _PROCESS_SNAPSHOT_EXCLUDED_DIRS:
                            continue
                        metadata = os.stat(name, dir_fd=current_source_fd, follow_symlinks=False)
                        if stat.S_ISLNK(metadata.st_mode):
                            raise OSError("process source tree contains a symlink")
                        if metadata.st_dev != source_device:
                            raise OSError("process source tree crosses a mount boundary")
                        if stat.S_ISDIR(metadata.st_mode):
                            os.mkdir(name, stat.S_IMODE(metadata.st_mode) or 0o700, dir_fd=current_destination_fd)
                            child_source_fd = os.open(name, directory_flags, dir_fd=current_source_fd)
                            child_destination_fd = os.open(name, directory_flags, dir_fd=current_destination_fd)
                            try:
                                copy_directory(child_source_fd, child_destination_fd)
                            finally:
                                os.close(child_source_fd)
                                os.close(child_destination_fd)
                            continue
                        if not stat.S_ISREG(metadata.st_mode):
                            raise OSError("process source tree contains an unsupported special file")
                        input_fd = os.open(name, file_flags, dir_fd=current_source_fd)
                        temporary = f".nous-snapshot-{uuid.uuid4().hex}"
                        output_fd: int | None = None
                        try:
                            actual = os.fstat(input_fd)
                            if not stat.S_ISREG(actual.st_mode):
                                raise OSError("process source file changed during snapshot")
                            output_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), stat.S_IMODE(actual.st_mode) or 0o600, dir_fd=current_destination_fd)
                            while True:
                                block = os.read(input_fd, 1024 * 1024)
                                if not block:
                                    break
                                offset = 0
                                while offset < len(block):
                                    offset += os.write(output_fd, block[offset:])
                            os.fsync(output_fd)
                            os.close(output_fd)
                            output_fd = None
                            os.replace(temporary, name, src_dir_fd=current_destination_fd, dst_dir_fd=current_destination_fd)
                        except Exception:
                            try:
                                os.unlink(temporary, dir_fd=current_destination_fd)
                            except (FileNotFoundError, OSError):
                                pass
                            raise
                        finally:
                            if output_fd is not None:
                                os.close(output_fd)
                            os.close(input_fd)
                os.fsync(current_destination_fd)

            copy_directory(source_fd, destination_fd)
        finally:
            os.close(destination_fd)
    finally:
        os.close(source_fd)


class ManagedExecutor:
    """Managed adapter: canonical action lifecycle surrounds every effect.

    The sandbox directory is ephemeral adapter runtime state only.  Policy,
    grants, probes, action status, and completion evidence are written through
    ``TaskStore`` before/after the adapter starts a subprocess.
    """

    def __init__(self, store: TaskStore, gateway: PolicyGateway, runtime_root: Path, actor: str = "managed-executor") -> None:
        self.store = store
        self.gateway = gateway
        self.runtime_root = runtime_root.resolve()
        self.actor = actor
        self.sandbox = CodexSandbox(self.runtime_root / "sandboxes")

    @staticmethod
    def _target_path(action: ActionDescriptor) -> Path | None:
        if not action.repository_root or not action.targets:
            return None
        target = Path(action.targets[0])
        return target.resolve() if target.is_absolute() else (Path(action.repository_root) / target).resolve()

    def _preflight(self, action: ActionDescriptor) -> dict[str, object]:
        target = self._target_path(action)
        if not action.repository_root:
            return {"result": "fail", "target_fingerprint": "", "facts": {"reason": "missing root"}, "redaction_summary": {}}
        root = Path(action.repository_root).resolve()
        if target is not None and target != root and root not in target.parents:
            return {"result": "fail", "target_fingerprint": str(target), "facts": {"reason": "scope escape"}, "redaction_summary": {}}
        if action.kind in {"process.run", "git.local"}:
            try:
                _assert_source_tree_has_no_symlinks(root)
            except OSError:
                return {"result": "fail", "target_fingerprint": str(root), "facts": {"reason": "source tree contains a symlink"}, "redaction_summary": {}}
        return {"result": "pass", "target_fingerprint": str(target or root), "facts": {"root": str(root), "target_before_hash": _hash_path(target) if target else None, "command_digest": hashlib.sha256("\0".join(action.command or ()).encode()).hexdigest()}, "redaction_summary": {}}

    def _postflight(self, action: ActionDescriptor, returncode: int) -> dict[str, object]:
        target = self._target_path(action)
        return {"result": "pass" if returncode == 0 else "fail", "target_fingerprint": str(target or Path(action.repository_root or "").resolve()), "facts": {"returncode": returncode, "target_after_hash": _hash_path(target) if target else None}, "redaction_summary": {}}

    def _start(self, action: ActionDescriptor, approvals: tuple[Approval, ...]) -> dict[str, object]:
        started = self.store.begin_managed_action(action, self.actor, self.gateway, self._preflight(action), approvals)
        decision = started["decision"]
        if decision.outcome == "unsupported":
            raise UnsupportedActionError(decision.reason)
        if decision.outcome != "grant" or not decision.grant_id:
            raise PolicyDeniedError(f"{decision.outcome}: {decision.reason}; digest={decision.action_digest}")
        return started

    def write_text(self, action: ActionDescriptor, content: str, approvals: tuple[Approval, ...] = ()) -> ExecutionResult:
        if action.kind != "filesystem.write" or len(action.targets) != 1:
            raise UnsupportedActionError("write_text requires one filesystem.write target")
        started = self._start(action, approvals)
        root = Path(action.repository_root or "").resolve()
        target = Path(action.targets[0])
        relative = target.resolve().relative_to(root) if target.is_absolute() else target
        # The dedicated filesystem adapter writes the one canonical target
        # directly.  It never exposes a repository-wide process write grant.
        began = datetime.now(timezone.utc).isoformat()
        grant = started["grant"]
        state = started["state"]
        assert isinstance(grant, dict) and isinstance(state, dict)
        scope = grant["effective_scope"]
        assert isinstance(scope, dict)
        root_fd: int | None = None
        try:
            self.store.verify_execution_grant(action.task_id, int(state["revision"]), self.actor, str(grant["lease_token"]), action.action_id, str(grant["grant_id"]))
            root_fd = _open_bound_repository_root(str(scope["repository_root"]), str(scope["repository_root_identity"]))
            _write_text_dirfd(root_fd, relative, content)
        except OSError as exc:
            ended = datetime.now(timezone.utc).isoformat()
            finished = self.store.finish_managed_action(action.task_id, int(state["revision"]), self.actor, str(grant["lease_token"]), action.action_id, str(grant["grant_id"]), 74, {"result": "fail", "facts": {"reason": "filesystem confinement rejected write", "error_class": type(exc).__name__}, "redaction_summary": {}})
            return ExecutionResult(action.action_id, action.digest, str(grant["grant_id"]), 74, "", "managed write rejected by confinement", began, ended, int(finished["state"]["revision"]))
        finally:
            if root_fd is not None:
                os.close(root_fd)
        ended = datetime.now(timezone.utc).isoformat()
        finished = self.store.finish_managed_action(action.task_id, int(state["revision"]), self.actor, str(grant["lease_token"]), action.action_id, str(grant["grant_id"]), 0, self._postflight(action, 0))
        return ExecutionResult(action.action_id, action.digest, str(grant["grant_id"]), 0, "", "", began, ended, int(finished["state"]["revision"]))

    def run_process(
        self,
        action: ActionDescriptor,
        approvals: tuple[Approval, ...] = (),
        timeout_seconds: float = 60,
        validation: ValidationSpec | None = None,
    ) -> ExecutionResult:
        if action.kind not in {"process.run", "git.local"} or not action.command:
            raise UnsupportedActionError("run_process requires a supported managed command action")
        validation_spec = validation or ValidationSpec()
        effective_timeout = validation_spec.timeout_seconds or timeout_seconds
        started = self._start(action, approvals)
        workspace = Path(tempfile.mkdtemp(prefix="process-workspace-", dir=self.runtime_root))
        root_fd: int | None = None
        try:
            try:
                self.store.verify_execution_grant(action.task_id, int(started["state"]["revision"]), self.actor, str(started["grant"]["lease_token"]), action.action_id, str(started["grant"]["grant_id"]))
                grant = started["grant"]
                assert isinstance(grant, dict)
                scope = grant["effective_scope"]
                assert isinstance(scope, dict)
                root_fd = _open_bound_repository_root(str(scope["repository_root"]), str(scope["repository_root_identity"]))
            except OSError as exc:
                return self._finish_without_effect(action, started, 74, "process source confinement rejected", type(exc).__name__, failure_category="safety_gap")
            try:
                _copy_source_snapshot_no_follow(root_fd, workspace / "repo")
            except OSError as exc:
                return self._finish_without_effect(action, started, 74, "process snapshot confinement rejected", type(exc).__name__, failure_category="safety_gap")
            copied_root = workspace / "repo"
            try:
                _assert_source_tree_has_no_symlinks(copied_root)
            except OSError as exc:
                return self._finish_without_effect(action, started, 74, "process copy confinement rejected", type(exc).__name__, failure_category="safety_gap")
            before = _tree_snapshot(copied_root)
            # Copying can be slow.  Verify once more at the actual process
            # start boundary so an expired/revoked lease cannot authorize a
            # command merely because it was valid before preparation began.
            validation_tmp = workspace / "tmp"
            validation_tmp.mkdir(mode=0o700)
            self.store.verify_execution_grant(action.task_id, int(started["state"]["revision"]), self.actor, str(started["grant"]["lease_token"]), action.action_id, str(started["grant"]["grant_id"]))
            try:
                validation_cwd = validation_spec.resolve_cwd(copied_root)
            except (OSError, ValueError, ValidationError) as exc:
                return self._finish_without_effect(action, started, 74, "validation cwd is unavailable in the disposable workspace", type(exc).__name__, failure_category="validation_environment_failure")
            return self._execute(
                action,
                started,
                action.command,
                copied_root,
                effective_timeout,
                postflight_root=copied_root,
                before_snapshot=before,
                validation_tmp=validation_tmp,
                validation_spec=validation_spec,
                validation_cwd=validation_cwd,
            )
        finally:
            # Runtime workspaces are adapter scratch space, never durable Task
            # evidence.  Probe summaries are persisted through TaskStore.
            shutil.rmtree(workspace, ignore_errors=True)
            if root_fd is not None:
                os.close(root_fd)

    @staticmethod
    def _safe_worktree_path(runtime_root: Path, value: Path) -> Path:
        root = (runtime_root / "worktrees").resolve()
        candidate = value.resolve()
        if candidate == root or root not in candidate.parents:
            raise UnsupportedActionError("managed worktree path must remain under executor runtime/worktrees")
        return candidate

    @staticmethod
    def _safe_git_identifier(value: str, label: str) -> str:
        if not value or any(character in value for character in ("\x00", "\n", "\r", " ")) or value.startswith("-"):
            raise UnsupportedActionError(f"managed Git {label} is not a safe identifier")
        return value

    def create_worktree(
        self,
        action: ActionDescriptor,
        *,
        worktree_path: Path,
        branch: str,
        base_ref: str,
        approvals: tuple[Approval, ...] = (),
        timeout_seconds: float = 60,
    ) -> ExecutionResult:
        """Create one isolated local worktree through the managed action path.

        This is intentionally a narrow adapter rather than a generic shell
        escape hatch.  It cannot push, configure remotes, or choose a path
        outside nous-owned runtime storage; the TaskStore journals the normal
        action/grant/probe lifecycle around the single local Git effect.
        """
        if action.kind != "git.local" or not action.repository_root or action.targets != (".",):
            raise UnsupportedActionError("create_worktree requires a root-scoped git.local action")
        branch = self._safe_git_identifier(branch, "branch")
        base_ref = self._safe_git_identifier(base_ref, "base ref")
        destination = self._safe_worktree_path(self.runtime_root, worktree_path)
        argv = ("worktree", "add", "-b", branch, str(destination), base_ref)
        if action.command != argv:
            raise UnsupportedActionError("Git worktree action descriptor does not exactly match the adapter operation")
        started = self._start(action, approvals)
        grant = started["grant"]
        state = started["state"]
        assert isinstance(grant, dict) and isinstance(state, dict)
        scope = grant["effective_scope"]
        assert isinstance(scope, dict)
        root_fd: int | None = None
        began = datetime.now(timezone.utc).isoformat()
        try:
            self.store.verify_execution_grant(action.task_id, int(state["revision"]), self.actor, str(grant["lease_token"]), action.action_id, str(grant["grant_id"]))
            root_fd = _open_bound_repository_root(str(scope["repository_root"]), str(scope["repository_root_identity"]))
            destination.parent.mkdir(parents=True, exist_ok=True)
            before = self._git_probe_at_fd(root_fd)
            # On the supported Linux runtime, the retained fd gives Git the
            # exact preflighted repository even if its pathname is replaced.
            result = subprocess.run(
                ("git", "-C", f"/proc/self/fd/{root_fd}", *argv),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
                # The pathname names the inherited descriptor inside the Git
                # child; without pass_fds, subprocess closes it before Git
                # can resolve the confined repository.
                pass_fds=(root_fd,),
            )
            ended = datetime.now(timezone.utc).isoformat()
            after = self._git_probe_at_fd(root_fd)
            postflight = {
                "result": "pass" if result.returncode == 0 and after["repository_probe"] == "pass" else "fail",
                "facts": {
                    "returncode": result.returncode,
                    "operation": "worktree_add",
                    "worktree_exists": destination.is_dir(),
                    "branch": branch,
                    "before_head": before["head"],
                    "after_head": after["head"],
                    "before_dirty_paths": before["dirty_paths"],
                    "after_dirty_paths": after["dirty_paths"],
                },
                "redaction_summary": {},
            }
            finished = self.store.finish_managed_action(action.task_id, int(state["revision"]), self.actor, str(grant["lease_token"]), action.action_id, str(grant["grant_id"]), result.returncode, postflight)
            return ExecutionResult(action.action_id, action.digest, str(grant["grant_id"]), result.returncode, result.stdout, result.stderr, began, ended, int(finished["state"]["revision"]))
        except (OSError, subprocess.TimeoutExpired) as exc:
            ended = datetime.now(timezone.utc).isoformat()
            finished = self.store.finish_managed_action(action.task_id, int(state["revision"]), self.actor, str(grant["lease_token"]), action.action_id, str(grant["grant_id"]), 74, {"result": "fail", "facts": {"reason": "managed Git confinement or timeout rejected worktree", "error_class": type(exc).__name__}, "redaction_summary": {}})
            return ExecutionResult(action.action_id, action.digest, str(grant["grant_id"]), 74, "", "managed Git worktree rejected", began, ended, int(finished["state"]["revision"]))
        finally:
            if root_fd is not None:
                os.close(root_fd)

    @staticmethod
    def _git_probe_at_fd(root_fd: int) -> dict[str, object]:
        """Typed local Git probe used as evidence, never a remote operation."""
        root = f"/proc/self/fd/{root_fd}"
        head = subprocess.run(("git", "-C", root, "rev-parse", "HEAD"), text=True, capture_output=True, pass_fds=(root_fd,), check=False)
        status = subprocess.run(("git", "-C", root, "status", "--porcelain=v1"), text=True, capture_output=True, pass_fds=(root_fd,), check=False)
        worktrees = subprocess.run(("git", "-C", root, "worktree", "list", "--porcelain"), text=True, capture_output=True, pass_fds=(root_fd,), check=False)
        branches = subprocess.run(("git", "-C", root, "for-each-ref", "--format=%(refname:short)", "refs/heads"), text=True, capture_output=True, pass_fds=(root_fd,), check=False)
        dirty = [] if status.returncode else sorted(line[3:] for line in status.stdout.splitlines() if len(line) >= 4)
        worktree_paths = sorted(
            line.removeprefix("worktree ")
            for line in worktrees.stdout.splitlines()
            if line.startswith("worktree ")
        ) if worktrees.returncode == 0 else []
        return {
            "head": head.stdout.strip() if head.returncode == 0 else None,
            "dirty_paths": dirty,
            "worktree_paths": worktree_paths,
            "branches": sorted(line for line in branches.stdout.splitlines() if line) if branches.returncode == 0 else [],
            "repository_probe": "pass" if head.returncode == 0 and status.returncode == 0 and worktrees.returncode == 0 and branches.returncode == 0 else "fail",
        }

    @staticmethod
    def _validate_local_git_command(action: ActionDescriptor) -> None:
        command = action.command or ()
        if not command or command[0] in {"push", "fetch", "pull", "clone", "remote", "submodule", "config"}:
            raise UnsupportedActionError("remote or configuration Git operations are not a managed local Git surface")
        operation = command[0]
        allowed = {"status", "diff", "rev-parse", "worktree", "branch", "add", "commit", "format-patch", "apply", "cherry-pick"}
        if operation not in allowed or any(part in {"-c", "--upload-pack", "--receive-pack"} for part in command):
            raise UnsupportedActionError("Git command is outside the narrow managed local Git allowlist")
        if operation == "worktree" and (len(command) < 2 or command[1] not in {"list", "remove"}):
            raise UnsupportedActionError("worktree add is available only through create_worktree; unsupported worktree operation")
        if operation == "branch" and len(command) > 1 and command[1] not in {"--show-current", "--delete", "-d", "-D"}:
            raise UnsupportedActionError("branch creation is available only through create_worktree")
        if action.loop_id != "root":
            # A Child can only stage the exact targets whose semantic
            # resource contracts were pinned into its grant.  `add --all`
            # and generic patch/apply/cherry-pick operations would otherwise
            # turn a single file reservation into whole-worktree authority.
            if operation == "add":
                if "--all" in command or "." in command or "--" not in command:
                    raise UnsupportedActionError("child git add requires explicit declared targets after --")
                operands = command[command.index("--") + 1 :]
                if not operands or tuple(operands) != action.targets:
                    raise UnsupportedActionError("child git add operands must exactly match action targets")
            elif operation == "commit":
                # Do not permit --all/-a/--include/pathspecs or any other
                # staging switch after the pre-effect staged-path probe.  A
                # Child commit may commit only the exact index produced by a
                # preceding managed git-add command over declared targets.
                if len(command) != 3 or command[1] != "-m" or not command[2]:
                    raise UnsupportedActionError("child git commit must be exactly: commit -m <message>")
            elif operation not in {"status", "diff", "rev-parse"}:
                raise UnsupportedActionError("child Git operation is outside its narrow isolated write surface")

    @staticmethod
    def _staged_paths_at_fd(root_fd: int) -> list[str]:
        result = subprocess.run(
            ("git", "-C", f"/proc/self/fd/{root_fd}", "diff", "--cached", "--name-only", "-z"),
            text=True,
            capture_output=True,
            pass_fds=(root_fd,),
            check=False,
        )
        if result.returncode != 0:
            raise OSError("cannot probe staged Git paths")
        return sorted(path for path in result.stdout.split("\0") if path)

    @staticmethod
    def _staged_paths_within_targets(paths: list[str], targets: tuple[str, ...]) -> bool:
        allowed = {Path(target).as_posix() for target in targets}
        return bool(paths) and all(
            path in allowed or any(path.startswith(target.rstrip("/") + "/") for target in allowed)
            for path in paths
        )

    def run_local_git(
        self,
        action: ActionDescriptor,
        approvals: tuple[Approval, ...] = (),
        timeout_seconds: float = 60,
    ) -> ExecutionResult:
        """Run one allowlisted local Git operation through the managed boundary."""
        if action.kind != "git.local" or not action.command:
            raise UnsupportedActionError("run_local_git requires a git.local ActionDescriptor")
        self._validate_local_git_command(action)
        started = self._start(action, approvals)
        grant = started["grant"]
        state = started["state"]
        assert isinstance(grant, dict) and isinstance(state, dict)
        scope = grant["effective_scope"]
        assert isinstance(scope, dict)
        began = datetime.now(timezone.utc).isoformat()
        root_fd: int | None = None
        cleanup_fd: int | None = None
        try:
            self.store.verify_execution_grant(action.task_id, int(state["revision"]), self.actor, str(grant["lease_token"]), action.action_id, str(grant["grant_id"]))
            root_fd = _open_bound_repository_root(str(scope["repository_root"]), str(scope["repository_root_identity"]))
            before = self._git_probe_at_fd(root_fd)
            cleanup_before: dict[str, object] | None = None
            cleanup_target = grant.get("cleanup_target")
            if cleanup_target is not None:
                if action.command[:2] != ("worktree", "remove"):
                    return self._finish_without_effect(action, started, 73, "cleanup target is bound only to worktree removal", "ValidationError")
                cleanup_fd = _open_bound_repository_root(
                    str(cleanup_target["worktree_path"]),
                    str(cleanup_target["worktree_identity"]),
                )
                cleanup_before = self._git_probe_at_fd(cleanup_fd)
                if cleanup_before["repository_probe"] != "pass" or cleanup_before["dirty_paths"] != []:
                    return self._finish_without_effect(action, started, 73, "child worktree is dirty or cannot be probed before cleanup", "ValidationError")
            if action.loop_id != "root" and action.command[0] == "commit":
                staged = self._staged_paths_at_fd(root_fd)
                if not self._staged_paths_within_targets(staged, action.targets):
                    return self._finish_without_effect(action, started, 73, "child staged paths exceed declared semantic targets", "ValidationError")
            result = subprocess.run(
                ("git", "-C", f"/proc/self/fd/{root_fd}", *action.command),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
                pass_fds=(root_fd,),
            )
            after = self._git_probe_at_fd(root_fd)
            ended = datetime.now(timezone.utc).isoformat()
            postflight = {
                "result": "pass" if result.returncode == 0 and after["repository_probe"] == "pass" else "fail",
                "facts": {
                    "returncode": result.returncode,
                    "operation": action.command[0],
                    "before_head": before["head"],
                    "after_head": after["head"],
                    "before_dirty_paths": before["dirty_paths"],
                    "after_dirty_paths": after["dirty_paths"],
                    "before_worktree_paths": before["worktree_paths"],
                    "after_worktree_paths": after["worktree_paths"],
                    "before_branches": before["branches"],
                    "after_branches": after["branches"],
                    "cleanup_before_repository_probe": cleanup_before["repository_probe"] if cleanup_before else None,
                    "cleanup_before_dirty_paths": cleanup_before["dirty_paths"] if cleanup_before else None,
                },
                "redaction_summary": {},
            }
            finished = self.store.finish_managed_action(action.task_id, int(state["revision"]), self.actor, str(grant["lease_token"]), action.action_id, str(grant["grant_id"]), result.returncode, postflight)
            return ExecutionResult(action.action_id, action.digest, str(grant["grant_id"]), result.returncode, result.stdout, result.stderr, began, ended, int(finished["state"]["revision"]))
        except (OSError, subprocess.TimeoutExpired) as exc:
            ended = datetime.now(timezone.utc).isoformat()
            finished = self.store.finish_managed_action(action.task_id, int(state["revision"]), self.actor, str(grant["lease_token"]), action.action_id, str(grant["grant_id"]), 74, {"result": "fail", "facts": {"reason": "managed local Git confinement or timeout rejected operation", "error_class": type(exc).__name__}, "redaction_summary": {}})
            return ExecutionResult(action.action_id, action.digest, str(grant["grant_id"]), 74, "", "managed local Git operation rejected", began, ended, int(finished["state"]["revision"]))
        finally:
            if cleanup_fd is not None:
                os.close(cleanup_fd)
            if root_fd is not None:
                os.close(root_fd)

    def _finish_without_effect(
        self,
        action: ActionDescriptor,
        started: dict[str, object],
        returncode: int,
        reason: str,
        error_class: str,
        *,
        failure_category: str | None = None,
    ) -> ExecutionResult:
        grant = started["grant"]
        state = started["state"]
        assert isinstance(grant, dict) and isinstance(state, dict)
        timestamp = datetime.now(timezone.utc).isoformat()
        facts = {
            "reason": reason,
            "error_class": error_class,
            "returncode": returncode,
            "actual_changed_paths": [],
            "unexpected_changed_paths": [],
        }
        if failure_category is not None:
            facts["failure_category"] = failure_category
        finished = self.store.finish_managed_action(action.task_id, int(state["revision"]), self.actor, str(grant["lease_token"]), action.action_id, str(grant["grant_id"]), returncode, {"result": "fail", "facts": facts, "redaction_summary": {}})
        return ExecutionResult(action.action_id, action.digest, str(grant["grant_id"]), returncode, "", reason, timestamp, timestamp, int(finished["state"]["revision"]))

    def _execute(
        self,
        action: ActionDescriptor,
        started: dict[str, object],
        argv: tuple[str, ...],
        root: Path,
        timeout_seconds: float = 60,
        postflight_root: Path | None = None,
        before_snapshot: dict[str, str] | None = None,
        validation_tmp: Path | None = None,
        validation_spec: ValidationSpec | None = None,
        validation_cwd: Path | None = None,
    ) -> ExecutionResult:
        grant = started["grant"]
        assert isinstance(grant, dict)
        state = started["state"]
        assert isinstance(state, dict)
        began = datetime.now(timezone.utc).isoformat()
        spec = validation_spec or ValidationSpec()
        completed = self.sandbox.run(
            argv,
            writable_root=root,
            timeout_seconds=timeout_seconds,
            # Validation agents must not create interpreter cache files in
            # the disposable repository snapshot.  Any other mutation is
            # still detected by the before/after tree probe below.
            extra_env={
                **spec.environment_dict(),
                # These two controls are owned by the managed adapter.  A
                # validation policy may add variables, but cannot re-enable
                # bytecode writes or redirect its temporary files into the
                # copied source tree.
                "PYTHONDONTWRITEBYTECODE": "1",
                **({"TMPDIR": str(validation_tmp)} if validation_tmp is not None else {}),
            },
            write_roots=(validation_tmp,) if validation_tmp is not None else (),
            cwd=validation_cwd or root,
        )
        ended = datetime.now(timezone.utc).isoformat()
        returncode = completed.returncode
        postflight = self._postflight(action, returncode)
        base_facts = dict(postflight.get("facts", {}))
        base_facts.update({"started_at": began, "completed_at": ended})
        postflight = {**postflight, "facts": base_facts}
        if postflight_root is not None and before_snapshot is not None:
            after = _tree_snapshot(postflight_root)
            changed = _changed_paths(before_snapshot, after)
            unexpected = [path for path in changed if not spec.is_allowed_side_effect(path)]
            failure_category = _validation_failure_category(returncode, completed.stderr)
            stdout_bytes = len(completed.stdout.encode("utf-8", errors="replace"))
            stderr_bytes = len(completed.stderr.encode("utf-8", errors="replace"))
            output_too_large = spec.max_output_bytes is not None and max(stdout_bytes, stderr_bytes) > spec.max_output_bytes
            if output_too_large:
                returncode = 75
                failure_category = "validation_environment_failure"
            # A process executes in a disposable copy.  Generated caches and
            # logs are allowed only when the command policy names them.  Any
            # other mutation remains fail-closed and is never copied back.
            if unexpected:
                returncode = 73
                failure_category = "safety_gap"
                postflight = {
                    "result": "fail",
                    "target_fingerprint": str(postflight_root),
                    "facts": {
                        "returncode": returncode,
                        "actual_changed_paths": changed,
                        "unexpected_changed_paths": unexpected,
                        "allowed_side_effects": list(spec.allowed_side_effects),
                        "reason": "process wrote inside isolated workspace",
                        "failure_category": failure_category,
                        "started_at": began,
                        "completed_at": ended,
                        "validation_cwd": spec.cwd,
                        "stdout_bytes": stdout_bytes,
                        "stderr_bytes": stderr_bytes,
                        "stderr_summary": safe_text_summary(completed.stderr),
                    },
                    "redaction_summary": {},
                }
            elif output_too_large:
                postflight = {
                    "result": "fail",
                    "target_fingerprint": str(postflight_root),
                    "facts": {
                        "returncode": returncode,
                        "failure_category": failure_category,
                        "reason": "validation process output exceeded configured bound",
                        "stdout_bytes": stdout_bytes,
                        "stderr_bytes": stderr_bytes,
                        "max_output_bytes": spec.max_output_bytes,
                        "started_at": began,
                        "completed_at": ended,
                        "validation_cwd": spec.cwd,
                        "actual_changed_paths": changed,
                        "unexpected_changed_paths": [],
                        "allowed_side_effects": list(spec.allowed_side_effects),
                        "stderr_summary": safe_text_summary(completed.stderr),
                    },
                    "redaction_summary": {},
                }
            else:
                facts = dict(postflight.get("facts", {}))
                facts.update(
                    {
                        "actual_changed_paths": changed,
                        "unexpected_changed_paths": [],
                        "allowed_side_effects": list(spec.allowed_side_effects),
                        "failure_category": failure_category or ("test_gap" if returncode != 0 else None),
                        "validation_cwd": spec.cwd,
                        "stdout_bytes": stdout_bytes,
                        "stderr_bytes": stderr_bytes,
                        "stderr_summary": safe_text_summary(completed.stderr),
                    }
                )
                postflight = {**postflight, "facts": facts}
        finished = self.store.finish_managed_action(action.task_id, int(state["revision"]), self.actor, str(grant["lease_token"]), action.action_id, str(grant["grant_id"]), returncode, postflight)
        return ExecutionResult(action.action_id, action.digest, str(grant["grant_id"]), returncode, completed.stdout, completed.stderr, began, ended, int(finished["state"]["revision"]))
