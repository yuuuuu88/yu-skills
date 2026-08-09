# TAKT Log Analysis and Recovery Reference

## Contents

- [Artifact locations](#artifact-locations)
- [Minimal inspection](#minimal-inspection)
- [Failure classification](#failure-classification)
- [Recovery record](#recovery-record)

## Artifact locations

For a run `<slug>`, inspect:

```text
.takt/runs/<slug>/meta.json
.takt/runs/<slug>/logs/*.jsonl
.takt/runs/<slug>/reports/
.takt/session-state.json
```

The JSONL is an event stream. Reports are useful summaries, but source code, test output, and terminal events are the authority when a report says that a finding was fixed.

## Minimal inspection

```bash
run_dir=".takt/runs/<slug>"
cat "$run_dir/meta.json"
tail -n 40 "$run_dir"/logs/*.jsonl
grep -R -n -E 'workflow_abort|Max steps|invalid_request|command not found|ERROR|WARN' "$run_dir"
```

Use a JSON parser when the log contains escaped prompts or long model output:

```bash
python3 - <<'PY'
import json, glob
path = glob.glob('.takt/runs/<slug>/logs/*.jsonl')[0]
for raw in open(path, encoding='utf-8'):
    event = json.loads(raw)
    if event.get('type') in {'workflow_start', 'step_start', 'step_complete', 'workflow_abort'}:
        print(event.get('type'), event.get('step'), event.get('iteration'), event.get('reason'))
PY
```

## Failure classification

### Provider/model

Symptoms:

- HTTP 400 `invalid_request_error`
- model unsupported for the ChatGPT account
- failure at the first agent step

Action:

1. Preserve the failed run.
2. Select a model known to work for the account.
3. Check CLI `--model` overrides and Codex config.
4. Keep `xhigh` if supported.
5. Validate with `takt list`, then start a new bounded run.

### Environment/PATH

Symptoms:

- `python: command not found`
- wrong `pytest`, `node`, or package manager resolved
- quality gate fails before project code runs

Action:

1. Prepend the repository virtualenv to `PATH`.
2. Use the repository launcher.
3. Print `command -v python pytest node takt` before rerunning.
4. Re-run the failed quality gate independently.

### Workflow convergence

Symptoms:

- same reviewer findings across iterations
- fixer report says “fixed” but the next reviewer repeats the same finding
- `N/N` max-step exhaustion after a long run

Action:

1. Compare the latest two reports and finding IDs.
2. Verify the alleged fix in source and tests.
3. Fix the underlying issue or dispute it with evidence.
4. Use `review-fix-dual` or a narrower task instead of another full `dual` run.

### Repository implementation

Symptoms:

- provider and gates start normally
- tests, typecheck, lint, or build fail on project code

Action:

1. Read the complete failure, not only the summary.
2. Reproduce the failing command outside TAKT.
3. Fix and test directly.
4. Re-run TAKT only when its review/fix loop still adds value.

## Recovery record

A useful handoff records:

- run slug and final event
- workflow and iteration limit
- provider/model/reasoning effort used
- exact failure class
- configuration or environment change
- whether a fresh run or resume is appropriate
- independent quality-gate results

Never include API keys, bearer tokens, private keys, or full secret-bearing environment values in that record.
