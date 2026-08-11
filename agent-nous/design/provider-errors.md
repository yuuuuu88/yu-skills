# Provider Error and Retry Design

## Provider boundary

Plan / Implement / Reviewのbackendは、typed `AgentOutput` envelopeだけを返す。providerはTask State、phase artifact、managed actionを直接変更できない。JSON command providerのstdoutは1つのJSON objectに限定し、未知のtop-level fieldも拒否する。

## Error categories

`ProviderDiagnostic`は次のcategoryを正本とする。

```text
invalid_json
invalid_envelope
invalid_schema
missing_required_field
invalid_managed_action
output_too_large
timeout
provider_process_failure
write_proposal_conflict
unsafe_path
validation_environment_failure
```

各診断は`phase`、`cycle`、`provider`、`action_id`、JSON error path、expected、actual type、safe stderr summary、retryable、redaction済みdetails、diagnostic IDを持つ。Taskごとの`diagnostics.jsonl`はappend-onlyで、Task Stateのrevisionを消費しない。

- stderrはhash、短いpreview、行数、truncated flagだけを保存する。
- secret token、absolute/home path、raw provider outputは保存しない。
- `taskctl diagnose`は直近の分類と件数だけを表示し、詳細なraw本文を復元しない。

## Retry policy

- retry可能なprovider failureは同じphaseを**一度だけ**再試行する。
- timeout、temporary process failure、invalid JSONはadapterがretryableと明示した場合だけ対象になる。schema不正、managed action不正、secret検出、unsafe pathは再試行しない。
- retryは同じTask revision、同じphase、同じactive artifact boundaryで行う。新しいmanaged actionを自動複製しない。
- retry contextには直前診断のredacted public form、対象scope、attempt数だけを渡す。stderr本文やsecret値は渡さない。
- 二度目も失敗した場合、そのcycle内でさらにproviderを呼ばず、Implement/Reviewはre-planまたはHuman確認へ戻す。Plan providerの失敗は安全にTaskを停止する。

## Review classification

Review出力には必要に応じて`review_classification`を付ける。

| 分類 | 用途 |
| --- | --- |
| `code_gap` | 実装の受入条件・振る舞いが未達 |
| `test_gap` | 検証が実行され、失敗した |
| `validation_environment_gap` | 検証環境・sandbox・依存・timeoutの問題 |
| `provider_gap` | providerの一時停止・出力失敗 |
| `schema_gap` | envelope、finding、managed actionの契約不正 |
| `documentation_gap` | 必須文書・利用説明が未達 |
| `safety_gap` | scope、path、side effectの安全境界違反 |
| `completed` | acceptance criteriaを証拠付きで満たした |
| `human_required` | 自動推測せず人間判断へ戻す |

環境エラーを自動的に`code_gap`へ変換してはいけない。受入条件ごとのReview evidenceは`passed`、`failed`、`unverified`を明示し、`completed`には全criterionのpass evidenceが必要である。

## Finding schema

findingは`finding_id`、`severity`、`disposition`、`summary`を必須とし、severityは`blocker|major|minor|advisory`、dispositionは`blocking|advisory|question`に限定する。任意の`category`も上記Review classificationのうち完了・人間要求以外だけを許可する。型不正は黙って捨てず、`finding_schema_errors`にJSON pathと具体的メッセージを残す。

## Cross Review

Cross Reviewは最大2 reviewerまでで、各workerは同じ入力snapshotと固有perspectiveだけを見る。

- `parallel`は従来の独立並列review。片方のprovider failureはfallback policyで扱う。
- `after_primary_success`はprimaryが成功したときだけsecondaryを起動する。primaryが環境エラー・timeout・schema failureで失敗した場合、secondaryを無駄に起動せず、failure recordと次の判断を返す。
- `fallback=single`でも、成功した一方のevidenceだけで完了できるかは通常のvalidation境界で再確認する。
- `any_needs_plan_blocks_completion`はprovider/schema/環境問題までコード不備として扱う規則ではない。分類を保存し、環境問題は再設定またはHuman確認へ、コード/test問題は再Planへ振り分ける。
- 実行済みvalidationが`validation_environment_failure`または`safety_gap`になった場合は、Review artifactを保存したうえで`needs_human_input`へ止める。Humanは環境修正後のReview再開、再Plan、停止を選べる。同じ環境エラーをサイクル数だけ繰り返すことはしない。

## Report and stop conditions

最終reportにはtask開始・終了時刻、cycle timing、phase/provider/validation所要時間、managed action数、changed artifacts、retry数、failure category counts、`needs_plan`数、completion rate、current blocker、termination summaryを含める。

同じfailure signatureが続き、managed actionもchanged artifactもない場合、デフォルト2回（設定範囲2〜3回）で自動replanを停止し、選択肢付きHuman Questionを保存する。`max_cycles`到達時も、未完了理由・最終error・残作業をreportして停止する。
