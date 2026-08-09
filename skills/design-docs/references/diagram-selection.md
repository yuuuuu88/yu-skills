# 図の選定と後付け運用

図は装飾ではなく、文章より短く正確に関係・順序・境界・状態を伝えるために使う。図が一つの問いに答えない場合、図を分割するか作らない。

## 図の選定表

| 図 | 答える問い | いつ使うか | 使わない/対象外 | 最小要素 |
|---|---|---|---|---|
| C4 System Context | システムは誰・何と接続するか | 利用者または外部システムがある時 | 外部境界のない小さなライブラリ | 対象システム、利用者、外部システム、関係ラベル、境界 |
| C4 Container | アプリ、サービス、データストアの責務は何か | Web、API、DB、外部サービスを含む時 | 単一プロセスで構成要素が1つの時 | コンテナ、責務、技術、依存、通信方式 |
| C4 Component | 一つのコンテナをどう分割するか | コンテナ内部の責務や依存が複雑な時 | 小さなコンテナ、またはコード構造と重複する時 | コンポーネント、責務、インターフェース、境界 |
| C4 Code | コードの詳細構造は何か | 自動生成やコードレビューに価値がある時 | 手書きでコードと二重管理になる時 | クラス/モジュール、関係、生成元、対象版 |
| System Landscape | 複数システム全体の関係は何か | 複数プロダクト、組織、顧客環境がある時 | 単一プロダクトでContextに収まる時 | システム、組織、主要連携、責任境界 |
| UML Dynamic / Sequence | 重要な処理で誰がどの順に動くか | API、非同期、外部連携、障害時の順序が重要な時 | 単純な1往復を文章で誤解なく説明できる時 | lifeline、message、条件、失敗、timeout、同期/非同期 |
| UML State | 対象はどの状態を経るか | 状態、イベント、禁止遷移がある時 | 状態を保持しない処理、状態が1つだけの時 | 状態、遷移、イベント、ガード、初期/終端 |
| ER | データの関係、制約、多重度は何か | 永続DBと複数entityがある時 | 永続データがない、または単純な1構造体だけの時 | entity/table、PK、FK、関係、多重度、制約 |
| DFD / Data Flow / Trust Boundary | データはどこへ流れ、境界はどこか | 機密・個人情報・外部送信・変換がある時 | データが1境界内で完結しリスクもない時 | actor、process、store、flow、分類、trust boundary |
| Deployment | どの環境・ノードへ配置するか | 本番、クラウド、複数環境、専用構成がある時 | ローカル単一プロセスで配置差分がない時 | environment、node、runtime、network、artifact、secret boundary |
| UI / Screen Flow | 利用者はどの画面をどう移動するか | 複数画面、権限分岐、エラー導線がある時 | UIがない、または画面が1つで遷移がない時 | screen、action、condition、entry/exit、認証状態、エラー |
| Business Flow / BPMN | 人・部署・システムの業務手順は何か | 承認、引き継ぎ、業務分岐がある時 | 業務役割を持たない技術処理 | participant/lane、task、gateway、event、終了条件 |
| Domain Model | 業務上の概念と関係は何か | 業務ルールや概念が複雑な時 | ER図や用語集で十分な時 | concept、attribute、relation、rule、境界 |
| Threat Model | 何が攻撃され、どう緩和するか | 攻撃面、認証、権限、機密データがある時 | 外部公開、認証、機密データがなく、対象外判断を記録した時 | asset、actor、process、boundary、threat、control |
| Migration / Retirement Flow | 移行・保存・削除の順序は何か | 乗換え、廃止、保持・削除、切戻しがある時 | 移行・廃止・削除を行わない時 | source、target、phase、retention、rollback、完了条件 |

C4公式はContext、Container、Component、Codeの階層とLandscape、Dynamic、Deploymentの補助図を示すが、全階層を作る必要はなく、価値のある図だけ選ぶとしている。UMLやBPMNも全種類を作るのではなく、関係者の問いに対応する図だけを選ぶ。

図の後付け順序は次の通りである。

```text
placeholder → spec → editable source → rendered → review
```

## 図候補レコード

図を後で載せる場合は、本文に図ID付きプレースホルダーを置き、`docs/13-diagrams/README.md` または `docs/13-diagrams/specs/<diagram-id>.md` に次を残す。

```yaml
diagram_id: DG-C4-001
document_path: docs/04-architecture/architecture-description.md
type: c4-system-context
status: planned
source_of_truth: true
doc_version: 0.1.0
product_version: 1.0.0
created: 2026-08-09
updated: 2026-08-09
purpose: 利用者、対象システム、外部サービスの境界を示す
audience: [product, engineering, operations]
scope: 本番の論理システム境界
required_elements: [利用者, Webアプリ, 認証基盤, 外部サービス]
required_relationships: [ログイン, 主要操作, 外部連携]
related_requirements: [REQ-F-001, REQ-S-001]
related_designs: [ARCH-001]
related_adrs: [ADR-001]
related_tests: []
legend: 要素種別と境界線の意味を本文で定義する
source_file: docs/13-diagrams/sources/DG-C4-001.mmd
rendered_file: docs/13-diagrams/rendered/DG-C4-001.svg
alt_text: 利用者と外部サービスを含む対象システムの境界図
update_trigger: 認証または外部連携の境界が変わったとき
verification_date: null
```

図にはタイトル、目的、対象範囲、凡例、要素種別、関係の向きとラベル、関連ID、alt textを付ける。機密情報や実データは図の例に入れない。

## 後付けの順序

1. `planned` の図ID付きプレースホルダーを設計書へ置く。
2. 図仕様を確定する。
3. Mermaid、PlantUML、Structurizr、draw.io XML等の編集可能なソースを作る。
4. SVG/PNG等をレンダリングする。画像だけを正本にしない。
5. 本文のプレースホルダーを生成物への参照に置き換える。
6. 図と本文の要素・関係・関連IDを照合する。
7. `in-review → approved` とし、最終検証日を更新する。

図を作成しない理由も `not-applicable` または図インベントリに残す。例: 「単一CLIで外部連携がなく、システム境界図よりREADMEの処理説明の方が誤解が少ない」。

## 参考資料

- [C4 Model: Diagrams](https://c4model.com/diagrams) — Context、Container、Component、Codeと補助図の使い分け。調査日: 2026-08-09
- [C4 Model: Notation](https://c4model.com/diagrams/notation) — 要素の責務、技術、関係、凡例。調査日: 2026-08-09
- [OMG UML](https://www.omg.org/spec/UML/) — 構造、振る舞い、相互作用モデルの候補。調査日: 2026-08-09
- [OMG BPMN 2.0.2](https://www.omg.org/spec/BPMN/2.0.2/) — 業務プロセスの標準記法。調査日: 2026-08-09
- [OWASP Threat Modeling Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Threat_Modeling_Cheat_Sheet.html) — 分解、脅威識別、緩和、検証の反復。調査日: 2026-08-09
