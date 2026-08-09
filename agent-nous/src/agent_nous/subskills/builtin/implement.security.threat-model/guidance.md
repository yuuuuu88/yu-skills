# Threat Modeling

このガイダンスは公開された考え方を、Nousの安全なPlan/Implement/Review/Knowledge境界向けに独立して再設計したもの。外部本文の転載や外部skillの実行は行わない。

## 運用

変更する資産、境界、信頼主体、攻撃経路、影響、軽減策を図または表にし、重要な経路から実装する。

## 判定規則

秘密・権限・外部入力・テナント境界を必ず列挙し、軽減策をReviewで再確認できるテストへ結び付ける。

## 出典

- OWASP: https://owasp.org/www-community/Threat_Modeling
