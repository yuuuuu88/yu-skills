# 枝・派生版の差分設計

プラン、顧客、テナント、地域、環境、モバイル版、オンプレミス版など、共通部分から枝分かれするプロダクトは、共通設計書を複製しない。共通文書を正本とし、枝には差分だけを書く。

## 配置

```text
docs/12-variants/
├── README.md
└── <variant-id>/
    ├── README.md
    ├── delta.md
    ├── requirements-delta.md       # 変更がある場合のみ
    ├── architecture-delta.md      # 変更がある場合のみ
    ├── api-data-delta.md           # 変更がある場合のみ
    ├── security-operations-delta.md
    └── diagrams-delta.md           # 図の差分が必要な場合のみ
```

## front matter

```yaml
---
id: DELTA-001
type: variant-delta
title: Enterprise版の差分設計
status: draft
source_of_truth: true
doc_version: 0.1.0
product_version: 1.0-enterprise
created: 2026-08-09
updated: 2026-08-09
variant_id: enterprise
base_document: ARCH-001
base_version: 1.1.0
applies_to: [enterprise]
related_requirements: [REQ-V-001]
related_designs: []
related_diagrams: []
related_adrs: []
related_tests: []
---
```

`base_document` と `base_version` は、差分の主たる基準文書を機械的に解決するために必須である。複数の補助文書を参照する場合は、本文の「関連文書」にIDと版を列挙する。front matterの基準キーは `base_document` と `base_version` に固定する。

## 必須項目

1. 枝の識別子、対象利用者、適用範囲。
2. `base_document` と `base_version`、および補助的に参照するベース文書。
3. 追加する要件、変更する要件、制限・削除する要件。
4. アーキテクチャ、機能、API、データ、UI、セキュリティ、運用の差分。
5. 互換性、migration、保持・削除、性能・容量への影響。
6. **UI差分**: 画面、導線、権限、表示、アクセシビリティ。
7. **運用差分**: 配置、監視、SLO、runbook、障害対応、廃止。
8. 追加・変更する図候補とADR。
9. 差分を検証するテスト、受入条件、リリース条件。
10. ベースとの差分理由、共通化へ戻す可能性、未解決の衝突。

## 差分の書き方

| 操作 | 記録方法 |
|---|---|
| 追加 | `ADD: 何を、どの要件で追加するか` |
| 変更 | `CHANGE: ベースの何を、何へ変えるか` |
| 制限 | `CONSTRAIN: 枝で許可しない範囲と理由` |
| 削除 | `REMOVE: 枝から除外する機能と互換性影響` |
| 継承 | `INHERIT: ベースIDと版を参照し、再掲しない` |

変更のない項目は書かず、ベース文書へのリンクで継承を示す。「現行ベースを参照」のように版を省略した差分は不正とする。

## 解決順序と衝突

仕様を読む順序を次に固定する。

```text
共通要件・設計
  → 枝の差分設計
  → 環境・顧客固有の差分
  → 実装・運用設定
```

- 同じIDを枝で上書きする場合は、差分文書に変更理由とベース版を記録する。
- 二つの差分が同じ項目を変更したら、暗黙にマージせず `conflict` として判断を求める。
- ベース版が更新されたら、差分の再適用レビューを行う。
- 差分が複数枝に共通化できる場合は、共通要件へ戻すADRまたは変更記録を残す。

## 参考資料

- [ISO/IEC/IEEE 42010:2022](https://www.iso.org/standard/74393.html) — 関心事ごとに複数のviewを管理する考え方。調査日: 2026-08-09
- [ADR — Architecture Decision Records](https://adr.github.io/) — 判断、理由、トレードオフ、結果を記録する方法。調査日: 2026-08-09
