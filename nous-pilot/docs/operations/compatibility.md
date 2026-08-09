---
id: OPS-004
type: operations
title: Nous Pilot compatibility matrix
status: accepted
source_of_truth: true
created: 2026-08-09
updated: 2026-08-09
related: [OPS-003, PLAN-001]
managed_by: repository-bootstrap
---
# Nous Pilot compatibility matrix

| Component | Supported baseline | Notes |
|---|---|---|
| Python | 3.11, 3.12, 3.13 | CI exercises all three versions |
| OS | Linux, macOS, Windows | `check_compatibility()` rejects other platforms before work starts |
| Git | local Git repository | Remote operations are a separate policy-gated adapter |
| agent-nous | compatible local runtime | `doctor` must pass before execution |
| Agent provider | any JSON stdin/stdout subprocess | command must be explicitly configured |
| Browser/MCP/OTLP | host adapter only | no listener or network sender is opened by the package |

The compatibility check reports the Python/runtime facts available to the
current process. It is not a substitute for running the full test matrix.
Git and the configured local Agent command still need to be available on the
host; remote Git, browser, and deployment behavior is not implied by support.
