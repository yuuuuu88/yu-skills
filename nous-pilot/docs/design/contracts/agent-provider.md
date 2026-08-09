---
id: CT-003
type: contract
title: Agent provider adapter contract
status: accepted
source_of_truth: true
created: 2026-08-09
updated: 2026-08-09
related: [ARCH-001, CT-002, SEC-001]
managed_by: repository-bootstrap
---
# Agent provider adapter contract

## Provider identity

```text
provider_id
provider_version
package_hash
capabilities[]
transport
supports_roles[]
timeout_policy
retry_policy
cost_policy
credential_policy
network_policy
```

`provider_id`とpackage hashはTask開始時にpinし、resume時に別実体へ無断交換しない。

## Invocation input

```text
run_id
task_id
loop_id
phase
role
child_contract
input_artifact_refs[]
repository_snapshot
policy_grant
output_schema
resource_budget
```

## Invocation output

```text
run_id
status
artifact_refs[]
evidence_refs[]
files_changed[]
commands_run[]
findings[]
decision
usage
failure_code
resume_hint
```

Raw stdout/stderrはcanonical stateへ保存せず、secret-safe digestと必要なdiagnostic summaryを保存する。

Phase 1の実装では、`NousRuntimeClient`が上記のruntime boundaryを担当し、`RecordingProvider`がローカル契約テスト用のdeterministic fake providerを担当する。これはlive providerの代替ではなく、provider契約と失敗時の制御を検証するためのfixtureである。

個人開発向けの実行では、`SubprocessAgentProvider`または同等の
`external_bridge.py`を使う。外部AgentはJSON objectをstdinで1件受け取り、
`status`、`summary`、`outputs`を含むJSON objectをstdoutで1件返す。commandが
設定されていない場合はblockedとし、固定実装やCoordinatorへの暗黙fallbackを行わない。

最小の wire 形式は次の通り。

```json
{"role":"plan","request": {"objective":"..."}, "context": {}}
```

```json
{"status":"success","summary":"短い要約","outputs":{"acceptance_criteria":[]},"run_id":"optional-id"}
```

Providerは `shell=False`、`PATH`等を絞った環境、timeout、stdout/stderrの合計
上限で子プロセスを囲む。既定ではtargetのsymlinkと`.git`を除いた一時copyを
working directoryにし、外部Agentの直接writeをstaging/change lifecycleから
分離する。JSON object以外、重複キー、非標準JSON値、secret-like key/value、
unsafeな変更path/argvは拒否する。raw stdout/stderrはartifactへ保存せず、
失敗理由だけをsecret-freeに返す。`RecordingProvider`はこの境界の決定的な
fixtureであり、live providerを意味しない。OS-level network sandboxはhost側の
別policy adapterが必要であり、このローカルprotocolはnetworkを暗黙に許可しない。

`SubprocessAgentProvider`の実行契約は次のとおりとする。

- commandはargvの文字列列として渡し、`subprocess.run(..., shell=False)`で起動する。
- stdin/stdoutはUTF-8 JSONを1件だけ扱う。stdoutとstderrの合計は
  `max_output_bytes`以内でなければならず、超過は秘密情報を含まない`failed`結果にする。
- `timeout_seconds`を超えた実行、起動失敗、または非ゼロ終了は、秘密情報やstderrを含まない
  `failed`結果にする。
- 子プロセスにはホスト環境を引き継がず、既定では`PATH`、
  `NOUS_PILOT_ROLE`、`NOUS_PILOT_PROVIDER_ID`だけを渡す。追加環境変数も予約名・
  secret-likeなキーを拒否する。
- malformed JSON、JSON object以外、重複キー、非標準数値、schema不一致、または
  `secret`、`password`、`token`、`api_key`、`private_key`などを含むキーは
  `ContractError`として拒否する。エラーにはstdout/stderrの本文を含めない。
- `result_from_mapping`はJSON互換性とsecret-likeなキーを検証し、outputsをdetached copy
  として`AgentResult`へ渡す。外部mappingの変更が結果へ波及してはならない。

## Failure semantics

- timeoutは成功扱いにしない
- schema不一致はprovider errorとして記録する
- scope外writeはpolicy violationとして停止する
- provider crashはbounded retry後、resume可能なblocked stateへ移す
- provider unavailable時は明示的な再選択またはHuman gateを要求する

## Capability negotiation

Providerはrole、structured output、tool use、network、credential、workspace、timeout、cancellationの能力を宣言する。Pilotは要求する能力とprovider能力を開始前に比較し、不足時にsilent substitutionしない。
