# Action Policy

## 目的

nousが実行しようとする操作を、実行主体やPhaseに依存せず同じ規則で分類し、承認、事前確認、事後確認、復旧、監査を機械的に強制する。

- すべての操作は開始前に既知の`action_type`へ正規化する
- Green / Yellow / Redの判定をAction Policy Matrixから導く
- Red Actionは有効なapprovalとの完全一致なしに開始しない
- secretは永続化と通常のoutbound payloadへ到達する前に検出し、redactまたはblockする
- `action_started`だけが残った場合はaction type固有のprobeでreconcileする
- policy decisionと実行前後のevidenceをEvent Journalへ残す

本書はaction分類の正規契約である。Humanへの質問形式は[Human Input Policy](./human-input-policy.md)、状態とEventは[Task State and Recovery Design](./task-state.md)、永続化操作は[taskctl Internal Utility Design](./taskctl.md)に従う。

## Policy Gateway

runtime enforcementのcomponent contractは [Runtime Policy Gateway Design](./policy-gateway.md) をcanonical authorityとする。本書はGatewayが評価するAction分類、risk、approval、probe、reconcile policyを定義する。

Phase Agent、Subskill、repository script、tool adapter、Orchestratorがtarget repositoryまたは外部状態へ作用させるすべてのmanaged Actionは、実行前にPolicy Gatewayを通る。型付きtaskctl commandによるTask Runtime内のcanonical mutationはtrusted control-plane operationであり、許可pathとSchemaへ閉じた別境界として扱う。

```txt
Action Proposal
  -> action_type normalization
  -> secret gate
  -> repository / task policy overlay
  -> risk evaluation
  -> approval binding
  -> preflight probe
  -> action_started + approval_consumed
  -> executor
  -> postcheck probe
  -> action_completed | action_failed | reconciliation_required
```

Policy Gatewayは未登録の`action_type`、解釈できないtarget、検査できないcommand、欠落したprobeをfail closedで拒否する。Actionを小さく分割しても、元の副作用やriskを低く分類してはならない。

## Action Descriptor Contract

Action Proposal、Execution Request、Approval、Execution Grant、Action Eventは、同じ`ActionDescriptor`を参照する。これが承認対象と実行対象の同一性を表すcanonical contractであり、各componentが独自のaction keyを追加しない。

```txt
action_id:
action_type:
task_id:
loop_id:
action_digest:
plan_ref:
plan_content_hash:
expected_state_revision:
actor_identity:
repository_identity:
environment:
normalized_target:
normalized_request:
expected_effect:
network_required:
paid_action:
paid_action_bound:
idempotency_key:
policy_overlays:
```

- `action_digest`はsecretを除去したaction type、正規化request / target、environment、Plan ref / content hash、paid action / bound、expected effectから作る
- `expected_state_revision`はcompare-and-swap用の観測値であり、Human承認の意味をTask State revisionへ暗黙拡張しない
- shell文字列だけでなく、実行file、引数、working directory、environment名、API methodとendpoint classを正規化対象にする
- credential値、authorization header、query内のsecretはhash入力にも永続記録にも含めない
- `paid_action: true`は常にRedであり、Action単位または明確に回数を限定したapprovalを要求する
- 初期契約ではtoken、API usage、累積costを記録するUsage Ledgerを持たない

## Risk決定とPolicy優先順位

有効riskは、次の順に適用した最も厳しい分類とする。

```txt
hard safety rule
repository-specific policy
task-specific policy
Action Policy Matrix default
```

上位policyは下位policyを緩和できない。repositoryまたはTaskは、ActionをGreenからYellow / Redへ上げる、approvalを追加要求する、networkやtargetを禁止することだけができる。conflict、未知のoverlay、判定不能はRedまたは`block`とする。

Policy decisionは次を持つ。

```txt
policy_version:
default_risk:
effective_risk:
policy_outcome: allow | approval_required | block
reason_codes:
applied_overlays:
approval_ref:
required_preflight:
required_postcheck:
```

## Action Policy Matrix

Runtimeで使用するbase MatrixはSkill Assetの`assets/policies/action-policy.json`として配布し、`action-policy.schema.json`で検証する。Human-readableな本表とmachine assetは同じpolicy versionを持ち、差異があればrelease gateを失敗させる。

より厳しいpolicyの保存先:

```txt
${NOUS_HOME}/config/policies/*.json       user / control repository policy
tasks/{task_id}/policy-overrides.json     Taskとtarget repositoryから正規化したoverlay
```

`policy-overrides.json`は元のrepository policy、Human指示、Task constraintへのprovenance refを持ち、taskctlだけが確定する。overlayはbase Matrixを緩和できず、未知field、provenance不明、競合するruleを含む場合はblockする。

`approval`の`required`は、実行時にscope照合済みapprovalが必要であることを表す。`conditional`は記載条件でのみ必要とする。各行のauditに加えて、すべてのActionでpolicy decision、actor、action digest、before / after evidence ref、結果、redaction summaryを記録する。

| action_type | default risk | approval | required preflight probe | required postcheck probe | reconcile / retry rule | required audit detail |
| --- | --- | --- | --- | --- | --- | --- |
| `file_read` | Green | none | realpath、read scope、symlink、size、repository policy | 読取対象hashまたはmetadata、secret検出結果 | external stateなし。対象変更時は再読取 | normalized path、contentは保存せずhash |
| `file_write` | Green | conditional: scope外またはsecurity-sensitive fileはRed | realpath、write scope、before hash、free space、secret gate | after hash、mode、限定diff、schema/test | temporary writeからのreplace状態を確認。before hash不一致は再実行禁止 | before / after hash、changed paths |
| `file_delete` | Yellow | conditional:復元不能な削除はrequired | ownership、target hash、参照、backupまたはquarantine先 | target不在、quarantine / backup hash | quarantine済みなら完了。部分削除は修復、完全削除の曖昧状態はHumanへ戻す | deleted target、recovery locator |
| `repo_script_run` | Yellow。inspection済み`test` / `lint` / `build`だけGreenへ降格可 | conditional: Red判定時required | 下記Repository Script Rule、working tree、command hash、network / write scope | exit code、changed paths、test report、network / external effect | idempotentかつ定義不変の場合だけretry。副作用不明ならHumanへ戻す | script定義hash、inspection ref、transitive commands |
| `package_install` | Yellow | conditional: global install、untrusted source、未検査lifecycle scriptはrequired | registry / source、package manager config、lockfile、install scripts、network、disk | lockfile / manifest diff、installed version、unexpected writes | lockfileとpackage DBを照合。部分installはclean repair planへ戻す | package、version、source、install-script判定 |
| `git_commit_control_repo` | Green | none | repository identity、branch、status、staged path、secret gate | commit hash、parent、committed paths | commit hashが存在すれば完了。index差分があれば再commit禁止 | commit、parent、path list |
| `git_push_control_repo` | Yellow | required: repository-scoped standing approval可 | repository / remote / branch、fetch、fast-forward、commit、secret gate | remote refがexpected commitを指すこと | remote ref一致なら完了。未更新かつfast-forward可能なら同じcommitをretry。不一致は停止 | local / remote refs、approval ref |
| `git_commit_target_repo` | Yellow | conditional: repository policyが要求する場合 | repository identity、branch、staged scope、secret gate、target artifact refs | commit hash、parent、committed paths、durability state | commit存在時は完了。ambiguous indexは再実行禁止 | target repo、commit、durability transition |
| `git_push_target_repo` | Red | required | exact repository / remote / branch / commit、fetch、fast-forward、secret gate | remote ref、provider response | expected commit一致なら完了。divergenceまたはunknownはHumanへ戻す | refs、approval ref、durability transition |
| `git_merge` | Red | required | exact source / target refs、CI evidence、working tree、merge strategy | resulting ref、merge commit、checks | resulting ref一致時のみ完了。conflictや別commitは再Plan | source / target / result refs |
| `external_http_get` | Yellow | conditional: stricter policyまたはpaid endpointはrequired | endpoint / queryの正規化、secret gate、network policy、cache / freshness | status、response hash、source metadata | safe methodかつsame request hashの場合だけretry | endpoint class、query hash、response ref |
| `external_http_post` | Red | required | endpoint、body hash、secret gate、idempotency / dedupe key、expected effect | provider object ID、status、read-back probe | provider IDまたはread-backで完了判定。証拠なしの再送は禁止 | request hash、provider ID、approval ref |
| `paid_api_call` | Red | required | provider、operation、最大単価または最大請求影響、回数scope、idempotency、secret gate | provider receipt / usage response、result ref | provider idempotency keyまたはreceiptなしではretry禁止 | expected charge bound、receipt、approval ref |
| `db_migration_local` | Yellow | conditional: destructive / irreversibleはrequired | DB identityがlocal、schema version、backup、SQL hash、destructive analysis | migration table、schema diff、verification query | migration IDとschemaを照合。partial stateはrepair migrationへ戻す | DB class、migration hash、schema evidence |
| `db_migration_remote` | Red | required | exact DB / environment、backup、migration hash、lock、rollback / compensation | migration table、schema / data probe、monitoring | migration IDと実schemaで判定。partial適用は自動retryしない | environment、migration、backup、approval ref |
| `deploy` | Red | required | exact project / environment / artifact digest、checks、rollback、secret gate | deployment ID、artifact digest、health probe | deployment IDとdigest一致なら完了。不一致やdegradedはrollback判断へ戻す | deployment / artifact / health refs |
| `external_message_send` | Red | required | recipient / channel、body hash、secret gate、dedupe key、Human名義確認 | provider message IDまたはread-back | message IDなしでは再送禁止 | recipient class、body hash、message ID |
| `secret_handling` | Red | required for runtime use; persistence / ordinary outboundは禁止 | sourceがapproved secret provider、destination adapter、least privilege、no-log設定 | secret非永続化、redaction scan、adapter result | 値を再読取・再送せずadapter resultで判定。漏洩疑いは停止 | secret kindとprovider refのみ。値 / value hashは禁止 |
| `subskill_workspace_write` | Yellow | conditional: underlying actionまたはscopeが要求する場合 | Manifest、declared write set、tool allowlist、network、before diff | after diff、undeclared write、validation | 宣言scope内の変更だけretry可。scope外writeは停止 | subskill ID / version、declared / actual diff |
| `subskill_external_effect` | Red | required | Manifest、tool / network allowlist、underlying action proposal、approval | underlying action postcheck、undeclared effect検査 | underlying action typeのreconcileを使用。分類不能なら再実行禁止 | subskill ID、underlying action、approval ref |

## Repository Script Rule

`test`、`lint`、`build`という名前だけではGreenにしない。次をすべて満たした実行だけをGreenとして扱う。

1. 対象repositoryを初めて扱う時、script定義と直接・間接に呼ぶcommandをinspectionする
2. manifest、task runner config、lockfile、wrapper script、参照先fileのhashをinspection evidenceへ束縛する
3. 前回inspection以降に定義または参照先が変わった場合、実行前に再inspectionする
4. network送信、secret読取、対象外write、install / deploy / migration、shell download-execute、難読化command、動的に解決される未知commandがない
5. local / isolated environmentで、write scopeとpostcheckを限定できる

inspection済みの`test` / `lint` / `build`以外はデフォルトYellowである。名前にかかわらず、未知のcommand、検査不能な間接実行、不審なdownload、credential access、広い削除はYellowへ留めずRedまたはblockへ上げる。定義hashが一致しない場合、以前のGreen判定を再利用しない。

## Approval Contract

### Storage

Approvalのcanonical storeはRoot Taskごとの`approvals.json`とし、taskctlだけが更新する。`requested`を含むcurrent stateをここへ保存し、`state.json`は`pending_approval_request_refs[]`と現在利用可能な`active_approval_refs[]`だけを持つ。Event Journalはlifecycle eventを保持するが、approvalの現在状態をEventだけから推測して実行してはならない。

Approval record:

```txt
approval_id:
approval_mode: exact_action | bounded_standing
status: requested | active | denied | revoked | expired | exhausted
requested_action_descriptor_ref:
granted_by:
granted_at:
expires_at:
revoked_at:
revoke_reason:
scope:
  task_id:
  loop_ids:
  repository_identities:
  environments:
  normalized_targets:
allowed_action_types:
excluded_action_types:
max_count:
consumed_count:
approved_plan_ref:
approved_plan_content_hash:
approved_action_digests:
action_constraints:
expected_effect:
paid_action_bound:
created_from_question_id:
```

- `requested_action_descriptor_ref`はrequest時点のAction digest、scope、risk、Plan ref/hash、target、課金境界を持つimmutable descriptorを指す。HumanQuestionはこのApproval requestを`approval_request_ref`で参照する
- `exact_action`は`expires_at`、`max_count`、`approved_plan_ref`、`approved_plan_content_hash`、`approved_action_digests`、target / repository / environment境界を必須とする
- `bounded_standing`はHumanが明示した反復可能なGreen / Yellow Actionだけに使い、repository、remote、branch、environment、allowed action type、normal form、forbidden option、expected effect classを`action_constraints`へ固定する
- `bounded_standing`はRed / paid Action、force push、history rewrite、別repository、別environmentを許可できない。期限やcountを無期限にする場合も、取消可能なprovenanceと固定scopeを必要とする
- `excluded_action_types`とhard safety ruleは常に優先する
- 別Task、別repository、別environment、変更後のcommand / API hashへ承認を継承しない
- paid actionはprovider、operation、最大請求影響または1回当たりの上限、最大回数を`paid_action_bound`へ固定する
- Usage Ledgerは持たないため、累積token / costをapprovalから推計しない。`max_count`の消費と各Actionのreceiptだけを記録する

### Scope照合とconsume

`action-start`は、approvalの`status = active`、時刻、Task、Loop、repository、environment、action type、normalized target、Plan ref/hash、action digest、expected effect、paid action bound、残りcountを同じ`ActionDescriptor`で照合する。`exact_action`は完全一致を要求し、`bounded_standing`はAction Proposalが固定`action_constraints`に包含され、forbidden optionを含まないことを要求する。

照合成功後、`approval_consumed`と`action_started`を同じrecoverable multi-file transactionへ記録し、`consumed_count`を増やす。同じ`action_id`のretryは再consumeせず、reconcile後に元の消費を参照する。`max_count`到達後は`exhausted`とし、新しいActionへ使えない。

### Revoke、expire、deny

- Humanの拒否は`approval_denied`を記録し、active approvalを作らない
- Humanはactive approvalをいつでも撤回でき、`approval_revoked`後の新規Action開始を禁止する
- `expires_at`到達時点でapprovalはEvent有無にかかわらず無効である。次のtaskctl operationで`approval_expired`を一度だけ記録する
- revoke / expire前に開始済みのActionは自動完了扱いにせず、停止可能性と外部状態をreconcileする。新しいretryには新承認を要求する
- approval event enumは`approval_requested`、`approval_granted`、`approval_denied`、`approval_revoked`、`approval_expired`、`approval_consumed`に固定する

## Secret Gate

Secret GateはTask request、Human回答、Phase artifact、Action proposal、Event、Checkpoint、probe evidence、stdout / stderr、generated view、commit、push、outbound requestを**永続化または送信する前**に検査する。

検出対象には、既知provider token形式、private key、credential付きURL、authorization header、`.env`値、高entropy credential候補、repository policyで追加されたpatternを含む。

```txt
high confidence secret
  -> persistenceとordinary outboundをblock
  -> valueを保持せずredaction summaryだけを記録

secret-like / uncertain
  -> valueを`[REDACTED:<kind>]`へ置換可能ならredact
  -> 意味が失われる場合はActionを停止してHumanへ確認

confirmed false positive
  -> Human approvalとpattern-scoped exceptionをsecret-exceptions.jsonへ記録
  -> repository / Task /期限を越えて再利用しない
```

redaction recordは`kind`、`location class`、`count`、detector IDだけを持つ。元の値、その可逆表現、値のhashをTask Filesへ保存しない。認証が必要なadapterはapproved secret providerから実行時にcredentialを注入し、通常payload、action digest、Event、probe evidenceから分離する。

Secret false-positive exceptionのcanonical recordとSchemaは[Task Files Design](task-files.md)の`secret-exceptions.json`を唯一のauthorityとする。本書はrecord fieldを再定義しない。適用時は同Schemaのactive recordと現在scopeを照合し、private key、既知provider tokenなどhigh-confidence hard blockは例外対象にしない。

## Outbound Rule

初期の共通outbound ruleは次に限定する。

- secretまたはsecret-likeで未解決の値を通常のquery、body、header、attachmentへ含めることを禁止する
- 送信前にpayloadを最小化し、Secret Gateを通す
- repository-specific policyまたはtask-specific policyが、private context、個人情報、顧客情報、特定endpointをより厳しく制限する場合は、そのpolicyを優先する
- stricter policyがない非secret情報は、Action Policy Matrixのaction typeとriskに従う
- trusted adapterが認証用headerへ実行時注入するcredentialは、approved `secret_handling`として通常payloadと分離する

Human approvalがあっても、secretを通常payloadとして送信するhard blockは解除しない。

## Probe Evidence Contract

probe resultは本文やsecretを直接Eventへ埋めず、canonical artifactまたは外部provider evidenceへの参照として保存する。

```txt
evidence_id:
action_id:
original_action_id:
probe_kind: preflight | postcheck | reconcile
observed_at:
observer_identity:
target_fingerprint:
result: pass | fail | ambiguous
reconcile_outcome: completed_verified | not_started_safe_to_retry | partial_requires_repair | ambiguous_requires_human
facts:
artifact_refs:
redaction_summary:
```

`reconcile_outcome`は`probe_kind = reconcile`の時だけ必須とし、それ以外では禁止する。

`action_started`は`policy_outcome`、`gateway_outcome`、`before_probe_ref`を必須とする。`action_completed`はsuccessを示す`after_probe_ref`を必須とする。`action_failed`または`reconciliation_required`も、判定に使ったafter / reconcile evidenceを参照する。

## Reconcile Contract

各Action typeはMatrixに加えて次を定義する。

```txt
pre_probe:
idempotency_key_location:
success_probe:
partial_state_patterns:
safe_retry_condition:
compensation_strategy:
human_fallback_condition:
```

再開時は、保存済みaction digest、idempotency key、before evidenceと現在の実状態を比較する。結果は次のいずれかに正規化する。

```txt
completed_verified
not_started_safe_to_retry
partial_requires_repair
ambiguous_requires_human
```

`action_reconciled` Eventは`original_action_id`、reconcile evidence ref、上記outcomeを保存する。`completed_verified`はcompletion確定、`not_started_safe_to_retry`は元のAction identityでの安全なretry、`partial_requires_repair`はrepair Plan、`ambiguous_requires_human`はHuman待ちへ遷移させる。

`not_started_safe_to_retry`以外を新規実行としてretryしない。provider object ID、remote ref、artifact hashなどのsuccess probeがない外部送信・課金Actionは、曖昧時に再実行しない。

## 不変条件

- Action開始前に既知の`action_type`とpolicy decisionがある
- unknown、判定不能、scope不明のActionはfail closedにする
- Red Actionはscope照合済みapprovalなしに開始しない
- paid actionはUsage Ledgerの有無にかかわらずRedとする
- repository scriptのGreen判定はinspection evidenceと定義hashへ束縛する
- secretはcanonical data、Event、log、commit、push、通常outbound payloadへ保存・送信しない
- repository / Task固有の厳しいpolicyを共通policyより優先する
- approvalのdeny / revoke / expire / consumeを固定Eventで記録する
- action-startとcompletionにpolicy decisionおよびbefore / after evidenceを残す
- interrupted Actionはaction type固有のreconcileなしに再実行しない
