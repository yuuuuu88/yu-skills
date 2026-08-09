# Root-Cause Debugging

このガイダンスは公開された考え方を、Nousの安全なPlan/Implement/Review/Knowledge境界向けに独立して再設計したもの。外部本文の転載や外部skillの実行は行わない。

## 運用

症状を直接隠さず、再現、観測、仮説、最小修正、再発防止テストの順に原因を絞る。

## 判定規則

ログや推測だけで原因を断定せず、反証可能な観測を追加して一つずつ仮説を潰す。

## 出典

- Google SRE: https://sre.google/sre-book/monitoring-distributed-systems/
