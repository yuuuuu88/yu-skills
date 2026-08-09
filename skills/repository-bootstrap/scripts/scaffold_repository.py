#!/usr/bin/env python3
"""Create or reconcile the folder-first repository-bootstrap baseline."""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
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

LEGACY_DIR_MOVES = {
    "docs/requirements": "docs/product",
    "docs/architecture": "docs/design/architecture",
    "docs/decisions": "docs/design/decisions",
}

LEGACY_FILE_MOVES = {
    "docs/design/initial-slice.md": "docs/design/features/initial-slice.md",
}

LINK_REPLACEMENTS = {
    "docs/requirements/": "docs/product/",
    "docs/architecture/": "docs/design/architecture/",
    "docs/decisions/": "docs/design/decisions/",
    "docs/design/initial-slice.md": "docs/design/features/initial-slice.md",
    "requirements/brief.md": "product/brief.md",
    "architecture/overview.md": "design/architecture/overview.md",
}


def frontmatter(doc_id: str, doc_type: str, title: str, status: str, date: str, related: str = "[]", source: str = "true") -> str:
    return "\n".join([
        "---",
        f"id: {doc_id}",
        f"type: {doc_type}",
        f"title: {title}",
        f"status: {status}",
        f"source_of_truth: {source}",
        f"created: {date}",
        f"updated: {date}",
        f"related: {related}",
        "managed_by: repository-bootstrap",
        "---",
        "",
    ])


def doc(doc_id: str, doc_type: str, title: str, status: str, date: str, text: str, related: str = "[]", source: str = "true") -> str:
    return frontmatter(doc_id, doc_type, title, status, date, related, source) + text.strip() + "\n"


def folder_index(doc_id: str, title: str, responsibility: str, date: str, documents: str) -> str:
    return doc(doc_id, "documentation-index", title, "accepted", date, f"""# {title}

## Responsibility

{responsibility}

## Documents

{documents}

## Rules

Keep documents in this folder aligned with its responsibility. Keep progress in docs/plans/.
""")


def render(project: str, description: str, stack: str, date: str) -> dict[str, str]:
    return {
        ".codex": doc("CODEX-001", "codex-handoff", "Codex project handoff", "accepted", date, f"""# Codex project handoff

## Project

{project}: {description}

## Stack

{stack}

## Repository map

- docs/product/: problem, users, requirements, glossary
- docs/design/: architecture, features, contracts, cross-cutting, decisions
- docs/operations/: local development and operations
- docs/research/: external evidence
- docs/plans/: implementation order and progress
- src/: product code when applicable
- tests/: automated tests when applicable

## Design sources of truth

- Product: docs/product/
- Architecture: docs/design/architecture/
- Features: docs/design/features/
- Contracts: docs/design/contracts/
- Cross-cutting: docs/design/cross-cutting/
- Decisions: docs/design/decisions/
- Operations: docs/operations/
- Research: docs/research/
- Progress: docs/plans/

## Development commands

Fill in actual commands in AGENTS.md and docs/operations/local-development.md.

## Permanent constraints

- Do not print or commit secrets.
- Keep progress in docs/plans/ and design in docs/design/.
- Record important external research sources.

## Current entry point

Start with docs/plans/bootstrap-plan.md.
""", source="false"),
        "AGENTS.md": doc("RULES-001", "agent-rules", "Repository agent rules", "accepted", date, """# Repository agent rules

## Read first

- README.md
- .codex
- docs/README.md
- docs/plans/bootstrap-plan.md

## Source of truth

- Product: docs/product/
- Architecture: docs/design/architecture/
- Features: docs/design/features/
- Contracts: docs/design/contracts/
- Cross-cutting: docs/design/cross-cutting/
- Decisions: docs/design/decisions/
- Operations: docs/operations/
- Research: docs/research/
- Progress: docs/plans/

## Workflow

1. Inspect the tree and rules.
2. Update design before implementation.
3. Implement the smallest slice.
4. Validate.
5. Update design and plan after behavior changes.
6. Report created, updated, moved, and deleted files.

## Allowed by default

- Read and search local files.
- Search the internet for current or unknown information.
- Create, update, move, and delete repository files.
- Run local lint, typecheck, test, and build.
- Use subagents for research, implementation, and review.

## Subagents

Worktree is optional. Use an isolated worktree when parallel writes could conflict.

## Approval gates

Ask before production or hosted DB/Auth/Storage changes, paid or high-volume AI use, external data transmission, push, PR publication, deployment, or release.

## Safety

Never print or commit secrets. Repair references after moving or deleting files. Keep progress in docs/plans/, not design documents.

## Validation

Fill in actual commands for this repository.

## Completion report

Include created, updated, moved, and deleted files; source-of-truth documents; validation; unresolved questions; and the next implementation entry point.
"""),
        "README.md": doc("REPO-README-001", "repository-index", f"{project} repository", "accepted", date, f"""# {project}

{description}

## Start here

- Product brief: docs/product/brief.md
- Architecture: docs/design/architecture/overview.md
- Initial feature design: docs/design/features/initial-slice.md
- Operations: docs/operations/local-development.md
- Plan: docs/plans/bootstrap-plan.md
- Codex handoff: .codex
"""),
        "docs/README.md": doc("DOCS-INDEX-001", "documentation-index", "Documentation index", "accepted", date, """# Documentation index

| Area | Source of truth | Purpose |
|---|---|---|
| Product | product/ | Problem, users, requirements, glossary |
| Design | design/ | Architecture, features, contracts, cross-cutting, decisions |
| Operations | operations/ | Local development, deployment, runbooks |
| Research | research/ | External evidence, comparison, experiments |
| Plans | plans/ | Tasks, order, progress, checkpoints |

Design documents do not contain implementation progress. Progress belongs in plans/.
"""),
        "docs/product/README.md": folder_index("PRODUCT-INDEX-001", "Product documents", "Problem, users, requirements, goals, non-goals, and glossary.", date, "- brief.md: product brief and initial requirements"),
        "docs/product/brief.md": doc("REQ-001", "requirements", "Product brief", "draft", date, """# Product brief

## Problem

Describe the user or system problem.

## Users and stakeholders

Describe primary users, affected stakeholders, and decision makers.

## Goals

- Goal 1

## Non-goals

- Non-goal 1

## Functional requirements

- REQ-F-001: Describe a required behavior.

## Quality requirements

- REQ-Q-001: Describe a measurable quality target.

## Constraints and assumptions

- State known constraints and assumptions.

## Acceptance criteria

- State how the requirement is verified.

## Open questions

- Record unresolved requirement questions.

## References
"""),
        "docs/design/README.md": folder_index("DESIGN-INDEX-001", "Design documents", "System design and technical decisions. Keep progress in docs/plans/.", date, """- architecture/: system structure and boundaries
- features/: feature-level designs
- contracts/: API, data, and external contracts
- cross-cutting/: security, identity, errors, and observability
- decisions/: ADRs"""),
        "docs/design/architecture/README.md": folder_index("ARCH-INDEX-001", "Architecture documents", "System-wide structure, boundaries, runtime behavior, deployment, quality goals, and risks.", date, "- overview.md: system architecture overview"),
        "docs/design/architecture/overview.md": doc("ARCH-001", "architecture", "System architecture overview", "draft", date, """# System architecture overview

## Scope and context
## Quality goals
## Constraints
## Solution strategy
## System context
## Containers / major building blocks
## Runtime scenarios
## Deployment view
## Cross-cutting concerns
## Risks and technical debt
## Glossary
## Related documents
"""),
        "docs/design/features/README.md": folder_index("FEATURES-INDEX-001", "Feature designs", "Detailed designs for user-facing features and vertical slices.", date, "- initial-slice.md: first implementation slice"),
        "docs/design/features/initial-slice.md": doc("DES-001", "design", "Initial implementation slice", "draft", date, """# Initial implementation slice

## Scope
## Related requirements
## Assumptions
## User or system flow
## Inputs and outputs
## Interfaces and data
## Validation and error handling
## Authentication and authorization
## Security and privacy
## Observability
## Test strategy
## Alternatives and trade-offs
## Open questions
## References
""", related="[REQ-001, ARCH-001]"),
        "docs/design/contracts/README.md": folder_index("CONTRACTS-INDEX-001", "Design contracts", "API, data model, events, and external interface contracts.", date, "- Add api.md, data-model.md, or other contracts when required."),
        "docs/design/cross-cutting/README.md": folder_index("CROSS-CUTTING-INDEX-001", "Cross-cutting design", "Concerns shared across features such as identity, security, errors, configuration, and observability.", date, "- Add security.md, authentication.md, or observability.md when required."),
        "docs/design/decisions/README.md": folder_index("DECISIONS-INDEX-001", "Architecture decisions", "One important decision per ADR. Accepted ADRs are replaced by later ADRs rather than rewritten.", date, "- Add 0001-decision-name.md when an ADR is needed."),
        "docs/operations/README.md": folder_index("OPERATIONS-INDEX-001", "Operations documents", "Local development, deployment, monitoring, runbooks, recovery, and operational constraints.", date, """- local-development.md: local setup and validation
- runbooks/: repeatable operational procedures"""),
        "docs/operations/local-development.md": doc("OPS-001", "operations", "Local development", "draft", date, """# Local development

## Prerequisites
## Environment variables

List variable names and safe example files. Never include secret values.

## Install

~~~bash
# Fill in the install command.
~~~

## Run

~~~bash
# Fill in the local development command.
~~~

## Validate

~~~bash
# Fill in lint, typecheck, test, and build commands.
~~~

## Troubleshooting
## Related documents
"""),
        "docs/operations/runbooks/README.md": folder_index("RUNBOOKS-INDEX-001", "Operations runbooks", "Repeatable procedures for incidents, maintenance, recovery, and operational changes.", date, "- Add one Markdown file per runbook."),
        "docs/research/README.md": folder_index("RESEARCH-INDEX-001", "Research notes", "External research, comparisons, experiments, and evidence. Record source URL and research date.", date, "- Add one Markdown file per research topic when references alone are not enough."),
        "docs/plans/README.md": folder_index("PLANS-INDEX-001", "Plans and progress", "Implementation order, task state, checkpoints, blockers, and handoffs.", date, "- bootstrap-plan.md: repository bootstrap plan"),
        "docs/plans/bootstrap-plan.md": doc("PLAN-001", "plan", "Bootstrap implementation plan", "active", date, """# Bootstrap implementation plan

## Objective

Move from repository initialization to the first verified implementation slice.

## Ordered tasks

- [ ] Confirm problem, users, goals, and non-goals
- [ ] Complete product brief
- [ ] Complete architecture overview
- [ ] Complete initial feature design
- [ ] Decide whether ADRs are needed
- [ ] Complete local development commands
- [ ] Run documentation validation
- [ ] Start implementation
- [ ] Run stack-specific validation
- [ ] Update design after behavior changes

## Validation commands
## Checkpoints
## Blockers
## Decision links
""", related="[REQ-001, ARCH-001, DES-001]"),
    }


def move_tree(source: Path, target: Path, moved: set[Path], conflicts: list[str]) -> None:
    if not source.exists():
        return
    if source.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        for child in list(source.iterdir()):
            move_tree(child, target / child.name, moved, conflicts)
        try:
            source.rmdir()
        except OSError:
            pass
        return
    if target.exists():
        conflicts.append(f"{source} -> {target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    moved.add(target)


def migrate_legacy(root: Path) -> tuple[set[Path], list[str]]:
    moved: set[Path] = set()
    conflicts: list[str] = []
    for old, new in LEGACY_DIR_MOVES.items():
        move_tree(root / old, root / new, moved, conflicts)
    for old, new in LEGACY_FILE_MOVES.items():
        move_tree(root / old, root / new, moved, conflicts)
    return moved, conflicts


def rewrite_links(root: Path) -> None:
    for path in root.glob("docs/**/*.md"):
        text = path.read_text(encoding="utf-8")
        updated = text
        for old, new in LINK_REPLACEMENTS.items():
            updated = updated.replace(old, new)
        if updated != text:
            path.write_text(updated, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--project-name", default=None)
    parser.add_argument("--description", default="Project description is not defined yet.")
    parser.add_argument("--stack", default="Not decided yet.")
    parser.add_argument("--migrate-legacy", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    if not root.is_dir():
        print(f"error: repository root does not exist: {root}", file=sys.stderr)
        return 2

    if args.dry_run:
        print("dry-run: legacy migration and scaffold would be evaluated")
    migrated: set[Path] = set()
    conflicts: list[str] = []
    if args.migrate_legacy and not args.dry_run:
        migrated, conflicts = migrate_legacy(root)
        rewrite_links(root)
        for path in sorted(migrated):
            print(f"move {path.relative_to(root)}")
        for conflict in conflicts:
            print(f"conflict preserved: {conflict}", file=sys.stderr)

    rendered = render(args.project_name or root.name, args.description, args.stack, dt.date.today().isoformat())
    for relative, content in rendered.items():
        target = root / relative
        if target.exists() and target.is_dir():
            print(f"error: expected file but found directory: {relative}", file=sys.stderr)
            return 2
        if target in migrated:
            print(f"preserve migrated {relative}")
            continue
        if target.exists() and not args.overwrite:
            print(f"skip {relative} (exists; use --overwrite to reconcile)")
            continue
        print(f"{'update' if target.exists() else 'create'} {relative}")
        if not args.dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    if conflicts:
        print(f"warning: {len(conflicts)} migration conflicts preserved for manual merge", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
