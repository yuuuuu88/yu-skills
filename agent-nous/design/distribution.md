# 配布・リポジトリ衛生

## 方針

`agent-nous`のGitリポジトリには、実行可能なソース、契約スキーマ、Skill定義、設計書、評価fixtureだけを含める。Taskの状態やRequest ledgerなど、利用者・実行環境・秘密情報に依存するデータは配布物に含めない。

## 配布対象

- `src/agent_nous/`: Python runtimeと契約スキーマ
- `skill/nous/`: Codex Skill定義、参照ポリシー、launcher
- `bin/nous`: checkoutからruntimeを起動するlauncher
- `examples/`: 利用者がコピーする設定テンプレート
- `design/`, `research/`, `spike/`: 人間が読む設計・調査・検証資料
- `tests/`: 再現可能な自動テスト

## 配布しないもの

次のパスはローカル設定またはruntime生成物であり、`.gitignore`で除外する。

- `.nous/`: project-local設定。配布時は`examples/nous.config.example.json`を使う
- `tasks/`: canonical Task state、checkpoint、event、knowledge、resume情報
- `requests/`: request ledgerとlock
- `runtime/`, `sandboxes/`, `agent-runs/`: managed executorの一時実行領域
- `eval-runs/`: 評価実行結果
- `.venv/`, `__pycache__/`, `.pytest_cache/`, build/cache類
- `.env*`、秘密鍵・証明書類

公開設定例では、通常の単一Reviewと、最大2件の独立Reviewerを使うbounded cross-reviewを選べる。Reviewerの結果はEngineが固定規則で集約し、ReviewのReviewや無制限の承認チェーンを設定しない。Provider一時失敗時のfallbackも設定で明示する。

`tasks/`や`requests/`を過去に誤って生成していても、既存の追跡ファイルを自動で削除するのではなく、まず`git ls-files`で追跡状態を確認する。追跡済みのruntimeデータがある場合だけ、内容を確認してから`git rm --cached`でindexから外す。

## リリース前チェック

```sh
git status --short --ignored
git ls-files tasks requests .nous
python3 -m compileall -q src tests
python3 -m pytest -q
python3 -m pip wheel --no-deps --no-build-isolation --wheel-dir /tmp/agent-nous-dist .
```

期待値:

- `git ls-files tasks requests .nous`は空（利用者のlocal configを除く）
- runtime生成物が`git status --short`に出ない
- wheelに`tasks/`, `requests/`, `.nous/`, `eval-runs/`が入らない
- テストとwheel作成が成功する

評価fixtureなど意図的にGit管理するJSONは、`src/agent_nous/eval/`配下に置き、runtime結果と混同しない。


## Clean distribution gate

The release gate runs `scripts/check_distribution.py`. It checks three views of the same release: tracked Git paths, `git archive HEAD`, and (when supplied) the built wheel. Runtime paths such as `.nous/`, `tasks/`, `requests/`, sandbox output, and evaluation runs must be absent from all three views. The CI workflow runs this check after tests and wheel creation, so an accidentally tracked local state file fails before publication.
