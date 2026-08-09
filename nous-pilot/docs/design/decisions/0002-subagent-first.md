---
id: ADR-0002
type: adr
title: Make subagents a mandatory development boundary
status: accepted
source_of_truth: true
created: 2026-08-09
updated: 2026-08-09
related: [REQ-001, ARCH-001, CT-002, SEC-001]
managed_by: repository-bootstrap
---
# ADR-0002: Make subagents a mandatory development boundary

## Context

一つのAgentがResearch、Plan、Implement、Test、Reviewを続けると、自己レビュー、確認バイアス、context overload、調査不足を検知しにくい。サブエージェントを任意の補助にすると、速度優先の経路で使われなくなる。

## Decision

Pilotのdevelopment modeでは、役割別の外部subagent invocationを必須にする。Coordinatorはsource codeを直接編集しない。Standard modeは独立Reviewを最低2視点で要求し、provider unavailable時はblocking stateへ移す。

## Consequences

- 観点分離、独立検証、並列化、失敗局所化、利用実績の評価が可能になる
- 小さなタスクでもlatencyとcostが増える
- quick modeを別途定義し、その適用範囲をpolicyへ明示する必要がある
- Agent providerの起動・再開・schema検証の品質が重要になる

## Alternatives

- 同じAgentに役割を順番に依頼する: 最小コストだが独立性がない
- Agent数を固定して増やす: 観点を保証せず、過剰並列と重複を招く
- 人間Reviewだけにする: 自律性と継続性を失う
