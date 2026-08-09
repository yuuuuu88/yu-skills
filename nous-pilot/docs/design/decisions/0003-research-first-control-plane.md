---
id: ADR-0003
type: adr
title: Make research-first development the initial control-plane slice
status: accepted
source_of_truth: true
created: 2026-08-09
updated: 2026-08-09
related: [REQ-001, ARCH-001, DES-001]
managed_by: repository-bootstrap
---
# ADR-0003: Make research-first development the initial control-plane slice

## Context

開発作業の品質は、実装前に既存コード、設計、テスト、外部仕様、制約を理解できるかに左右される。調査を省略して実装へ進むと、不要な変更、古い前提、既存機能との衝突を招く。

## Decision

Pilotの最初のvertical sliceは、要求を受けたResearch AgentがKnowledgeとprovenanceを作り、Plan Agentがそれを使って実装契約を作る経路にする。実装はlocal-only、検証付き、外部副作用なしで開始する。

## Consequences

- 調査不足をPlan開始前に検出できる
- 外部情報の取得とprompt injection対策が初期設計に入る
- 最初の実装までのlatencyが増える
- Research sourceの鮮度、競合、品質graderが必要になる
