# Boundary Contract Testing

このガイダンスは公開された考え方を、Nousの安全なPlan/Implement/Review/Knowledge境界向けに独立して再設計したもの。外部本文の転載や外部skillの実行は行わない。

## 運用

境界をまたぐ入出力の約束を、利用側の期待と提供側の実装検証に分けてテストする。

## 判定規則

契約を実装詳細ではなく観測可能なデータ・エラー・互換性として固定し、破壊的変更を先に検出する。

## 出典

- Pact: https://pactflow.io/how-pact-works/
