#!/usr/bin/env python3
"""Standalone repository-bootstrap validation used by CI.

The full repository-bootstrap skill can run a stricter superset when this
repository is checked out beside ``skills/``. This local validator keeps the
project distributable as a standalone repository and has no dependencies.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REQUIRED = (
    ".codex", "AGENTS.md", "README.md", "nous-pilot.toml", "pyproject.toml",
    "docs/README.md", "docs/product/brief.md", "docs/design/architecture/overview.md",
    "docs/design/contracts/agent-provider.md", "docs/operations/local-development.md",
    "docs/plans/bootstrap-plan.md",
)
KEYS = {"id", "type", "title", "status", "source_of_truth", "created", "updated", "related", "managed_by"}
LINKS = re.compile(r"(?<!!)!?\[[^\]]+\]\(([^)]+)\)")
SECRETS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
)


def validate(root: Path) -> list[str]:
    errors: list[str] = []
    for relative in REQUIRED:
        if not (root / relative).is_file():
            errors.append(f"missing required file: {relative}")
    for path in sorted(root.glob("docs/**/*.md")) + [root / "README.md", root / "AGENTS.md", root / ".codex"]:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(root).as_posix()
        if not text.startswith("---\n") or "\n---\n" not in text[4:]:
            errors.append(f"frontmatter missing: {relative}")
        else:
            header = text[4:text.find("\n---\n", 4)]
            keys = {line.split(":", 1)[0] for line in header.splitlines() if ":" in line}
            for key in sorted(KEYS - keys):
                errors.append(f"{relative} missing frontmatter key: {key}")
        for pattern in SECRETS:
            if pattern.search(text):
                errors.append(f"secret-like value: {relative}")
        for target in LINKS.findall(text):
            target = target.strip().strip("<>")
            if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            if not (path.parent / target.split("#", 1)[0]).resolve().exists():
                errors.append(f"broken link in {relative}: {target}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path, nargs="?", default=Path("."))
    args = parser.parse_args()
    root = args.root.resolve()
    errors = validate(root)
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    if errors:
        return 1
    print(f"ok: bootstrap checks passed for {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
