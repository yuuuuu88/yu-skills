# Observability Evidence Planning

このガイダンスは公開仕様・実践知をNous向けに独立再設計したもの。外部skillの本文、script、server、installerは同梱しない。

## 運用

完了判定と障害切り分けに必要なログ、metric、trace、相関ID、secret除外を先に定義する。

## 判定規則

観測値の名前と意味を固定し、正常系・失敗系・保持期間・閲覧権限まで受入条件にする。

## 出典

- OpenTelemetry: https://opentelemetry.io/docs/concepts/semantic-conventions/
