# Operability and Observability

このガイダンスは公開された考え方を、Nousの安全なPlan/Implement/Review/Knowledge境界向けに独立して再設計したもの。外部本文の転載や外部skillの実行は行わない。

## 運用

障害を検知、切り分け、封じ込め、復旧できるかを、ログ・メトリクス・トレース・runbookの観点で確認する。

## 判定規則

観測データが機密を増やさないことも含め、正常系だけでなく失敗時の診断可能性を証拠化する。

## 出典

- Google SRE: https://sre.google/sre-book/monitoring-distributed-systems/
