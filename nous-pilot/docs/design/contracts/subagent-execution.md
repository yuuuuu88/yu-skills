---
id: CT-002
type: contract
title: Subagent-first execution contract
status: accepted
source_of_truth: true
created: 2026-08-09
updated: 2026-08-09
related: [REQ-001, ARCH-001, DES-001, SEC-001, ADR-0002]
managed_by: repository-bootstrap
---
# Subagent-first execution contract

## Purpose

サブエージェントを「利用可能な補助機能」ではなく、開発工程を分離する必須実行境界にする。Coordinatorの単独実装、単独テスト、単独自己レビューを既定の成功経路にしない。

## Roles

| Role | Responsibility | Independence |
|---|---|---|
| Research Agent | repository、Web、docs、testsからKnowledgeを収集 | Research wave内でsourceや観点を分ける |
| Plan Agent | 要求、根拠、依存、scope、受入条件、検証を構造化 | Implement結果を前提にしない |
| Implement Agent | 宣言されたwrite scopeで変更とテストを作る | Test/Reviewと分離 |
| Test Agent | 実行可能な検証を実施しevidenceを収集 | Implementの自己申告を信用しない |
| Review Agent | 受入条件、差分、設計、security、testを評価 | 他Reviewerの結論を見ない |
| Repair Agent | findingsを限定scopeで修正 | Judgeの判定を上書きしない |
| Judge Agent | evidenceを統合し継続・再計画・Human gateを判断 | source変更を直接行わない |

## Required composition

### Standard mode

- Research: 1以上
- Plan: 1
- Implement: 1以上
- Test: 1
- Review: 2以上の独立視点
- Judge: 1

### Deep mode

- Research: 3以上の異なる調査観点
- Plan: 1 architect + 1 risk planner
- Implement: write scopeが独立する場合は2以上
- Test: 1 functional + 1 regression/security
- Review: 3以上の独立視点
- Judge: 1 integration judge

## Hard gates

- `subagents.required = true`が既定値
- required roleのproviderがない場合は`blocked_provider_unavailable`
- required roleの起動に失敗した場合は`blocked_agent_launch`
- independent reviewが不足した場合は`blocked_review_quorum`
- Coordinatorによるsource writeを検知した場合は`invalid_coordinator_write`
- fake provider以外のsolo fallbackは明示的なdiagnosticなしに実行しない

## Handoff isolation

Agentへ渡すものは、Child Contract、scope内Task Definition、active Knowledge、dependency output、対象snapshot、policy grantに限定する。以下は渡さない。

- 他Childのraw transcript
- superseded Plan
- 未確定のCoordinatorの思考
- 無関係なResearch note
- secret valueや不要なcredential

## Parallel execution

Research、Test、Reviewはread-onlyまたはsnapshot上で並列化できる。Implementはwrite set、reservation、worktree、integration順序が揃った場合だけ並列化する。並列化しない場合も、役割分離と別invocationの記録は維持する。

## Evidence requirements

各Agentは、結果の文章だけでなく次を返す。

- role、provider、run ID
- input artifact digest
- output artifact refs
- files read/written
- commands and exit status
- findings and severity
- assumptions and unresolved questions
- next-action recommendation

## Utilization metrics

Pilotは予定Agent数、起動数、成功数、失敗数、retry数、並列wave数、独立review数、Coordinator direct-write検知、fallback回数、phase latency、token/costを記録する。subagent利用率を品質評価から隠さない。
