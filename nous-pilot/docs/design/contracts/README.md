---
id: CONTRACTS-INDEX-001
type: documentation-index
title: Nous Pilot design contracts
status: accepted
source_of_truth: true
created: 2026-08-09
updated: 2026-08-09
related: [CT-001, CT-002, CT-003, CT-004]
managed_by: repository-bootstrap
---
# Nous Pilot design contracts

## Responsibility

Task、Agent、Provider、handoff、verification、integration、外部Adapterのschemaと互換性境界。

## Documents

- [control-plane.md](./control-plane.md): Root Taskとphase lifecycle
- [subagent-execution.md](./subagent-execution.md): subagent-first実行契約
- [agent-provider.md](./agent-provider.md): provider adapter契約
- [verification.md](./verification.md): validation evidence契約
- [phase2-4-contracts.md](./phase2-4-contracts.md): control-plane extension and external adapter contracts

## Rules

Contracts are versioned. A behavior change that affects inputs, outputs, identity, failure, or authorization requires a contract update and compatibility test.
