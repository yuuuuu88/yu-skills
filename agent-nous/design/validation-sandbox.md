# Validation Sandbox Design

## 目的

Reviewのvalidationは、target repositoryを直接実行場所にしない。各Actionごとにmanaged executorが一時workspaceへsource snapshotを作り、そこでだけ検証を実行する。workspaceは検証終了後に削除し、検証中のファイルはtarget repositoryへコピーし戻さない。

この境界により、pytest cache、compileallのbytecode、coverage、ログのような通常副作用と、source変更・symlink・workspace外への書き込みを区別する。

## 実行手順

1. Policy Gatewayが完全なargv prefixと対象repositoryを確認する。
2. managed executorがrepository rootのidentityとsymlinkをpreflightする。
3. descriptor付きの一時workspaceへ、symlinkを追跡しないsource snapshotを作る。
4. `cwd`をsnapshot内の相対directoryへ解決する。
5. 専用`TMPDIR`、`PYTHONDONTWRITEBYTECODE=1`、指定環境変数、timeoutを付けて実行する。
6. 実行前後のtree snapshotを比較し、変更path・hash・symlinkを記録する。
7. 許可された副作用だけなら検証結果を保存する。それ以外の変更は`unsafe_path`相当のsafety gapとしてfail-closedにする。
8. stdout/stderrのサイズ、終了code、safe stderr summary、所要時間をpostflight evidenceへ保存し、workspaceを破棄する。

## Validation policy

設定は`engine.validation.allowed_processes`に置く。旧形式のargv listも受け付けるが、詳細なpolicyはobjectで表す。

```json
{
  "validation": {
    "allowed_processes": [
      {
        "command": ["python3", "-m", "pytest"],
        "cwd": "tests",
        "environment": {"PYTEST_ADDOPTS": "--strict-markers"},
        "timeout_seconds": 120,
        "allowed_side_effects": [
          ".pytest_cache",
          ".pytest_cache/**",
          "**/__pycache__",
          "**/__pycache__/**",
          "**/*.pyc",
          "validation.log"
        ],
        "max_output_bytes": 4194304
      }
    ]
  }
}
```

- `command`はargv prefixであり、shell文字列として解釈しない。
- `cwd`と副作用globはworkspace相対で、`..`、absolute path、workspace全体に一致するglobを拒否する。
- environmentの値は実行時memoryだけに保持し、Action recordには名前とSHA-256だけを残す。
- `TMPDIR`と`PYTHONDONTWRITEBYTECODE`はmanaged executorが強制し、policyから上書きできない。
- `.pytest_cache`や`__pycache__`はdirectory自身と配下を別々に許可する。source fileの変更をcache globで隠すことはできない。
- allowlistに一致しないcommandはprocess開始前に拒否し、追加すべき設定名とcommand digestを返す。raw command引数は診断へ出さない。
- 詳細policy（cwd、environment、timeout、出力上限、side-effect）はEngine設定を正本とする。Agent proposalが同じ設定を再掲することはできるが、scopeを広げる上書きは拒否する。

## 失敗分類

| 条件 | 保存する分類 | Reviewへの意味 |
| --- | --- | --- |
| timeout、実行file不足、import環境不足、exit 126/127 | `validation_environment_failure` | `validation_environment_gap`。コード不備とは断定しない |
| test commandが実行され、assertion等で失敗 | `test_gap` | `test_gap`。修正可能なコード/テスト問題として再Plan |
| 許可外source変更、symlink、workspace escape | `safety_gap` / `unsafe_path` | 安全側で停止し、環境またはpolicyを人間が確認 |
| stdout/stderr上限超過 | `validation_environment_failure` | 出力上限またはprovider設定を調整 |

postflight evidenceには`returncode`、`actual_changed_paths`、`unexpected_changed_paths`、`failure_category`、`started_at`、`completed_at`、`duration_ms`、`stderr_summary`を保存する。stderr本文やabsolute pathは保存しない。

## 運用トラブルシューティング

1. `taskctl diagnose TASK_ID`で`runtime_diagnostics`とactionのafter evidenceを確認する。
2. `validation_environment_gap`なら、command prefix、cwd、依存package、timeout、side-effect policyを確認する。実行済みvalidationの環境失敗・scope escapeは、コード不備と断定せず同じReview境界で自動再Planせず、Human Questionから再開・再Plan・停止を選ぶ。
3. `test_gap`なら、検証結果をコード変更のReview入力として扱う。
4. `safety_gap`ならglobを広げる前に、意図しない変更pathとpolicy境界を確認する。
5. validation環境エラーを検出した場合、engineは無限replanを続けず`needs_human_input`へ停止する。通常のコード/test失敗はfailure signatureを比較し、同じ進展なしが2〜3回続いた場合に`human_required`へ移行する。
