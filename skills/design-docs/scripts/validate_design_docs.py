#!/usr/bin/env python3
"""Validate the reusable design-docs Skill without third-party dependencies.

This is intentionally a conservative validator. It checks the Skill packet and
its documentation conventions; it does not parse a product's actual design
documents or decide whether a document is applicable.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit


REQUIRED_FILES = (
    "SKILL.md",
    "agents/openai.yaml",
    "references/document-catalog.md",
    "references/directory-layout.md",
    "references/writing-style.md",
    "references/diagram-selection.md",
    "references/variant-delta.md",
    "references/templates.md",
    "references/traceability.md",
    "references/quality-checklist.md",
    "references/research-basis.md",
    "scripts/validate_design_docs.py",
)

LOCAL_LINK = re.compile(r"!?(?:\[[^\]]*\])\(([^)]+)\)")
FORBIDDEN_FRONT_MATTER_KEYS = re.compile(
    r"(?m)^\s*(?:requirements|diagrams|adrs|requiredity|base_documents):"
)
HIGH_CONFIDENCE_SECRET = re.compile(
    r"(?i)(?:-----BEGIN (?:RSA|OPENSSH|EC|DSA) PRIVATE KEY-----|"
    r"(?:sk|rk|pk)-[A-Za-z0-9]{20,}|"
    r"AKIA[0-9A-Z]{16})"
)
DIAGRAM_ASSET_SUFFIXES = {".mmd", ".puml", ".pu", ".svg", ".png", ".jpg", ".jpeg", ".drawio", ".pptx"}


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def check_required_files(skill_dir: Path, errors: list[str]) -> None:
    for relative in REQUIRED_FILES:
        if not (skill_dir / relative).is_file():
            fail(errors, f"missing required file: {relative}")


def check_skill_front_matter(skill_dir: Path, errors: list[str]) -> None:
    skill = skill_dir / "SKILL.md"
    if not skill.is_file():
        return
    text = skill.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        fail(errors, "SKILL.md must start with YAML front matter")
    for marker in ("required", "conditional", "not-applicable", "base_version"):
        if marker not in text:
            fail(errors, f"SKILL.md is missing required concept: {marker}")


def check_agent_config(skill_dir: Path, errors: list[str]) -> None:
    config = skill_dir / "agents/openai.yaml"
    if not config.is_file():
        return
    text = config.read_text(encoding="utf-8")
    for key in ("display_name:", "short_description:", "default_prompt:"):
        if key not in text:
            fail(errors, f"agents/openai.yaml is missing {key}")


def check_links(root: Path, skill_dir: Path, errors: list[str]) -> None:
    for document in sorted(skill_dir.rglob("*.md")):
        text = document.read_text(encoding="utf-8")
        for raw_target in LOCAL_LINK.findall(text):
            target = raw_target.strip().split("#", 1)[0].split("?", 1)[0]
            if not target or urlsplit(target).scheme or target.startswith("//"):
                continue
            resolved = (document.parent / target).resolve()
            if not resolved.exists():
                fail(errors, f"broken local link: {document.relative_to(root)} -> {target}")


def check_conventions(root: Path, skill_dir: Path, errors: list[str]) -> None:
    documents = [*skill_dir.rglob("*.md"), skill_dir / "agents/openai.yaml"]
    all_text = "\n".join(document.read_text(encoding="utf-8") for document in documents if document.is_file())
    for marker in ("source_of_truth", "alt text", "REQ-F"):
        if marker not in all_text:
            fail(errors, f"Skill packet is missing required concept: {marker}")
    for document in documents:
        if not document.is_file():
            continue
        text = document.read_text(encoding="utf-8")
        if FORBIDDEN_FRONT_MATTER_KEYS.search(text):
            fail(errors, f"non-canonical front matter key found: {document.relative_to(root)}")
        if HIGH_CONFIDENCE_SECRET.search(text):
            fail(errors, f"high-confidence secret pattern found: {document.relative_to(root)}")
    if "./skills/design-docs/SKILL.md" not in (root / "README.md").read_text(encoding="utf-8"):
        fail(errors, "root README.md does not register skills/design-docs/SKILL.md")


def check_no_diagram_assets(skill_dir: Path, errors: list[str]) -> None:
    for path in skill_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in DIAGRAM_ASSET_SUFFIXES:
            fail(errors, f"diagram asset must not be created by this Skill: {path.relative_to(skill_dir)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".", help="repository root")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    skill_dir = root / "skills/design-docs"
    errors: list[str] = []

    if not skill_dir.is_dir():
        print(f"ERROR: missing {skill_dir}", file=sys.stderr)
        return 2

    check_required_files(skill_dir, errors)
    check_skill_front_matter(skill_dir, errors)
    check_agent_config(skill_dir, errors)
    check_links(root, skill_dir, errors)
    check_conventions(root, skill_dir, errors)
    check_no_diagram_assets(skill_dir, errors)

    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        print(f"{len(errors)} validation error(s)")
        return 1

    print(f"PASS: design-docs Skill validation ({len(REQUIRED_FILES)} required files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
