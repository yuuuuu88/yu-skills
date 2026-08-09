#!/usr/bin/env python3
"""Validate the repository-bootstrap baseline without external dependencies."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


REQUIRED_FILES = [
    ".codex",
    "AGENTS.md",
    "README.md",
    "docs/README.md",
    "docs/product/README.md",
    "docs/product/brief.md",
    "docs/design/README.md",
    "docs/design/architecture/README.md",
    "docs/design/architecture/overview.md",
    "docs/design/features/README.md",
    "docs/design/features/initial-slice.md",
    "docs/design/contracts/README.md",
    "docs/design/cross-cutting/README.md",
    "docs/design/decisions/README.md",
    "docs/operations/README.md",
    "docs/operations/local-development.md",
    "docs/operations/runbooks/README.md",
    "docs/research/README.md",
    "docs/plans/README.md",
    "docs/plans/bootstrap-plan.md",
]

REQUIRED_KEYS = {
    "id",
    "type",
    "title",
    "status",
    "source_of_truth",
    "created",
    "updated",
    "related",
    "managed_by",
}

LEGACY_PATHS = (
    "docs/requirements",
    "docs/architecture",
    "docs/decisions",
    "docs/design/initial-slice.md",
)

SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
]
LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
PROGRESS_PATTERN = re.compile(r"(?m)(^- \[[ xX]\]|進捗|未着手|実装済み|完了)")
DESIGN_DIRS = ("docs/product/", "docs/design/")


class Validator:
    def __init__(self, root: Path, strict: bool, all_docs: bool) -> None:
        self.root = root
        self.strict = strict
        self.all_docs = all_docs
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def parse(self, path: Path) -> tuple[str, str] | None:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            self.errors.append(f"frontmatter missing: {self.relative(path)}")
            return None
        end = text.find("\n---\n", 4)
        if end < 0:
            self.errors.append(f"frontmatter closing marker missing: {self.relative(path)}")
            return None
        keys = set()
        for line in text[4:end].splitlines():
            match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", line)
            if match:
                keys.add(match.group(1))
        missing = sorted(REQUIRED_KEYS - keys)
        if missing:
            self.errors.append(f"{self.relative(path)} missing keys: {', '.join(missing)}")
        return text[end + 5 :], text

    def files(self) -> list[Path]:
        result = [self.root / item for item in REQUIRED_FILES if (self.root / item).is_file()]
        if self.all_docs:
            result += sorted(self.root.glob("docs/**/*.md"))
            result += [
                path
                for path in (self.root / "README.md", self.root / "AGENTS.md", self.root / ".codex")
                if path.is_file()
            ]
        return list(dict.fromkeys(result))

    def check_links(self, path: Path, text: str) -> None:
        for target in LINK_PATTERN.findall(text):
            target = target.strip().strip("<>")
            if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            candidate = (path.parent / target.split("#", 1)[0]).resolve()
            if not candidate.exists():
                self.errors.append(f"broken link in {self.relative(path)}: {target}")

    def check_secrets(self, path: Path, text: str) -> None:
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                self.errors.append(f"secret-like value in {self.relative(path)}")

    def check_legacy_paths(self) -> None:
        for relative in LEGACY_PATHS:
            path = self.root / relative
            if path.exists():
                self.errors.append(f"legacy path remains: {relative}")

    def run(self) -> int:
        for item in REQUIRED_FILES:
            path = self.root / item
            if not path.exists():
                self.errors.append(f"missing required file: {item}")
            elif path.is_dir():
                self.errors.append(f"expected file but found directory: {item}")

        self.check_legacy_paths()

        for path in self.files():
            parsed = self.parse(path)
            if not parsed:
                continue
            body, full = parsed
            self.check_links(path, body)
            self.check_secrets(path, full)
            relative = self.relative(path)
            if relative.startswith(DESIGN_DIRS) and PROGRESS_PATTERN.search(body):
                self.warnings.append(f"progress marker in design document: {relative}")

        if (self.root / ".codex").is_dir():
            self.errors.append(".codex must be a single file")

        for warning in self.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        for error in self.errors:
            print(f"error: {error}", file=sys.stderr)

        if self.errors or (self.strict and self.warnings):
            return 1
        print(f"ok: validated {len(self.files())} files under {self.root}")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--all-docs", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        print(f"error: repository root does not exist: {root}", file=sys.stderr)
        return 2
    return Validator(root, args.strict, args.all_docs).run()


if __name__ == "__main__":
    raise SystemExit(main())
