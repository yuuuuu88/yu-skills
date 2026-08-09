from __future__ import annotations

import difflib
import hashlib
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Iterable

from .models import ContractError, safe_identifier


class ChangeApplyDenied(ContractError):
    """Raised when a staged change crosses the local human-gated boundary."""


@dataclass(frozen=True)
class DraftChange:
    path: str
    content: str

    def __post_init__(self) -> None:
        path = Path(self.path)
        windows_path = PureWindowsPath(self.path)
        if (
            not self.path.strip()
            or "\\" in self.path
            or path.is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
            or "." in path.parts
            or "." in windows_path.parts
            or ".." in path.parts
            or ".." in windows_path.parts
        ):
            raise ContractError("change path must be a relative path inside the workspace")
        if ".git" in path.parts:
            raise ContractError("change cannot target Git metadata")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StagedWorkspace:
    root: Path
    changes: tuple[DraftChange, ...]

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(item.path for item in self.changes)


@dataclass(frozen=True)
class ChangePreview:
    repository_root: Path
    paths: tuple[str, ...]
    current_revision: str | None
    requested_revision: str | None
    dirty: bool
    changed_paths: tuple[str, ...]

    @property
    def applies_cleanly(self) -> bool:
        return not self.dirty and (
            self.requested_revision is None or self.requested_revision == self.current_revision
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "repository_root": str(self.repository_root),
            "paths": list(self.paths),
            "current_revision": self.current_revision,
            "requested_revision": self.requested_revision,
            "dirty": self.dirty,
            "changed_paths": list(self.changed_paths),
            "applies_cleanly": self.applies_cleanly,
        }


@dataclass(frozen=True)
class AppliedChanges:
    repository_root: Path
    paths: tuple[str, ...]
    revision_before: str | None


class TargetRepositoryAdapter:
    """Preview and apply staged files without touching Git metadata.

    Preview and patch generation are autonomous local operations. Applying
    files requires an explicit human approval flag and a clean repository by
    default. Git commit/push is intentionally not implemented here.
    """

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.expanduser().resolve()
        if not self.repository_root.is_dir():
            raise ContractError("target repository must be an existing directory")

    def _git(self, *args: str) -> str | None:
        git = shutil.which("git", path=os.defpath)
        if git is None:
            return None
        try:
            result = subprocess.run(
                [git, *args],
                cwd=self.repository_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={
                    "PATH": os.defpath,
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_OPTIONAL_LOCKS": "0",
                    "GIT_TERMINAL_PROMPT": "0",
                },
                timeout=10,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    def revision(self) -> str | None:
        return self._git("rev-parse", "HEAD")

    def dirty(self) -> bool:
        # Ignored runtime/build artifacts are intentionally not part of the
        # change gate. Untracked files remain visible and still block apply.
        status = self._git("status", "--porcelain=v1", "--untracked-files=all")
        return status is None or bool(status)

    def dirty_paths(self) -> tuple[str, ...]:
        status = self._git("status", "--porcelain=v1", "--untracked-files=all")
        if status is None:
            raise ContractError("target repository is not a readable Git repository")
        return tuple(line[3:] if len(line) >= 3 else line for line in status.splitlines() if line)

    def _target(self, path: str) -> Path:
        change = DraftChange(path, "")
        target = (self.repository_root / change.path).resolve()
        if self.repository_root not in target.parents:
            raise ContractError("change escaped target repository")
        if ".git" in Path(change.path).parts:
            raise ContractError("change cannot target Git metadata")
        unresolved = self.repository_root / change.path
        if unresolved.is_symlink() or any(part.is_symlink() for part in unresolved.parents if part != self.repository_root):
            raise ContractError("change cannot overwrite a symlink")
        return target

    def preview(self, changes: Iterable[DraftChange], *, base_revision: str | None = None) -> ChangePreview:
        materialized = tuple(changes)
        paths = tuple(change.path for change in materialized)
        if len(paths) != len(set(paths)):
            raise ContractError("duplicate target paths are not allowed in one change set")
        current_revision = self.revision()
        if current_revision is None:
            raise ContractError("target repository must be a Git repository")
        if base_revision is not None and current_revision != base_revision:
            raise ContractError("target repository revision changed since the plan")
        changed_paths: list[str] = []
        for change in materialized:
            target = self._target(change.path)
            current = target.read_text(encoding="utf-8") if target.is_file() else None
            if current != change.content:
                changed_paths.append(change.path)
        return ChangePreview(
            self.repository_root,
            paths,
            current_revision,
            base_revision,
            self.dirty(),
            tuple(changed_paths),
        )

    def apply(
        self,
        changes: Iterable[DraftChange],
        *,
        base_revision: str | None = None,
        human_approved: bool = False,
        allow_dirty: bool = False,
    ) -> AppliedChanges:
        if not human_approved:
            raise ChangeApplyDenied("local change application requires explicit human approval")
        materialized = tuple(changes)
        preview = self.preview(materialized)
        if base_revision is not None and preview.current_revision != base_revision:
            raise ChangeApplyDenied("target repository revision changed since the plan")
        if allow_dirty:
            raise ChangeApplyDenied("dirty-tree override is not supported; preview and clean the target repository first")
        if preview.dirty:
            raise ChangeApplyDenied("target repository has uncommitted changes")
        # Recheck identity immediately before creating any target-side temp
        # files. This is not a filesystem lock, but closes the common stale-plan
        # window without ever writing Git metadata.
        if self.revision() != preview.current_revision or self.dirty():
            raise ChangeApplyDenied("target repository changed after preview")
        targets = [(self._target(change.path), change) for change in materialized]
        revision_before = preview.current_revision
        temporary: list[tuple[Path, Path]] = []
        try:
            for target, change in targets:
                target.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as handle:
                    handle.write(change.content)
                    temporary.append((Path(handle.name), target))
            for temporary_path, target in temporary:
                os.replace(temporary_path, target)
        finally:
            for temporary_path, _ in temporary:
                temporary_path.unlink(missing_ok=True)
        return AppliedChanges(self.repository_root, tuple(change.path for change in materialized), revision_before)

    def commit_candidate(
        self,
        changes: Iterable[DraftChange],
        output_path: Path,
        *,
        base_revision: str | None = None,
    ) -> Path:
        """Write a patch artifact only; this never creates a Git commit."""
        materialized = tuple(changes)
        preview = self.preview(materialized, base_revision=base_revision)
        if preview.dirty:
            raise ChangeApplyDenied("commit candidate generation requires a clean target repository")
        raw_output = output_path.expanduser()
        if raw_output.is_symlink() or any(part.is_symlink() for part in raw_output.parents):
            raise ContractError("commit candidate output cannot follow a symlink")
        output = raw_output.resolve()
        if ".git" in output.parts:
            raise ContractError("commit candidate cannot be written inside Git metadata")
        if self.repository_root in output.parents or output == self.repository_root:
            raise ContractError("commit candidate must be written outside the target repository")
        output.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        for change in materialized:
            target = self._target(change.path)
            before = target.read_text(encoding="utf-8").splitlines(keepends=True) if target.is_file() else []
            after = change.content.splitlines(keepends=True)
            if before == after:
                continue
            lines.append(f"diff --git a/{change.path} b/{change.path}\n")
            lines.extend(difflib.unified_diff(
                before,
                after,
                fromfile=f"a/{change.path}" if target.exists() else "/dev/null",
                tofile=f"b/{change.path}",
            ))
        output.write_text("".join(lines), encoding="utf-8")
        return output


class WorkspaceManager:
    """Stage proposed changes outside the target repository by default."""

    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = runtime_root.resolve()
        self.runtime_root.mkdir(parents=True, exist_ok=True)

    def stage(self, workspace_id: str, changes: list[DraftChange]) -> StagedWorkspace:
        safe_identifier(workspace_id, "workspace_id")
        root = (self.runtime_root / workspace_id).resolve()
        if self.runtime_root not in root.parents:
            raise ContractError("workspace escaped runtime root")
        root.mkdir(parents=True, exist_ok=False)
        for change in changes:
            target = (root / change.path).resolve()
            if root not in target.parents:
                raise ContractError("change escaped staged workspace")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(change.content, encoding="utf-8")
        return StagedWorkspace(root, tuple(changes))


# Descriptive alias used by callers that treat the adapter as a lifecycle
# service rather than a Git integration. Both names retain the same safe API.
RepositoryChangeLifecycle = TargetRepositoryAdapter
