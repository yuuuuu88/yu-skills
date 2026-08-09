# TAKT Configuration and Provider Reference

## 1. Effective configuration

TAKT commonly reads:

- `$HOME/.takt/config.yaml`: user-wide defaults
- `<repository>/.takt/config.yaml`: project-local overrides when present
- workflow YAML: steps, personas, loops, `max_steps`, and workflow-scoped provider options
- CLI flags: the current invocation's explicit overrides

Use the installed version's config loader or `takt list` to detect parse errors. Do not infer effective values only from the YAML text when a project config or CLI flag may override them.

The global/project config uses normalized names internally. For Codex, the important YAML shape is:

```yaml
provider: codex
model: <account-supported-model>
concurrency: 5
auto_pr: false
provider_options:
  codex:
    reasoning_effort: xhigh
```

`reasoning_effort` accepts `minimal`, `low`, `medium`, `high`, and `xhigh` in the observed TAKT schema. `xhigh` controls effort only. It cannot authorize a model unavailable to the authenticated account.

## 2. Model compatibility

Separate three questions:

1. Does TAKT parse the model string?
2. Does the Codex CLI accept the model flag?
3. Does the authenticated ChatGPT/Codex account have access to the model?

Only the third question explains a provider response such as:

```text
invalid_request_error: The '<model>' model is not supported when using Codex with a ChatGPT account.
```

Treat that as a hard configuration failure. Do not retry unchanged, lower effort as a workaround, or switch providers without authorization.

Use a model already proven by a successful local run or explicitly listed by the provider/account. In the crypto-bot incident, `gpt-5.6-runa` was accepted by TAKT's config loader but rejected by the Codex account; `gpt-5.6-luna` had already completed real workflow steps and was the safe replacement. This is an environment-specific example, not a universal model list.

Keep model and effort consistent between:

- `$HOME/.takt/config.yaml`
- `$HOME/.codex/config.toml`, when Codex itself supplies a default
- any `--model` CLI option
- workflow-level provider options

A CLI `--model` wins for that run. Remove stale overrides when diagnosing a repaired global config.

## 3. Runtime environment

TAKT's own Node executable and the target repository's tools are separate concerns. For a Python repository, use:

```bash
export PATH="$PWD/.venv/bin:$HOME/.local/node-24.15.0/bin:$HOME/.local/bin:$PATH"
```

The project virtualenv must precede the system path so that both `python` and `pytest` resolve consistently. A missing `python` during a quality gate is an environment failure, not an implementation failure.

Do not put tokens or keys in `config.yaml`, `.env` committed files, task text, or reports. Provider authentication should remain in the provider's existing credential store or ignored environment files.

## 4. Safe baseline

Start with:

```yaml
auto_pr: false
concurrency: 5
provider_options:
  codex:
    reasoning_effort: xhigh
```

Do not add `max_steps` to global config. The workflow YAML owns that limit. The current TAKT schema's `workflow_overrides` is intended for quality-gate additions and does not replace the workflow's step limit.
