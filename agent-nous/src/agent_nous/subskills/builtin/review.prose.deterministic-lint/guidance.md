# Deterministic Japanese Lint Gate

このガイダンスは公開仕様・実践知をNous向けに独立再設計したもの。外部skillの本文、script、server、installerは同梱しない。

## 運用

表記ゆれ、禁止語、用語、句読点など機械的に判定できる項目を、文脈レビューより先に固定的な検査へ渡す。

## 判定規則

lintの合格を文章品質全体の証明とせず、ルールの版、設定、対象範囲、除外理由を証拠に残す。

## 出典

- textlint and prh: https://github.com/textlint/textlint
