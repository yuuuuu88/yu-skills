# 要件トレーサビリティ

要件を設計書へ移すだけでは不十分である。設計した内容がどの図・判断・テスト・リリース・運用・廃止へつながるかを、安定したIDで追跡する。

## 推奨するつながり

```text
PURPOSE / GOAL
  → NEED / STAKEHOLDER
  → REQ-F / REQ-NFR / CONSTRAINT
  → DESIGN / API / DATA / UI / OPS
  → DIAGRAM / ADR
  → TEST / ACCEPTANCE
  → RELEASE / MONITORING / RUNBOOK
  → RETIREMENT / DATA DELETION
```

## マトリクス

`docs/02-requirements/traceability-matrix.md` を正本にする。行は要件ID、列は成果物のIDと検証状態とする。

| 要件ID | 要件 | 設計 | 図 | ADR | テスト/受入 | リリース | 運用/廃止 | 状態 |
|---|---|---|---|---|---|---|---|---|
| REQ-F-001 | 主要操作を実行できる | FEAT-001, API-001 | DG-SEQ-001 | ADR-001 | TEST-001 | REL-001 | RUN-001 | covered |
| REQ-NFR-001 | 95パーセンタイルで2秒以内 | ARCH-001, PERF-001 | DG-DEP-001 | ADR-002 | PERF-001 | REL-001 | SLO-001 | partial |

`partial` や `open` には、欠落箇所、影響、次の確認、責任者を併記する。`covered` はリンクがあることではなく、検証結果が受入条件を満たしたことを意味する。

## IDのルール

- 目的: `PURPOSE-###`
- ニーズ: `NEED-###`
- 要件: `REQ-F-###`、`REQ-NFR-###`、`REQ-S-###`（セキュリティ）、`REQ-V-###`（variant）、`CON-###`
- 設計: `ARCH-###`、`FEAT-###`、`API-###`、`DATA-###`、`UI-###`、`OPS-###`
- 図: `DG-<種別>-###`
- 判断: `ADR-###`
- 検証: `TEST-###`、`AC-###`
- 枝差分: `DELTA-###`

IDは文書のファイル名や見出し変更から独立させる。同じIDを別の意味で再利用しない。

## 検査

1. 要件一覧から、設計または正当な `not-applicable` を持たない行を探す。
2. 設計書の `requirements` から、存在しない要件IDを探す。
3. `approved` 文書に未解決の `TBD`、`open`、理由のない `partial` がないか確認する。
4. 図・ADR・テストの参照先が存在し、対象要件と内容が一致するか確認する。
5. 枝の差分が共通要件・ベース版・追加テストへつながるか確認する。
6. リリース・運用・廃止の文書へ一つも到達できない要件を見つける。

要件、設計、テストのいずれかにしか存在しない仕様を黙って受け入れない。事実ならマトリクスへ追加し、不要なら `not-applicable` の判断と理由を記録する。
