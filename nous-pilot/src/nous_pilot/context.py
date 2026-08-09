from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

_EXCLUDED = {".git", ".nous", "__pycache__", ".pytest_cache", ".venv", "node_modules"}
_ALLOWED_TOP_LEVEL = {"README.md", "AGENTS.md", ".codex", "pyproject.toml", "package.json", "docs", "src", "tests", "scripts", "examples"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class FileObservation:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class RepositoryContext:
    repository_root: Path
    files: tuple[FileObservation, ...]
    source_of_truth_paths: tuple[str, ...]
    validation_hints: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "repository_root": str(self.repository_root),
            "files": [item.__dict__ for item in self.files],
            "source_of_truth_paths": list(self.source_of_truth_paths),
            "validation_hints": list(self.validation_hints),
        }


def build_repository_context(root: Path, *, max_files: int = 500) -> RepositoryContext:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("repository root does not exist")
    observations: list[FileObservation] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in _EXCLUDED for part in relative.parts):
            continue
        if not path.is_file() or path.is_symlink():
            continue
        if relative.parts[0] not in _ALLOWED_TOP_LEVEL:
            continue
        observations.append(FileObservation(str(relative), path.stat().st_size, _sha256(path)))
        if len(observations) >= max_files:
            break
    source_paths = tuple(
        item.path for item in observations
        if item.path in {"README.md", "AGENTS.md", ".codex"} or item.path.startswith("docs/")
    )
    validation_hints = tuple(
        command for command in (
            "python3 -B -m pytest -q -p no:cacheprovider",
            "python3 -B scripts/validate_compile.py",
        )
        if any(item.path == "pyproject.toml" for item in observations)
    )
    return RepositoryContext(root, tuple(observations), source_paths, validation_hints)
