# 文書カタログ

## 目的

このカタログは、プロダクトのライフサイクル全体で必要な文書の種類、責務、適用判定、正本パスを決める。文書名はプロダクトに合わせて統合してよいが、要件ID、検証方法、トレーサビリティは失わない。

## 判定値

| 判定 | 意味 | 扱い |
| --- | --- | --- |
| `required` | 本プロダクトで必須 | 原則として作成する。統合した場合は統合先を記録する。 |
| `conditional` | 条件を満たす場合に必須 | 条件、判定、再評価日を記録する。 |
| `not-applicable` | 現在は対象外 | 理由、統合先/代替、再評価条件、判断者、判断日を必ず記録する。 |

`not-applicable` は「不要なので省略」とだけ書いてはならない。既存文書で代替できる場合は代替パスを示し、条件が変わったときに再判定できるようにする。

## カタログ項目

各行または個別レコードに次を持たせる。

- `doc_id`、名称、分類、推奨パス
- `applicability`: `required` / `conditional` / `not-applicable`
- 適用条件または不要理由
- 統合先または代替文書
- 再評価条件と再評価予定
- 判断者、判断日、status
- 関連要件ID、関連ADR、検証方法

個別の判定レコード例:

```yaml
---
id: DOC-UI-004
type: applicability-decision
title: ネイティブモバイル画面設計
status: approved
source_of_truth: true
doc_version: 1.0.0
product_version: 1.0.0
created: 2026-08-09
updated: 2026-08-09
applies_to: [core]
applicability: not-applicable
reason: 本プロダクトはWebブラウザ専用で、ネイティブアプリを提供しない。
integration_or_replacement: 05-design/ui/README.md
reevaluation_trigger: ネイティブモバイルアプリの開発を決定したとき
decided_by: product-owner
decided_at: 2026-08-09
---
```

## requiredの例外

`required` は、運用・保守・廃止を含む通常のプロダクトライフサイクルを対象にした既定値である。CLI、ライブラリ、短期検証、社内限定の単発ツールなどでは、対象外と判断した文書を `not-applicable` に上書きしてよい。ただし、理由、統合先/代替、再評価条件、判断者、判断日を記録する。

例: ライブラリが公開サービスを運用しない場合、`DOC-OPS-003`（監視）を `not-applicable` にし、利用者向けの障害報告方法を代替文書として示す。

## 推奨カタログ

### 00 ガバナンス・管理

| ID | 文書 | 判定 | 内容 | 推奨パス |
| --- | --- | --- | --- | --- |
| DOC-GOV-001 | 文書索引 | required | 読者別入口、文書一覧、状態、正本 | `docs/README.md` |
| DOC-GOV-002 | 文書カタログ | required | 文書種別と適用判定 | `docs/00-governance/document-catalog.md` |
| DOC-GOV-003 | 文書作成ガイド | required | 文体、ID、front matter、レビュー | `docs/00-governance/documentation-policy.md` |
| DOC-GOV-004 | 用語集 | required | ドメイン用語、略語、禁止する同義語 | `docs/00-governance/glossary.md` |
| DOC-GOV-005 | 変更・版管理方針 | required | status、doc_version、レビュー、廃止 | `docs/00-governance/change-control.md` |
| DOC-GOV-006 | トレーサビリティ方針 | required | 要件から廃止までのID規約 | `docs/00-governance/traceability.md` |
| DOC-GOV-007 | 調査・参考資料台帳 | conditional | 外部情報や規格を使う場合 | `docs/14-research/sources.md` |

### 01 プロダクト・発見

| ID | 文書 | 判定 | 内容 | 推奨パス |
| --- | --- | --- | --- | --- |
| DOC-PRD-001 | ビジョン | required | 目的、価値、成功指標 | `docs/01-product/vision.md` |
| DOC-PRD-002 | 問題定義・現状分析 | required | 解く問題、現状業務、制約 | `docs/01-product/problem-statement.md` |
| DOC-PRD-003 | ステークホルダー一覧 | required | 利用者、管理者、運用者、外部関係者 | `docs/01-product/stakeholders.md` |
| DOC-PRD-004 | ペルソナ | conditional | 利用者の役割・知識・課題が複数ある場合 | `docs/01-product/personas.md` |
| DOC-PRD-005 | ユーザーニーズ | required | 利用者が達成したい結果 | `docs/01-product/user-needs.md` |
| DOC-PRD-006 | ユーザージャーニー | conditional | 複数画面、複数役割、長い業務体験がある場合 | `docs/01-product/user-journeys/` |
| DOC-PRD-007 | ビジネス要件 | required | 事業目標、成果、制約 | `docs/01-product/business-requirements.md` |

### 02 要件定義

| ID | 文書 | 判定 | 内容 | 推奨パス |
| --- | --- | --- | --- | --- |
| DOC-REQ-001 | 要件定義書 | required | 要件体系、優先度、根拠、状態 | `docs/02-requirements/requirements.md` |
| DOC-REQ-002 | ステークホルダー要件 | required | 関係者ごとの要求と制約 | `docs/02-requirements/stakeholder-requirements.md` |
| DOC-REQ-003 | 機能要件 | required | システムが提供する機能 | `docs/02-requirements/functional-requirements.md` |
| DOC-REQ-004 | 非機能・品質要件 | required | 性能、可用性、セキュリティ、保守性等 | `docs/02-requirements/quality-requirements.md` |
| DOC-REQ-005 | 制約・前提 | required | 技術、法務、予算、環境の制約 | `docs/02-requirements/constraints.md` |
| DOC-REQ-006 | ユースケース／ユーザーストーリー | conditional | 操作や業務シナリオの分岐がある場合 | `docs/02-requirements/use-cases/` |
| DOC-REQ-007 | 受入基準 | required | 要件を満たした判定条件 | `docs/02-requirements/acceptance-criteria.md` |
| DOC-REQ-008 | 要件トレーサビリティ | required | 要件から設計・検証・運用・廃止への対応 | `docs/02-requirements/traceability-matrix.md` |

### 03 ドメイン・業務

| ID | 文書 | 判定 | 内容 | 推奨パス |
| --- | --- | --- | --- | --- |
| DOC-DOM-001 | ドメインモデル | conditional | 業務概念、エンティティ、関係 | `docs/03-domain/domain-model.md` |
| DOC-DOM-002 | ビジネスルール | conditional | 判定、制約、計算、権限 | `docs/03-domain/business-rules.md` |
| DOC-DOM-003 | 業務プロセス | conditional | 人・部署・システムの業務順序 | `docs/03-domain/business-processes/` |
| DOC-DOM-004 | ドメイン用語・状態 | conditional | 状態、遷移、ライフサイクル | `docs/03-domain/state-model.md` |

### 04 アーキテクチャ

| ID | 文書 | 判定 | 内容 | 推奨パス |
| --- | --- | --- | --- | --- |
| DOC-ARCH-001 | アーキテクチャ記述 | required | 範囲、関心事、原則、制約、ビュー | `docs/04-architecture/architecture-description.md` |
| DOC-ARCH-002 | システムコンテキスト | required | 利用者、システム境界、外部システム | `docs/04-architecture/views/context.md` |
| DOC-ARCH-003 | コンテナ構成 | required | アプリ、データストア、主要サービスの責務 | `docs/04-architecture/views/containers.md` |
| DOC-ARCH-004 | コンポーネント構成 | conditional | コンテナ内部の責務分割が複雑な場合 | `docs/04-architecture/views/components/` |
| DOC-ARCH-005 | 実行時構成 | conditional | 非同期、キュー、複雑な実行時協調がある場合 | `docs/04-architecture/runtime.md` |
| DOC-ARCH-006 | データアーキテクチャ | conditional | 永続データ、データ連携、個人情報がある場合 | `docs/04-architecture/data-architecture.md` |
| DOC-ARCH-007 | 統合アーキテクチャ | conditional | 外部API、イベント、複数システム連携がある場合 | `docs/04-architecture/integration.md` |
| DOC-ARCH-008 | 配置・インフラ | conditional | 本番環境、クラウド、複数環境がある場合 | `docs/04-architecture/deployment.md` |
| DOC-ARCH-009 | 可用性・災害復旧 | conditional | 復旧時間、データ損失、業務継続要求がある場合 | `docs/04-architecture/availability-dr.md` |
| DOC-ARCH-010 | アーキテクチャ判断記録 | conditional | 重要、費用大、リスク大、不可逆な選択の理由、代替案、トレードオフ | `docs/04-architecture/adr/` |

### 05 詳細設計

| ID | 文書 | 判定 | 内容 | 推奨パス |
| --- | --- | --- | --- | --- |
| DOC-DES-001 | 機能詳細設計 | conditional | 入出力、処理、失敗、認可、テスト | `docs/05-design/features/` |
| DOC-DES-002 | ドメイン詳細設計 | conditional | 集約、サービス、ルール実装境界 | `docs/05-design/domain/` |
| DOC-DES-003 | DB・ER設計 | conditional | テーブル、キー、関係、制約、インデックスの論理設計 | `docs/05-design/data/` |
| DOC-DES-004 | API利用・処理設計 | conditional | APIの利用手順、処理順、認証、エラー、retry | `docs/05-design/api/` |
| DOC-DES-005 | 外部連携処理設計 | conditional | 外部サービスを使う処理、再試行、タイムアウト、障害時挙動 | `docs/05-design/integrations/` |
| DOC-DES-006 | UI/UX設計 | conditional | 画面、操作、アクセシビリティ、状態 | `docs/05-design/ui/` |
| DOC-DES-007 | 画面遷移・ユーザーフロー | conditional | 画面間の遷移、分岐、戻り | `docs/05-design/ui/flows/` |
| DOC-DES-008 | 状態遷移設計 | conditional | 状態、イベント、ガード、遷移後処理 | `docs/05-design/workflows/state-transitions/` |
| DOC-DES-009 | エラー・例外設計 | required | 利用者、API、運用者向けの失敗時挙動 | `docs/05-design/error-handling.md` |
| DOC-DES-010 | バッチ・ジョブ設計 | conditional | 定期処理、キュー、非同期ジョブ | `docs/05-design/workflows/jobs/` |
| DOC-DES-011 | データ移行設計 | conditional | 既存データ、変換、検証、切戻し | `docs/05-design/data-migration/` |

### 06 契約・インターフェース

| ID | 文書 | 判定 | 内容 | 推奨パス |
| --- | --- | --- | --- | --- |
| DOC-CON-001 | API仕様 | conditional | 外部または内部APIの機械可読契約 | `docs/06-contracts/api/openapi.yaml` |
| DOC-CON-002 | イベント・メッセージ契約 | conditional | 非同期メッセージ、schema、互換性 | `docs/06-contracts/events/` |
| DOC-CON-003 | DBスキーマ契約 | conditional | migration、DDL、schema、RLS | `docs/06-contracts/db/` |
| DOC-CON-004 | 外部サービス契約 | conditional | provider、SLA、制限、障害時挙動 | `docs/06-contracts/integrations/` |

### 07 セキュリティ・プライバシー

| ID | 文書 | 判定 | 内容 | 推奨パス |
| --- | --- | --- | --- | --- |
| DOC-SEC-001 | セキュリティ設計 | conditional | 認証、認可、秘密、境界、監査 | `docs/07-security/security-design.md` |
| DOC-SEC-002 | プライバシー・データ分類 | conditional | 個人情報、機密度、目的、保持 | `docs/07-security/privacy-data-classification.md` |
| DOC-SEC-003 | 脅威モデル | conditional | 資産、攻撃面、信頼境界、緩和策 | `docs/07-security/threat-model.md` |
| DOC-SEC-004 | 権限マトリクス | conditional | 役割、操作、対象、拒否条件 | `docs/07-security/authorization-matrix.md` |
| DOC-SEC-005 | セキュリティ検証 | conditional | 脆弱性、侵入、秘密検出、権限テスト | `docs/07-security/verification.md` |

### 08 検証・品質

| ID | 文書 | 判定 | 内容 | 推奨パス |
| --- | --- | --- | --- | --- |
| DOC-VAL-001 | 品質計画 | required | 品質目標、ゲート、責任、証跡 | `docs/08-validation/quality-plan.md` |
| DOC-VAL-002 | テスト戦略 | required | テストレベル、対象、責任、環境 | `docs/08-validation/test-strategy.md` |
| DOC-VAL-003 | テスト計画・ケース | required | 要件ごとの検証手順、期待結果 | `docs/08-validation/test-cases/` |
| DOC-VAL-004 | 検証マトリクス | required | 要件→設計→テスト→結果 | `docs/08-validation/verification-matrix.md` |
| DOC-VAL-005 | テスト結果・受入記録 | required | 実施日、環境、結果、未解決事項 | `docs/08-validation/test-results/` |
| DOC-VAL-006 | 品質属性評価 | conditional | 性能、可用性、保守性等を測定する場合 | `docs/08-validation/quality-evidence/` |

### 09 リリース・変更

| ID | 文書 | 判定 | 内容 | 推奨パス |
| --- | --- | --- | --- | --- |
| DOC-REL-001 | リリース計画 | required | 範囲、条件、手順、責任、判定 | `docs/09-release/release-plan.md` |
| DOC-REL-002 | 環境・設定差分 | conditional | 開発、検証、本番の差分 | `docs/09-release/environments.md` |
| DOC-REL-003 | CI/CD設計 | conditional | build、test、deploy、承認 | `docs/09-release/ci-cd.md` |
| DOC-REL-004 | ロールアウト・ロールバック | required | 段階導入、切戻し、データ整合 | `docs/09-release/rollout-rollback.md` |
| DOC-REL-005 | 変更履歴 | required | リリース単位の変更、非互換、移行 | `docs/09-release/changelog.md` |

### 10 運用・保守

| ID | 文書 | 判定 | 内容 | 推奨パス |
| --- | --- | --- | --- | --- |
| DOC-OPS-001 | サービス概要 | conditional | 本番または継続運用するサービスがある場合 | `docs/10-operations/service-overview.md` |
| DOC-OPS-002 | 運用手順・Runbook | conditional | 起動、停止、定期作業、障害対応を担当者が行う場合 | `docs/10-operations/runbooks/` |
| DOC-OPS-003 | 監視・アラート設計 | conditional | 本番サービス、可用性・性能要求がある場合 | `docs/10-operations/monitoring.md` |
| DOC-OPS-004 | SLI/SLO/SLA | conditional | サービスレベルを測定・契約する場合 | `docs/10-operations/service-levels.md` |
| DOC-OPS-005 | インシデント対応 | conditional | 外部提供、業務影響、セキュリティ事故がある場合 | `docs/10-operations/incident-response.md` |
| DOC-OPS-006 | バックアップ・復旧 | conditional | 永続データまたは復旧要求がある場合 | `docs/10-operations/backup-recovery.md` |
| DOC-OPS-007 | セキュリティ運用 | conditional | 認証、権限、機密情報、脆弱性対応がある場合 | `docs/10-operations/security-operations.md` |
| DOC-OPS-008 | 利用者・管理者マニュアル | conditional | 非開発者が利用・管理する場合 | `docs/10-operations/manuals/` |
| DOC-OPS-009 | 保持・削除ポリシー | conditional | 個人情報、契約、法令、保存期間がある場合 | `docs/10-operations/retention-deletion.md` |

### 11 廃止・移行

| ID | 文書 | 判定 | 内容 | 推奨パス |
| --- | --- | --- | --- | --- |
| DOC-RET-001 | 廃止方針・条件 | conditional | 外部利用、保存データ、後継版などのライフサイクルを管理する場合 | `docs/11-retirement/retirement-policy.md` |
| DOC-RET-002 | 非推奨・告知計画 | conditional | 外部利用者、公開API、複数顧客がある場合 | `docs/11-retirement/deprecation-plan.md` |
| DOC-RET-003 | 後継への移行計画 | conditional | 後継サービスや新schemaへ移行する場合 | `docs/11-retirement/migration-plan.md` |
| DOC-RET-004 | データ保存・削除計画 | conditional | 顧客データ、個人情報、監査記録がある場合 | `docs/11-retirement/data-disposition.md` |
| DOC-RET-005 | 廃止実施記録 | conditional | 廃止を実施した時点で必須 | `docs/11-retirement/closure-record.md` |

運用・廃止の `conditional` 判定は、プロダクトごとに上書きしてよい。判定時は、運用責任者、本番環境の有無、継続提供の有無、永続データ、外部利用者、後継・移行の有無を確認する。適用しない場合も、理由、統合先/代替、再評価条件、判断者、判断日を残す。

### 12 枝・派生版

| ID | 文書 | 判定 | 内容 | 推奨パス |
| --- | --- | --- | --- | --- |
| DOC-VAR-001 | variantカタログ | conditional | 共通版と枝の一覧、基準版、適用条件 | `docs/12-variants/variant-catalog.md` |
| DOC-VAR-002 | 差分要件 | conditional | 枝固有の要件差分 | `docs/12-variants/<variant>/requirements-delta.md` |
| DOC-VAR-003 | 差分設計 | conditional | API、データ、UI、運用、図、テスト差分 | `docs/12-variants/<variant>/design-delta.md` |
| DOC-VAR-004 | 差分ADR | conditional | 枝だけの判断理由、衝突、トレードオフ | `docs/12-variants/<variant>/adr/` |

### 13 図・参考資料

| ID | 文書 | 判定 | 内容 | 推奨パス |
| --- | --- | --- | --- | --- |
| DOC-DIA-001 | 図カタログ・作図方針 | required | 図の必要性、記法、ID、レビュー | `docs/13-diagrams/README.md` |
| DOC-DIA-002 | 図仕様 | conditional | 後付け図の目的、要素、関係、alt text | `docs/13-diagrams/specs/` |
| DOC-DIA-003 | 編集可能な図ソース | conditional | Mermaid、PlantUML、BPMN XML等 | `docs/13-diagrams/sources/` |
| DOC-DIA-004 | rendered図 | conditional | SVG、PNG、PDF等の生成物 | `docs/13-diagrams/rendered/` |

`DOC-DIA-003` と `DOC-DIA-004` は図仕様が必要と判定されたときだけ作る。本文だけで関係を明確に説明できる場合は `not-applicable` とし、その理由と再評価条件を記録する。

## 判定時の統合ルール

小規模プロダクトでは、次の統合を認める。

- `01-product` のビジョン、問題定義、ビジネス要件を1文書へ統合する。
- `02-requirements` の機能要件、品質要件、制約を1つの要件定義書へ統合する。
- `04-architecture` と `05-design` の小さな機能を1つの設計書へ統合する。
- `08-validation` の戦略と計画を統合する。

統合した場合も、カタログの各ID、章見出し、トレーサビリティリンクを残す。統合先が複数の責務を持つことをREADMEで説明する。

## 参考資料

- [ISO/IEC/IEEE 29148:2018](https://www.iso.org/standard/72089.html) — 要件情報と要件工学の指針。調査日: 2026-08-09
- [ISO/IEC/IEEE 15289:2019](https://www.iso.org/standard/74909.html) — ライフサイクル情報項目の考え方。調査日: 2026-08-09
- [ISO/IEC/IEEE 12207](https://www.iso.org/cms/%20render/live/en/sites/isoorg/contents/data/standard/09/02/90219.html) — ソフトウェアライフサイクルの範囲。調査日: 2026-08-09
