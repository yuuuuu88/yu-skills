---
id: SCRIPTS-INDEX-001
type: scripts-index
title: Nous Pilot scripts
status: draft
source_of_truth: false
created: 2026-08-09
updated: 2026-08-09
related: [PLAN-001]
managed_by: repository-bootstrap
---
# Scripts

`validate_compile.py` performs read-only in-memory syntax validation and
`validate_bootstrap.py` checks the standalone documentation/package baseline.
`agents/` contains deterministic fixtures plus `external_bridge.py`, which
translates the runtime context to the generic JSON subprocess provider. No
script performs remote Git, network, or deployment actions.
