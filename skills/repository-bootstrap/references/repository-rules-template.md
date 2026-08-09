# Repository Rules Template

## AGENTS.md

AGENTS.mdにはリポジトリ固有の実行ルールだけを書く。

~~~markdown
---
id: RULES-001
type: agent-rules
title: Repository agent rules
status: accepted
source_of_truth: true
created: 2026-08-02
updated: 2026-08-02
related: []
managed_by: repository-bootstrap
---

# Repository agent rules

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

1. Inspect.
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

List actual commands for this repository.

## Completion report

Include created, updated, moved, and deleted files; source-of-truth documents; validation; unresolved questions; and the next implementation entry point.
~~~

## .codex

.codexはCodex用の恒久引き継ぎファイルにする。日々の進捗は複製しない。

~~~markdown
---
id: CODEX-001
type: codex-handoff
title: Codex project handoff
status: accepted
source_of_truth: false
created: 2026-08-02
updated: 2026-08-02
related: [ARCH-001, PLAN-001]
managed_by: repository-bootstrap
---

# Codex project handoff

## Project
## Stack
## Repository map
## Design sources of truth
## Development commands
## Permanent constraints
## Current entry point

See docs/plans/bootstrap-plan.md for current task state. Do not write secrets here.
~~~
