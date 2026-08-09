---
name: nous
description: Orchestrate durable Plan→Implement→Review tasks with knowledge gathering, autonomous replanning, checkpoints, human decision gates, parallel child loops, and pause/resume recovery. Always use when the user explicitly invokes $nous, including status, pause, resume, plan-only, or review-only operations. Implicitly use only when the user clearly requests a managed multi-step autonomous workflow spanning planning or research through implementation and review. Do not implicitly use for one-off questions, isolated edits, single commands, plan-only requests, or review-only requests.
---

# Nous

Manage one durable task through Knowledge-assisted Plan, Implement, and Review. Treat nous as the single control plane: reconstruct context from task artifacts rather than relying on chat history, checkpoint every durable boundary, and continue autonomously until completion or a genuine human decision is required.

## Start at the runtime boundary

Use `scripts/nous` for runtime operations. Do not edit canonical task JSON, journals, checkpoints, or registries by hand.

1. Run `scripts/nous taskctl doctor`.
2. Resolve `NOUS_HOME` through the runtime. Never invent a fallback task directory.
3. Classify the request as new task, status, pause, resume, or an explicit phase operation.
4. Read [references/runtime.md](references/runtime.md) before creating, mutating, pausing, resuming, parallelizing, or completing a task.
5. Read [references/policies.md](references/policies.md) before executing side effects, asking for approval, using external information, or invoking a Subskill.

If the runtime is unavailable or configuration is ambiguous, fail closed and offer short repair choices. Do not replace deterministic runtime operations with ad hoc file writes.

## Route the user intent

- **New objective:** create a Root Task, capture objective, constraints, non-goals, acceptance criteria, repository scope, and human action boundaries.
- **Status / progress:** show the Root Task summary and actionable blockers; do not start a new task.
- **Pause:** stop scheduling new actions, reach a safe checkpoint, persist resumable state, and report durability warnings.
- **Resume / continue:** locate the existing task, reconcile state and external artifacts, then continue from its recorded resume target.
- **Explicit partial operation:** when `$nous` is explicit, operate on the named task or phase without forcing the entire loop.

When multiple tasks match, present task ID plus one-line summary as mutually exclusive choices. When one task matches unambiguously, continue without asking.

## Run the managed loop

For a new or resumed autonomous task:

1. Acquire the task lease and load the State or Resume Packet.
2. **Plan:** gather only the Knowledge needed to remove uncertainty; define requirements, acceptance criteria, validation, risks, dependencies, and rollback boundaries. Record claims with sources and distinguish evidence from assumptions.
3. If work can be safely separated, define typed Child Loop contracts and a dependency DAG before starting workers.
4. **Implement:** perform only actions covered by the active Plan and policy grant. Keep changes small, reversible, and validated. Record deviations and newly discovered constraints.
5. **Review:** independently test the result against acceptance criteria, inspect evidence and diffs, and return exactly one decision:
   - `completed`: all required criteria and validations pass;
   - `needs_plan`: defects, changed assumptions, or incomplete work require a new cycle;
   - `needs_human_input`: only a human decision or approval can unblock progress.
6. On `needs_plan`, preserve the prior cycle as history, increment the cycle, build a fresh current Plan from active Knowledge and review findings, and continue automatically.
7. On `completed`, let the runtime validate completion authority and persist the final checkpoint. A worker must never mark the Root Task complete directly.

Do not treat old Plans, raw research notes, child logs, or chat history as current Knowledge. Promote only validated claims into active Knowledge.

## Ask only when necessary

Research repository evidence and authoritative sources before asking. Ask when intent is unresolved, acceptance criteria would change, valid alternatives require a value choice, a required capability is unavailable, or a Red action lacks approval.

Use short choices:

- combine independent questions;
- provide 2–3 mutually exclusive options;
- put the recommended option first and label it `Recommended`;
- state one impact or trade-off per option;
- permit free text when the options are insufficient;
- block only the affected action, loop, or phase when independent work can continue.

Persist the question, selected answer, affected scope, and exact resume target before resuming.

## Parallelize conservatively

Parallelize only when each child has an independent objective, pinned inputs, acceptance criteria, declared outputs, explicit read/write resources, an acyclic dependency graph, and a defined integration strategy.

- Give each child only its contract, relevant state, active Knowledge, and dependency outputs.
- Prefer isolated subagents for read-only research.
- Permit concurrent repository writes only through managed isolation such as separate Git worktrees and non-conflicting resource reservations.
- If isolation, policy enforcement, or conflict detection is unavailable, fall back to logical sequential execution.
- After materialized child loops finish, perform Integration Review; child completion alone cannot complete the Root Task.

## Select Subskills by capability

Request capabilities, not hard-coded Subskill names. Let the registry filter compatible providers, then select the smallest useful composition for the current phase.

- Pin Subskill ID, version, provider identity, package hash, inputs, outputs, and selection rationale.
- Prefer named methodological anchors appropriate to the task; do not apply every available Subskill by default.
- Route every Subskill side effect through the same managed policy gateway as core actions.
- If a required provider is missing or changed, use an approved fallback, re-plan, or ask; never silently substitute an incompatible provider.

## Enforce managed execution

Send every managed filesystem, process, Git, network, credential, external, and Subskill action through the runtime Policy Gateway and dedicated adapter. Execute only after receiving a valid single-use grant bound to the exact action.

- Unknown action types, ambiguous targets, missing probes, and unbrokerable actions fail closed.
- Secrets must not enter canonical artifacts, logs, ordinary outbound payloads, or action digests.
- Human approval does not make secret persistence, force-push, history rewrite, or undeclared scope expansion safe.
- Treat host tools outside the managed adapters as outside nous guarantees; do not use their side effects as canonical completion evidence.
- Never claim containment or policy enforcement that the host/runtime did not actually provide.

Use [references/policies.md](references/policies.md) for risk, approval, Knowledge, Subskill, and safety details.

## End every session durably

Before yielding control, pausing, or ending a session:

1. stop starting new actions;
2. reconcile any action whose completion is uncertain;
3. persist phase output, event evidence, state revision, and checkpoint through the runtime;
4. render the current status and resume view;
5. report the task ID, lifecycle/execution status, current phase and cycle, completed work, blockers, next action, and durability level.

If the task remains active, leave an exact machine-readable resume target. Do not require the user to identify or reopen individual child workers.
## Nous Loop

NousのPlan → Implement → Review → Plan … の反復を **Nous Loop** と呼ぶ。1回のPlan/Implement/Reviewは1サイクルであり、Reviewが証拠付きで受入条件を通過するまで次サイクルへ進む。

Humanが「Nous Loopを最低3回して」と指定した場合は、runtime設定の`min_cycles: 3`へ変換する。`min_cycles`はRootのPlan→Implement→Reviewサイクルの最低回数で、Reviewが早く`completed`になっても下限に達するまで次のPlanへ戻す。既定値は1、上限は`max_cycles`であり、`min_cycles > max_cycles`は設定エラーとして止める。`min_cycles > 1`では`max_steps`も最低`min_cycles * 3`にする。

通常運用では、各フェーズを親エージェント内で直接処理せず、外部サブエージェントを起動して実行する。インプロセスbackendはテストまたは明示的な組み込み検証に限る。結果のdiagnosticsとphase artifactを確認し、3ステップで終わった場合でも実質的な作業と検証があったかを確認する。
