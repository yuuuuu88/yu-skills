# TAKT Workflow Selection and Loop Reference

## Workflow choice

| Task shape | Workflow | Built-in limit observed |
|---|---|---:|
| New implementation with plan → tests → implementation → review/fix | `dual` | 60 steps |
| Existing code review plus fixes | `review-fix-dual` | 30 steps |
| Small frontend/backend implementation | `dual-mini` | 20 steps |

Inspect installed workflow YAML instead of assuming these values never change:

```bash
find "$HOME/.local" -path '*takt/builtins/*/workflows/*.yaml' -print 2>/dev/null
sed -n '1,35p' "$TAKT_WORKFLOW_FILE"
```

Use the smallest workflow that satisfies the task. A full `dual` run is expensive for a review-only task and has more opportunities for a reviewer/fixer cycle to repeat.

## Loop interpretation

A workflow can be active while repeatedly visiting the same step. Track:

- total iteration / `max_steps`
- current step and step iteration
- whether reports or finding IDs changed
- whether the worktree changed for an intended reason
- whether the supervisor moved to a terminal gate

`Max steps reached` means the loop budget was consumed. It does not mean the terminal froze. Repeated findings with no new evidence are a convergence failure; reduce scope or fix the finding outside the loop.

The built-in dual workflow may have cycles such as:

```text
reviewers → final-gate → fix → reviewers
```

A threshold-based loop monitor can still consume the full budget when findings are stale, contradictory, or reported in a form the fixer does not recognize. Do not increase the budget as the first response.

### Final-gate conflicts

Treat the final gate and loop-judge evidence as authoritative when it conflicts with an ordinary reviewer report. A common failure mode is:

1. reviewers overwrite their shared report files with `APPROVE`;
2. the final gate still sees an older unresolved finding set;
3. the fixer addresses the newer report instead of the final-gate finding;
4. the loop repeats until `max_steps`.

Inspect the latest JSONL `step_complete.content` for `final-gate` / `merge-readiness-review` and the latest `phase_judge_stage.response`. Do not extend the budget until the contradictory finding set is reconciled.

## Parallelism

`concurrency: 5` is a practical baseline for independent review personas. It is not a guarantee that five workers will make useful progress. More workers can multiply duplicate findings, provider load, and conflicting edits. Keep concurrency stable while diagnosing a failure; change it only for a deliberate throughput experiment.

## Quiet mode and recovery

`--quiet` changes terminal presentation only. It does not change workflow semantics. For the first retry after an error, omit `--quiet` so the model, provider, current step, and error are visible. Use quiet mode only after the provider and environment have passed a smoke run.

## Fresh run versus resume

Start a fresh run when:

- the failure occurred before meaningful work, especially during the first step
- the model or provider configuration changed
- the worktree or task text changed materially
- the previous run reached its max-step limit
- the previous session has terminal error state and no deliberate checkpoint

Consider `--continue` only when:

- the same task and worktree are intended
- the last step has a valid resume point
- configuration is unchanged and known to work
- the agent's generated artifacts are still authoritative

Always inspect `.takt/session-state.json` and run `meta.json` before resuming. An old run existing on disk is not enough evidence to resume it.
