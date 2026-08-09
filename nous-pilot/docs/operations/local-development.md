---
id: OPS-001
type: operations
title: Nous Pilot local development
status: draft
source_of_truth: true
created: 2026-08-09
updated: 2026-08-09
related: [PLAN-001, ARCH-001, SEC-001]
managed_by: repository-bootstrap
---
# Nous Pilot local development

## Prerequisites

- Python 3.11+
- Linux, macOS, or Windows (the supported platform set)
- Git
- 既存`agent-nous` runtimeを利用できるローカル環境
- live providerを使わない場合はfake providerだけで検証可能

## Environment variables

通常の個人開発では、編集する設定ファイルはリポジトリ直下の`nous-pilot.toml`一つだけです。詳細な`.nous/config.json`はCLIが自動生成し、手編集しません。

初期設定:

```toml
[project]
mode = "personal"

[run]
cycles = 3
lease_seconds = 600
timeout_seconds = 300

[agents]
mode = "external"
reviewers = 2
# Optional: a subprocess that reads one JSON request from stdin and returns one JSON result.
# command = ["/absolute/path/to/your-agent"]

[research]
web = true

[remote]
mode = "ask"
```

外部実行環境を上書きする場合だけ、次の環境変数を任意で使います。

必要な場合だけ:

- `NOUS_BINARY`（`nous`がPATHにない場合だけ）
- `NOUS_HOME`（runtime保存先を変える場合だけ）

API keyやtokenなどの秘密情報は`nous-pilot.toml`へ入れず、必要なproviderの実行環境へだけ注入します。

## Install

The package has no mandatory runtime dependency. For a no-network smoke check,
use the Python and test tools already available in the environment; do not run
an install command just to inspect compatibility or build a wheel.

When `pytest` is not already available, a normal development environment may
install the optional test dependency:

```bash
python3 -m pip install -e '.[test]'
```

That optional developer install is not required by the package at runtime.
The no-install distribution check is documented in
[distribution.md](./distribution.md).

## Run

Repository inspection is available through the package CLI:

```bash
PYTHONPATH=src python3 -m nous_pilot.cli inspect .
```

Create or execute a local durable Root Task with a request JSON file:

```json
{
  "request_id": "req-local-001",
  "objective": "describe the requested change",
  "repository_root": "/absolute/path/to/repository",
  "risk_class": "green",
  "validation_profile": "unit"
}
```

```bash
PYTHONPATH=src python3 -m nous_pilot.cli init
PYTHONPATH=src python3 -m nous_pilot.cli doctor
PYTHONPATH=src python3 -m nous_pilot.cli create request.json \
  --run-id local-run-001
PYTHONPATH=src python3 -m nous_pilot.cli run request.json \
  --run-id local-run-001
PYTHONPATH=src python3 -m nous_pilot.cli start "describe the requested change" \
  --repository /absolute/path/to/repository --run-id local-run-002
PYTHONPATH=src python3 -m nous_pilot.cli status TASK_ID
PYTHONPATH=src python3 -m nous_pilot.cli show TASK_ID
PYTHONPATH=src python3 -m nous_pilot.cli resume TASK_ID
```

作業を実行せず、自然言語の目的を検証済みrequest JSONへ変換するには
`objective`（別名 `request`）を使う。

```bash
PYTHONPATH=src python3 -m nous_pilot.cli objective \
  "add a focused regression test" --repository . > request.json
```

`init`は既存の`nous-pilot.toml`を上書きせず、必要な場合だけ`--force`を
要求する。`status`、`show`、引数なしの`resume`はread-onlyであり、resumeを
実際に開始するには`resume TASK_ID --execute`を明示する。

PowerShellでは`PYTHONPATH=src`の代わりに
`$env:PYTHONPATH="src"; python -m nous_pilot.cli ...`を使う。

`create` is the safe default when only a Root Task is needed. `run` explicitly starts the configured local Nous Loop. If `nous` is not on `PATH`, set `NOUS_BINARY` once in the shell; no additional project config is needed.

The external Agent command is optional but required for a real development
run. When it is absent, the bridge returns a durable `blocked` result instead
of silently using a fixed demo implementation or a solo fallback. The command
must consume one JSON object and return one JSON object with `status`,
`summary`, and optional `outputs` fields.

Changes are staged and reviewed by default. A local target-repository apply
requires an explicit human gate through `TargetRepositoryAdapter`; commit
candidates are patch artifacts only. No commit, push, PR, or deployment is
performed by the package.

The `NousRuntimeClient` and `PilotControlPlane` adapters create a Root Task and run it through the local `agent-nous` runtime. They always pass an explicit `--nous-home`, use `shell=False`, and do not perform remote Git, PR, deployment, or credential operations.

## Validate

```bash
PYTHONPATH=src python3 -c "from nous_pilot.distribution import require_compatibility; require_compatibility()"
python3 ../skills/repository-bootstrap/scripts/validate_repository_bootstrap.py . --all-docs --strict
python3 -B scripts/validate_compile.py
python3 -B -m pytest -q -p no:cacheprovider
```

`-B` and `-p no:cacheprovider` keep validation read-only so the managed executor can verify that the process did not mutate its isolated snapshot.

## Troubleshooting

- bootstrap validation error: frontmatter、broken link、secret-like value、design progress markerを確認する
- provider unavailable: solo fallbackせず、設定、capability、timeout、credential policyを確認する
- task resume issue: agent-nousのtaskctl doctor、diagnose、resume artifactを確認する
- worktree conflict: reservation、base revision、write set、integration orderを確認する
- runtime adapter error: `agent-nous` binary、explicit `NOUS_HOME`、TaskStore doctor、そして local engine configを確認する
- unsupported runtime: `python -m nous_pilot.distribution` 相当の compatibility check で表示される Python version/platform の理由を修正する

## Related documents

- [Bootstrap plan](../plans/bootstrap-plan.md)
- [Distribution and compatibility](./distribution.md)
- [Agent provider contract](../design/contracts/agent-provider.md)
- [Security](../design/cross-cutting/security.md)
