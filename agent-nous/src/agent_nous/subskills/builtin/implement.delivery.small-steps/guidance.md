# Small Reversible Changes

このガイダンスは公開された考え方を、Nousの安全なPlan/Implement/Review/Knowledge境界向けに独立して再設計したもの。外部本文の転載や外部skillの実行は行わない。

## 運用

変更を小さく統合可能な単位に分け、途中状態でも壊れないfeature flag・互換層・段階投入を選ぶ。

## 判定規則

大きな変更を一度に通すのではなく、各commitまたはcheckpointでビルド・テスト・復旧可能性を確認する。

## 出典

- Trunk Based Development: https://trunkbaseddevelopment.com/
