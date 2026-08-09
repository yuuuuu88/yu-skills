# Nous Subskill Catalog and Intake Policy

更新: 2026-08-04

Subskill は Plan / Implement / Review を置き換えるものではなく、各フェーズへ短い専門的な観点を注入する**助言パッケージ**である。実行権限、承認、Task Stateの変更権限は持たない。1フェーズ・1サイクルにつき、設定された候補から最小限の構成を選び、同じフェーズ中は厳密にpinする。Reviewが `needs_plan` を返した場合は次サイクルで再選定する。

## 分類

| 分類 | 例 | Nousでの扱い |
|---|---|---|
| 公式プロバイダ形式 | OpenAI Codex `SKILL.md` / Plugins、Anthropic Agent Skills、Google Gemini CLI skills/extensions | 形式・メタデータ・ライセンスを調査。採用時はimmutable revisionをpinし、プロバイダ固有の実行はadapter境界の外へ置く |
| 外部能力プロトコル | MCP、A2A | Subskillとして同梱せず、外部能力境界として扱う。tool call、network、認証、承認、出力をNousポリシーで再検証 |
| 個人・コミュニティ配布 | skills.sh、Awesome Copilot、obra/superpowers、Matt Pocock、Softaworks、Trail of Bits、Vercel、Supabaseなど | **発見の入口**。自動importしない。ライセンス、commit SHA、依存、script、hook、MCP設定、secret/network要求を検査してから候補化 |
| 言語・文書レビュー | 文化庁、公用文・敬語、JTCA/JTF、textlint/prh、個人の校正skill | 公式原則→決定的lint→文脈レビューの三層。本文の大量転載ではなく、要約、出典URL、適用範囲、ライセンスだけを保持 |
| ドメイン別実装・検証 | DB/RLS、security、accessibility、performance、migration、テスト | 読み取り専用 guidance を内蔵可能。外部scannerやCLIは別途 managed action として明示的に許可 |

## 現在の内蔵セット

内蔵providerは **58個**（既存の概念に基づくものを含む）。このうち今回追加した **35個** は、公開仕様・実践知を出典として記録しつつ、原文・script・MCP serverを持ち込まず、Nousのphase契約向けに独立再設計したguidanceである。`originator` は全て `Nous independent synthesis`、manifestの`content_hash`と固定された`guidance_hash`、`provenance`（参照のみ・revision表現・確認日・変換方法）をRegistryで検証する。

### Plan

- requirements / acceptance / examples / use-cases / slicing / quality metrics
- discovery / pre-mortem risk / ADR / outcome slicing / hypothesis experiments
- Agent Skills capability contract / MCP tool boundary / accessibility acceptance / observability evidence
- 既存: Wiegers, Adzic, Cockburn, INVEST, Gilb

### Implement

- boundaries / domain model / modularity / refactoring / incremental testing
- root-cause debugging / contract testing / property testing / threat modeling
- expand-contract migration / performance budget / small reversible changes / provider boundary / provider-neutral observability
- 既存: Clean Architecture, DDD, Parnas, Fowler, TDD

### Review

- coupling / inspection / safety / security / mutation testing
- `review.prose.japanese`: 目的、読者、用語、読みやすさ、敬語、事実保全
- `review.prose.terminology`: 用語集、表記ゆれ、固有名詞、単位
- `review.prose.technical-japanese`: 前提、手順、期待結果、例外、技術内容保全
- requirements traceability / release rollback / data minimization / accessibility / observability / behavioral test evidence
- plain Japanese / factual preservation / contextual keigo / deterministic lint
- Agent Skills supply chain / MCP tool safety

### Knowledge

- evidence grade / systematic review / PRISMA / lateral reading / SIFT
- primary-first / contradiction search / claim-evidence matrix / reproducible research / untrusted knowledge boundary

Knowledge guidanceはregistryに内蔵するが、現行の自動Nous LoopはPlan / Implement / Reviewの3フェーズを実行する。Knowledgeを独立フェーズとして選択する場合は、runtime・config・artifact contractを先に拡張する。

`review.prose.textlint-ja` のような決定的チェック adapter は、textlintやprhの実行環境・ルールセット・ライセンスを確認した後に追加する。組み込みguidanceだけで外部コマンドを暗黙実行しない。

## 採用ライフサイクル

```text
discover metadata
  -> inspect complete tree and license
  -> pin tag/commit/tree hash
  -> scan scripts/hooks/dependencies/MCP/network/secret behavior
  -> rewrite or vendor only permitted guidance
  -> SubskillRegistry validation and content hash
  -> phase/cycle selection
  -> policy grant for any external action
  -> validate output and record evidence
```

## Persisted selection migration

Earlier v1 selection records are accepted only as legacy input at their
task/cycle/phase path. On the next active engine checkpoint, Nous deliberately
re-selects from the current registry and atomically replaces the v1 record with
a v2 record. This is a re-selection, not an implicit claim that v1 contained
enough information to prove its input snapshot or guidance hash. Child-loop v1
records fail closed and require parent reconciliation before retry.

### 信頼ポリシー

1. 浮動する `main`、人気順、download数、マーケットプレイス掲載だけでは採用しない。
2. providerごと、skillごとのライセンスを確認する。リポジトリ全体のライセンスを個別ファイルへ推測適用しない。
3. prompt、script、hook、MCP server、installer、self-update、credentialアクセス、外部network送信を一括で持ち込まない。
4. Anthropicのdocument skillsなど、source-available / proprietaryなものは内容や派生物をvendorしない。許可されたprovider APIの外部実行adapterだけを検討する。
5. コミュニティskillは**候補**として記録し、内蔵する場合は独自にレビューした短いguidanceへ再設計する。原文のコピー、隠れた指示、秘密情報要求は拒否する。
6. 外部skillの更新は新しいartifactとして、差分・ライセンス・hash・スキャン結果を再確認する。
7. 公開概念を参照した内蔵guidanceは、原文を隠すためではなく、著作権・供給網・実行権限を分離するために独立再設計する。出典URLは provenance として残す。
8. Built-in guidanceには固定hashを必須とし、配布後の本文差し替えを選択前に検出する。外部providerは後方互換のためhashなしを許容するが、配布へ昇格するときは固定hashとprovenanceを追加する。

## 参照候補

### 公式・標準

- OpenAI Codex sample skill: https://github.com/openai/codex/tree/main/codex-rs/skills/src/assets/samples/skill-creator
- OpenAI Plugins: https://github.com/openai/plugins
- Anthropic Agent Skills: https://github.com/anthropics/skills
- Google skills: https://github.com/google/skills
- Gemini CLI extensions: https://github.com/google-gemini/gemini-cli/blob/main/docs/extensions/index.md
- Agent Skills specification: https://agentskills.io/specification
- MCP Registry: https://modelcontextprotocol.io/registry/about

### コミュニティ・個人（discover only）

- obra/superpowers: https://github.com/obra/superpowers
- Matt Pocock skills: https://github.com/mattpocock/skills
- Softaworks agent-toolkit: https://github.com/softaworks/agent-toolkit
- Trail of Bits skills: https://github.com/trailofbits/skills
- Vercel agent-skills: https://github.com/vercel-labs/agent-skills
- Supabase agent-skills: https://github.com/supabase/agent-skills
- skills.sh: https://www.skills.sh/
- GitHub Awesome Copilot: https://github.com/github/awesome-copilot

### 日本語レビュー

- 文化庁「公用文作成の考え方」: https://www.bunka.go.jp/seisaku/kokugo_nihongo/kokugo_shisaku/94336802.html
- 文化庁「敬語の指針」: https://www.bunka.go.jp/seisaku/bunkashingikai/kokugo/hokoku/pdf/keigo_tosin.pdf
- JTCA 日本語スタイルガイド: https://jtca.org/learn-tc/publication/guide_jsg/
- JTF日本語スタイルチェッカー: https://www.jtf.jp/jp/style_guide/jtfstylechecker.html
- textlint: https://github.com/textlint/textlint
- textlint technical writing preset: https://github.com/textlint-ja/textlint-rule-preset-ja-technical-writing
- prh: https://github.com/prh/prh

この一覧は推奨ランキングではない。導入前に対象時点のライセンス、revision、実行面、品質を再確認する。
