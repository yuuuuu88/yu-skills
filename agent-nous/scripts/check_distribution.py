from __future__ import annotations

import argparse
import subprocess
import sys
import tarfile
import zipfile
from io import BytesIO
from pathlib import Path


FORBIDDEN_PREFIXES = (
    ".nous/",
    "tasks/",
    "requests/",
    "runtime/",
    "sandboxes/",
    "agent-runs/",
    "eval-runs/",
)


def _is_forbidden(path: str) -> bool:
    normalized = path.lstrip("./")
    return any(normalized == prefix.rstrip("/") or normalized.startswith(prefix) for prefix in FORBIDDEN_PREFIXES)


def _tracked_paths(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
    )
    return [item for item in result.stdout.decode("utf-8").split("\0") if item]


def _archive_paths(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "archive", "HEAD"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
    )
    with tarfile.open(fileobj=BytesIO(result.stdout), mode="r:") as archive:
        return [member.name for member in archive.getmembers() if member.isfile()]


def _wheel_paths(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as wheel:
        return wheel.namelist()


def _check(label: str, paths: list[str]) -> list[str]:
    return [f"{label}: {path}" for path in paths if _is_forbidden(path)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that runtime state is absent from the distribution.")
    parser.add_argument("--wheel", type=Path, help="also inspect a built wheel")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    errors = _check("tracked", _tracked_paths(root))
    errors.extend(_check("archive", _archive_paths(root)))
    if args.wheel:
        errors.extend(_check("wheel", _wheel_paths(args.wheel.resolve())))
    if errors:
        print("distribution check failed:", file=sys.stderr)
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("distribution check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
