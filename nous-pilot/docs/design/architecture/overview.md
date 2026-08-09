---
id: ARCH-001
type: architecture
title: Nous Pilot system architecture overview
status: accepted
source_of_truth: true
created: 2026-08-09
updated: 2026-08-09
related: [REQ-001, DES-001, CT-001, CT-002, CT-003, SEC-001, ADR-0001, ADR-0002]
managed_by: repository-bootstrap
---
# Nous Pilot system architecture overview

## Scope and context

Nous Pilotは、自然言語要求またはIssue相当の入力を、調査可能で再開可能な開発Taskへ変換する上位制御プレーンである。実行の耐久性、Policy Gateway、Task state、Subskill選択、Nous Loopは既存`agent-nous`を内部runtime boundaryとして利用する。

Pilotが追加する責務は、開発作業に特化したintake、repository context、workspace、Agent role composition、verification、CI/PR feedback、integration判定である。

## Quality goals

- **役割分離:** Coordinator、Research、Plan、Implement、Test、Review、Judgeの責務を混ぜない
- **独立性:** ReviewとResearchは先行Agentの結論へ過度に依存しない
- **再開性:** Process、Provider、hostが変わってもTask artifactから継続できる
- **証拠性:** 受入判定を差分、テスト、レビュー、外部根拠へ結び付ける
- **安全性:** Agent出力、Repository instructions、Web本文、Providerをuntrusted boundaryとして扱う
- **拡張性:** Agent provider、verification adapter、TaskStore、Issue/PR adapterを交換できる
- **効率:** 独立調査と独立レビューは並列化し、共有writeは隔離する

## Constraints

- Coordinatorは製品コードを直接編集しない
- required subagentがない場合はsolo fallbackしない
- live network、remote Git、paid API、production操作はdefault deny
- write Childはworktree、reservation、integration strategyを要求する
- Providerの能力、timeout、retry、cost、secret boundaryを実行前に宣言する
- 外部サービスの仕様はProvider Adapterへ閉じ込め、Coreは契約だけを知る

## Solution strategy

```text
                 +-----------------------------+
Request / Issue ->| Intake + Task Control Plane  |
                 +--------------+--------------+
                                |
                    +-----------v-----------+
                    | Research Coordinator  |
                    +-----------+-----------+
                                |
                    +-----------v-----------+
                    | Nous Loop Orchestrator |
                    +--+------+------+------+--+
                       |      |      |      |
                    Plan   Implement Test  Review
                       |      |      |      |
                       +------v------+------+
                              Handoff
                                |
                    +-----------v-----------+
                    | Evidence + Judge       |
                    +------+-------------+----+
                           |             |
                    needs_plan       human gate
                           |             |
                           +--> next Loop
```

Pilotは次の層で構成する。

1. **Intake layer:** 要求、Issue、CI/PR eventをRoot Taskへ変換する
2. **Context layer:** repository map、設計正本、関連差分、検証コマンド、外部Knowledgeをsnapshot化する
3. **Composition layer:** capabilityに応じてAgent roleとSubskillを選び、Child Loop DAGを作る
4. **Execution layer:** 既存`agent-nous`のmanaged phase、policy、lease、checkpointを呼び出す
5. **Workspace layer:** Git worktree、reservation、patch、integrationを管理する
6. **Verification layer:** unit、lint、typecheck、build、API、browser、migration、securityなどのevidenceを統一する
7. **Integration layer:** Child resultを統合し、Review、CI、PR feedbackを次のPlanへ戻す
8. **Operations layer:** trace、cost、retry、stop reason、human gate、recoveryを表示・保存する

## System context

| 外部主体 | Pilotとの関係 | 初期境界 |
|---|---|---|
| Human | 目的、制約、価値判断、Red actionを承認する | CLI / artifact / gate |
| Repository | source、docs、tests、Git historyを提供する | Local Git Adapter |
| Agent provider | Research、Plan、Implement、Test、Reviewを実行する | Provider Adapter |
| Public Web | 技術、競合、仕様、論文の根拠を提供する | Read-only Research Adapter |
| CI / Issue / PR | Taskの入力と検証feedbackを提供する | Event Adapter |
| TaskStore | state、handoff、evidence、leaseを永続化する | agent-nous TaskStore boundary |

## Major building blocks

- `pilot.intake`: 入力正規化、重複、依存、priority、risk分類
- `pilot.context`: repository map、design packet、knowledge snapshot、handoff生成
- `pilot.orchestration`: Root/Child Loop、DAG、phase composition、integration
- `pilot.agents`: role別Agent providerの契約と実行
- `pilot.workspace`: worktree、branch、reservation、patch、commit候補
- `pilot.verification`: 証拠収集、テスト実行、artifact digest、quality gates
- `pilot.feedback`: CI/PR/reviewのfindingを構造化して再計画へ戻す
- `pilot.policy`: action risk、secret、network、approval、scope制約
- `pilot.observability`: event、trace、metrics、subagent utilization、cost
- `agent-nous runtime`: durable state、lease、checkpoint、policy gateway、subskill registry、Nous Loop

## Runtime scenarios

### Research-first development

1. Intakeが要求とrepository identityを固定する
2. Research Agentsが独立してコード、docs、公開情報を調べる
3. Knowledge Gateがsource、claim、freshness、conflictを検証する
4. Plan Agentが受入条件、非目標、DAG、validationを作る
5. Human gateが必要な判断だけを停止理由付きで提示する

### Subagent-first implementation

1. Planがimplementable Child Contractを作る
2. Workspace Adapterがwrite scopeを隔離する
3. Implement Agentが変更とテストを作る
4. Test Agentが実装者と別のcontextで検証する
5. Review Agentsが独立して差分とevidenceを評価する
6. Judgeが`completed`、`needs_plan`、`needs_human_input`を一つ選ぶ

### CI/PR feedback

1. CIまたはPR eventをAdapterが受け取る
2. eventをTask evidenceとfindingへ変換する
3. feedbackのscope、severity、再現性をReview Agentが確認する
4. `needs_plan`なら新しいNous Loopを作り、修正Agentへ渡す
5. remote操作は明示policyとhuman gateを通った場合だけ許可する

## Deployment view

初期は単一ホストのCLIとlocal/shared filesystem TaskStoreを想定する。Provider processと検証processはmanaged executorの境界で起動し、将来はremote worker、Postgres TaskStore、OTLP、MCP HTTP、CI/PR adapterを追加できる。

## Cross-cutting concerns

Security、secret handling、network egress、audit、observability、human gateの共通設計は[security](../cross-cutting/security.md)、[observability](../cross-cutting/observability.md)、[control-plane contract](../contracts/control-plane.md)を参照する。

## Risks and technical debt

- providerごとの実行能力と出力品質の差
- 並列writeのsemantic conflictとintegrationコスト
- browserや長時間integration testの環境依存
- public Webの鮮度、誤情報、prompt injection
- live CI/PR連携時のcredential、rate limit、課金
- Coordinatorが暗黙に実装者へ戻るfallback経路

## Glossary

- **Nous Loop:** Plan → Implement → Review → Planの耐久的反復
- **Pilot:** Nous Loopを開発制御プレーンとして運用する上位プロジェクト
- **Coordinator:** Task、Agent、Policy、証拠、判定を統合する実行主体
- **Subagent:** 一つの役割とChild Contractを実行する外部Agent worker
- **Handoff:** phase間で渡す構造化artifact
- **Evidence:** テスト、差分、ログ、source、review findingなどの検証根拠
- **Human gate:** 人間の判断・承認が必要な境界

## Related documents

- [Initial slice](../features/initial-slice.md)
- [Control-plane contract](../contracts/control-plane.md)
- [Subagent execution contract](../contracts/subagent-execution.md)
- [Verification contract](../contracts/verification.md)
- [Security](../cross-cutting/security.md)
