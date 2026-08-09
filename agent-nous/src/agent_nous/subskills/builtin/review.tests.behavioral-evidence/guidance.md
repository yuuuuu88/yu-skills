# Behavioral Test Evidence

このガイダンスは公開された考え方を、Nousの安全なPlan/Implement/Review/Knowledge境界向けに独立して再設計したもの。外部本文の転載や外部skillの実行は行わない。

## 運用

テストの数ではなく、利用者にとって重要な振る舞い、失敗経路、境界、回帰防止をカバーしているか確認する。

## 判定規則

実装詳細に密着した脆いテストを増やさず、失敗したときに原因が分かる検証を優先する。

## 出典

- Martin Fowler: https://martinfowler.com/articles/practical-test-pyramid.html
