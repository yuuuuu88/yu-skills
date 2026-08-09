---
name: takt-operations
description: Configure, run, monitor, and troubleshoot TAKT workflows safely, including provider/model selection, reasoning effort, concurrency, workflow choice, virtualenv PATH setup, log analysis, max-step recovery, review-loop control, and fresh-run versus resume decisions. Use when Codex needs to initialize TAKT for a repository, debug a failed or apparently stuck TAKT run, choose a workflow, or prepare an autonomous implementation/review run.
---

# TAKT Operations

TAKT is an orchestration layer, not the model itself. Treat its global config, project environment, workflow YAML, CLI options, and provider account as separate configuration layers. Inspect all applicable layers before changing or rerunning a task.

## Operating sequence

Follow this order:

1. Inspect the repository, current branch, worktree, and existing `.takt/runs`.
2. Inspect the effective TAKT configuration and the provider's local configuration.
3. Select the workflow based on the task, not by habit.
4. Make the runtime environment deterministic, especially Python and Node paths.
5. Start a new run unless the previous run is demonstrably resumable and the same task should continue.
6. Monitor the log and stop interpreting a repeated step as progress without checking iteration and finding changes.
7. After a run, inspect artifacts and execute the repository's own quality gates independently.

Do not commit, push, enable automatic PR creation, contact production systems, or make paid/external side effects unless explicitly authorized.

## Preflight

Run from the target repository:

```bash
git status --short --branch
find .takt/runs -maxdepth 2 -type f -name meta.json -print 2>/dev/null | sort | tail -5
takt --version
```

If the repository has a Python virtualenv, put it first in `PATH` before TAKT starts. Quality gates and agent shell commands commonly invoke `python`, not only `python3`.

Preferred project launcher pattern:

```bash
export PATH="$PWD/.venv/bin:$HOME/.local/node-24.15.0/bin:$HOME/.local/bin:$PATH"
takt --version
```

Use the bundled `scripts/run_takt.sh` when available. It discovers the Git root, adds `.venv/bin`, and then invokes the installed `takt`. If `.venv/bin/python` is absent, stop and create/install the repository environment rather than silently using a different interpreter.

## Configuration layers

Resolve values in this order:

1. CLI flags for the current run.
2. Project `.takt/config.yaml` when the repository uses one.
3. Global `$HOME/.takt/config.yaml`.
4. Workflow YAML and TAKT defaults for workflow-specific behavior.
5. Provider/account capability checks at the actual provider boundary.

Keep these responsibilities separate:

| Concern | Typical location | Rule |
|---|---|---|
| Provider and default model | global/project config | Use a model accepted by the authenticated account |
| Reasoning depth | `provider_options.codex.reasoning_effort` | `xhigh` means highest configured effort; it does not make an unsupported model valid |
| Parallelism | `concurrency` | Five is a reasonable upper bound for this workflow style; increase only with evidence |
| Automatic PR | `auto_pr` / CLI | Keep false unless the user explicitly wants automated PR creation |
| Workflow loop limit | workflow YAML `max_steps` | Do not add `max_steps` to global config; it is workflow configuration |
| Quality gates | workflow YAML or `workflow_overrides` | Keep commands reproducible and run them independently after TAKT |

A config loader accepting a model string does not prove the provider account supports that model. A real provider error such as:

```text
The '<model>' model is not supported when using Codex with a ChatGPT account.
```

means the model must be replaced with one known to work for that account. Do not respond by lowering reasoning effort or by repeatedly retrying the same unsupported model. Preserve `xhigh` when the supported model accepts it.

When a local Codex config already contains a model that completed a previous run, prefer that model over an unverified alias. Record the failed model and the successful replacement in the handoff, without recording credentials.

## Workflow selection

Choose the smallest workflow that matches the work:

| Situation | Recommended workflow | Reason |
|---|---|---|
| New multi-layer implementation requiring plan, tests, implementation, and reviews | `dual` | Full development sequence; built-in loop can run up to 60 steps |
| Existing implementation needs review and fixes | `review-fix-dual` | Review/fix sequence with a 30-step cap |
| Small implementation with frontend/backend review | `dual-mini` | Shorter 20-step loop |

For an existing dirty worktree, prefer a review workflow and make the task explicitly read-only or narrowly scoped during the first pass. Do not launch a second implementation workflow against the same worktree while another run may still be active.

`--quiet` only reduces terminal output; it does not make the run safer or faster. Omit it during the first recovery run so provider errors, current steps, and loop behavior are visible. Add it only after the run is known to be healthy.

Do not use `--continue` merely because a prior run exists. Inspect `.takt/session-state.json`, the run `meta.json`, and the last JSONL events first. For a provider/model failure at the first step, fix the configuration and start a fresh run. Resume only when the same task, worktree, and artifacts are intentionally continued.

## Reading a run log

TAKT's useful evidence is in:

```text
.takt/runs/<run-slug>/meta.json
.takt/runs/<run-slug>/logs/*.jsonl
.takt/runs/<run-slug>/reports/
```

Check these fields before concluding that a process is stuck:

- `workflow_start`: workflow name and task text
- `step_start`: current step, persona, iteration, provider, model, and reasoning effort
- `step_complete`: whether the step actually returned
- `workflow_abort`: terminal reason
- `meta.json`: current step, iteration, resume point, and timestamps

Useful commands:

```bash
latest="$(find .takt/runs -type f -name '*.jsonl' -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)"
tail -n 30 "$latest"
grep -n -E 'workflow_abort|Max steps|invalid_request|command not found|ERROR|WARN' "$latest"
```

Use `scripts/summarize_takt_run.py` for a compact, dependency-free summary when a run has a large JSONL log.

Interpret common outcomes precisely:

- `Reached max iterations (N/N)` / `Max steps reached`: the workflow exhausted its loop budget. It is not evidence of a frozen process. Inspect repeated findings and choose a smaller workflow or a narrower task.
- `Step "..." failed` with HTTP 400 model error: provider configuration failure. Fix model/account compatibility before rerunning.
- `python: command not found`: runtime PATH failure. Put the repository virtualenv before system paths.
- Repeated reviewer rejection with unchanged finding IDs or unchanged report text: no convergence. Do not wait for more iterations; stop and fix the underlying code/config or use a bounded workflow.
- If ordinary reviewer reports say `APPROVE` but `final-gate` or a loop judge says `REJECT` / `needs_fix`, treat the run as not ready. Later report files can overwrite earlier iteration artifacts; inspect the latest `step_complete.content` and `phase_judge_stage.response` in the JSONL before deciding.
- `Status: error` after the first step: no implementation should be assumed. Inspect the worktree and run artifacts rather than claiming partial implementation.

## Safe recovery procedure

Use this procedure after a failed run:

1. Preserve the run directory and inspect the final JSONL event.
2. Classify the failure as provider/model, environment/PATH, workflow convergence, repository code, or external side effect.
3. Fix only the relevant layer. Do not change concurrency or workflow limits to hide a provider or code failure.
4. Validate the effective config with the TAKT config loader or a harmless command such as `takt list`.
5. Start a fresh, bounded run with visible output.
6. After the run, run project tests, typecheck, lint, and build independently.
7. Compare reports against source code. A report claiming “fixed” is not evidence until the diff and tests confirm it.

For the common review recovery:

```bash
./scripts/run_takt.sh \
  --workflow review-fix-dual \
  --branch feature/research-quality-gates \
  --task "対象範囲を限定したレビューと品質ゲート修正"
```

Do not reuse a failed model name in the command. If a CLI `--model` override is present, it takes precedence over the repaired global config.

## Repository safety

- Keep `auto_pr: false` by default.
- Never put API keys, bearer tokens, private keys, or model credentials in a skill, task text, report, screenshot, or committed config.
- Keep external-network and production operations within the user's explicit authorization.
- Do not treat staged/untracked state as a reason for an agent to run `git add`, `commit`, or `push` when the workflow says not to.
- When a workflow edits a dirty worktree, record the pre-run state and review the complete diff before accepting its output.

## References

- Read [configuration-and-provider.md](references/configuration-and-provider.md) for model/account compatibility, config fields, and provider troubleshooting.
- Read [workflow-selection-and-loops.md](references/workflow-selection-and-loops.md) for workflow limits, concurrency, quiet mode, and resume decisions.
- Read [log-analysis-and-recovery.md](references/log-analysis-and-recovery.md) for JSONL inspection patterns and failure classification.
