---
id: DES-001
type: design
title: Initial issue-to-draft-change slice
status: accepted
source_of_truth: true
created: 2026-08-09
updated: 2026-08-09
related: [REQ-001, ARCH-001, CT-001, CT-002, CT-003, ADR-0002]
managed_by: repository-bootstrap
---
# Initial issue-to-draft-change slice

## Scope

ローカルGit repositoryに対する自然言語要求またはIssue形式入力から、Research、Plan、Implement、Test、独立Review、修正判断までを一つのdurable Taskとして実行する。初期sliceの外部副作用はローカルartifactとローカル変更に限定し、remote push、PR公開、merge、deployは含めない。

## Related requirements

- REQ-F-001 through REQ-F-010
- REQ-Q-001 through REQ-Q-007

## Assumptions

- 実行対象はGit repositoryである
- `agent-nous` runtimeがTask、lease、checkpoint、policy、phase artifactを提供する
- fake providerで契約テストを実行できる
- 実装者とReviewersは別のAgent invocationとして記録される

## User or system flow

1. Humanまたはlocal intakeが要求、repository path、base revision、risk、希望validationを入力する
2. PilotがRoot TaskとTask Definition Packetを作る
3. Research waveがrepository map、既存設計、tests、関連public sourceを調べる
4. Plan Agentがactive KnowledgeからPlan、Child Contract、Acceptance Matrix、Validation Planを作る
5. Coordinatorがrequired roles、resource、policy、worktreeを検証する
6. Implement Agentが隔離workspaceでコード・テスト・docsを変更する
7. Test Agentが対象コマンドを実行してevidenceを返す
8. Review Agent A/Bが他Reviewerの結果を見ずに評価する
9. Judgeがfindingとevidenceを統合し、`completed`、`needs_plan`、`needs_human_input`を返す
10. `needs_plan`の場合、旧cycleを保持したまま次のNous Loopを開始する

### Target change lifecycle

Implement Agentの変更はまずruntime外のstaging workspaceへ保存し、target
repositoryへ自動反映しない。`TargetRepositoryAdapter.preview()`はGitの
base revisionとdirty stateを観測するだけである。反映する場合は、Humanが
`human_approved=True`を明示し、相対path・symlink・`.git`除外、base revision
一致、clean treeを満たしたローカル apply を実行する。`commit_candidate()`
はtarget外へpatch artifactを生成するだけで、commit、push、PR、deployは
実行しない。

## Inputs and outputs

### Input

- request text or normalized issue
- repository locator and base revision
- declared goals, non-goals, constraints
- risk and approval policy
- allowed Agent provider identities
- validation command allowlist
- loop limits and resource budgets

### Output

- durable Root Task
- Research Knowledge and provenance
- versioned Plan and Child Loop contracts
- isolated workspace reference and change summary
- test and verification evidence
- independent Review findings
- final Judge decision and resume target
- local patch or commit candidate when policy permits

## Interfaces and data

Primary contracts:

- [Control-plane contract](../contracts/control-plane.md)
- [Subagent execution contract](../contracts/subagent-execution.md)
- [Agent provider contract](../contracts/agent-provider.md)
- [Verification contract](../contracts/verification.md)

Required artifact types:

- `task_definition`
- `knowledge_packet`
- `plan`
- `parallelization_plan`
- `child_loop_contract`
- `phase_handoff`
- `verification_evidence`
- `review_decision`
- `integration_result`

## Validation and error handling

- input schema、repository identity、base revision、scope、provider capabilityを開始前に検証する
- required Agentの起動失敗はblocking errorとして保存する
- schema不一致、scope外変更、証拠不足、worktree競合は当該phaseを成功扱いにしない
- Reviewの不一致はJudgeに渡し、解消できない場合は`needs_plan`または`needs_human_input`とする
- process interruption後はcheckpoint、lease、実ファイル状態をreconcileして再開する
- hidden evaluation、secret、private network、未承認外部操作は入力・ログ・artifactから除外する

## Authentication and authorization

初期sliceはlocal user identityを前提にし、実行ごとにrepository、branch、workspace、provider、validation commandをpolicyへ束縛する。将来のteam/remote運用ではHuman identity、organization policy、secret broker、CI identityをAdapterとして追加する。

## Security and privacy

- Agentには必要なscopeだけを渡し、他Childのraw transcriptを渡さない
- public Web本文はuntrusted dataとしてprompt injection対策を適用する
- secret valueはAgent prompt、canonical state、trace、Review artifactへ書かない
- filesystem、process、network、Git、credential actionはmanaged adapter経由にする
- remote変更は初期sliceの外部副作用境界に置く

## Observability

phase順序、Agent role、provider identity、start/end、retry、timeout、tool call count、output digest、validation result、Review decision、subagent utilizationをsecret-free eventとして記録する。

## Test strategy

- contract tests: artifact schema、phase transition、policy grant、fallback blocking
- integration tests: fake providerによるResearch → Plan → Implement → Test → Review
- concurrency tests: independent read/review wave、write reservation、integration
- failure tests: provider launch failure、timeout、invalid output、worktree conflict、process interruption
- evaluation tests: task outcome、research coverage、review defect detection、subagent utilization、cost/time

## Alternatives and trade-offs

- 単一Agent連鎖: latencyとcostは低いが、自己レビューとcontext overloadを許すため採用しない
- すべてのAgentを完全並列:速いが、契約・依存・write conflictを複雑化するためadaptive DAGにする
- remote PRを最初から自動化:価値は高いがcredentialと外部副作用が大きいためlocal sliceの後にする
- Coordinatorの直接編集fallback:詰まりにくいが、subagent-first要件と評価可能性を壊すため禁止する

## Open questions

- standard modeでのAgent数・モデル・費用上限
- browser verificationの実行環境
- CI/PR Adapterの最初のprovider
- integration patchをcommitへ変換する操作境界

## References

- [Product brief](../../product/brief.md)
- [Architecture overview](../architecture/overview.md)
- [Subagent execution contract](../contracts/subagent-execution.md)
