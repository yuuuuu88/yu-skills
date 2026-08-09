---
id: ADR-0001
type: adr
title: Keep Nous Pilot separate from agent-nous
status: accepted
source_of_truth: true
created: 2026-08-09
updated: 2026-08-09
related: [ARCH-001, ARCH-002, REQ-001]
managed_by: repository-bootstrap
---
# ADR-0001: Keep Nous Pilot separate from agent-nous

## Context

`agent-nous`は汎用のdurable Plan → Implement → Review runtimeであり、Nous Pilotはrepository、Git、verification、CI/PR、development metricsを扱う上位ドメインである。両者を同じプロジェクトとして拡張すると、汎用runtimeと開発固有の責務が結合する。

## Decision

Nous Pilotを`agent-nous`の兄弟ディレクトリ・別プロジェクトとして構築し、runtimeは契約付きAdapter経由で利用する。

## Consequences

- runtimeの安全境界とPilotの開発機能を独立して検証できる
- Pilotがagent-nousのTask stateを直接編集しない
- contract versionとAdapterの維持コストが発生する
- agent-nousの更新をPilotの互換性テストで検知する

## Alternatives

- agent-nousへ全機能を追加する: 初期実装は短いが責務とリリースの境界が崩れる
- 完全に別runtimeを作る: 重複と耐久性リスクが大きい
