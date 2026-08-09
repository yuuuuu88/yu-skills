---
name: design-docs
description: 要件定義から設計・契約・検証・運用・廃止までを、IDと差分でつながる設計書体系として作成・更新・レビューする。文書カタログ、not-applicable判断、派生版の差分設計、図の後付け仕様、トレーサビリティを必要とするプロダクト設計で使う。
---

# 設計書作成

プロダクトの要求を、読者が迷わず使える設計書体系へ変換する。単一の巨大文書を作らず、責務ごとの文書、相互リンク、要件ID、検証記録で一貫性を保つ。

## 使うとき

次の依頼で使う。

- プロダクトの設計書一式を新規作成する。
- 要件定義書から設計・API・DB・UI・セキュリティ・運用文書を展開する。
- 既存の設計書体系を棚卸しし、欠落・重複・リンク切れを直す。
- 共通プロダクトに対するEnterprise版、モバイル版、オンプレミス版などの差分設計を作る。
- 図を後で作図できるよう、図の必要性と仕様を先に定義する。

## 使わないとき（非ゴール）

- 新規リポジトリの標準フォルダ、AGENTS、.codex、実装計画を初期化するだけの依頼は `repository-bootstrap` を使う。
- 既存Markdownを平易な日本語へリライトするだけの依頼は `clear-japanese-docs` を使う。
- Mermaid、PPTX、draw.io、SVG、PNGなどの図の実体を作成・描画する依頼は、このSkillでは図仕様だけを作り、図作成に対応するSkillへ渡す。
- ソースコード、DB、認証、インフラを実装する依頼は、設計書に実装契約を記録した後、別の実装作業として扱う。
- 標準規格の本文を大量に転載しない。根拠は要約とURLで示す。

## 守る境界

- 書き込み対象は依頼で許可された設計書ディレクトリと索引に限定する。
- 既存の未コミット変更を上書きしない。作業前後に差分を確認する。
- 図の実体を作らない。`planned` の図は本文プレースホルダーと図仕様までにする。
- 秘密情報、トークン、個人情報の実値を設計書・ログ・参考資料へ書かない。
- 推測は事実と分け、`assumption`、`TBD`、`open` などの状態を明示する。

## 既存Skillとの責務と引き渡し

実行順序と正本の責務を次で固定する。

```text
repository-bootstrap（リポジトリの初期構造・README・ルール）
  → design-docs（要件ID、適用判定、設計、正本、差分、図インベントリ）
  → clear-japanese-docs（意味を変えない文章の明確化）
  → 図作成Skill（図仕様をもとにした実体の作図）
```

- `repository-bootstrap` が先に作った `docs/product`、`docs/design`、`docs/operations` などは、移行が必要なら対応表を作ってから番号付き構造へ統合する。上書きや無言の削除をしない。
- `repository-bootstrap` はフォルダ、README、プロジェクトルール、初期計画を担当する。`design-docs` は要件・設計・適用判定・ID・source-of-truth・図インベントリを担当する。
- `clear-japanese-docs` は文章の順序、重複、読みやすさを改善してよいが、要件ID、規範性、適用判定、関連ID、正本、図状態を変更してはならない。意味変更が必要なら `design-docs` へ戻す。
- 図作成Skillへ渡すのは、図ID、図仕様、editable sourceの形式、関連要件/ADR、alt text、凡例、検証条件である。図作成Skillは図仕様を勝手に要件の正本へしない。
- 既存文書の移行表には、旧パス、新パス、正本、保持/統合/廃止、確認者、確認日を記録する。

## 実行手順

1. **対象と既存情報を確認する**
   - プロダクトの目的、対象利用者、ライフサイクル、製品の枝、読者、許可された書き込み範囲を確定する。
   - 要件定義書、既存設計書、コード、設定、テスト、運用文書、既存差分を読む。
   - 既存文書の正本、重複文書、古いリンク、未決事項を記録する。
2. **要件を正規化する**
   - `purpose → need → requirement` の順に分解する。
   - 機能要件、品質要件、制約、法務・セキュリティ要求に一意のIDを付ける。
   - 各要件に優先度、根拠、検証方法、状態、関連読者を付ける。
3. **文書カタログを作る**
   - `references/document-catalog.md` の分類を使い、各文書を `required`、`conditional`、`not-applicable` のいずれかにする。
   - `not-applicable` は黙って省略しない。理由、統合先または代替、再評価条件、判断者、判断日を必ず記録する。
4. **共通設計を作る**
   - 番号付き責務別ディレクトリへREADMEを置く。
   - 要件、ドメイン、アーキテクチャ、詳細設計、契約、セキュリティ、検証、リリース、運用、廃止を段階的に作る。
   - 各文書に読者、目的、範囲、前提、制約、設計内容、検証、関連ID、変更履歴を持たせる。
5. **枝を差分で表す**
   - 共通文書を複製しない。
   - 差分文書に `base_document` と `base_version` を記録し、要件・API・データ・UI・運用・図・テストの追加、変更、削除を記録する。
   - 衝突は暗黙に解決しない。`references/variant-delta.md` の優先順位と確認ルールに従う。
6. **図の必要性を決める**
   - `references/diagram-selection.md` で図種別を `required`、`conditional`、`not-applicable` にする。
   - 図を作らない段階でも、図ID、目的、範囲、要素、関係、要件/ADRリンク、alt text、凡例、検証日を残す。
7. **トレーサビリティを張る**
   - `references/traceability.md` に従い、`purpose → need → requirement → design → diagram/ADR → test → release/operations/retirement` をIDでつなぐ。
   - 一方向だけでなく、設計・テスト・図・運用から要件へ戻れることを確認する。
8. **検査して引き渡す**
   - `references/quality-checklist.md` でID重複、欠落、リンク、front matter、N/A理由、枝の基準版、図仕様、秘密情報、許可範囲を検査する。
   - リポジトリルートから `python3 skills/design-docs/scripts/validate_design_docs.py .` を実行し、機械検査の出力を保存する。
   - 未解決の判断、要件の欠落、再評価トリガー、次の作図・レビュー作業を出力する。

## 出力契約

設計書を作成・更新したときは、少なくとも次を返す。

1. **文書一覧**: 作成・更新・統合したパス、文書ID、status、正本。
2. **文書カタログ判定**: `required`、`conditional`、`not-applicable` とその理由。
3. **要件トレーサビリティ**: 要件IDから設計、図/ADR、テスト、リリース/運用/廃止へのリンク。
4. **枝の差分**: 対象variant、base_document、base_version、差分、衝突。
5. **図の計画**: 図ID、必要性、後付け状態、次に必要な作業。図の実体は含めない。
6. **未決事項と仮定**: 判断者、期限または再評価条件。
7. **検証結果**: 必須ファイル、リンク、ID、front matter、秘密情報、許可範囲の検査結果。

## 文書の正本ルール

- `docs/README.md` は全体索引と読者別の入口である。
- `00-governance/document-catalog.md` は文書の種類、適用判定、正本パス、再評価条件の正本である。
- 要件の意味は要件文書、設計の意味は対応する設計文書、判断理由はADR、APIの機械可読契約はOpenAPIなどの契約ファイルを正本にする。
- 図は編集可能なsourceを正本とし、rendered画像は生成物とする。図仕様はsourceと本文の整合を検査する。
- 派生版はbase文書を参照する。base文書の複製や、差分を本文へ黙って混ぜることを禁止する。

## 参考資料

調査結果は各referenceの「参考資料」に分散して記録する。主張は要約し、長い引用を載せない。調査日は **2026-08-09** である。

## 参照ファイル

- [文書カタログ](./references/document-catalog.md): 文書種別、適用判定、正本パス
- [ディレクトリ構造](./references/directory-layout.md): 番号付き配置、README、相互リンク
- [文体・メタデータ](./references/writing-style.md): 文体、規範語、front matter、版
- [図の選定](./references/diagram-selection.md): 図種、必要性、後付け仕様
- [差分設計](./references/variant-delta.md): 枝、ベース版、衝突、差分
- [テンプレート](./references/templates.md): 要件、アーキテクチャ、契約、ADR、運用の骨格
- [トレーサビリティ](./references/traceability.md): 要件から廃止までのID接続
- [品質チェック](./references/quality-checklist.md): 完成判定と独立レビュー
- [調査根拠](./references/research-basis.md): 標準、実務手法、調査日
