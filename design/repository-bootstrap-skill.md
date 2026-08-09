---
id: SKILL-DESIGN-001
type: skill-design
title: Repository Bootstrap Skill design
status: accepted
source_of_truth: true
created: 2026-08-02
updated: 2026-08-02
related: []
managed_by: repository-bootstrap
---

# Repository Bootstrap Skill design

## Purpose

新規または初期状態のリポジトリを、実装前に読める・設計できる・AIが安全に動ける状態へ整える。フォルダは後から増やすのではなく、最初に責務を決めた構成を生成する。

~~~text
inspect → migrate/reconcile layout → write rules → write design packet → validate → implementation
~~~

## Design principles

- **Folder-first**: 将来使う責務のフォルダを初期生成する。ただし、責務を説明するREADMEを必ず置き、意味のない空フォルダにはしない。
- **Domain-first**: `docs/`直下は product、design、operations、research、plans の大分類に揃える。architectureやdecisionsのような設計の種類は `design/` 配下に置く。
- **Design is not progress**: 設計書は目的、制約、構造、契約、判断を記録する。実装状態、チェックリスト、担当、ブロッカーは `docs/plans/` に置く。
- **Source of truth is explicit**: 各文書はfrontmatterでID、種類、状態、正本性、関連文書を持つ。
- **Reconciliation over preservation**: リポジトリ内の既存ファイルは、設計に合わせて更新・移動・統合・削除してよい。移動前に参照を検索し、情報を失わない形で再配置する。
- **AI-native handoff**: `.codex`、`AGENTS.md`、README、文書インデックス、実装計画を同じ初期成果物として作る。

## Fixed policy

- Skill名は `repository-bootstrap` とする。
- `.codex/` ではなく、Codexの恒久引き継ぎファイル `.codex` を常に生成する。
- `docs/plans/` と `docs/plans/bootstrap-plan.md` は必須とする。
- Skillが生成・更新するMarkdown文書と `.codex` はfrontmatter必須とし、Markdownコメントで代替しない。
- サブエージェントは調査、独立実装、レビューに使ってよい。worktreeは競合リスクに応じて選び、必須にはしない。
- インターネット検索は、最新仕様、未知の技術、価格、規制、外部APIの確認に使ってよい。根拠URLと調査日をresearchまたは設計書に残す。
- ローカルの作成、更新、移動、削除、テスト、lint、buildは自動で行ってよい。
- 本番サービス、DB/Auth/Storage、課金、大量AI利用、機密情報の外部送信、push、PR、デプロイは確認ゲートにする。

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

### Folder responsibilities

| Folder | Responsibility | Progress allowed? |
|---|---|---|
| `product/` | 問題、利用者、要件、用語、目標 | No |
| `design/architecture/` | 全体構造、境界、実行時、配置 | No |
| `design/features/` | 機能単位の詳細設計 | No |
| `design/contracts/` | API、DB、イベント、外部インターフェース | No |
| `design/cross-cutting/` | 認証、認可、セキュリティ、エラー、ログ | No |
| `design/decisions/` | ADR、重要な選択とトレードオフ | No |
| `operations/` | 起動、デプロイ、障害対応、復旧 | No |
| `research/` | 外部調査、比較、実験、根拠 | No |
| `plans/` | 実装順序、タスク、進捗、チェックポイント | Yes |

## Required design packet

1. `docs/product/brief.md`: 問題、利用者、目的、スコープ、受け入れ条件
2. `docs/design/architecture/overview.md`: 境界、構成、外部依存、データフロー、品質目標
3. `docs/design/features/initial-slice.md`: 最初の機能の入出力、処理、失敗、認証、テスト
4. `docs/design/decisions/`: 重要な判断のADR、またはADR不要の判断
5. `docs/operations/local-development.md`: 起動、テスト、lint、build、環境変数
6. `docs/plans/bootstrap-plan.md`: 設計から実装へ進む順序、検証、保留事項

文書の状態は `draft`、`proposed`、`accepted`、`deprecated` などのライフサイクルで表し、実装進捗を代用しない。

## Legacy migration

| Legacy path | New path | Rationale |
|---|---|---|
| `docs/requirements/brief.md` | `docs/product/brief.md` | 要件をプロダクト責務へ集約 |
| `docs/architecture/` | `docs/design/architecture/` | architectureをdesign配下へ統一 |
| `docs/design/initial-slice.md` | `docs/design/features/initial-slice.md` | 機能設計の配置を統一 |
| `docs/decisions/` | `docs/design/decisions/` | ADRを設計判断として集約 |
| `docs/operations/` | `docs/operations/` | 既存の責務を維持 |
| `docs/plans/` | `docs/plans/` | 進捗の正本を維持 |

移動後は旧フォルダを残さず、相対リンクを新パスへ修正する。移動先に同名ファイルがある場合は衝突として保持し、自動削除せず手動統合対象として報告する。

## Implementation

~~~text
skills/repository-bootstrap/
├── SKILL.md
├── agents/openai.yaml
├── references/
│   ├── layout-policy.md
│   ├── document-templates.md
│   └── repository-rules-template.md
└── scripts/
    ├── scaffold_repository.py
    └── validate_repository_bootstrap.py
~~~

`scaffold_repository.py` はfolder-firstの標準ファイルを作成・更新し、legacyパスを移行し、`--dry-run`、`--migrate-legacy`、`--overwrite`を提供する。移行時の衝突は自動削除せず報告する。

`validate_repository_bootstrap.py` は必須ファイル、frontmatter、相対リンク、秘密情報らしき値、product/design文書内の進捗マーカー、legacyパス、`.codex`のファイル形式を検査する。

## References

- [C4 Model](https://c4model.com/)
- [C4 diagrams](https://c4model.com/diagrams)
- [arc42 overview](https://arc42.org/overview)
- [ADR](https://adr.github.io/)
- [ADR templates](https://adr.github.io/adr-templates/)
- [Google Documentation Best Practices](https://google.github.io/styleguide/docguide/best_practices.html)
- [OpenAI Codex AGENTS.md](https://github.com/openai/codex/blob/main/docs/agents_md.md)
