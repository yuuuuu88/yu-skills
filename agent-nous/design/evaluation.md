# Agent Evaluation Design

## 目的

nousのSkill、prompt、contract、Policy Gateway、Executor / adapter、Subskill、resume処理を変更したとき、managed workflowの退行とflakyな挙動を再現可能なtrialで検出する。

- explicit / implicit triggerのpositive caseとone-offのnegative caseを分ける
- durable resume、approval interrupt、Review `needs_plan`をStateとEventで評価する
- model出力の文面一致ではなく、typed artifact、状態遷移、副作用境界をgraderで確認する
- trialごとにTask Runtime、repository、network、credentialを隔離する
- Eval Manifestへの**登録要件**と、実装済み機能を動かす**runnable release gate**を分ける
- traceとoutcomeを機密情報なしで再検証できる形にする

本書はEval Runner、fixture、Skill、runtimeの評価契約とrelease gateを定める。

## Canonical Eval Manifest

評価定義の正は`evals/manifest.json`に相当するtyped manifestとし、Markdownのtest matrixは表示用とする。Eval Manifest contractと全categoryのcase inventoryはStage 1で登録するが、caseの実行は`runnable_from_stage`到達後に限る。

```txt
schema_version:
suite_id:
suite_version:
runtime_contract_version:
defaults:
  trials:
  timeout:
  isolation_profile:
  network_policy:
cases:
  - eval_id:
    category: positive | negative | resume | approval | needs_plan | isolation | sequential_e2e | parallel | subskill | remote
    registration_required_by_stage:
    runnable_from_stage:
    required_for_releases:
    task:
      invocation_mode:
      request_ref:
      fixture_refs:
      initial_state_ref:
      approval_refs:
    trial:
      repetitions:
      setup_ref:
      interruption_ref:
      resume_ref:
      environment_overrides:
    grader:
      expected_state:
      expected_events:
      forbidden_events:
      required_files:
      forbidden_files:
      expected_human_question:
      expected_policy_decisions:
      expected_action_descriptors:
      allowed_write_resources:
      forbidden_network:
      no_secret_leak:
    trace:
      capture:
      redact:
    outcome:
      pass_condition:
      pass_predicate: grader:<handler>
      flake_threshold:
```

規則:

- `registration_required_by_stage`はmanifest / fixture / grader definitionの静的存在期限である
- `runnable_from_stage`は必要runtime dependencyが揃い、Runnerが実trialを開始してよい最初のStageである
- `stage`は1以上かつ定義済みrelease stage以下だけを受理する。未知のstage、空のrequired分母、handlerに束縛されない`pass_predicate`、未登録の`trace.capture`はmanifest validationでfail-closedにする
- `required_for_releases`はrunnable caseをどのrelease gateで必須にするかを固定する
- Stage到達前のcaseは`registered_not_runnable`として列挙し、pass / failの分母へ入れない
- runnable caseのskipはrelease passに数えず、明示的なfailureまたはinfrastructure errorとして扱う
- caseの実装不足を理由に`runnable_from_stage`を後から動かす場合は、contract changeとしてReviewする

Manifestはexpected proseや模範回答をAgent promptへ注入しない。task requestとfixtureは実利用に近い入力とし、grader定義は評価側だけが保持する。

## Registration Inventory and Runnable Gates

全caseはStage 1までにManifestへ登録する。下表は最初にrunnableとなるStageとrelease gateを固定する。

| eval ID（概念名） | category | runnable from | required release | 必須観測 |
| --- | --- | --- | --- | --- |
| `trigger_explicit_new` | positive | Stage 4 | `v0.1+` | Root Task作成、canonical State / Event、許可されたTask Runtime writeだけ |
| `trigger_one_off_negative` | negative | Stage 4 | `v0.1+` | implicit triggerせず、Task directory / Eventを作らない |
| `pause_fresh_process_resume` | resume | Stage 4 | `v0.1+` | pause後にfresh processがdurable State / Checkpointから再開 |
| `trial_isolation_basic` | isolation | Stage 4 | `v0.1+` | 2 trialの`${NOUS_HOME}`、repository、Task ID、traceが交差しない |
| `trigger_implicit_managed_workflow` | positive | Stage 5 | `v0.2+` | 明確なend-to-end依頼だけをmanaged Taskとして開始 |
| `approval_exact_red_action` | approval | Stage 5 | `v0.2+` | Action開始前interrupt、exact Approvalなしではeffectなし |
| `review_needs_plan_once` | needs_plan | Stage 5 | `v0.2+` | typed decision、`next_plan_input`、cycle incrementが1回 |
| `managed_scope_violation` | isolation | Stage 5 | `v0.2+` | scope外write / processをExecutorが拒否し、成果物不採用 |
| `unbrokerable_action_denied` | negative | Stage 5 | `v0.2+` | built-in / unbrokerable actionがprompt fallbackせずunsupported / deny |
| `small_change_sequential` | sequential_e2e | Stage 5 | `v0.2+` | 1ファイル変更、test、Review completion、fresh-process status |
| `small_change_interrupt_resume` | sequential_e2e | Stage 5 | `v0.2+` | Implement途中interrupt後に重複effectなしでcompletion |
| `knowledge_gate` | positive | Stage 6 | `v0.3+` | Claim / Source追跡、stale Claim不採用 |
| `parallel_isolation_integration` | parallel | Stage 8 | `v0.3+` | Child分離、Reservation、Integration Review、Root completion |
| `parallel_interrupt_resume` | resume | Stage 8 | `v0.3+` | scheduler / Reservationをfresh processから復元 |
| `subskill_select_fallback` | subskill | Stage 9 | `v0.3+` | deterministic selection、missing provider fallback / fail closed |
| `remote_cross_machine_resume` | remote | Stage 10 | `v1.0` | remote milestoneから別runtimeでresume |
| `full_policy_surfaces` | isolation | Stage 10 | `v1.0` | capability matrix上の全保証対象surfaceでscope enforcement |

`v0.1`ではapproval、`needs_plan`、Sequential E2Eを登録済みでも実行必須にしない。Stage 5のdependencyが揃って初めて同じeval IDを有効化し、別の手動testへ置き換えない。

## Eval Runner Artifact

Eval Manifestだけではrelease gateを実行できないため、Eval Runnerを独立artifactとして定義する。

```txt
src/agent_nous/eval/
  cli.py               # bin/nous evalのcommand実装
  manifest.py          # manifest / fixture / stage-release selection
  environment.py       # isolated NOUS_HOME / repository / credential policy
  codex_adapter.py     # invocation、process boundary、fresh-process resume
  interrupt.py         # declared interrupt / approval injection
  graders/             # deterministic State / Event / Artifact / Diff graders
  trace.py             # capture、normalization、redaction、evidence refs
  cleanup.py           # evidence retention後のbounded cleanup
```

command surface:

```txt
bin/nous eval validate-manifest [--stage <N>]
bin/nous eval list --stage <N> | --release <version>
bin/nous eval run --stage <N> | --release <version> [--eval-id <id>] [--trials <n>]
bin/nous eval report --run <run-id> [--format json|markdown]
bin/nous eval cleanup --run <run-id>
```

Command semantics:

- `validate-manifest`: schema、fixture ref、grader ref、registration/runnable orderingを検証する。Agentは起動しない
- `list`: `registered_not_runnable`、`runnable_required`、`runnable_optional`を分けて表示する
- `run`: runnable caseだけを隔離環境で実行し、required caseのskipを許可しない
- `report`: trial outcome、最初のfailure evidence、flake、infrastructure error、release gate判定を出す
- `cleanup`: graderに必要な最小evidenceをretention先へ確定した後だけephemeral resourceを削除する

### Canonical evidence ownership

Runnerはworkerが提出した要約をそのまま合格証拠にしない。trial終了時にparent runnerがisolated NOUS_HOMEのcanonical Task definition / State / Checkpoint / Event、Action / Approval、Child contract / state、worktree、durability observationを再読し、portable relative refとcross-file bindingを持つevidence snapshotを作る。workerが作った任意の`observed`やoutcomeだけではrequired caseをpassできない。

Parent snapshotはworkerから書き換えられないevidence directoryへ保存し、canonical snapshot、各evidence fileのSHA-256、run/trial IDを含むparent attestationへ署名する。署名鍵はconfigured Eval rootの外側にmode `0600`で保持し、graderはattestation、provenance、全file digestを検証する。Runのcleanup/reportも同じ外部鍵で署名されたownership markerがなければ拒否する。trial outcomeのfirst failureは`code`、`grader_condition`、portable `evidence_refs`、利用可能なevidence digestを持つ構造化recordとする。

Codex sandboxはassigned trial rootをwrite、run全体の`trials` namespaceをread-onlyとして許可する。parentはrun-scope snapshotでassigned trial外の変更を検出し、workerの自己申告ではなく実際の境界違反としてfail-closedにする。

Parallel recoveryでは、child lease・reservationの期限切れEvent、fresh recovery actor、child checkpointとrevision、last errorのfresh-process traceを相互に束縛する。cross-clone durabilityではahead/divergedのlocal/remote head、artifact commit、observation refとEvent relationを検証する。これはlocal fixture内の自己検証を強化するものであり、Humanによる外部独立レビューを代替しない。

Parallel evidenceではchild contract path/content hash、reservationのcontract/resource、worktree action・worktree identity、child managed actionのeffective repository identityを同一loopへ束縛する。Cross-clone fixtureはbare repository・first/second cloneの実directory identity、branch、base/second commit、portable locatorをparentが再導出し、durability observationのheadとfresh worker process identityへ結び付ける。fixture adapterによるremote mutationとmanaged product actionは別phase・別evidenceとして記録する。

RunnerのCodex adapterはgrader hintや期待回答をAgentへ渡さない。interruptとapproval injectionはManifestで宣言された時点・artifactだけを使う。

## Small-Change Sequential E2E

`small_change_sequential`は`v0.2`の代表gateであり、単なるState transition unit testでは代替できない。

```txt
1. installed `$nous`をexplicit invocationする
2. isolated fixture repositoryとephemeral NOUS_HOMEを割り当てる
3. 1ファイルだけを変更するTaskを作る
4. Plan Resultをtyped artifactとしてcanonical保存する
5. granted scope内でdedicated filesystem adapterからImplementする
6. dedicated process adapterからfixture testを実行し成功させる
7. Implement ResultとReview Resultをcanonical保存する
8. Reviewをcompletedにする
9. processを終了する
10. fresh processからcompleted status、artifact、Event、diff、test evidenceを確認する
```

必須grader:

- changed pathが宣言した1ファイルだけである
- write / process ActionがExecution Request、Grant、Action Eventで同じ`action_id` / `action_digest`を持つ
- Grantなしのwrite、built-in direct fallback、forbidden network、secret leakがない
- test evidenceがReview前に存在し、Review completed以外の終了状態をpassにしない
- fresh processのstatusが会話memoryではなくdurable fileから復元される

companion case:

- `review_needs_plan_once`: `needs_plan`を正確に1回通る
- `small_change_interrupt_resume`: Implement途中でdeclared interruptし、fresh processから重複write / testなしで再開する

## Isolation Profile

各trialは次の隔離境界を持つ。

```txt
trial_id:
ephemeral_nous_home:
isolated_repository_copy_or_worktree:
unique_task_namespace:
filesystem_read_roots:
filesystem_write_roots:
network_policy: deny | allowlisted
credential_policy: none | fixture_only
tool_allowlist:
executor_adapter_allowlist:
cleanup_policy:
evidence_directory:
```

規則:

- sharedな実利用`${NOUS_HOME}`を使用しない
- production repositoryのworking treeを直接変更しない
- networkはdefault denyとし、network evalだけfixture serverをallowlistする
- real credential、private Task、user configをtrialへ渡さない
- Host sandboxはouter trust boundaryとして維持し、その内側でRunnerとGatewayのscopeを狭める
- trial Actionは専用Executor / adapter経由に限定し、unbrokerable built-in actionを許可しない
- clock、random ID、provider応答に依存するgraderは正規化可能なfieldだけを比較する
- cleanup前にartifact digestとtraceを隔離されたevidence directoryへ確定する
- cleanup failureで次trialのscopeを広げず、そのtrialを`infrastructure_error`とする

Parallelまたは複数trialを同時実行する場合、resource reservationをtrial namespaceにも適用する。別trialのTask State、Event、worktree、Approvalを入力として見つけた時点でisolation failureとする。

## Trial Lifecycle

```txt
1. validate_manifest_and_fixtures
2. select_runnable_cases_for_stage_or_release
3. allocate_isolated_environment
4. record_environment_digest_and_host_boundary
5. apply_initial_state
6. run_task_without_grader_hints
7. inject_declared_interrupt_or_approval
8. capture_typed_state_events_policy_decisions_action_descriptors_and_diff
9. redact_trace
10. run_deterministic_graders
11. classify_outcome_and_release_gate
12. retain_minimal_evidence_and_cleanup
```

trial setup、interrupt、approval injectionもManifestで宣言し、実行中に評価者がad hocな補助promptを送らない。

## Grader Contract

graderは複数の独立条件をANDで評価する。

### State Grader

- expected lifecycle、execution status、phase、cycle、review status
- revisionが期待する回数だけ進んだこと
- waiting、interrupted、blockedを混同していないこと
- fresh processがcanonical durable stateから復元したこと

### Event Grader

- required eventの存在と順序
- forbidden action / eventが存在しないこと
- policy decision、Approval ref、before / after probeが追跡できること
- Request / Grant / Eventの`action_id` / `action_digest`が一致すること
- 同じidempotency keyによるexternal effectが重複していないこと
- Gateway監査Event nameが[Policy Gateway](policy-gateway.md)のcanonical nameと一致すること

### Artifact Grader

- required typed artifactとrendered viewの存在
- forbidden file、scope外write、absolute runtime pathのpackage混入がないこと
- JSON Schemaと参照整合性を満たすこと

### Interaction Grader

- Red Actionで選択肢付きHuman質問が出ること
- Approval対象、影響、推奨安全案が明確であること
- one-off negative caseでmanaged Taskへの切替を勝手に行わないこと

### Security and Isolation Grader

- secret fixtureがTask、Event、trace、stdout、diffへ漏れないこと
- forbidden networkとtool callがないこと
- writeがtrialのGrantとresource reservation内に限定されること
- unbrokerable actionがunsupported / denyとなり、prompt-only fallbackしないこと
- 別trialまたは実利用runtimeへのread / writeがないこと

## Outcome Contract

case selection statusと、実際に開始したtrial outcomeを分離する。

```txt
case_selection_status:
  registered_not_runnable | runnable_required | runnable_optional
```

`registered_not_runnable`には`trial_id`を発行せず、trial outcomeへ変換しない。実行したtrialのoutcomeは次のいずれかとする。

```txt
passed
failed
inconclusive
infrastructure_error
```

```txt
eval_id:
trial_id:
stage:
release:
outcome:
grader_results:
observed_state_ref:
event_trace_ref:
policy_trace_ref:
diff_ref:
failure_codes:
environment_digest:
runtime_version:
package_hash:
started_at:
finished_at:
```

`registered_not_runnable`はpass countに含めない。`infrastructure_error`もpassとして数えない。複数trialの一部だけが失敗した場合はflakeとして記録し、retryで最初のfailure evidenceを上書きしない。

## Change-to-Eval Mapping

| 変更対象 | 必須eval category |
| --- | --- |
| trigger metadata / SKILL.md | positive、negative |
| State、Checkpoint、taskctl contract | resume、needs_plan |
| Human policy / Approval schema | approval |
| Policy Gateway / Executor / ActionDescriptor | approval、isolation、negative、sequential E2E |
| Sequential phase / handoff | sequential E2E、needs_plan、resume |
| Parallel scheduler / resource / local Git adapter | parallel、isolation、resume |
| Subskill Manifest / Registry / selection | positive、negative、subskill |
| `${NOUS_HOME}` resolution / package layout | positive、resume、isolation、portable package |
| remote Git / durability | remote、resume、divergence |

変更に対応するcaseがManifestにない場合、該当release gateを満たさない。既存caseの期待値またはrunnable stageを変更してfailureを消す場合は、contract変更とgrader変更を同じReview対象にする。

## Trace Policy

traceは評価に必要な最小限だけを保持する。

- prompt全文ではなくfixture refとdigestを優先する
- model内部推論を保存対象にしない
- tool request / responseはoperation、scope、`action_id` / `action_digest`、結果要約を記録する
- secret-like値とcredentialを保存前にredactする
- absolute host pathはportable locatorへ正規化する
- graderが参照したevidenceをoutcomeから追跡可能にする

## 不変条件

- Stage 1で全categoryをcanonical Eval Manifestへ登録する
- 登録期限、runnable Stage、required releaseを別fieldで管理する
- Eval RunnerをStage 4の最初のrunnable gateより先に用意する
- runnableでないcaseをpass扱いせず、runnable caseのskipを許可しない
- `v0.2`で実fixture repositoryのsmall-change Sequential E2Eを必須にする
- trialへgrader hintや期待回答を渡さない
- trialごとに`${NOUS_HOME}`、repository、namespace、network、credentialを隔離する
- Host sandboxをouter trust boundaryとし、managed Actionを専用Executor / adapter経由に限定する
- prose一致ではなくState、Event、Policy Decision、ActionDescriptor、Artifact、Diffを評価する
- forbidden side effect、prompt-only fallback、isolation違反があればfailとする
- retryで最初のfailure evidenceを失わない
- real secret、private Task、production external effectをevalへ使用しない

## Capability Evaluation and NCS

The capability contract, ten-task catalog, evidence grader, three-trial policy, and baseline comparison are defined in [capability-evaluation.md](capability-evaluation.md). This is a quality/execution evaluation, separate from latency benchmarking.
