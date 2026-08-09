# 設計書テンプレート

プロダクトの規模に合わせて統合・分割する。文書ごとに目的、読者、範囲、関連ID、検証を残す。

## 共通front matter

```yaml
---
id: DOC-001
type: <document-type>
title: <title>
status: draft
source_of_truth: true
doc_version: 0.1.0
product_version: 0.1.0
created: 2026-08-09
updated: 2026-08-09
applies_to: [core]
related_requirements: []
related_designs: []
related_diagrams: []
related_adrs: []
related_tests: []
---
```

## 要件定義

```markdown
# <要件文書名>

## 目的・対象読者
## 対象範囲・非対象範囲
## ステークホルダーと前提
## 要件
### REQ-F-001 <短い名前>
- 必須条件:
- 根拠:
- 受入条件:
- 検証方法:
## 品質・非機能要件
## 制約・依存
## 未決事項
## 関連文書・変更履歴
```

要件文は一文一要件にし、主語・条件・測定値・検証方法を明記する。「高速」「簡単」だけで終わらせない。

## アーキテクチャ

```markdown
# <アーキテクチャ記述>

## 目的・関係者の関心事
## 範囲・前提・制約
## 品質目標
## システムコンテキスト
> 図プレースホルダー: DG-C4-001
## 解決方針
## 構成・責務・境界
## 実行時の代表シナリオ
## 配置・外部依存
## セキュリティ・データ境界
## ADRとトレードオフ
## リスク・技術的負債
## 検証・関連要件
```

ISO/IEC/IEEE 42010の考え方に沿い、関係者の関心事ごとにビューを分ける。図や用語を一枚に詰め込まない。

## 機能詳細設計

```markdown
# <機能名>
## 目的・関連要件
## 入力・前提・権限
## 正常系
## 状態・分岐・冪等性
## エラー・タイムアウト・再試行
## 出力・副作用
## UI/API/DBへの影響
## セキュリティ・監視
## テスト・受入条件
```

## API・データ契約

```markdown
# <契約名>
## 正本と互換性方針
## エンドポイント / schema / entity
## 入力・出力・エラー
## 認証・認可・レート・上限
## timeout・retry・冪等性
## versioning・migration・保持/削除
## 例（秘密情報・実データを含めない）
## 検証・関連要件
```

OpenAPI、JSON Schema、migrationなどの機械可読ファイルが正本なら、Markdownは説明・判断・入口に限定する。

## ADR

```markdown
# ADR-001: <判断>
## Status
## Context
## Decision
## Alternatives considered
## Consequences and trade-offs
## Requirements and risks
## Follow-up / revisit trigger
```

重要な判断は、背景・選択肢・採用理由・結果を小さなADRとして記録する。後から本文に理由を埋め込んでADRと矛盾させない。

## 検証・運用・廃止

```markdown
# <運用/検証/廃止文書>
## 目的・対象読者・適用範囲
## 前提・権限・安全確認
## 手順または判定条件
## 成功・失敗・ロールバック
## 監視・証跡・連絡
## データ保持・削除・移行
## 関連要件・設計・テスト
## 未決事項・変更履歴
```

## 枝・派生版の差分設計

```yaml
---
id: DELTA-001
type: variant-delta
title: Enterprise版差分設計
status: draft
source_of_truth: true
doc_version: 0.1.0
product_version: 1.0-enterprise
created: 2026-08-09
updated: 2026-08-09
applies_to: [enterprise]
variant_id: enterprise
base_document: ARCH-001
base_version: 1.0.0
related_requirements: [REQ-V-001]
related_designs: []
related_diagrams: [DG-DEP-001]
related_adrs: [ADR-ENT-001]
related_tests: [TEST-ENT-001]
---
```

```markdown
# Enterprise版差分設計

## 適用対象とbase文書
## 継承する仕様
## 要件差分（追加・変更・制限・削除）
## API差分
## データ差分
## UI差分
## セキュリティ・運用・リリース差分
## 図差分（図IDとbase図の版）
## テスト・受入条件差分
## 衝突、判断者、再評価条件
## 変更履歴
```

## 図仕様

```yaml
---
diagram_id: DG-C4-001
title: 図タイトル
type: c4-system-context
status: planned
doc_version: 0.1.0
product_version: 1.0
source_of_truth: true
created: 2026-08-09
updated: 2026-08-09
related_requirements: [REQ-F-001]
related_designs: [ARCH-001]
related_diagrams: []
related_adrs: [ADR-001]
related_tests: []
---
```

```markdown
# DG-C4-001 図タイトル

- 目的:
- 読者:
- 対象範囲:
- 必須要素:
- 関係と矢印の意味:
- 通信方式・信頼境界:
- 凡例:
- alt text:
- 状態: planned
- editable source: `docs/13-diagrams/sources/DG-C4-001.mmd`
- rendered: `docs/13-diagrams/rendered/DG-C4-001.svg`
- 最終検証日: 未検証
```

## not-applicable判定

```yaml
---
id: DOC-UI-001
type: ui-design
title: ネイティブUI設計
status: approved
source_of_truth: true
doc_version: 1.0.0
product_version: 1.0.0
created: 2026-08-09
updated: 2026-08-09
applies_to: [core]
applicability: not-applicable
related_requirements: []
related_designs: []
related_diagrams: []
related_adrs: []
related_tests: []
reason: 本プロダクトはWeb専用でネイティブUIを持たない
integration_or_replacement: docs/05-design/ui/README.md
reevaluation_trigger: ネイティブアプリ開発を開始したとき
decided_by: product-owner
decided_at: 2026-08-09
---
```

## 要件トレーサビリティ

```markdown
| PURPOSE | NEED | REQUIREMENT | DESIGN | DIAGRAM/ADR | TEST | RELEASE | OPERATIONS | RETIREMENT |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PURPOSE-001 | NEED-001 | REQ-F-001 | FEAT-001 | DG-SEQ-001 / ADR-001 | TEST-001 | REL-001 | OPS-001 | RET-001 |
```

## 参考資料

- [ISO/IEC/IEEE 29148:2018](https://www.iso.org/standard/72089.html) — 要件情報の整理と検証可能性。調査日: 2026-08-09
- [ADR — Architecture Decision Records](https://adr.github.io/) — 判断記録の構成。調査日: 2026-08-09
- [OpenAPI Specification](https://spec.openapis.org/oas/) — API契約を機械可読にする仕様。調査日: 2026-08-09
