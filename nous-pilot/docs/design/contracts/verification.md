---
id: CT-004
type: contract
title: Verification and evidence contract
status: accepted
source_of_truth: true
created: 2026-08-09
updated: 2026-08-09
related: [REQ-001, DES-001, CT-001, SEC-001]
managed_by: repository-bootstrap
---
# Verification and evidence contract

## Verification request

```text
verification_id
task_id
loop_id
workspace_revision
profile
commands[]
expected_artifacts[]
allowed_processes[]
network_policy
resource_budget
```

## Evidence record

```text
verification_id
command_argv
working_directory
started_at
finished_at
exit_code
stdout_digest
stderr_digest
artifact_refs[]
changed_paths[]
environment_fingerprint
status
```

Evidence is an observation, not a provider claim. The Coordinator checks command identity, exit status, workspace revision, and artifact digest before using it for Judge decisions.

## Verification profiles

- `static`: format、lint、typecheck、schema、dependency policy
- `unit`: unit and contract tests
- `integration`: provider、TaskStore、workspace、adapter integration
- `regression`: existing project suite and changed-area checks
- `security`: secret scan、scope、network、permission、dependency checks
- `product`: API、browser、migration、UI、data behavior through dedicated adapters

## Gate rules

- required profile without evidence blocks completion
- stale evidence after a source change is not reusable
- failed command creates a finding and may trigger `needs_plan`
- external service evidence includes provider, endpoint class, timestamp, and policy grant without persisting credentials
- hidden holdout evidence remains outside the public repository
