# Phase Handoff Design

## 目的

Plan / Implement / Reviewが、会話履歴やMarkdown解析に依存せず、前工程の確定結果、現在状態、次工程への入力を理解できるhandoff contractを定義する。

Phase Artifactのfield、型、条件付き検証は [Phase Artifact Contract Design](./phase-artifacts.md) をcanonical authorityとする。この文書は、そのartifactをPhase間でどのように受け渡すかを定める。

## 情報の分離

| 種類 | 内容 | Canonical source |
| --- | --- | --- |
| State | 現在Phase、Cycle、Lifecycle、active artifact refs | `state.json` |
| Stable Context | 確定済み要求、判断、Knowledge | `stable-context.json` |
| Cycle Output | Plan / Implement / Reviewの確定結果 | `cycles/{cycle}/*-result.json` |
| Review Decision | Review loopの次状態判定 | `cycles/{cycle}/03-review-decision.json` |
| Parallel Contract | DAGとChildへの委譲契約 | Parallelization Planと`loops/{loop_id}/contracts/rev-{revision:04d}.json` |
| Human View | Humanが読むPhase内容とcurrent view | canonical JSONから生成したMarkdown |

MarkdownはHuman-readable viewであり、Phase Outputの正規入力または正規出力ではない。

## Write Boundary

Phase Agentはcanonical Task Filesを直接作成・更新しない。確定経路は次に限定する。

```txt
Phase Agent
  -> Draft Phase Output Envelope
  -> Orchestratorによるcontext / policy / revision照合
  -> taskctlによる型検証とrecoverable multi-file transaction
  -> canonical JSON + State / Event / Checkpoint
  -> rendered Markdown
```

Phase Agentが返すDraftにはPlan Result、Implement Result、Review Resultなどのtyped payloadを含める。Draftはworker responseまたはruntime draftであり、確定前に次Phaseが参照してはならない。

`taskctl`だけが次を確定・生成する。

- `state.json`, `events.jsonl`, `checkpoints/*.json`
- `cycles/{cycle}/*-result.json`, `03-review-decision.json`
- Parallelization Plan、Child Loop Contract
- canonical JSONからの`summary.md`, Phase Markdown、`current-*`, `resume.md`, `STATUS.md`生成

Implement Agentがtarget repositoryの許可されたscopeへ変更を加えることは、このTask Files write boundaryとは分離する。

複数canonical fileの確定は同じ`transaction_id`を持つprepared → canonical replace → committed protocolで行う。generated Markdown、Registry、STATUSはcommit後に再生成できるview / cacheであり、canonical commit成功の条件に含めない。

## State Packet

OrchestratorはPhase開始前に、canonical sourceからState Packetを組み立てる。

```txt
task_id
loop_id
cycle
phase
phase_kind
state_revision
objective
current_status
stable_context_refs[]
knowledge_references[]
previous_phase_artifact_ref
latest_review_decision_ref
open_question_refs[]
next_required_artifact_type
stop_conditions[]
```

State PacketはOrchestratorからPhase Agentへの入力contractであり、Taskのcanonical stateそのものではない。全pathはTask directory基準のrelative referenceとhashを持つ。

Resume時はTask State、Checkpoint、Event Journal、target repositoryの実状態を照合したResume PacketをState Packetへ内包する。復旧規則は [Task State and Recovery Design](./task-state.md) に従う。

## 共通Phase Handoff

各Phase Agentは開始時に次を確認する。

1. `task_id`、`loop_id`、`cycle`、`phase`、`state_revision`
2. ObjectiveとAcceptance Criteria
3. activeなStable ContextとKnowledge Reference
4. 前Phaseのcanonical typed artifact
5. 未解決Questionと停止条件
6. 自分が返すべきartifact typeと条件付き必須field

各PhaseのDraft ResultはField Dictionaryに従い、共通して次を持つ。

```txt
inputs_read
phase_summary
work_done
decisions
assumptions
open_questions
next_phase_input
handoff_status
knowledge_references
human_question_refs
blocking_reasons
```

`input_read`、`phase_status`などの別名を使わない。表示見出しとの対応は [Task Template Design](./task-templates.md) が定める。

`handoff_status: blocked`はPlan / Implementで使用できるが、`blocking_reasons[]`と解除条件を必須とする。taskctlは影響Loopを`lifecycle_status: blocked`へ遷移し、phase / cycleを維持する。Gateway `deny`または`replan_required`を独自のcycle incrementへ変換せず、代替手順がある場合はReview Decision `needs_plan`を経由する。Approval待ちは`needs_human_input`として、Questionへ`approval_request_ref`と`resume_target`を保存する。

## Plan → Implement

Plan AgentはDraft Plan Resultを返す。Plan Resultのcanonical pathは`cycles/{cycle}/01-plan-result.json`であり、`01-plan.md`と`current-plan.md`は確定後に生成されるviewである。

Implementへ渡せるPlanには最低限次が必要である。

```txt
objective
requirements[]
acceptance_criteria[]
implementation_steps[]
validation_plan[]
knowledge_references[]
```

Planが並列化を選ぶ場合は、Parallelization Planと全Child Loop Contract initial revisionも同じDraft Envelopeに含める。OrchestratorはDAG、resource scope、approval boundaryを照合し、`taskctl`がcross-artifact validationに成功した場合だけ`loops/{loop_id}/contracts/rev-{revision:04d}.json`へimmutableに確定する。`contract.json`はactive revisionから生成するcurrent viewであり、hash付きcanonical参照に使わない。

Planを拒否する条件:

- Acceptance CriteriaまたはValidation Planが空
- CriterionとValidationの対応がない
- 根拠のない外部仕様を確定要件として扱う
- `handoff_status = needs_human_input`なのに`human_question_refs[]`が空
- parallel modeなのにDAG、Child Contract、resource scope、integration strategyが不足

## Implement → Review

Implement Agentは確定Plan Resultを入力とし、Draft Implement Resultを返す。canonical pathは`cycles/{cycle}/02-implement-result.json`である。

Reviewへ渡す情報:

```txt
plan_ref
implemented[]
changed_artifacts[]
not_changed[]
deviations_from_plan[]
validation_results[]
validation_not_done[]
known_issues[]
review_focus[]
target_artifact_durability[]
knowledge_references[]
```

Planからの差異、未実施validation、既知の問題を空値で隠さない。該当しない場合はtyped empty arrayを使い、未評価の場合はhandoffを完了扱いにしない。

`02-implement.md`はImplement Resultのrendered viewであり、Agentの自由記述fileではない。

## Review → next state

Review Agentは同じDraft EnvelopeにReview ResultとReview Decisionを返す。

```txt
Review Result
  checked[]
  accepted_criteria_ids[]
  issues[]
  root_causes_or_hypotheses[]
  validation_evidence[]
  review_decision_link

Review Decision
  review_status
  summary
  next_plan_input[]
  human_question_refs[]
  recommended_follow_up[]
  confidence
```

canonical path:

```txt
cycles/{cycle}/03-review-result.json
cycles/{cycle}/03-review-decision.json
```

両artifactは同じ`transaction_id`のrecoverable multi-file transactionで確定する。`03-review.md`と`current-review.md`は両JSONから生成するviewである。

条件付きhandoff:

| Review status | 必須条件 | 次状態 |
| --- | --- | --- |
| `completed` | 全Criterion accepted、blocking issueなし、必須validation完了 | Childはintegration-ready。ChildなしRootはstandard Review、ChildありRootはIntegration Reviewの場合だけcompleted候補 |
| `needs_plan` | issueと`next_plan_input[]`が各1件以上 | Cycleを増やしてPlanへ遷移 |
| `needs_human_input` | `human_question_refs[]`が1件以上 | 同じCycleのReviewで`waiting_human`へ遷移 |

Review Agentは単なる「NG」を返さず、evidenceと次の入力をtyped fieldへ分ける。Human判断が必要なissueを`needs_plan`として自律修正へ流さない。

Review AgentはStateを直接更新しない。OrchestratorがReview DecisionとRoot topologyを照合してtaskctlへ遷移を要求する。materialized Child Loopが0件ならstandard Root Review、1件以上ならIntegration ReviewだけがRoot completionのauthorityとなる。Child Loopのstandard Review `completed`はRoot completionではない。

## Stable ContextとKnowledge

Stable Contextへ昇格する情報:

- 確定した要求とHuman回答
- 現在有効な設計判断
- activeなKnowledge Claim
- 現在の未解決QuestionとRisk

昇格しない情報:

- 古いPlanの詳細
- 失敗した仮説
- Phase workerの一時メモ
- 未検証のResearch Note
- 実装差分の全文

Phase ArtifactはKnowledge本文を複製せず、`knowledge_references[]`でClaim ID、Source refs、status、as-of、hash、利用目的を参照する。Claim / Sourceの意味と昇格条件は [Knowledge Design](./knowledge.md) に従う。

## Phase別の入力優先順位

generated MarkdownはHuman確認用であり、Agentへの正規入力はcanonical JSON referenceを優先する。

### Plan Agent

1. Task StateとState Packet
2. Task objective / Acceptance Criteriaのcanonical source
3. Stable Contextとactive Knowledge References
4. `state.json.latest_review_decision_ref`が指すReview Decision
5. `needs_plan`の場合はDecisionの`next_plan_input[]`
6. 必要な場合だけDecisionが参照するReview Result

古いPlan Resultを暗黙に入力へ含めない。

### Implement Agent

1. Task StateとState Packet
2. Task objective / Acceptance Criteriaのcanonical source
3. Stable Contextとactive Knowledge References
4. `state.json.active_plan_ref`が指すPlan Result
5. Planが参照するParallelization PlanまたはChild Contract

### Review Agent

1. Task StateとState Packet
2. Task objective / Acceptance Criteriaのcanonical source
3. Stable Contextとactive Knowledge References
4. active Plan Result
5. 同じCycleのImplement Result
6. target artifactの実差分とvalidation evidence

## Context Size Control

- AgentへTask directory全体を渡さない。
- State Packetはartifact ID、relative ref、hashを持ち、必要なartifactだけを解決する。
- Research Notesは対応Claimの検証が必要な場合だけ読む。
- generated viewをtyped inputの代替にしない。
- Child Agentへ他ChildのDraftや未確定結果を渡さない。

## 不変条件

- Phase AgentはDraft Envelopeを返し、canonical Task Filesを直接更新しない。
- OrchestratorだけがDraftをTask contextとpolicyへ照合する。
- `taskctl`だけがcanonical JSONとそのrendered viewを確定する。
- PlanはAcceptance CriteriaとValidation PlanなしにImplementへ進めない。
- ImplementはPlanとの差異とvalidation状態をReviewへ明示する。
- Review ResultとReview Decisionを分離し、同時に確定する。
- `current-plan.md`と`current-review.md`はPhase Agentの出力ではない。
- 次PhaseはMarkdownではなくState active refが指すtyped artifactを読む。
