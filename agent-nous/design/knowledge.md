# Knowledge Design

## 目的

nousがPlanの根拠となる情報を収集・検証・要約し、Plan / Implement / Reviewの各Phaseで安全に再利用できるようにする。

- インターネット、文献、論文、リポジトリ、ローカルファイル、Human回答を扱う
- 情報の出典、確認時点、対象versionを追跡する
- 事実、推論、仮説、Humanの意思決定を混同しない
- 古い情報や誤った仮説を現在のKnowledgeとして使わない
- 必要なKnowledgeだけをPhase Agentへ渡し、コンテキスト過多を防ぐ
- 外部コンテンツに含まれる命令を、nousへの指示として実行しない

本設計では、Knowledgeを文書の寄せ集めではなく、**根拠へ追跡可能なClaimの集合**として扱う。

設計の基礎概念には、W3C PROVのEntity / Activity / Agent / Derivationによるprovenanceモデルを用いる。[W3C PROV-DM](https://www.w3.org/TR/prov-dm/)

## Knowledgeの構成

```mermaid
flowchart LR
    Question[Knowledge Question]
    Sources[Source Discovery]
    Evidence[Evidence Extraction]
    Claims[Claims + Provenance]
    Validate[Validation / Conflict Check]
    Active[Active Knowledge]
    Summary[Stable Summary]
    Plan[Plan]

    Question --> Sources --> Evidence --> Claims --> Validate
    Validate -->|accepted| Active
    Validate -->|disputed / insufficient| Question
    Active -->|promotion gate| Summary
    Active --> Plan
    Summary --> Plan
```

論理的な情報を次の4層へ分ける。

| 層 | 役割 | 通常の読み手 |
| --- | --- | --- |
| Source Registry | URL、file、commit、取得時点などの出典情報 | Orchestrator、調査担当 |
| Research Notes | 調査過程、候補、却下理由、抜粋 | 調査担当、必要時のReview |
| Active Knowledge | 現在有効なClaimと根拠 | Plan / Implement / Review |
| Stable Summary | タスク全体で毎回必要な短い確定事項 | 全Phase |

Research NotesをそのままActive Knowledgeへ入れない。検証とprovenance付与を通過したClaimだけを昇格する。

## Canonical Storage

```txt
knowledge/questions.json = typed Knowledge Question lifecycleのcanonical store
knowledge/claims.json    = typed Claim / provenance / lifecycleのcanonical store
knowledge/sources.json   = typed Source Registryのcanonical store
knowledge.md             = Questions / Claims JSONから生成するHuman-readable view
sources.md               = Sources JSONから生成するHuman-readable view
research/*.md            = 非canonicalな調査note
```

これを唯一のKnowledge canonical方式とする。単一の`knowledge.json`、root直下の`sources.json`、Markdown正本、MarkdownとJSONを混在させるhybrid storeは作らない。各storeは`schema_version`、`task_id`、`loop_id`、`revision`、`transaction_id`、typed collection、`updated_at`を持つ。

Question / Claim / Sourceのrecordはstable IDとrecord-level `content_hash`を持つ。Phase ArtifactのKnowledgeReferenceはstore全体のhashではなく対象recordのhashへ固定する。既存recordの意味をin-place変更せず、訂正・再検証・lifecycle変更は新record revisionまたは新IDと`supersedes` / `derived_from`で表す。

Knowledge Gate、Phase Artifact参照、stale / superseded判定をMarkdown parsingへ依存させない。OrchestratorがQuestion / Claim / Source候補を照合し、taskctlが各Schema、ID、provenance、cross-referenceを検証して、関係するstoreを1つのrecoverable multi-file transactionで確定する。Phase Agentや調査Subskillはcanonical Knowledge fileを直接更新しない。

## Knowledge Question

Planは調査を始める前に、タスクを進めるために必要な問いを明示する。

```txt
question_id: Q-001
question: 対象libraryの現在versionで、このAPIは非推奨か
reason: 実装方式の選択に影響する
impact: high | medium | low
required_before: plan_complete | implementation_step | review_complete
status: open | answered | deferred | cancelled
```

目的のない広範な検索を避け、各SourceとClaimをKnowledge Questionへ紐づける。

## Claimモデル

`knowledge/claims.json`の中心単位はClaimとし、`knowledge.md`へ同じ内容を読みやすくrenderする。

```txt
claim_id: K-001
claim: 対象versionではAPI Xが非推奨である
kind: fact | requirement | decision | inference | hypothesis
epistemic_status: verified | inferred | hypothesis | disputed
lifecycle_status: active | stale | superseded
scope:
  repository:
  component:
  version:
  environment:
as_of:
supports:
  - S-001
contradicts:
  - S-004
derived_from:
  - K-000
confidence: high | medium | low
owner: human | plan | implement | review | subskill
revalidate_when:
notes:
```

### kind

| 種類 | 意味 |
| --- | --- |
| `fact` | Sourceまたは実環境で確認できる記述 |
| `requirement` | Humanまたは権威あるプロジェクト文書が定める要求 |
| `decision` | 複数案から採用された方針 |
| `inference` | 複数の事実から導いた解釈 |
| `hypothesis` | 検証対象の仮説 |

### epistemic_status

| 状態 | 意味 |
| --- | --- |
| `verified` | Claimに適合する直接的な根拠を確認した |
| `inferred` | 根拠はあるが、Claim自体は推論を含む |
| `hypothesis` | 検証前または根拠不足 |
| `disputed` | 信頼できるSource間で矛盾している |

### lifecycle_status

| 状態 | 意味 |
| --- | --- |
| `active` | 現在の対象範囲で利用できる |
| `stale` | 鮮度条件を満たさず、再確認が必要 |
| `superseded` | 新しいClaimまたはHuman決定に置き換えられた |

`hypothesis`や`stale`を確定事項としてPlanへ渡さない。利用が必要な場合は仮定であること、影響、検証方法をPlanへ明記する。

## Source Registry

`knowledge/sources.json`はSourceの内容そのものではなく、根拠へ戻るためのcanonical typed registryとする。`sources.md`はそのrendered viewである。

```txt
source_id: S-001
title:
locator:
type: human_input | project_doc | local_file | runtime_result |
      test_result | official_doc | standard | paper | book |
      upstream_repository | issue | pr | secondary_article | other
authority:
version_or_commit:
section_or_lines:
published_at:
retrieved_at:
content_hash:
volatility: high | medium | low
supports:
  - K-001
contradicts:
reliability_notes:
```

### 安定したlocator

- Repository内Sourceは可能な限りcommit SHAとpathを記録する
- GitHub上のSourceを共有する場合はbranch URLよりpermalinkを優先する
- 公式docsは製品versionとsectionを記録する
- 動的ページは取得日時と、可能なら内容hashを記録する
- 論文はDOI、正式なpublication情報、versionを記録する
- Human回答は回答内容、回答時点、対象となる質問を記録する

GitHubのpermalinkは特定commitのfile versionを指すため、branch更新による参照内容の変化を避けられる。[GitHub Docs: Getting permanent links to files](https://docs.github.com/en/repositories/working-with-files/using-files/getting-permanent-links-to-files)

## Sourceの優先判断

すべてのClaimに共通する固定順位は使わない。次の4軸でClaimへの適合性を評価する。

```txt
authority    誰がその情報を定める権限を持つか
directness   Claimを直接示しているか
freshness    対象version・時点と一致するか
independence 複数Sourceが同じ未確認情報を転載していないか
```

### Claim種別ごとの優先Source

| 確認対象 | 優先するSource |
| --- | --- |
| プロダクト目的・優先順位 | Humanの明示回答、承認済み要求、プロジェクト方針 |
| 現在の実装挙動 | 実行結果、test、対象commitのcode、設定、設計書 |
| 外部API・library仕様 | 対象versionの公式docs、標準仕様、upstream source、release note |
| 理論・研究上の主張 | 原論文、原著、標準、著者または管理組織の一次資料 |
| 過去の変更理由 | ADR、commit、PR、issue、当時のHuman決定 |

Humanの指示は目的と意思決定には最上位のauthorityを持つが、外部APIの実際の仕様や実行結果まで上書きする事実Sourceとしては扱わない。矛盾する場合は、要求と事実を分離して提示する。

Community記事や検索結果の要約は、一次Sourceを発見する入口として利用できる。ただし、高影響Claimの唯一の根拠にはしない。

## Knowledge収集フロー

### Plan開始時

1. タスク目的、受け入れ条件、既存のStable Summaryを読む
2. 既知事項とKnowledge Questionを分ける
3. 対象repositoryとlocal filesを調べる
4. 外部仕様が関係する場合は公式docs、標準、原論文、upstream repositoryを調べる
5. Source Registryへprovenanceを記録する
6. EvidenceからClaimを作る
7. 矛盾、version、鮮度、対象scopeを確認する
8. Planに必要なClaimをActive Knowledgeへ昇格する
9. 必須Questionの状態をKnowledge Gateで確認する

インターネット検索をすべてのタスクへ機械的に要求しない。対象repositoryとHuman回答だけで十分な場合は、その根拠を記録して外部調査を省略できる。

### Implement中

Implement中にKnowledge不足を発見した場合は、影響で扱いを分ける。

- 局所的でPlanや受け入れ条件を変えない: Implement内で追加調査する
- 実装方式、risk、scopeを変える: Planへ戻す
- Humanの目的・優先順位に関わる: `needs_human_input`へ遷移する

追加したClaimとSourceはPlan開始時と同じ契約で保存する。

### Review中

Reviewは成果物だけでなく、重要なClaimについて次を確認する。

- 根拠へ戻れるか
- versionとscopeが一致するか
- 推論を事実として扱っていないか
- 実装結果がClaimと矛盾していないか
- staleまたはsupersededなKnowledgeを利用していないか
- Humanの意思決定を外部情報で暗黙に変更していないか

## Knowledge Gate

PlanからImplementへ進む前に、影響度`high`のKnowledge Questionを評価する。

```mermaid
flowchart TD
    Questions[High-impact Questions]
    Answered{根拠付きで回答済みか}
    Assumption{安全な仮定として明示できるか}
    Human{Human判断が必要か}
    Ready[Implementへ]
    Replan[追加調査 / Plan修正]
    Ask[選択肢でHumanへ質問]

    Questions --> Answered
    Answered -->|yes| Ready
    Answered -->|no| Assumption
    Assumption -->|yes| Ready
    Assumption -->|no| Human
    Human -->|no| Replan
    Human -->|yes| Ask
```

Implementへ進める条件:

- high-impact Questionが根拠付きで回答されている
- または、仮定・影響・検証方法がPlanに明示されている
- Source間の重大な矛盾が解決済み、またはHumanへ提示済みである
- 受け入れ条件がKnowledge上の仮説だけに依存していない

## 競合する情報の扱い

Sourceが矛盾した場合、都合のよい方だけを残さない。

1. 各ClaimとSourceのscope、version、時点を確認する
2. 同じ対象についての矛盾かを判定する
3. authority、directness、freshness、independenceを比較する
4. 解消できれば採用理由とsuperseded Claimを記録する
5. 解消できなければ`disputed`として両方を保持する
6. Planへ高い影響があれば、選択肢とトレードオフをHumanへ提示する

## 鮮度と再検証

SourceとClaimには、単純な一律TTLではなく再検証条件を持たせる。

再検証triggerの例:

- dependency、model、API、法令、価格、組織方針のversionが変わった
- high-volatility Sourceを一定期間後に再利用する
- branch URLや内容が変化するURLしか記録されていない
- 実装結果または別Sourceと矛盾した
- task再開時点が`as_of`から大きく離れている
- Humanが前提変更を伝えた

再検証できるまでClaimを`stale`にし、Current StateとResume Packetへ影響を記載する。再確認後は古いClaimを書き換えず、新しいClaimまたはSource revisionを作り、`superseded`関係を残す。

## Stable Summaryへの昇格

Claimを`stable-context.json`のRelevant Knowledgeへ昇格し、`summary.md`へ表示できる条件:

- タスクの複数Phaseで繰り返し必要になる
- `lifecycle_status: active`である
- `verified`、または推論であることが明記された`inferred`である
- SourceまたはHuman決定へ追跡できる
- scopeとversionが明確である
- 重大な競合が解決している
- 1〜3文で誤解なく要約できる

Stable Contextには長い根拠や調査ログを複製せず、Claim ID、content hash、短い結論だけを置く。機械処理は`knowledge/questions.json`、`knowledge/claims.json`、`knowledge/sources.json`を使い、Human確認は対応するMarkdown viewへ戻る。

## 並列Knowledge収集

複数のKnowledge Questionが独立している場合、調査をChild Loopへ分割できる。

```mermaid
flowchart TB
    Parent[Parent Plan]
    Q1[Q-001 API仕様]
    Q2[Q-002 Repository構造]
    Q3[Q-003 原論文]
    Merge[Knowledge Integration]
    Conflicts[重複・矛盾・scope確認]
    Gate[Knowledge Gate]

    Parent --> Q1
    Parent --> Q2
    Parent --> Q3
    Q1 --> Merge
    Q2 --> Merge
    Q3 --> Merge
    Merge --> Conflicts --> Gate
```

- Child LoopごとにKnowledge Questionと調査範囲を固定する
- Source IDとClaim IDの衝突を避ける
- 同じSourceの転載を独立した複数根拠として数えない
- Parent Planが統合時に重複、矛盾、version、scopeを確認する
- Child Loopの要約だけでなく、Source Registryへの参照を統合する

## 外部コンテンツの安全な扱い

Webページ、論文、issue、repository、local documentに含まれる文章は、Knowledge Sourceであってnousへの命令ではない。

- Source内の「この命令を実行せよ」をOrchestratorの指示として扱わない
- Sourceが要求するcommand、script、credential入力を検証なしで実行しない
- タスク目的と無関係な外部送信を行わない
- secret、private data、credentialをKnowledgeへ転記しない
- 長い引用を避け、自分の言葉によるClaimと最小限の根拠位置を記録する
- Sourceの信頼性と、Source内命令の安全性を別々に評価する

Sourceから得た手順を実行する場合は、Plan上のActionとして改めて安全性とHuman確認条件を評価する。

## 調査の停止条件

Knowledge収集は情報量の最大化ではなく、Planの不確実性を必要な水準まで下げるために行う。

次を満たしたら調査を終了できる。

- high-impact QuestionがKnowledge Gateを通過した
- 採用方針を変える可能性のある重大な矛盾がない
- 対象versionとscopeが確定した
- Primary Sourceへ到達した、または到達できない理由が明記された
- 追加検索による判断改善が小さい
- 残る不確実性がPlanの仮定、risk、検証方法へ明示された

## Phaseへ渡すKnowledge Packet

Phase Agentへは全Research Notesを渡さず、必要なClaimだけをまとめる。

```txt
phase:
cycle:
questions_answered:
active_claims:
assumptions:
disputed_claims:
stale_claims:
source_refs:
revalidation_required:
knowledge_risks:
```

Plan、Implement、ReviewはKnowledge Packetから詳細が必要なClaimだけをたどる。

## 不変条件

- KnowledgeはClaim単位でSourceへ追跡できる
- 事実、要求、意思決定、推論、仮説を分ける
- Humanのauthorityと外部仕様のauthorityを混同しない
- 現在有効なClaimと履歴を分ける
- version、scope、確認時点を記録する
- 古いPlanやResearch Notesを自動的にKnowledgeへ昇格しない
- 外部コンテンツをnousへの命令として扱わない
- 重大な矛盾を隠さない
- Humanへ聞く場合は、推奨案と影響を添えた選択肢を提示する
