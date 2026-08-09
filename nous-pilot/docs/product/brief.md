---
id: REQ-001
type: requirements
title: Nous Pilot product brief
status: accepted
source_of_truth: true
created: 2026-08-09
updated: 2026-08-09
related: [ARCH-001, DES-001, CT-001, ADR-0001, ADR-0002]
managed_by: repository-bootstrap
---
# Product brief

## Problem

一般的なAIコーディング支援は、質問への回答や単発のパッチには強い一方、調査、要件整理、実装、テスト、独立レビュー、修正、CI対応を一つの耐久的な開発プロセスとして維持することが難しい。長い作業では、コンテキストの劣化、自己レビュー、調査不足、失敗後の再開不能、サブエージェント未使用が品質上のリスクになる。

既存の`agent-nous`は、永続Task、Knowledge、Plan → Implement → Review、再計画、Policy Gateway、Subskill、並列Child Loopの基盤を提供する。Nous Pilotはその基盤を使い、ソフトウェア開発全体を複数の専門Agentで運用する。

## Users and stakeholders

- 個人開発者: 要求やIssueから安全に実装作業を進めたい
- 小規模開発チーム: 調査・実装・レビューの記録と再現性を確保したい
- レビュー担当者: Agentの判断ではなく差分と証拠を確認したい
- 運用担当者: 停止理由、利用Agent、費用、再開地点を把握したい

## Goals

1. 公開情報とリポジトリ内情報を調査して、根拠付きのPlanを作る
2. Plan、Implement、Test、Reviewを役割分離されたサブエージェントで実行する
3. 独立レビューの指摘から再計画・修正を自動的に行う
4. worktree、Task state、handoff、validation evidenceを使って長時間作業を再開可能にする
5. ローカル変更から始め、Policy Gateを通した外部操作へ段階的に拡張する
6. サブエージェントの利用状況、並列性、品質、コストを測定可能にする

## Non-goals

- 初期スライスで自動merge、無条件のremote push、無人デプロイを行うこと
- 任意のAgentやSkillを暗黙に発見・実行すること
- Coordinatorが全工程を代替して一人で実装すること
- hidden evaluationの問題文や正解を公開リポジトリに保存すること
- すべての開発環境を最初から一つのprovider仕様に固定すること

## Functional requirements

- REQ-F-001: 自然言語要求またはIssue相当の入力をdurable Root Taskへ変換できる
- REQ-F-002: Research Agentがリポジトリ、公開Web、設計書、テストから根拠付きKnowledgeを作成できる
- REQ-F-003: Plan Agentが受入条件、非目標、依存関係、リスク、検証計画を構造化して返せる
- REQ-F-004: Implement Agentが宣言されたscopeの隔離workspaceで変更を作成できる
- REQ-F-005: Test Agentが既存テストとプロジェクト固有検証を実行し、evidenceを返せる
- REQ-F-006: Review Agentが実装者と独立した視点で差分と受入条件を評価できる
- REQ-F-007: Reviewが`needs_plan`を返した場合、旧Planを履歴として保持して次のNous Loopを開始できる
- REQ-F-008: 必須サブエージェントの起動失敗を、無断のsingle-agent fallbackではなくblocking stateとして記録できる
- REQ-F-009: CI、Issue、PRなどの外部イベントを、承認済みAdapter経由でTaskへ取り込める
- REQ-F-010: Task終了時に変更、検証証拠、レビュー判定、Agent利用状況、次の操作を確認できる

## Quality requirements

- REQ-Q-001: Task stateとphase artifactはプロセス停止後も再開に必要な情報を保持する
- REQ-Q-002: Agent間handoffはschema検証可能な構造化データであり、自由文だけに依存しない
- REQ-Q-003: 競合するwriteはworktreeまたはmanaged reservationで隔離する
- REQ-Q-004: 秘密情報、未許可のnetwork、課金、remote変更はPolicy Gatewayの外で発生しない
- REQ-Q-005: standard modeはPlan、Implement、Test、Reviewを別Agentとして記録し、Reviewを最低2視点で実行する
- REQ-Q-006: すべての受入判定は、受入条件と検証evidenceへ追跡可能である
- REQ-Q-007: 同一入力に対するProvider差、失敗、再試行、介入を評価レポートに反映できる

## Constraints and assumptions

- 初期実装は既存`agent-nous`のPython runtimeと契約を再利用する
- 初期workspaceはローカルGitを想定し、複数hostのTaskStoreはAdapter境界に置く
- サブエージェントproviderは交換可能な契約を持つ
- 公開リポジトリにはテスト用のfake providerを含め、live providerの秘密情報は含めない
- humanは実装者ではなく、目的、リスク、外部副作用、価値判断の承認者になる

## Acceptance criteria

1. 一つの要求を受け、Research → Plan → Implement → Test → Cross-reviewのartifactがTaskに残る
2. Coordinatorによる直接の製品コード編集なしで、Implement Agentが隔離workspaceに変更を作る
3. Reviewで問題を検出した場合、修正または再計画のLoopへ遷移する
4. 必須Agentが起動できない場合、理由、再開地点、必要な設定を示して停止する
5. 外部副作用なしのローカルテストで、上記の成功・失敗・再開・競合ケースを検証できる
6. 実装入口、設計の正本、運用手順、未確定事項が文書から追跡できる

## Open questions

- 最初に接続するAgent providerと、providerごとの権限・費用上限
- GitHub Issue/PR Adapterをlocal-only版の後にどの順序で追加するか
- TaskStoreを共有filesystemからPostgresへ拡張する時の運用要件
- browser、mobile、embedded、data migrationなどの検証Adapterの優先順位

## References

- [Initial slice](../design/features/initial-slice.md)
