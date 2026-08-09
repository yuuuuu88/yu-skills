---
id: CROSS-CUTTING-INDEX-001
type: documentation-index
title: Cross-cutting design
status: accepted
source_of_truth: true
created: 2026-08-09
updated: 2026-08-09
related: [SEC-001, OBS-001]
managed_by: repository-bootstrap
---
# Cross-cutting design

## Responsibility

Security、認証・認可、Policy、エラー、監査、observability、resource、Human gateなど複数機能に共通する設計。

## Documents

- [security.md](./security.md): trust boundary、secret、network、filesystem、外部副作用
- [observability.md](./observability.md): event、trace、metrics、diagnostics

## Rules

Cross-cutting documents define stable constraints. Keep implementation checkpoints in `docs/plans/`.
