# Provider Boundary Implementation

このガイダンスは公開仕様・実践知をNous向けに独立再設計したもの。外部skillの本文、script、server、installerは同梱しない。

## 運用

外部AI、tool、storage、authなどを交換可能なinterfaceの後ろに置き、実装詳細がphase契約へ漏れないようにする。

## 判定規則

providerの切替に必要な入力、出力、error、usage、timeout、policyを明示的なadapter契約にする。

## 出典

- Agent Skills specification: https://agentskills.io/specification
