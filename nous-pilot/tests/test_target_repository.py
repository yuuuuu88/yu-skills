import subprocess
from pathlib import Path

import pytest

from nous_pilot.models import ContractError
from nous_pilot.workspace import ChangeApplyDenied, DraftChange, TargetRepositoryAdapter


def _git_repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    commands = [
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.invalid"],
        ["git", "config", "user.name", "Nous Test"],
    ]
    for command in commands:
        subprocess.run(command, cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (root / "README.md").write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    return root, revision


def test_target_repository_preview_apply_and_patch_are_separate(tmp_path: Path) -> None:
    root, revision = _git_repo(tmp_path)
    adapter = TargetRepositoryAdapter(root)
    change = DraftChange("README.md", "after\n")
    preview = adapter.preview([change], base_revision=revision)
    assert preview.applies_cleanly
    assert preview.changed_paths == ("README.md",)
    patch = adapter.commit_candidate([change], tmp_path / "candidate.patch")
    assert "--- a/README.md" in patch.read_text(encoding="utf-8")
    with pytest.raises(ChangeApplyDenied):
        adapter.apply([change], base_revision=revision)
    applied = adapter.apply([change], base_revision=revision, human_approved=True)
    assert applied.paths == ("README.md",)
    assert (root / "README.md").read_text(encoding="utf-8") == "after\n"


def test_target_repository_rejects_git_metadata_and_revision_drift(tmp_path: Path) -> None:
    root, revision = _git_repo(tmp_path)
    adapter = TargetRepositoryAdapter(root)
    with pytest.raises(ContractError):
        adapter.preview([DraftChange(".git/config", "bad")])
    with pytest.raises(ChangeApplyDenied):
        adapter.apply([DraftChange("README.md", "after\n")], base_revision="wrong", human_approved=True)
    with pytest.raises(ContractError):
        adapter.commit_candidate([DraftChange("README.md", "after\n")], root / "candidate.patch")


def test_target_repository_rejects_duplicate_paths(tmp_path: Path) -> None:
    root, _ = _git_repo(tmp_path)
    adapter = TargetRepositoryAdapter(root)
    with pytest.raises(ContractError):
        adapter.preview([DraftChange("README.md", "one\n"), DraftChange("README.md", "two\n")])


def test_target_repository_checks_untracked_dirty_state_and_does_not_touch_git(tmp_path: Path) -> None:
    root, revision = _git_repo(tmp_path)
    before_git = (root / ".git" / "HEAD").read_bytes()
    (root / "untracked.txt").write_text("local\n", encoding="utf-8")
    adapter = TargetRepositoryAdapter(root)
    preview = adapter.preview([DraftChange("README.md", "after\n")], base_revision=revision)
    assert preview.dirty
    with pytest.raises(ChangeApplyDenied):
        adapter.apply([DraftChange("README.md", "after\n")], base_revision=revision, human_approved=True)
    assert (root / ".git" / "HEAD").read_bytes() == before_git


def test_target_repository_rejects_symlinked_parent(tmp_path: Path) -> None:
    root, _ = _git_repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)
    adapter = TargetRepositoryAdapter(root)
    with pytest.raises(ContractError):
        adapter.preview([DraftChange("linked/file.txt", "bad")])


def test_target_repository_ignores_ignored_files_but_rejects_patch_symlinks(tmp_path: Path) -> None:
    root, revision = _git_repo(tmp_path)
    (root / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "ignore"], cwd=root, check=True)
    (root / "ignored.txt").write_text("local\n", encoding="utf-8")
    adapter = TargetRepositoryAdapter(root)
    preview = adapter.preview([DraftChange("README.md", "after\n")])
    assert not preview.dirty
    assert "ignored.txt" not in adapter.dirty_paths()
    outside = tmp_path / "outside.patch"
    outside.write_text("do not overwrite\n", encoding="utf-8")
    (tmp_path / "candidate.patch").symlink_to(outside)
    with pytest.raises(ContractError):
        adapter.commit_candidate([DraftChange("README.md", "after\n")], tmp_path / "candidate.patch")
