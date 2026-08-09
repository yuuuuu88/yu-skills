# docsディレクトリ構造

## 目的

設計書を、読む順序と責務が分かる番号付きディレクトリへ配置する。番号は読む順序の目安であり、文書の重要度や実装進捗を表さない。

## 推奨構造

```text
docs/
├── README.md                         # 全体索引、読者別入口、正本ルール
├── 00-governance/                    # 文書運用の規約
│   ├── README.md
│   ├── document-catalog.md
│   ├── documentation-policy.md
│   ├── glossary.md
│   ├── change-control.md
│   └── traceability.md
├── 01-product/                       # 価値、問題、利用者、ビジネス要求
│   ├── README.md
│   ├── vision.md
│   ├── problem-statement.md
│   ├── stakeholders.md
│   ├── user-needs.md
│   └── user-journeys/
├── 02-requirements/                  # 要件、制約、受入条件
│   ├── README.md
│   ├── requirements.md
│   ├── functional-requirements.md
│   ├── quality-requirements.md
│   ├── constraints.md
│   ├── acceptance-criteria.md
│   └── traceability-matrix.md
├── 03-domain/                        # 業務概念、ルール、プロセス
│   ├── README.md
│   ├── domain-model.md
│   ├── business-rules.md
│   └── business-processes/
├── 04-architecture/                 # システム全体の構造とビュー
│   ├── README.md
│   ├── architecture-description.md
│   ├── views/
│   ├── runtime.md
│   ├── data-architecture.md
│   ├── deployment.md
│   └── adr/
├── 05-design/                       # 機能・ドメイン・UI・ワークフロー詳細
│   ├── README.md
│   ├── features/
│   ├── domain/
│   ├── ui/
│   ├── workflows/
│   ├── data/
│   ├── api/
│   ├── integrations/
│   ├── data-migration/
│   └── error-handling.md
├── 06-contracts/                    # 機械可読契約と外部境界
│   ├── README.md
│   ├── api/
│   ├── db/
│   ├── events/
│   └── integrations/
├── 07-security/                    # セキュリティ、プライバシー、脅威
│   ├── README.md
│   ├── security-design.md
│   ├── privacy-data-classification.md
│   ├── threat-model.md
│   └── authorization-matrix.md
├── 08-validation/                  # 品質計画、テスト、証跡
│   ├── README.md
│   ├── quality-plan.md
│   ├── test-strategy.md
│   ├── test-cases/
│   ├── test-results/
│   └── verification-matrix.md
├── 09-release/                     # リリース、環境、切戻し
│   ├── README.md
│   ├── release-plan.md
│   ├── environments.md
│   └── rollout-rollback.md
├── 10-operations/                 # 運用、監視、障害、保持
│   ├── README.md
│   ├── service-overview.md
│   ├── runbooks/
│   ├── monitoring.md
│   └── retention-deletion.md
├── 11-retirement/                 # 非推奨、移行、廃止
│   ├── README.md
│   ├── retirement-policy.md
│   ├── migration-plan.md
│   └── data-disposition.md
├── 12-variants/                   # 製品の枝。共通文書は置かない
│   ├── README.md
│   ├── variant-catalog.md
│   └── <variant-id>/
│       ├── README.md
│       ├── requirements-delta.md
│       ├── design-delta.md
│       ├── operations-delta.md
│       └── adr/
├── 13-diagrams/                  # 図仕様、編集可能source、rendered生成物
│   ├── README.md
│   ├── specs/
│   ├── sources/
│   └── rendered/
└── 14-research/                 # 外部調査と根拠
    ├── README.md
    └── sources.md
```

## READMEルール

- `docs/README.md` は全体の読み方、読者別の入口、ライフサイクル順、文書カタログへのリンクを持つ。
- 各番号付きフォルダにはREADMEを置き、責務、含める文書、正本、更新者、関連フォルダを記載する。
- `12-variants/<variant-id>/README.md` は、適用対象、基準版、差分文書、衝突、適用開始日を記載する。
- `13-diagrams/README.md` は図の状態とsource/renderedの関係を記載する。
- READMEは索引として使い、要件や設計の本文を複製しない。

## source-of-truthルール

1. **要件**: `02-requirements` の該当文書が意味の正本である。READMEや図に要件全文を複製しない。
2. **設計**: 各責務フォルダの本文が設計の正本である。要約には正本へのリンクを付ける。
3. **ADR**: 判断理由、代替案、トレードオフ、結果は `04-architecture/adr/` またはvariantのADRが正本である。
4. **API**: OpenAPIなどの機械可読契約が存在する場合、それをエンドポイントとschemaの正本にする。Markdownは意図と利用例を補足する。
5. **DB**: migration/DDLとDB契約のどちらを正本とするかを明記し、二重管理を避ける。生成されたER図は正本ではない。
6. **図**: `13-diagrams/specs` が目的と要素、`sources` が編集可能な図の正本、`rendered` は生成物である。画像だけを正本にしない。
7. **variant**: base文書が共通仕様の正本であり、variantは差分だけを正本とする。

## 詳細設計と契約の境界

- `05-design` は処理の意図と実装責務を記述する。例: APIを呼ぶ順序、DBを使う理由、画面状態、retry判断。
- `06-contracts` は境界で保証する機械可読の形を記述する。例: OpenAPIのendpoint/schema、migration/DDL、event schema、外部providerの制限。
- 同じ対象を両方に書く場合、`05-design` は契約IDと正本パスを参照し、schema・フィールド・互換性ルールを複製しない。
- APIの利用手順は `05-design/api/`、APIの外部契約は `06-contracts/api/`、DBの論理設計は `05-design/data/`、DB schema/RLS/migrationは `06-contracts/db/` を正本とする。
- 外部連携の処理フローは `05-design/integrations/`、providerの接続条件・SLA・制限は `06-contracts/integrations/` を正本とする。

## 相互リンクルール

- リンクはリポジトリ相対パスを使い、可能な限り文書IDの横に置く。
- 各文書は `related_requirements`、`related_designs`、`related_diagrams`、`related_adrs`、`related_tests` を列挙する。
- リンク先を移動したときは、README、カタログ、トレーサビリティ、図仕様を同じ変更で更新する。
- 同じ内容をコピーせず、参照元を1つにしてリンクする。
- 外部URLには名称、用途、調査日を付ける。外部URLの内容を正本扱いしない。
- `TBD`、`N/A`、`superseded` の文書も索引から隠さず、状態を表示する。

## 参考資料

- [arc42 Template Overview](https://arc42.org/overview) — 関心事に沿うアーキテクチャ文書の構成例。調査日: 2026-08-09
- [ISO/IEC/IEEE 42010:2022](https://www.iso.org/standard/74393.html) — 関心事、view、viewpointを分けて記述する考え方。調査日: 2026-08-09
- [Keep a Changelog](https://keepachangelog.com/en/2.0.0/) — 変更履歴の整理方法。調査日: 2026-08-09
