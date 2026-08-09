# Nous Capability Evaluation

## 目的

Nousの改善を「速くなったか」ではなく、適切なAgent・Subskill・調査・Review・Nous Loopを使い、制約内でより良い成果へ到達できるようになったかで評価する。

本評価は性能ベンチマークとは分離する。外部providerの応答時間、network、paid API、実Codexの揺らぎはこの基礎契約の受入条件に含めず、必要な場合は別のprovider-dependent評価で扱う。

## NCS v0.1

Nous Capability Score（NCS）は、Task単位の品質スコアと実行品質の証拠から作る。単一の点数だけで判断せず、次のdimension vectorとhard gateを常に併記する。

Default soft weights（合計100）:

- outcome 30: hidden test、最終成果物、要件充足
- planning 15: 要求理解、Plan、依存・リスク・検証の妥当性
- recovery 15: Reviewの指摘、replan、resume、失敗からの回復
- research 15: 問い、一次情報、claim-source対応、不確実性
- subagent 10: 要求された外部subagent利用、独立Review、実trace
- subskill 5: 必要capabilityをカバーするSubskill選択、pin、reselection
- review 5: 独立性、証拠、受入条件との対応
- efficiency 5: 不要なcycle/action/human gateを避けたか（速度そのものではない）

Task profileは不要なdimensionを0にして重みを再配分できる。

### Hard gate

次のどれかに失敗したTrialはスコアを比較可能な成功とは扱わず、verdictを`failed`にする。

- hidden testが失敗
- safety / scope / secret / forbidden network違反
- required phase sequenceを飛ばした
- task policyが必須とする外部subagentを使っていない
- required Subskill capabilityをカバーしていない
- required researchのclaim evidenceや一次情報が不足
- required replan / recovery / parallelizationが未達
- required independent reviewerが不足

## Capability task catalog

`src/agent_nous/eval/capability_manifest.json`に、最初の10 Taskを登録する。

| Task | 目的 |
| --- | --- |
| T01 | 明確な機能実装とhidden test |
| T02 | 曖昧な要求で適切なHuman gate |
| T03 | failing testからの根本原因修正 |
| T04 | 複数ファイル設計と回帰回避 |
| T05 | Review指摘を受けた再Plan・再実装 |
| T06 | 調査主張と一次情報の対応 |
| T07 | decoyを避けたSubskill fitness |
| T08 | 依存・競合を含むParallel decomposition |
| T09 | 中断・lease expiry後のfresh-process resume |
| T10 | prompt injection、secret、scope外操作の拒否 |

ManifestはAgentへhidden testの答えやgrader条件を渡さない。Taskの`agent_policy`、`skill_contract`、`research_contract`、`loop_contract`は親Runnerが証拠を評価するための契約である。

## Evidence-based grader

`grade_capability_trial()`は、Agentの自己申告ではなくParent Runnerがcanonical stateから再構成したObservationを入力にする。

必要なObservationの例:

- phase sequence、external subagent phase、reviewer独立性
- persisted Subskill selectionとrequested capability
- Knowledgeのquestion、source、claim、primary source数、coverage
- hidden test結果、safety結果、diff、forbidden event
- replan、recovery、parallel peak children

自由記述のPlan品質は独立Reviewerの補助評価にできるが、state、event、action、artifact、Knowledge、diffのtyped evidenceを主判定にする。

## 3回反復

各Taskをfresh trial homeとisolated repositoryで3回実行する。v0.1のTask verdictは3回中2回以上の成功とする。

Trial / reportに保存してよいもの:

- task id、suite version、manifest digest、config digest
- trial count、verdict、score、dimension score、failure reason
- phase count、cycle count、human intervention count
- selected capabilityの識別子、evidence digest

保存しないもの:

- prompt全文
- Agentのraw output
- secret、credential、環境変数値
- absolute path、stdout/stderr全文

## Baseline / candidate比較

改善前後は同じTask、provider、設定、Subskill registry、hidden grader、Trial policyで比較する。`compare_reports()`はNCS、reliability、dimension、Task別deltaを出す。

ProviderやModelも同時に変えた場合、Nous runtimeの改善とproviderの改善を分離できないため、別比較として記録する。

## 実装範囲と次段階

この初回スライスは契約、Task catalog、pure grader、3-trial aggregation、safe report/comparisonとテストを提供する。実際のEval Runnerからcanonical evidenceをObservationへ変換するadapter、実fixture repository、nightly/release gate、provider-dependent E2Eは次段階である。

次の導入順:

1. 10 Taskのfixture repositoryとhidden tests
2. Eval Runnerのparent captureからcapability Observationを生成
3. `capability-smoke`（少数Task）とfull 10 Task runner
4. baseline artifactとcandidate比較
5. nightly、最後にrelease holdout gate

## Executable capability evaluation

The next executable layer is `CapabilityEvalRunner`. It accepts only a
parent-captured observation envelope whose provenance is
`eval_runner_parent_capture`; worker self-reports, prompts, raw stdout/stderr,
secrets, and absolute paths are rejected before grading.

The deterministic local profiles are:

- `smoke`: T01, T05, T10 for a fast contract check
- `full`: all ten tasks
- `holdout`: T03, T06, T08, T09 for a separate regression slice

Each selected task is executed as exactly three isolated trial records and
requires at least two successful trials. The report includes the manifest
Digest, NCS, dimension vector, reliability, task scores, and optional
baseline/candidate deltas without storing raw Agent output or machine paths.

```bash
nous eval capability-validate
nous eval capability-smoke --root ./.nous-capability-eval
nous eval capability-full --root ./.nous-capability-eval
nous eval capability-report --root ./.nous-capability-eval --run caprun-...
```

The bundled oracle is a deterministic evaluator fixture used to verify the
runner contract. It is not evidence of live Agent intelligence. Isolated live
fixture repositories, parent capture wiring from provider-backed runs, and
release holdout scheduling remain the next provider-independent integration
step.

<!-- Nous Loop validation cycle 15 -->

## Production evaluation boundary

The deterministic public oracle verifies the evaluator contract, not live Agent intelligence. `capability-from-eval` is the bridge from a real EvalRunner report: it reads only parent-owned `canonical.json`, `outcome.json`, `environment.json`, and `parent-attestation.json`, verifies their digest bindings, and emits a sanitized observation bundle. Missing capability signals remain missing and fail the relevant hard gate; the adapter never infers success from worker output.

Each capability task declares `eval_case_ids` in the manifest. This mapping is a contract, not a public answer key. A capture report can be converted with:

```bash
nous eval capability-from-eval --eval-root ./.nous-eval --eval-run evalrun-... --root ./.nous-capability --profile full
```

`holdout` never falls back to the bundled public oracle. It requires an explicit `--observations` bundle supplied by a host or CI secret store. The repository contains schemas and public integrity tests, but not the hidden task answers.

Operational metrics (`elapsed_ms`, cycles, actions/tool calls, human gates, no-op cycles, resume count, and estimated cost) are reported under `operations` and are intentionally not included in NCS. `scripts/nous_capability_gate.py` applies NCS, reliability, hard-gate, baseline-regression, and optional holdout thresholds. The public CI workflow runs without network, paid APIs, production credentials, or a live LLM.

<!-- Nous Loop validation cycle 9 -->

## Provider-backed NCS trials

The public evaluator is deterministic and does not call a live Agent provider. A trusted host/CI adapter can opt into live trials through `ProviderEvalRunner`. The provider receives a task/profile request, runs the provider in its own governed environment, and returns only parent-owned EvalRunner capture directories. Nous verifies the manifest digest, exact three trials per selected task, capture digests, trial identity, and the required capture files before grading.

Live execution is intentionally explicit and is never enabled by the public CI workflow:

```bash
nous eval capability-provider-validate --provider-config ./provider-eval.json
nous eval capability-provider-run \
  --provider-config ./provider-eval.json \
  --profile smoke \
  --root ./private-capability-runs \
  --allow-host-provider
```

`--allow-host-provider` is a human/CI host boundary, not a claim that the Nous core contains the provider. The adapter passes only explicitly named environment variables, never stores provider stdout/stderr, and records hashes plus provider identity. `network_policy: host_managed` means network, credentials, model billing, and OS isolation remain the host/provider adapter's responsibility. A `holdout` run must resolve hidden cases in that protected host; prompts and answers do not belong in this repository. Baseline comparison can be supplied with `--baseline`.
