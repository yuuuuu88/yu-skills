# Provider-Neutral Observability

このガイダンスは公開仕様・実践知をNous向けに独立再設計したもの。外部skillの本文、script、server、installerは同梱しない。

## 運用

provider固有APIに閉じず、相関ID、状態遷移、失敗理由、計測単位を境界で記録する。

## 判定規則

本文や秘密を不用意にログへ出さず、診断に必要な識別子とhashを優先する。

## 出典

- OpenTelemetry: https://opentelemetry.io/docs/concepts/semantic-conventions/
