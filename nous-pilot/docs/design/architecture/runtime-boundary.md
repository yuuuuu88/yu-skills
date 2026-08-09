---
id: ARCH-002
type: architecture
title: Nous Pilot and agent-nous runtime boundary
status: accepted
source_of_truth: true
created: 2026-08-09
updated: 2026-08-09
related: [ARCH-001, CT-001, ADR-0001]
managed_by: repository-bootstrap
---
# Nous Pilot and agent-nous runtime boundary

## Purpose

`agent-nous`と`nous-pilot`を同一コードベースへ混ぜず、既存runtimeの耐久性とPilotの開発ドメイン知識を分離する。

## agent-nous owns

- Root/Child Task state、lease、checkpoint、event journal
- Plan → Implement → ReviewのNous Loop lifecycle
- Policy Gatewayとmanaged executor
- Knowledge、phase handoff、Subskill registry
- provider invocationのruntime evidence
- local/shared TaskStore boundary

## Nous Pilot owns

- development request / issue intake
- repository context mapとdevelopment-specific Knowledge
- Agent role compositionとdevelopment Child Contract
- Git workspace、verification、CI/PR feedback adapter
- integration result、developer-facing status、development metrics
- software-development向けのSubskill catalogとevaluation task

Phase 1の実装では、`nous_pilot.runtime.NousRuntimeClient`がagent-nous CLIの唯一の呼び出し境界となり、`PilotControlPlane`がintakeからRoot Task作成へ接続する。Task JSONをPilotが直接編集する経路は持たない。

## Boundary rules

- PilotはTask stateを直接JSON編集せず、agent-nous runtime API/CLIを使う
- runtimeはPilot固有のIssue、GitHub、browser、language toolを知らない
- Pilotの外部AdapterはPolicy Gatewayを迂回しない
- runtime provider contractを変更する場合は、双方のcontract versionを更新する
- shared artifactはschemaとdigestを持ち、raw transcriptを正本にしない

## Integration pattern

```text
Nous Pilot domain command
        |
        v
agent-nous taskctl / managed runtime adapter
        |
        +-- TaskStore
        +-- Policy Gateway
        +-- Agent provider
        +-- Checkpoint and evidence
```
