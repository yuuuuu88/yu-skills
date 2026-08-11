# nous Design Docs

このディレクトリは、nousの設計書を置く場所です。

設計書には現在有効な設計だけを書きます。TODO、進捗、完了履歴は書きません。文書運用ルールは [../DOCS_POLICY.md](../DOCS_POLICY.md) を参照してください。

## 設計書一覧

- [Architecture Design](./architecture.md)
  - nous全体の構成、Orchestrator、3工程、Subskill Registryの関係。Mermaid図あり。
- [Autonomous Loop Design](./autonomous-loop.md)
  - `plan → implement → review → plan...` を人間の手をできるだけ挟まずに回すための状態遷移設計。
- [Phase Handoff Design](./phase-handoff.md)
  - 各工程が「前工程が何をしたか」「現在どういう状態か」「次工程に何を渡すべきか」を理解するための引き継ぎ契約。
- [Subskill Registry Design](./subskill-architecture.md)
  - Hybrid Selection、Capability Catalog、Manifest、入出力契約、composition、lifecycleによる疎結合設計。Mermaid図あり。
- [Parallel Loop Design](./parallel-loop.md)
  - DAG、Child Loop、subagent、Git worktree、直列fallback、Integration Reviewによるadaptive並列設計。Mermaid図あり。
- [Task State and Recovery Design](./task-state.md)
  - Task/Child Loopの状態、checkpoint、event journal、pause、予期しない終了からの再開設計。Mermaid図あり。
- [Knowledge Design](./knowledge.md)
  - Source、Claim、provenance、鮮度、競合、Knowledge Gate、並列調査の設計。Mermaid図あり。
- [Task Files Design](./task-files.md)
  - JSON + MarkdownによるTask Registry、状態、cycle、Child Loop、checkpoint、再開fileの配置設計。Mermaid図あり。
- [Human Input Policy](./human-input-policy.md)
  - バランス型の自律実行境界、risk分類、選択肢形式、事前承認、並列Loopの停止範囲を定義。Mermaid図あり。
- [Installable Skill Design](./installable-skill.md)
  - `$nous`のtrigger、package境界、progressive disclosure、resource、validation、forward test設計。Mermaid図あり。
- [Task Template Design](./task-templates.md)
  - 最小・条件付き生成、Markdown base/overlay、JSON Schema、generated view、versioningの設計。Mermaid図あり。
- [Task Operations Design](./task-operations.md)
  - create、status、pause、resume、Git commit/push、retention、archive、cleanup、別machine復旧の運用設計。Mermaid図あり。
- [taskctl Internal Utility Design](./taskctl.md)
  - Python、単一entrypoint＋subcommand、JSON protocol、recoverable multi-file transaction、lock、recovery、test設計。Mermaid図あり。
- [Cross-design Contract Map](./contract-map.md)
  - canonical authority、共通語彙、状態軸、保存先、component境界、validation matrixを定義。
- [Implementation Sequence Design](./implementation-sequence.md)
  - Skill scaffoldからdurability、sequential loop、parallel、Subskill、hardeningまでの実装順序。Mermaid図あり。
- [Phase Artifact Contract](./phase-artifacts.md)
  - Plan / Implement / Reviewのtyped canonical artifact、conditional validation、Markdown viewとの境界を定義。
- [Action Policy Design](./action-policy.md)
  - Action type、risk、approval、pre/post probe、reconcile、secret・outbound・audit policyを定義。
- [Policy Gateway Design](./policy-gateway.md)
  - Subskill・tool・filesystem・network・write scopeのruntime enforcementを定義。
- [Evaluation Design](./evaluation.md)
  - SkillとAgent workflowのeval manifest、隔離fixture、判定、退行検出を定義。
- [Distribution and Repository Hygiene](./distribution.md)
  - 配布対象、ローカル設定、Task/Request runtime、リリース前チェックを定義。
- [Validation Sandbox Design](./validation-sandbox.md)
  - disposable validation workspace、side effect、allowlist、postflight evidenceを定義。
- [Provider Error and Retry Design](./provider-errors.md)
  - provider error category、redaction、retry、Review classification、no-progress停止を定義。

## 現行の追加設計

- [Nous Loop](./autonomous-loop.md): Plan → Implement → Review の反復と完了条件
- [Subskill catalog](./subskill-catalog.md): 内蔵・外部候補・導入ポリシー

- [Open Web Research Boundary](./web-research.md)
