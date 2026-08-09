# MCP Tool Boundary Plan

このガイダンスは公開仕様・実践知をNous向けに独立再設計したもの。外部skillの本文、script、server、installerは同梱しない。

## 運用

MCP toolをsubskillと混同せず、入力、権限、外部作用、承認、出力検証を計画上の境界として定義する。

## 判定規則

tool annotationや説明文を安全性の証明にせず、親エンジンのpolicyで再評価する。

## 出典

- Model Context Protocol: https://modelcontextprotocol.io/specification/2025-11-25/server/tools
