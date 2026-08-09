---
id: CT-001
type: contract
title: Nous Pilot control-plane contract
status: accepted
source_of_truth: true
created: 2026-08-09
updated: 2026-08-09
related: [REQ-001, ARCH-001, DES-001, CT-002]
managed_by: repository-bootstrap
---
# Nous Pilot control-plane contract

## Root Task input

```text
request_id
objective
repository_locator
base_revision
constraints[]
non_goals[]
risk_class
allowed_actions[]
validation_profile
loop_policy
provider_policy
```

## Lifecycle

```text
intake → research → plan → ready_to_implement → implement
       → verify → cross_review → judge
                         ├── completed
                         ├── needs_plan → research/plan
                         └── needs_human_input
```

The lifecycle is persisted through the agent-nous runtime. A phase worker cannot directly transition the Root Task to `completed`.

## Phase result

Every phase returns:

- `artifact_type`
- `contract_version`
- `task_id`
- `loop_id`
- `phase`
- `status`
- `input_digest`
- `output_artifact_refs[]`
- `evidence_refs[]`
- `diagnostics[]`
- `next_action`
- `provider_identity`
- `subagent_run_id`

## Judge decision

Judge returns exactly one of:

- `completed`: all required acceptance criteria and validation evidence pass
- `needs_plan`: scope, defect, assumption, or evidence requires another Loop
- `needs_human_input`: a value choice, red action, unavailable capability, or policy decision blocks work

## Idempotency and recovery

- request and phase invocation carry stable IDs
- duplicate event delivery is deduplicated by request/event identity
- uncertain process completion is reconciled against runtime and workspace probes
- current phase, checkpoint, lease, contract revision, and resume target are durable
- old plans remain history and are not treated as active Knowledge
