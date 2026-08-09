# Nous Runtime Protocol

Use this reference for task creation, phase transitions, checkpoints, status, pause/resume, recovery, parallel execution, and completion.

## Contents

1. Runtime discovery
2. Intent and task operations
3. Canonical state model
4. New-task protocol
5. Phase protocol
6. Checkpoint and handoff
7. Pause and resume
8. Parallel child loops
9. Completion and session report

## 1. Runtime discovery

Invoke the project runtime only through the Skill launcher:

```sh
scripts/nous taskctl doctor
scripts/nous taskctl status
```

The launcher resolves the repository package relative to its installed source. The runtime resolves its home in this order:

1. `NOUS_HOME` environment variable;
2. user-level nous configuration;
3. current-project nous configuration.

Task state belongs under `${NOUS_HOME}/tasks`; it does not belong inside the installed Skill. If no valid home can be resolved, do not create one at an assumed path. Show the detected configuration problem and short repair choices.

Canonical task mutations must use `taskctl`. Markdown status and resume documents are rendered views, not writable authorities.

## 2. Intent and task operations

Route the request before changing state.

| Intent | Operation |
| --- | --- |
| New objective | Create a new Root Task |
| “status”, “progress” | List or show existing tasks |
| “pause” | Safely checkpoint and pause the selected task |
| “resume”, “continue” | Reconcile and resume an existing task |
| Explicit task/phase request | Operate on that task without creating another |

Typical runtime commands:

```sh
scripts/nous taskctl create
scripts/nous taskctl list
scripts/nous taskctl show TASK_ID
scripts/nous taskctl status
scripts/nous taskctl pause TASK_ID
scripts/nous taskctl resume-prepare TASK_ID
scripts/nous taskctl validate TASK_ID
scripts/nous taskctl render TASK_ID
```

Use command help or typed request input for exact arguments; do not infer unsupported flags. A status response should aggregate child state and surface only actionable details by default.

Do not merge a new objective into an active task merely because it uses the same repository. Ask only if the relationship is genuinely ambiguous.

## 3. Canonical state model

Separate semantic lifecycle from worker execution:

```text
lifecycle_status: active | waiting_human | blocked | completed | cancelled
execution_status: idle | running | pausing | paused | interrupted
phase: plan | implement | review
phase_kind: standard | integration
cycle: positive integer, initially 1
```

Examples:

- `active + paused` means incomplete but safely paused.
- `waiting_human + idle` means a persisted question or approval blocks progress.
- `active + interrupted` requires reconciliation before work resumes.

Task and loop IDs are immutable. Display titles may change without changing identity. Each state change uses a revision check, idempotency key, lock/lease, and recoverable transaction. Never bypass a revision conflict by overwriting the newer state.

Past cycle artifacts are immutable history. Only the active cycle and canonical state determine current instructions.

## 4. New-task protocol

Before implementation, capture a minimal stable definition:

- objective;
- scope and non-goals;
- acceptance criteria;
- known constraints and repository identity;
- human action boundaries and existing approvals;
- initial assumptions and unresolved questions;
- validation and durability expectations.

Create the task through `taskctl`, acquire a lease, and persist the initial checkpoint before starting substantial work. Research is part of Plan, not a fourth core phase.

Do not over-specify implementation before inspecting the repository and gathering the Knowledge needed to resolve material uncertainty.

## 5. Phase protocol

### Plan

Build a Plan from the task definition, stable context, active Knowledge, current repository evidence, prior review findings, and valid approvals.

The Plan must define:

- requirements and acceptance criteria mapping;
- implementation slices and dependencies;
- validation/evidence for each slice;
- risks, assumptions, rollback boundaries, and policy-sensitive actions;
- candidate child loops and their integration contract;
- required capabilities rather than concrete provider names.

Return a typed handoff. If ready, transition to Implement. If only a human can resolve a decision, persist a question with an exact resume target. If an external prerequisite blocks progress, preserve phase/cycle and record blocker plus release condition.

### Implement

Implement only against the active Plan revision. For each action:

1. normalize the action;
2. obtain a policy decision and, when allowed, an execution grant;
3. run it through the matching managed adapter;
4. collect postcondition evidence;
5. record completion, failure, or reconciliation need.

Record changed artifacts, validations, deviations, and open concerns. A discovered requirement change returns to Plan; it is not silently absorbed as implementation detail.

### Review

Review independently from implementation intent. Compare actual evidence and diffs against every acceptance criterion and policy requirement. Run required validations in the permitted isolated environment.

Return one decision:

```text
completed
needs_plan
needs_human_input
```

`needs_plan` increments the affected loop’s cycle and creates a new Plan artifact; it never edits the old Plan. `needs_human_input` must identify what is blocked and where to resume. Review produces a completion candidate; only the orchestrator plus `taskctl` may commit Root completion.

## 6. Checkpoint and handoff

Create a durable checkpoint at least when:

- a phase output is accepted;
- a human question or approval request is persisted;
- safe pause completes;
- an action has ambiguous external state;
- a child result or integration result is accepted;
- the session is about to end;
- task completion is committed.

A handoff packet should contain only the context needed by the next phase:

- task and loop identity;
- state revision, active phase/cycle, and active Plan reference;
- relevant stable decisions and Knowledge references;
- required inputs and accepted outputs;
- changed artifacts and validation evidence;
- blockers, pending approvals, and exact next transition.

Do not pass raw chat history, superseded Plans, unrelated research, or another child’s scratch notes.

## 7. Pause and resume

### Safe pause

1. Set execution to `pausing` and stop scheduling new actions.
2. Let safely interruptible work stop; do not blindly terminate an external side effect.
3. Reconcile started actions whose result is unknown.
4. Release or persist reservations and leases according to runtime policy.
5. Persist state, events, artifact durability, next action, and resume target.
6. Render the resume view and set execution to `paused`.

A failed remote backup does not invalidate a valid local pause. Report its durability consequence explicitly.

### Fresh-process resume

1. Locate the Root Task from the registry.
2. Validate canonical schemas and transaction markers.
3. Reconcile interrupted leases, in-flight actions, repository identity, HEAD/diff, reservations, and external evidence.
4. Rebuild generated views if necessary.
5. Construct a Resume Packet from durable state only.
6. Resume the recorded phase and transition; do not guess from conversation history.

If completion of an external action is ambiguous and retry could duplicate effects, stop and ask. If the expected postcondition is verified, record completion without replaying the action.

## 8. Parallel child loops

Parallel execution begins only after the parent Plan defines a DAG and immutable Child Loop contracts. Each contract includes:

- objective, scope, and non-goals;
- dependencies and pinned input versions;
- acceptance criteria and expected output contract;
- semantic read/write/shared resources;
- allowed/forbidden actions and risk class;
- validation and integration strategy;
- relevant Knowledge references.

Rules:

- Reject cyclic dependencies and ambiguous resources.
- Schedule only children whose dependencies are complete.
- Keep unrelated human waits local to the affected loop and downstream dependents.
- Use isolated subagents for read-only work when available.
- For concurrent writes, require managed Git worktrees/branches plus resource reservations.
- If isolation or probes are unavailable, execute logical child loops sequentially.
- Children never write parent canonical state directly; they return typed results.
- Contract changes create immutable `contracts/rev-NNNN.json` revisions and re-evaluate dependents.
- Complete materialized parallel work only after parent Integration Review validates composition, conflicts, and Root acceptance criteria.

The user controls the Root Task through nous; never require them to manage child sessions individually.

## 9. Completion and session report

Root completion requires:

- a canonical Review decision for the active cycle;
- every required acceptance criterion satisfied;
- no blocking findings, pending required validation, or pending approval;
- all materialized children terminal and integrated;
- required artifact durability recorded;
- `taskctl` validation and completion transaction successful.

End each interaction with a concise operational report:

```text
Task: <id and title>
State: <lifecycle / execution / phase / cycle>
Completed: <durable outcomes>
Waiting/blocked: <none or actionable item>
Next: <exact next action or resume target>
Durability: <local checkpoint / commit / remote or external backup status>
```

Do not claim completion when only a child, phase, command, or unverified side effect completed.
