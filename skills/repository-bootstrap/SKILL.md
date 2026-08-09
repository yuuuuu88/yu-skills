---
name: repository-bootstrap
description: Initialize or reconcile a new or early-stage software repository with a stable AI-friendly folder-first layout, project rules, Codex handoff, frontmatter-based design documents, and a mandatory implementation plan. Use when starting a repository, standardizing its structure, migrating an old docs layout, or preparing a project for AI-assisted implementation before coding.
---

# Repository Bootstrap

新規または初期状態のリポジトリを、実装前に読める・設計できる・AIが安全に動ける状態へ整える。

必ず次の順序で進める。

~~~text
inspect → migrate/reconcile layout → write rules → write design packet → validate → hand off to implementation
~~~

## Required outcome

次の成果物を必ず作成または更新する。

~~~text
.codex
AGENTS.md
README.md
docs/README.md
docs/product/README.md
docs/product/brief.md
docs/design/README.md
docs/design/architecture/README.md
docs/design/architecture/overview.md
docs/design/features/README.md
docs/design/features/initial-slice.md
docs/design/contracts/README.md
docs/design/cross-cutting/README.md
docs/design/decisions/README.md
docs/operations/README.md
docs/operations/local-development.md
docs/operations/runbooks/README.md
docs/research/README.md
docs/plans/README.md
docs/plans/bootstrap-plan.md
~~~

フォルダは先に作る。ただし、各フォルダにはREADME.mdを置き、責務を明示する。意味のない空フォルダは作らない。

## Standard layout

~~~text
docs/
├── README.md
├── product/
│   ├── README.md
│   └── brief.md
├── design/
│   ├── README.md
│   ├── architecture/
│   │   ├── README.md
│   │   └── overview.md
│   ├── features/
│   │   ├── README.md
│   │   └── initial-slice.md
│   ├── contracts/
│   │   └── README.md
│   ├── cross-cutting/
│   │   └── README.md
│   └── decisions/
│       └── README.md
├── operations/
│   ├── README.md
│   ├── local-development.md
│   └── runbooks/
│       └── README.md
├── research/
│   └── README.md
└── plans/
    ├── README.md
    └── bootstrap-plan.md
~~~

### Directory responsibilities

- product: 問題、利用者、要件、用語
- design/architecture: システム全体の構造、境界、実行時、配置
- design/features: 機能単位の詳細設計
- design/contracts: API、DB、外部インターフェース
- design/cross-cutting: 認証、認可、セキュリティ、エラー、ログ
- design/decisions: ADR
- operations: 起動、デプロイ、障害対応、復旧
- research: 外部調査、比較、実験
- plans: 実装順序と進捗

src、tests、scripts、examples、app、packages、services、infraなどは技術スタックに応じて追加する。

## Non-negotiable rules

### 先に読む

対象リポジトリの次を確認する。

1. Git statusとブランチ
2. ルートと主要ディレクトリ
3. AGENTS.md、README、.codex、docs
4. package manager、lint、test、build、CI
5. 既存設計、移行手順、運用ルール

秘密情報の実値を表示せず、環境変数は名前だけ確認する。

### リポジトリ内の再構成は許可する

設計に合わせて、リポジトリ内ファイルの作成・更新・移動・削除を行ってよい。

- 旧docsパスを新しい責務へ移動する
- 移動前にリンク、CI、テスト、生成元を検索する
- 既存内容を正本へ統合してから旧ファイルを削除する
- 変更対象と理由をdocs/plans/bootstrap-plan.mdに記録する
- 任意のソースコードを設計と無関係に大量削除しない
- リポジトリ外、DB、Auth、Storage、本番、課金、公開操作は別ゲートにする

既存ファイルがあることだけを理由に確認を求めない。必要な変更を行い、最後に差分を報告する。

### frontmatterを必須にする

Skillが生成・更新するREADME.md、AGENTS.md、.codex、docs/**/*.mdにはfrontmatterを付ける。Markdownコメントで代替しない。

~~~yaml
---
id: ARCH-001
type: architecture
title: System architecture
status: draft
source_of_truth: true
created: 2026-08-02
updated: 2026-08-02
related: []
managed_by: repository-bootstrap
---
~~~

statusは文書ライフサイクルであり、実装進捗ではない。進捗はdocs/plans/へ置く。

### 設計を先に完成させる

実装前に最低限を埋める。

- product/brief.md: 問題、利用者、目的、スコープ、受け入れ条件
- design/architecture/overview.md: 境界、構成、外部依存、データフロー、品質目標
- design/features/initial-slice.md: 最初の機能の入出力、処理、失敗、認証、テスト
- design/decisions/: 重要な選択のADR、またはADR不要の判断
- operations/local-development.md: 起動、テスト、lint、build、環境変数
- plans/bootstrap-plan.md: 実装順序、検証項目、進捗

設計書に「完了」「未着手」「担当者」などの進捗を書かない。

### 外部情報・サブエージェント

- 最新仕様、未知の技術、価格、規制、外部APIは検索してよい
- 重要な根拠はURL、調査日、採用理由を記録する
- サブエージェントは調査、独立実装、レビューに使ってよい
- worktreeは必須ではない。並列編集で競合する場合だけworktreeや一時ディレクトリを使う
- サブエージェントへ秘密情報や不要な本番データを渡さない
- 成果物はメインエージェントがレビューしてから採用する

### 外部副作用の確認ゲート

ローカル読み取り、検索、編集、lint、typecheck、test、buildは通常自動で行う。

次は実行前に確認する。

- 本番DB/Auth/Storageの変更・リセット・削除
- 大量AI API利用、課金、決済、メール送信
- remoteへのpush、PR公開、デプロイ
- 機密データの外部送信
- リポジトリ外の破壊的操作

## Workflow

### Phase 0: Inspect

1. 対象ルートを確定する。
2. Git status、既存ルール、設計書を読む。
3. 技術スタックと検証コマンドを特定する。
4. 旧パス、再構成対象、保持対象、削除候補をbootstrap-planに記録する。

### Phase 1: Migrate and scaffold

同梱scaffoldを必ずdry-runしてから実行する。

~~~bash
python3 /path/to/repository-bootstrap/scripts/scaffold_repository.py \
  /path/to/repository \
  --project-name "Project name" \
  --description "One sentence description" \
  --stack "Runtime / framework / storage" \
  --migrate-legacy \
  --overwrite
~~~

旧レイアウトは次のように移行する。

~~~text
docs/requirements/brief.md
  → docs/product/brief.md

docs/architecture/overview.md
  → docs/design/architecture/overview.md

docs/design/initial-slice.md
  → docs/design/features/initial-slice.md

docs/decisions/
  → docs/design/decisions/

docs/operations/
  → docs/operations/

docs/plans/
  → docs/plans/
~~~

移動後、空になった旧フォルダは削除する。targetに既存ファイルがある場合は内容を統合し、上書きで情報を失わない。

### Phase 2: Write design packet

生成されたプレースホルダーを対象プロジェクトの内容で埋める。

1. docs/product/brief.md
2. docs/design/architecture/overview.md
3. docs/design/features/initial-slice.md
4. 必要に応じてcontracts、cross-cutting、researchへ追加する
5. 重要な判断をdesign/decisions/へ追加する
6. docs/operations/local-development.md
7. docs/plans/bootstrap-plan.md

実装で設計が変わった場合は設計書を現在の正本へ更新し、重要な変更はADRにする。

### Phase 3: Validate and hand off

~~~bash
python3 /path/to/repository-bootstrap/scripts/validate_repository_bootstrap.py \
  /path/to/repository \
  --all-docs \
  --strict
~~~

確認する。

- 必須ファイルとdocs/plansが存在する
- frontmatterと必須metadataがある
- 相対リンクが切れていない
- 秘密情報らしき値がない
- requirements、architecture、designに進捗マーカーがない
- 旧レイアウトが残っていない
- .codexが単一ファイルである
- stack固有のlint、typecheck、test、buildが通る

完了報告には、作成・更新・移動・削除ファイル、正本、検証結果、未決定事項、実装開始点を含める。

## Document policy

| 内容 | 正本 |
|---|---|
| AI・開発者ルール | AGENTS.md |
| Codex恒久引き継ぎ | .codex |
| 人間向け入口 | README.md |
| 文書一覧 | docs/README.md |
| 問題・要件 | docs/product/ |
| 全体構造 | docs/design/architecture/ |
| 機能詳細 | docs/design/features/ |
| API・DB契約 | docs/design/contracts/ |
| 横断設計 | docs/design/cross-cutting/ |
| 意思決定 | docs/design/decisions/ |
| 運用 | docs/operations/ |
| 調査 | docs/research/ |
| 進捗 | docs/plans/ |

詳細はreferences/layout-policy.md、references/document-templates.md、references/repository-rules-template.mdを読む。
