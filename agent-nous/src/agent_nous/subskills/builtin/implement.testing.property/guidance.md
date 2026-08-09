# Property-Oriented Testing

このガイダンスは公開された考え方を、Nousの安全なPlan/Implement/Review/Knowledge境界向けに独立して再設計したもの。外部本文の転載や外部skillの実行は行わない。

## 運用

個別例だけでなく、入力集合に対して成立すべき不変条件と境界条件を定義し、縮約可能な反例を残す。

## 判定規則

ランダム性を品質の根拠にせず、seed・生成条件・反例を再実行可能な証拠として保存する。

## 出典

- Hypothesis: https://hypothesis.works/articles/what-is-property-based-testing/
