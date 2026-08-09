---
id: SRC-INDEX-001
type: code-index
title: Nous Pilot source tree
status: draft
source_of_truth: false
created: 2026-08-09
updated: 2026-08-09
related: [ARCH-001, PLAN-001]
managed_by: repository-bootstrap
---
# Source tree

The package is organized around explicit boundaries:

- `intake.py`: normalize and validate local development requests
- `context.py`: hash a bounded, runtime-excluding repository context
- `control_plane.py` / `runtime.py`: connect intake to the durable local agent-nous Root Task boundary
- `provider.py` / `orchestrator.py`: provider contract and bounded Research → Plan → Implement → Test → Review loop
- `workspace.py`: stage proposed writes outside the target repository and
  provide preview/human-gated local apply/patch-only change lifecycle
- `verification.py`: allowlisted, no-shell verification with hashed evidence
- `config.py` / `cli.py`: one-file personal configuration and operator commands
- `models.py`: typed contracts used by all boundaries
