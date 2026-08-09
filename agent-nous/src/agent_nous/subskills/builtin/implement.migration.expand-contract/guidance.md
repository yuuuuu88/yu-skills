# Expand-Contract Migration

このガイダンスは公開された考え方を、Nousの安全なPlan/Implement/Review/Knowledge境界向けに独立して再設計したもの。外部本文の転載や外部skillの実行は行わない。

## 運用

互換な拡張、二重読み書きまたは段階移行、切替、旧経路撤去を別段階に分ける。

## 判定規則

各段階で旧版・新版が共存でき、戻す手順とデータ整合性確認がある場合だけ移行を進める。

## 出典

- Martin Fowler: https://martinfowler.com/bliki/ParallelChange.html
