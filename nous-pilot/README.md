---
id: REPO-README-001
type: repository-index
title: Nous Pilot repository
status: accepted
source_of_truth: true
created: 2026-08-09
updated: 2026-08-09
related: [REQ-001, ARCH-001, DES-001, PLAN-001]
managed_by: repository-bootstrap
---
# Nous Pilot

調査から計画、実装、検証、独立レビュー、修正、CI・PR対応までを、複数のサブエージェントで安全に運用する自律的ソフトウェア開発オーケストレーターです。

Nous Pilotは、既存の[`agent-nous`](../agent-nous/README.md)が提供する永続的な **Nous Loop** を開発制御プレーンへ拡張する別プロジェクトです。

```text
Research → Plan → Implement → Test → Cross-review → Repair → CI/PR follow-up
                                      ↘ re-plan ↗
```

## このリポジトリの位置付け

- `agent-nous`: Durable Plan → Implement → Review runtime
- `nous-pilot`: Issue/requestから開発作業全体を運用する上位オーケストレーター
- `yu-skills`: 公開Skillとruntimeを含む親リポジトリ

## 最初に読むもの

1. [Product brief](./docs/product/brief.md)
2. [Architecture overview](./docs/design/architecture/overview.md)
3. [Initial slice](./docs/design/features/initial-slice.md)
4. [Subagent-first execution contract](./docs/design/contracts/subagent-execution.md)
5. [Bootstrap plan](./docs/plans/bootstrap-plan.md)
6. [Codex handoff](./.codex)

## 設計上の中心原則

- Coordinatorは計画・委譲・統合・判定を担当し、通常の実装を直接行わない
- 各工程は独立したAgentとして実行し、成果物を構造化されたhandoffで渡す
- ResearchとReviewは複数の独立視点を使い、結論を相互検証する
- サブエージェントが起動できない場合、黙って単独実行へ縮退せず、理由を保存して停止する
- すべての判断を、要求・差分・テスト・外部根拠・Review結果に結び付ける
- 危険な外部操作、秘密情報、課金、本番環境変更は明示的なポリシー境界の内側に置く

## 現在の実装状況

Phase 1〜4のローカル実装が完了しています。要求の正規化、agent-nous Root Task、外部subagent実行、Child Loop DAG、検証プロファイル、Issue/CI/PR/browser境界、SQLite永続化、観測、MCP policy、互換性・配布契約を含みます。設計と進捗は [docs/plans/bootstrap-plan.md](./docs/plans/bootstrap-plan.md) を正本とします。

remote Git、PR公開、deploy、live provider、認証情報、ネットワーク送信は安全側の既定値で無効です。本番接続は別途Policy・Human gate・Adapter配備が必要です。

最小実行例:

```bash
PYTHONPATH=src python3 -m nous_pilot.cli inspect .
PYTHONPATH=src python3 -m nous_pilot.cli doctor
PYTHONPATH=src python3 -m nous_pilot.cli run request.json --run-id local-001
```

設定は`nous-pilot.toml`一つで始められます。`[agents].command`にJSON stdin/stdout形式の外部Agentを指定すると、任意リポジトリ向けのPlan/Implement/Reviewへ接続できます。未指定時は安全にblockedとなり、固定デモ実装へ縮退しません。`NOUS_BINARY`や`NOUS_HOME`は環境差分がある場合だけ指定します。

詳細は [local development](./docs/operations/local-development.md) を参照してください。

対応ランタイムは Python 3.11 以上の Linux、macOS、Windows です。配布物の
build smoke check と公開・デプロイ境界は [distribution guide](./docs/operations/distribution.md)
にまとめています。
