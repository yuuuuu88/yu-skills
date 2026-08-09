# Cross-design Contract Map

## 目的

nousの複数設計書が同じ概念を異なる意味で定義しないよう、canonical authority、共通語彙、境界、参照方向を定める。

この文書は各設計の詳細を複製せず、「どの設計を正とするか」と「設計間で守る接続契約」を示す。

## Canonical Authority

| Contract | Canonical Design | 他設計の責務 |
| --- | --- | --- |
| System componentと境界 | `architecture.md` | component固有の詳細を参照する |
| Lifecycle / Execution / Phase / Cycle | `task-state.md` | 独自statusをTask statusとして追加しない |
| Review loop判定 | `autonomous-loop.md` | State transitionはTask Stateへ対応付ける |
| Typed Phase Artifact / Review Decision | `phase-artifacts.md` | Handoff、Template、Task Filesはfieldを再定義しない |
| Shared typed primitive / HumanQuestion | `task-templates.md`の`common.schema.json` | Phase ArtifactとHuman Policyはfieldを再定義せず、意味・表示・遷移だけを補足する |
| Root Task Definition / Stable Context | `task-files.md` | TemplateはSchemaとgenerated viewを提供し、Markdownを機械判定の正本にしない |
| Phase Output / Handoff | `phase-handoff.md` | typed artifactをPhase間Packetへ投影する |
| Source / Claim / Knowledge Gate | `knowledge.md` | PhaseはKnowledge statusを再定義しない |
| Human risk / Question / Approval interaction | `human-input-policy.md` | Action Approval recordのfieldを再定義しない |
| ActionDescriptor / Action type / Approval record / Binding / Reconcile | `action-policy.md` | Gateway、Human Policy、Event、operation設計は同じAction ID / digestとApproval Contractを参照する |
| Secret Exception record / Schema | `task-files.md` | Action PolicyとHuman Policyは適用条件だけを定め、record fieldを再定義しない |
| Child Loop / DAG / Integration | `parallel-loop.md` | Child stateはTask Stateを再利用する |
| Subskill Manifest / Selection | `subskill-architecture.md` | Orchestratorは具体IDへ依存しない |
| Managed Actionのruntime permission enforcement | `policy-gateway.md` | Manifestやpromptを権限強制手段として扱わず、仲介不能Actionを保証対象へ含めない |
| Task directory / canonical file | `task-files.md` | Templateはpathを変更しない |
| Template / JSON Schema / generated view | `task-templates.md` | RuntimeはSchemaとCatalogを利用する |
| Git / Retention / Archive / Cleanup | `task-operations.md` | Stateの意味を上書きしない |
| Internal command / Transaction / Exit | `taskctl.md` | Orchestrator判断をUtilityへ移さない |
| Skill trigger / package / disclosure | `installable-skill.md` | Runtime stateをSkill packageへ保存しない |
| Python runtime package / launcher | `installable-skill.md` | taskctlとEval Runnerは`src/agent_nous/`を実装正本とし、Skill scriptsはthin launcherに限定する |
| Agent eval / forward test | `evaluation.md` | StageとSkill設計はEval IDを参照する |

同じfieldやenumが複数文書に現れる場合、上表のcanonical designを変更してから参照側を更新する。

## Canonical Vocabulary

### Task Hierarchy

```txt
Root Task
  └── Loop root
      ├── Child Loop L01
      └── Child Loop L02
```

| 用語 | 意味 |
| --- | --- |
| Root Task | HumanのObjectiveと最終Acceptance Criteriaを持つ永続単位 |
| Loop | `plan → implement → review`を持つ実行単位 |
| Root Loop | Root Task自身のLoop。`loop_id: root` |
| Child Loop | Parentからscopeを委譲されたLoop |
| Parent Orchestrator | Childの生成、scheduling、統合を担うRoot側control plane |
| Worker | Loopを実行する一時的process / subagent / session |

`Child Task`をcanonical用語として使わない。独立したTop-level Objectiveが必要な場合だけ別Root Taskを作る。

### State Dimensions

```txt
lifecycle_status:
  active | waiting_human | blocked | completed | cancelled

execution_status:
  idle | running | pausing | paused | interrupted

phase:
  plan | implement | review

phase_kind:
  standard | integration

visibility:
  visible | archived

review_status:
  completed | needs_plan | needs_human_input

handoff_status:
  ready_for_next | needs_human_input | blocked

sync_status:
  clean | uncommitted | push_pending | conflicted | unavailable
```

`review_status`、`handoff_status`、`sync_status`を`lifecycle_status`として保存しない。Pauseとinterruptは`execution_status`であり、Task未完了を表すLifecycleとは独立する。

### Cycle

- Loopごとに1から始める
- Reviewの`needs_plan`で1増やす
- Human回答だけでは無条件に増やさない
- completed Cycle Outputはimmutable historyとする
- Integration ReviewもParent LoopのReview Phaseとして記録する

## State Transition Contract

| Trigger | Lifecycle | Execution | Phase | Cycle |
| --- | --- | --- | --- | --- |
| Task create | `active` | `idle` | `plan` | `1` |
| Phase worker start | unchanged | `running` | unchanged | unchanged |
| Plan ready | `active` | `idle` | `implement` | unchanged |
| Implement ready | `active` | `idle` | `review` | unchanged |
| Review `needs_plan` | `active` | `idle` | `plan` | `+1` |
| Review `needs_human_input` | `waiting_human` | `idle` | `review` | unchanged |
| Human answer | `active`または`waiting_human` | `idle` | Questionのtyped `resume_target`が指定するPhase | unchanged |
| Review `completed` | `completed` | `idle` | `review` | unchanged |
| Pause requested | unchanged | `pausing` | unchanged | unchanged |
| Safe pause | unchanged | `paused` | unchanged | unchanged |
| Lease loss | unchanged | `interrupted` | unchanged | unchanged |
| Blocker detected | `blocked` | `idle` | unchanged | unchanged |
| Blocker resolved | `active` | `idle` | checkpointから決定 | unchanged |
| Cancel | `cancelled` | `idle` | unchanged | unchanged |
| Explicit cancelled reopen | `active` | `idle` | `plan` | `+1` |

`completed`は同じTaskへ戻さない。追加作業は`derived_from_task_id`を持つfollow-up Root Taskとする。

## Canonical Storage Contract

### Machine-readable

```txt
state.json
events.jsonl
checkpoints/latest.json
tasks/registry.json
artifacts/index.json
task-definition.json
stable-context.json
approvals.json
secret-exceptions.json
knowledge/questions.json
knowledge/claims.json
knowledge/sources.json
cycles/{cycle}/01-plan-result.json
cycles/{cycle}/01-parallelization-plan.json
cycles/{cycle}/02-implement-result.json
cycles/{cycle}/03-review-result.json
cycles/{cycle}/03-review-decision.json
cycles/{cycle}/artifact-sets/{transaction_id}.json
loops/{loop-id}/contracts/rev-{revision:04d}.json
```

### Human-readable Source and Rendered Documents

```txt
task.md
summary.md
knowledge.md
sources.md
cycles/{cycle}/01-plan.md
cycles/{cycle}/02-implement.md
cycles/{cycle}/03-review.md
loops/{loop-id}/...
```

Task Definition、Stable Context、Phase Output、Review Decision、Knowledge Question / Claim / Sourceはtyped JSON artifactをcanonicalとする。対応するMarkdownはtaskctl rendererが生成するHuman-readable viewであり、State遷移やKnowledge Gateのためにparseしない。Human原文はSecret Gate適用後にtyped interpretationと分離して保持し、Research Notesなど機械判定を担わないMarkdownだけがsource documentになり得る。自律実行時も、Plan / Implement / Review Agentの出力はproposalとして扱い、親EngineがManagedExecutorで実行・観測したAction / Evidenceだけをcanonicalなphase artifactへ昇格させる。

### Generated Views

```txt
STATUS.md
resume.md
current-plan.md
current-review.md
```

generated viewはcanonical dataへ逆変換しない。State revisionまたはsource hashが一致しないviewは再生成する。

## Source of Truth Order

通常参照:

```txt
1. state.jsonのcurrent revisionとactive refs
2. active refが指すcanonical typed Phase Artifact
3. latest Checkpoint
4. Event Journal
5. Human-readable generated view
```

Recovery:

```txt
1. latest valid State / Checkpoint
2. Event Journalのintent / completion
3. Git、file、external resourceの実状態
4. generated view再生成
```

RecoveryではState Snapshotだけを盲信せず、外部の実状態と照合する。

## Identifier Contract

```txt
task_id: YYYYMMDD-HHmm-{slug}[-NN]
root loop_id: root
child loop_id: LNN-{slug}
claim_id: K-NNN または {loop-id}-K-NNN
source_id: S-NNN または {loop-id}-S-NNN
```

- IDは作成後に変更しない
- display titleは変更できる
- directory pathはstable Task IDを使う
- versionをIDへ含めず、別fieldで管理する
- Rootへ統合したChild Claimは新しいRoot IDと`derived_from`を持つ

## Human Input Contract

- Greenは自律実行する
- Yellowは仮定、Checkpoint、validation、rollbackを持つ
- Redは具体的なPre-approvalがなければHumanへ戻す
- 独立して回答できる質問は短い選択肢でまとめる
- 前の回答で選択肢が変わる質問は順序を分ける
- 固定の最大質問数を設けない
- Human回答なしをapprovalとして扱わない
- 質問は依存するAction / Loop / Phaseだけをblockする

Personal Control Repositoryへの通常push approvalは`task-operations.md`へ限定し、Target Repository、force push、history rewriteへ継承しない。

Action実行前には`action_type`を確定し、Action Policy Matrix、active approval、preflightを照合する。不明なAction typeとscope不一致のapprovalはfail closedする。

## Parallel Contract

- Parallel PlanはDAGでdependencyを表す
- Child Loopは独自State、Cycle、Checkpoint、Eventを持つ
- read-only調査はindependent subagentを優先する
- concurrent repository writeはworktreeで隔離する
- backendがなければlogical sequentialへfallbackする
- ChildはRoot共有fileを直接更新しない
- Child `completed`は統合可能を意味し、Root完了ではない
- Child LoopがないRootはstandard Reviewの`completed`で完了する
- 1つ以上のChild Loopを統合したRootはIntegration Reviewの`completed`でのみ完了する
- file pathだけでなくDB schema、API contract、lockfile、service、external resourceをtyped resourceとして宣言する
- writeまたはexclusive resourceは実行前にreservationし、semantic conflictを検出する

## Subskill Contract

- PhaseはCapabilityを要求し、具体Subskill IDを要求しない
- Registryはhard constraintで候補をfilterする
- Phase Agentはcompatible候補からTask contextで選ぶ
- Input / Output Envelopeを使う
- SubskillはState遷移、他Subskill起動、Root共有file更新を行わない
- selected versionとpackage hashをCycle内でpinする
- required safety Capabilityがない場合はfail closedする
- Manifestはpermission declarationであり、Policy Gatewayがtool、filesystem、network、write scopeを実行時に強制する
- 初期のuser-facing Codex Skillは`nous` 1つとし、Plan / Implement / ReviewをPhase名だけで別Skillにしない
- runtime SubagentはChild Loopの一時的実行backendであり、0個以上のSubskillを使える。Subskillは複数workerから再利用できる

## Package Boundary

```txt
agent-nous project
  design / TODO / Changelog / pyproject.toml / .venv / launcher / tasks runtime

skill/nous package
  SKILL.md / agents/openai.yaml / references / scripts / assets

installed Codex skill
  CODEX_HOME/skills/nous

target repositories
  Taskが変更するcode / docs / config
```

- Task dataをSkill packageへ保存しない
- source packageを正本とし、installed Skillはsource packageへのsymlink、launcherはproject venvを解決する
- Skill packageへproject README、TODO、CHANGELOGを含めない
- Target Repositoryへnous管理fileを置かない
- `NOUS_HOME`がTask Runtimeを指す
- Control Repository push approvalをTarget Repositoryへ流用しない

## taskctl Boundary

taskctlが担うもの:

- Schema validation
- Template rendering
- State transition適用
- Event / Checkpoint transaction
- Draft Phase Output Envelopeの検証とrecoverable multi-file commit
- Lock / Lease
- Parallel Plan、Child Loop、Resource Reservation、Child Result、Integration Reviewのcanonical更新
- Registry rebuild
- diagnose / repair / migrationの機械処理

Orchestratorが担うもの:

- Human intent理解
- Plan / Implement / Review
- risk classificationと質問
- Knowledge収集
- Parallel scheduling
- Phase Agentが行うSubskill選択の起動とSelection Record保存
- Git / external effectのpolicy判断

Phase Agentが担うもの:

- Capability Request作成
- Registryが返したcompatible候補からの最終選択
- Composition Planとtyped Phase Output作成

Policy Gatewayが担うもの:

- Action Policy、approval、permission scopeのruntime照合
- managed Executor / adapter経由Actionのtool、filesystem、network、Subskill実行の許可・拒否
- Codex host sandboxを外側のtrust boundaryとして扱い、仲介不能なbuilt-in Actionを`unsupported`または`deny`にする
- policy decisionとbefore / after evidenceの監査入力生成

taskctlはstdout JSON、stderr diagnostic、stable exit codeを返す。Git pushとexternal effectをinitial taskctlへ含めない。

## Version Contract

```txt
skill package hash
taskctl version
request / response contract version
State schema version
Template catalog version
Subskill registry revision
Subskill package version / hash
```

- active operationは利用versionをEventへ記録する
- unknown major versionを古いruntimeが上書きしない
- State migrationはplan / applyを分ける
- Template変更でpast Cycle Outputを書き換えない
- Resume時にruntime変更があればreconcileする

## Contract Validation Matrix

| Scenario | 主なContract |
| --- | --- |
| New Task | ID、Minimal Template、State create、Registry |
| Phase transition | Handoff、State table、Event、revision |
| Phase completion | typed Phase Artifact、conditional field、canonical write boundary |
| Review loop | Review status、cycle increment、active plan ref |
| Human wait | risk、Question、blocking scope、resume |
| Side effect | Action type、Policy Matrix、Approval、pre/post probe、reconcile |
| Pause / Resume | Checkpoint、Lease、Event、reconciliation |
| Unexpected exit | intent / completion、transaction marker、repair |
| Parallel Child | DAG、isolated state、worktree、Integration Review |
| Knowledge update | Claim、Source、freshness、Summary promotion |
| Subskill use | Capability、filter、selection record、Output Envelope |
| Subskill enforcement | Policy Gateway、declared scope、observed effect |
| Git milestone | validated path、commit scope、standing approval |
| Archive | visibility、stable path、Registry / STATUS |
| Cross-machine resume | pushed milestone、local-only artifact、new run ID |
| Agent behavior change | Eval Manifest、isolated fixture、positive / negative trigger |

## 不変条件

- Canonical authorityがあるfieldを参照側で再定義しない
- Taskの状態軸を1つの`status`へ潰さない
- generated viewをSource of Truthにしない
- typed Phase ArtifactなしにPhaseを完了させない
- Orchestrator判断をtaskctlやSubskillへ移さない
- active ChildとWorkerを区別する
- Human approval scopeを暗黙に拡張しない
- Action PolicyとPolicy GatewayをManifestやprompt上の注意だけで代替しない
- Action Proposal、Execution Request、Approval、Grant、Eventで別々のaction keyを作らず、同じActionDescriptorを参照する
- package、runtime、target repositoryを分離する
- version不一致をsilent fallbackで上書きしない
