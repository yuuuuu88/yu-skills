# Autonomous Loop Design

## 目的

nous が `plan → implement → review` のループを、できるだけ人間の手を挟まずに自律実行できるようにする。

ただし、自律性を上げることと、人間に聞くべきことを聞かないことは別問題として扱う。nousは「勝手に進める」のではなく、**安全に自動化できる範囲を明確にし、その範囲内では止まらない**ことを目指す。

## 基本方針

- ループはReviewの判定で閉じる。
- Reviewは必ず次の状態を1つだけ返す。
- `needs_plan` の場合、Human確認なしでPlanへ戻る。
- `needs_human_input` の場合、必ずHumanに質問して止まる。
- `completed` の場合、タスクを完了する。
- Planへ戻るときは、過去ログ全体ではなく、Reviewが作った「次Planへの入力」を中心に使う。
- Review判定はcanonical `03-review-decision.json`だけから読み、Markdownから抽出しない。
- Phase AgentのDraftはOrchestratorが`taskctl`へ渡し、同じ`transaction_id`を持つrecoverable multi-file transactionでtyped artifact、State、Eventを確定する。

## 状態遷移

```txt
lifecycle_status: active
execution_status: idle | running
phase: plan
  ↓ ready_for_next
phase: implement
  ↓ ready_for_next
phase: review
  ├── completed          -> Root completion authorityの条件を評価
  ├── needs_plan         -> phase: plan / cycle + 1
  └── needs_human_input  -> lifecycle_status: waiting_human
```

PlanまたはImplementが`handoff_status: blocked`を返す場合は、`blocking_reasons[]`と解除条件を必須にして`lifecycle_status: blocked`へ遷移し、phaseとcycleを維持する。Human回答後はQuestionに保存された`resume_target`から機械的に再開し、回答時に再開Phaseを推測し直さない。PauseやinterruptはLifecycleではなくExecution Statusで表す。

## Root completion authority

Review Agentは完了候補をtyped Review Decisionとして返すだけで、Stateを直接`completed`へ変更しない。Orchestratorがcanonical decisionと現在のRoot topologyを照合し、taskctlの状態遷移operationが完了を確定する。

```txt
Rootにmaterialized Child Loopが0件:
  phase_kind: standard のRoot Reviewがcompleted
  + 全Root Acceptance Criterion accepted
  + blocking issue / pending required validation / pending approvalが0件
  -> Root completed

Rootにmaterialized Child Loopが1件以上:
  全必須Childがintegration-readyまたは明示的除外
  + phase_kind: integration のIntegration Reviewがcompleted
  + 全Root Acceptance Criterion accepted
  + 未統合成果物 / orphan worker / blocking issueが0件
  -> Root completed
```

Child Reviewの`completed`は当該Childをintegration-readyにするだけで、Rootを完了させない。Parallelization Planが`sequential`へfallbackしても、Child Loopをmaterializeした後はIntegration Reviewを省略しない。

## Block / Policy / Replan mapping

Policy GatewayまたはPhase handoffの停止結果を独自のcycle遷移へ変換しない。正規化規則は次のとおり。

| 入力 | 保存とEvent | Lifecycle / Phase |
| --- | --- | --- |
| Plan / Implement `handoff_status: blocked` | `blocking_reasons[]`、checkpoint、`task_blocked` | 影響Loopを`blocked`、phase / cycle維持 |
| Gateway `deny` | `policy_decided`、`action_denied`。`action_started`は作らない | hard denyなら`blocked`、代替手順があるならReviewへ送り`needs_plan`を確定 |
| Gateway `interrupt_for_approval` | immutable Approval Request、`approval_requested`、Questionの`approval_request_ref` | 影響Loopだけ`waiting_human`、phase / cycle維持 |
| Gateway `replan_required` | evidenceをReview入力へ追加 | Review Decision `needs_plan`だけがcycleを増やす |
| runtime policy violation | `policy_violation`、必要なら`execution_grant_revoked`、`action_failed`、checkpoint | 影響scopeを停止し、安全に継続不能なら`blocked`または`waiting_human` |

`deny`や`replan_required`からOrchestratorが直接cycleを増やしてはならない。承認回答後はApproval RequestとQuestionの`resume_target`を照合し、同じAction Descriptorがなお有効な場合だけ再評価する。詳細なcurrent-stateとEvent契約は [Task State and Recovery Design](./task-state.md) を正とする。

## Reviewの終了判定

Review AgentはDraft Review ResultとDraft Review Decisionを返す。Orchestratorが照合し、`taskctl`が同じCycleの`03-review-result.json`と`03-review-decision.json`として確定する。Review Decisionは必ず以下のいずれかを返す。

```txt
review_status: completed | needs_plan | needs_human_input
```

### Bounded Cross Review

Reviewだけは、必要な場合に複数の独立Reviewerへ分配できる。各Reviewerは同じPlan / Implement成果物と、自分の短い観点だけを受け取り、他Reviewerの結果は見ない。Reviewer同士をさらにReviewする段階や、合意形成会議は作らない。

設定は`engine.review.mode: cross`と2件の`reviewers`で有効にする。Reviewerは最大2件で、`review_max_parallel`を上限に並列化する。既存設定で`review`セクションを省略した場合は、従来どおり単一Reviewerを使う。

集約規則はEngineが固定で適用する。

```txt
明示的な Human fallback       -> needs_human_input
それ以外で needs_plan がある   -> needs_plan
needs_human_input がある       -> needs_human_input
全Reviewerが completed         -> completed
```

通常の`needs_human_input`は、仕様判断なしで解消できる`needs_plan`より優先しない。これによりReviewerが「念のため確認」を返しただけでループが官僚的に停止することを防ぐ。Reviewerが`human_question`を返した事実はartifactへ残すが、Engineはまず再Planを実行する。破壊的操作・本番反映・セキュリティ/法務判断など、止めるべき運用では`fallback: "human"`を明示する。

同じvalidation proposalは1回だけ実行し、Reviewer間で同じ`action_id`の内容が異なる場合は安全側に`needs_plan`とする。Reviewで許可されるManaged Actionはallowlist済みの`process.run`検証だけであり、filesystem writeやGit変更は拒否する。Provider失敗も`needs_plan`として通常のcycle上限へ送るため、ReviewのReviewや無制限の承認手続きを追加しない。

Providerの一時失敗時は、`fallback: "single"`なら成功したReviewerを1回だけ単独採用し、artifactへ`degraded_single`と理由を残す。`fallback: "none"`では再Plan、`fallback: "human"`ではHuman gateを選ぶ。Security・production・外部公開の運用では`human`を推奨する。

Reviewerの期限は親Engine側でも監視する。期限を過ぎたread-only Reviewerは`TimeoutError`として失敗扱いにし、親のleaseを待たせない。外部JsonCommand Reviewerはsandbox subprocessのtimeout、in-process providerはdaemon read-only workerで囲い、期限後の結果をTaskへ取り込まない。

### completed

以下を満たす場合。

- 受け入れ条件を満たしている
- 重大な不具合がない
- 必要な検証が完了している
- 次にPlanへ戻すべき未解決事項がない

### needs_plan

未完了だが、Humanに聞かずに次サイクルへ進める場合。

例:

- 実装漏れが明確
- lint/type/test failureの原因と修正方針が明確
- Planの分解が粗く、再計画すれば進められる
- 追加調査が必要だが、公開情報やリポジトリ内情報で解決できる
- Review観点で改善点があるが、仕様判断を伴わない

### needs_human_input

Humanに聞くまで進めるべきでない場合。

例:

- 要件や優先順位が曖昧
- 仕様選択がプロダクト方針に関わる
- 破壊的操作、課金、本番反映、外部公開が必要
- セキュリティや法務・契約上の判断が必要
- Reviewで複数の妥当な方針があり、選択基準がない
- 受け入れ条件そのものを変える必要がある

## 自律ループの成立条件

nousが自律的にPlanへ戻るには、Review Agentが次のPlanに必要な情報を構造化して残す必要がある。

Review結果には最低限、以下を含める。

```txt
review_status: completed | needs_plan | needs_human_input
cycle: number
summary:
  - 今回何を確認したか
accepted:
  - 満たした受け入れ条件
issues:
  - 未解決の問題
next_plan_input:
  - 次のPlanで考えるべきこと
  - 修正対象
  - 追加調査が必要な領域
human_question_refs:
  - canonical HumanQuestionへの参照。なければ空
confidence:
  - high | medium | low
```

`needs_plan` の場合、Plan Agentは `next_plan_input` を主入力として次のPlanを作る。

条件付きvalidation:

- `completed`: 全Acceptance Criterionがaccepted、blocking issueなし、`next_plan_input[]`と`human_question_refs[]`は空
- `needs_plan`: `issues[]`と`next_plan_input[]`が各1件以上、`human_question_refs[]`は空
- `needs_human_input`: `human_question_refs[]`が1件以上
- Review ResultのevidenceとReview Decisionが矛盾する場合は両artifactを拒否し、Stateを変更しない
- Markdownだけが存在する場合はReview完了として扱わない

field名と詳細条件は [Phase Artifact Contract Design](./phase-artifacts.md) を正とする。

## 自律でPlanに戻るときのルール

Plan Agentは、前サイクルの全ログを無条件に読むのではなく、以下の優先順位で読む。

1. `task-definition.json`
2. `stable-context.json`
3. active `knowledge/claims.json` / `knowledge/sources.json`の必要Claim reference
4. `state.json.latest_review_decision_ref`が指す`03-review-decision.json`の`next_plan_input[]`
5. 必要な場合のみ、Decisionが参照する`03-review-result.json`
6. 原則として古い `01-plan.md` は読まない

理由:

- 古いPlanの誤った仮説に引きずられないようにする
- Reviewで確定した事実と未解決事項を中心に再計画する
- Knowledgeとして使う情報は、明示的にtyped Knowledgeへ昇格し、`knowledge/claims.json`から参照できるものだけにする

## Humanに質問する前の自律確認

Humanに質問する前に、nousは以下を確認する。

1. `task-definition.json`のObjective / Acceptance Criteria / Constraintsに答えがないか
2. `stable-context.json`に過去のHuman回答・決定がないか
3. `knowledge/claims.json` / `knowledge/sources.json` に有効なClaimと根拠がないか
4. リポジトリ内の設計書・README・テストから推論できないか
5. インターネットや公式ドキュメント調査で解決できないか

それでも不明、または推論で進めるリスクが高い場合だけHumanに聞く。

## Humanへの質問形式

Humanに判断を求める場合は、長い設計書を読ませて自由記述を求めるのではなく、原則として会話上で選択肢を提示する。

- 独立して回答できる質問は、認知負荷を抑えた短い形式でまとめる
- 前の回答によって選択肢が変わる質問は、順序を分ける
- 固定の最大質問数を設けず、1問ごとの背景と選択肢を短くする
- 選択肢は相互に重ならない2〜3個にする
- 推奨案を先頭に置き、`推奨`と明記する
- 各選択肢には影響またはトレードオフを1文で添える
- 選択肢に当てはまらない場合の自由入力を許可する
- 背景説明は判断に必要な最小限にし、詳細設計書は任意の参照先として示す
- Humanの回答はTask Stateと安定コンテキストへ記録する

例:

```txt
質問: 過去のPlanをどのように保存しますか？

A. cycleごとに保存（推奨）
   現在有効なPlanを明示しつつ、経緯も追跡できます。

B. 常に上書き
   構成は簡単ですが、判断の経緯を失います。

C. 自由入力
```

Humanへ戻すrisk条件、事前承認、質問のblocking scope、回答保存の詳細は [Human Input Policy](./human-input-policy.md) で定義する。

## 無限ループ防止

自律ループには停止条件が必要。

停止候補:

- 同じ種類のReview指摘が連続する
- `needs_plan` が一定回数続く
- テスト失敗の原因が特定できない
- 追加調査しても根拠が増えない
- Planが毎回大きく変わり、タスク境界が不安定
- 受け入れ条件が満たせない可能性が出た

この場合、Reviewは `needs_human_input` に切り替える。

実装上は、Review outputの分類・failure category・blocking issueを正規化したfailure signatureをcycle間で比較する。`managed_action_refs`も`changed_artifacts`もなく、同じsignatureが続く場合は進展なしと判定する。デフォルト2回、設定可能範囲2〜3回で、追加の自動Planを行わず、`replan`または`stop`を持つHuman Questionを保存して`waiting_human`へ遷移する。新しいmanaged effectまたは実際のartifact変更が確認された場合はstreakをリセットする。

Providerの一時失敗は同じphaseを1回だけretryする。retryは同じphase boundaryで行い、redacted diagnosisと必要なscopeだけを渡す。二度目の失敗、schema不正、unsafe path、同じmanaged actionのconflictは同じcycleで無限に再試行しない。

Reviewの`review_classification`は、コード問題と検証環境・provider問題を区別する。環境エラーは`validation_environment_gap`、provider出力不正は`provider_gap`または`schema_gap`として保存する。検証環境エラー・安全境界違反はコード不備と断定せず、Review artifactに原因とevidenceを残したうえで`needs_human_input`へ止める。人間が環境を直して同じReview境界を再開するか、再Plan・停止を選ぶ。finding schema不正は具体的なJSON pathを`finding_schema_errors`へ残す。

## 推奨する初期ポリシー

最初は以下の安全側ポリシーにする。

- 自律ループ最大回数: 3回
- 同一カテゴリの失敗が2回続いたらHuman確認
- 破壊的操作、課金、本番反映、外部公開は、具体的なPre-approvalがなければHuman確認
- Planの受け入れ条件を変更する場合はHuman確認
- 実装方針の局所修正は自律で進めてよい

この回数制限より前でもRed条件を検出した場合は即座にHumanへ戻す。risk分類は [Human Input Policy](./human-input-policy.md) に従う。

## 実装済みの実行エンジン

`src/agent_nous/engine.py` の `AutonomousEngine` が、これまで外部callerに任せていたphase実行を引き受ける。

- 現在のcanonical Stateから対象phaseの `AgentContext` を再構成する。
- `AgentBackend.launch()` でPlan / Implement / Review Agentをphaseごとに起動する。
- Implementの `action_proposals` とReviewの `validation_proposals` は、親Engineが `ManagedExecutor` で実行する。Agent出力だけでは変更・検証完了にならない。
- 実行済みActionのpostflight evidenceをphase artifactへ結び、`taskctl`へlease付きで記録する。
- Reviewが `needs_plan` を返す、または実行済みvalidationがコード/test問題として失敗すると、canonical Stateの次cycleから再Planする。cwd・依存・timeoutなどのvalidation環境失敗やscope escapeは、無条件に再Planせず`needs_human_input`で環境修正・再開または停止を求める。
- cycle上限に達した場合は推測で続行せず、Human Questionを保存して `waiting_human` で停止する。
- lease期限切れはfresh engine起動時にreconcileしてから再開する。
- Task reportにはtask/cycle/phase/provider/validationの時間、retry、managed action、changed artifact、failure分類、`needs_plan`、completion rate、current blocker、termination summaryを含める。運用診断は`taskctl diagnose`の`runtime_diagnostics`とpostflight evidenceを正本にする。

### Typed parallel Planの実行

Plan Agentが`parallelization_plan`と`child_contracts`を同時に返す場合、Engineは通常のPlan artifactを先にcanonicalへ確定してから、Child Loop Contract、DAG、reservation参照を`TaskStore.materialize_parallel_plan`の1 transactionでatomicにmaterializeする。その後は`ParallelExecutionEngine`が`max_concurrency`（1〜3）を上限とする`ThreadPoolExecutor`で各ChildのPlan / Implement / Review Agentを物理起動する。

- Childごとに独立した`loop_id`、lease、phase artifact、resultを持つ。
- Agent backend呼び出しは同時に進むが、TaskStoreの親revision、managed action、child checkpointのcanonical commitは短いcritical sectionで直列化する。これにより並列workerが親Stateを直接競合更新しない。
- write scopeのないChildの`process.run`検証は、ManagedExecutorの使い捨てsnapshot上だけで実行できる。親repositoryへの永続writeは許可しない。
- write scopeを持つChildは、immutable contractに対応するcanonical worktree、semantic reservation、managed commitが揃わない限り開始しない。推測で親repositoryへwriteせず、安全に停止してreplan/recoveryへ戻す。
- 全Childが`integration_ready`になった後、Rootの`phase_kind: integration`で別のReview Agentを起動し、Child result artifactを`active_artifacts`へ渡す。Integration ReviewだけがRoot completionを確定する。
- Child workerはphase実行中にheartbeatでleaseをrenewし、古いlease tokenでのmanaged action/phase commitをfenceする。Implement failureはReviewへ進めず、Child単位でPlanへ戻してbounded retryする。
- fresh processの起動時は`active_parallelization_plan_ref`とChild stateを検証して同じParallel schedulerへ復帰する。Integration Reviewの`needs_plan` / `needs_human_input` / provider failureもcanonical artifactとして記録し、次回の再PlanまたはHuman回答へ接続する。
- Child failure、期限切れlease、未完了Actionは自動で効果を再実行せず、Childをready/recovery境界へ戻す。write ChildはPlanから自動worktreeを作らず、事前にcanonical worktree・reservation・managed commitが束縛された場合だけ実行する。既存の`taskctl diagnose`とreconciliationで再開する。

Typed parallel Planを返さない通常Planは、従来どおり単一RootのPlan → Implement → Reviewとして動く。したがって、既存Agent commandとの後方互換を保ちながら、Plan Agentが必要と判断したタスクだけ物理並列へ切り替わる。

Agent providerは交換可能にする。`CallableAgentBackend` は決定的なintegration testとホスト統合用、`JsonCommandAgentBackend` は外部Agent command用である。後者はCodex sandbox内でtarget repositoryをread-only、専用scratchだけをwrite可能にし、AgentはJSON proposalだけを返す。lease tokenやgrant handleはAgent contextへ渡さない。

CLIからは、phaseごとのJSON Agent commandを指定して実行できる。

```sh
agent-nous/bin/nous --nous-home "$NOUS_HOME" --input engine-request.json \
  taskctl engine-run <task-id>
```

`engine-request.json` は `contract_version`、`request_id`、typed `actor`、`agent_commands.plan` / `implement` / `review`、必要なvalidation用 `allowed_processes` を持つ。Agent commandは `NOUS_AGENT_CONTEXT_FILE` のJSONを読み、`summary`、`outputs`、Review時は`decision`をJSONで返す。target repositoryへのwriteはproposalを通じてのみ行う。

Human向けには、同じ設定をプロジェクトの`.nous/config.json`へ一度だけ保存できる。`engine.agents`は`plan` / `implement` / `review`を必須とし、commandはargv配列またはshellを実行しない文字列形式で指定する。`engine.validation.allowed_processes`はReview検証に必要な完全なargv command prefixだけを必須で列挙し、空prefix、network、credential、paid、remote Gitを許可しない。旧形式の`engine.allowed_processes`も互換のため受け付ける。`max_cycles`、`max_steps`、lease期限、Agent timeoutもこのファイルで管理する。設定探索は`--engine-config` → project `.nous/config.json` → `nous.config.json` → user configで統一し、`taskctl engine-run <task-id>`はrequest IDとactorを自動生成してtyped requestへ展開する。明示的なrequest JSONは後方互換として残す。

## 結論

nousの自律性は、Agentの賢さだけではなく、Reviewが次Plan用の入力を構造化して残すことで成立する。

重要なのは以下。

- Reviewが必ず次状態を返す
- Review ResultとReview Decisionはtyped JSONとして同じrecoverable multi-file transactionで確定する
- `needs_plan` と `needs_human_input` を明確に分ける
- Planへ戻るときの入力を限定する
- 古いPlanをKnowledgeとして扱わない
- ループ回数と失敗パターンで停止条件を持つ
# Nous Loop

Nousにおける反復実行を **Nous Loop** と呼ぶ。

```text
Plan -> Implement -> Review -> Plan -> Implement -> Review -> ...
```

1回の `Plan -> Implement -> Review` が1サイクルである。Reviewが受入条件を証拠付きで通過したときだけ完了する。Reviewが指摘を返した場合は、指摘を次のPlanの入力にして次サイクルへ進む。上限、期限、権限、外部サブエージェントの失敗に達した場合は、安全に停止して診断情報を返す。

## 実行原則

- 通常の `engine-run` はPlan/Implement/Reviewを外部サブエージェントで実行する。インプロセスcallbackはテスト・明示的な組み込み用途だけである。
- 各フェーズの呼び出し、所要時間、provider、外部サブエージェント利用有無を実行トレースに残す。
- 速く終了したこと自体を成功の根拠にしない。Planの受入条件、Implementのmanaged action、Reviewのvalidation evidenceを診断する。
- `steps=3` は3フェーズを通過したという意味に過ぎず、実質的な作業を証明しない。`diagnostics.root_causes` と各artifactの `phase_quality` を併せて確認する。

## 設定

`.nous/config.json` の次の設定が配布時の既定値である。

```json
{
  "subagents": { "required": true, "mode": "external" },
  "min_cycles": 1,
  "max_cycles": 10,
  "lease_seconds": 600,
  "agent_timeout_seconds": 300
}
```

`required=false` は、外部agentを用意できないテストや組み込み検証に限定する。通常運用でfalseにすると、Nousのサブエージェント前提に反する。

早期にReviewが通っても最低回数だけNous Loopを回したい場合は、`min_cycles`を設定する。たとえば`"min_cycles": 3`なら、cycle 1・2のReviewが完了条件を満たしてもEngineが`needs_plan`へ変換し、cycle 3のReviewまで実行する。既定値は1で、`min_cycles`は`max_cycles`を超えられない。`min_cycles > 1`では、3フェーズ/サイクルを実行できるよう`max_steps`も最低`min_cycles * 3`が必要で、省略時はその値を満たす既定値を補う。サイクルを増やすほどAgent呼び出し・検証時間・AI利用量も増えるため、必要な確認強度に合わせて設定する。これはRootのPlan→Implement→Reviewサイクルを数え、並列Plan時もRoot Integration Reviewの再Plan境界で同じ下限を適用する。


## Trace correlation and observability

Every Root Task receives a deterministic 128-bit hexadecimal `trace_id` derived from its Task ID. Each durable loop boundary has a stable parent span, while each phase invocation and external Agent run receives a fresh 64-bit hexadecimal span ID. The parent Engine passes `trace_context` in the external Agent context and mirrors the identifiers in `NOUS_TRACE_ID`, `NOUS_SPAN_ID`, and `NOUS_PARENT_SPAN_ID`.

Trace metadata contains identifiers, timestamps, provider, and hashed stdout/stderr evidence only; prompts, document contents, credentials, and secret-like output are not included. The persisted execution trace preserves the same correlation fields after a fresh process reconstructs diagnostics. This is compatible with W3C/OpenTelemetry correlation without requiring an exporter or vendor lock-in.
