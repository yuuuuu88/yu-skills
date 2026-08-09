# Layout Policy

repository-bootstrapは、分類軸を揃えたfolder-first構成を使う。

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

## Folder responsibilities

- product: 問題、利用者、要件、用語
- design: システムと機能の設計
- design/architecture: 全体構造、境界、実行時、配置
- design/features: 機能単位の設計
- design/contracts: API、DB、外部インターフェース
- design/cross-cutting: 認証、セキュリティ、エラー、ログ
- design/decisions: ADR
- operations: 起動、デプロイ、障害対応、復旧
- research: 外部調査、比較、実験
- plans: 実装順序と進捗

## Placement rules

- すべての予定する責務のフォルダを初期生成する。
- すべての生成フォルダにREADME.mdを置く。
- architectureはdesignの下に置き、designと同じ階層に置かない。
- decisionsはdesignの下に置き、設計判断として扱う。
- requirementsはproductの下に置き、技術設計と混ぜない。
- plansだけが実装進捗を保持する。
- researchは証拠の正本。単なる設計書のReferencesは関連設計書に残す。

## Legacy migration

| Legacy path | New path |
|---|---|
| docs/requirements/brief.md | docs/product/brief.md |
| docs/architecture/ | docs/design/architecture/ |
| docs/design/initial-slice.md | docs/design/features/initial-slice.md |
| docs/decisions/ | docs/design/decisions/ |
| docs/operations/ | docs/operations/ |
| docs/plans/ | docs/plans/ |

移動前に、相対リンク、CI、テスト、生成元を検索する。targetが存在する場合は内容を統合し、既存内容を無言で捨てない。移動後に空のlegacy folderは削除する。
