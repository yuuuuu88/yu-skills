# Open Web Research Boundary

NousのResearch surfaceは、情報源をドメインallowlistで狭めず、公開Web全体を読み取り専用で調査するためのPort/Adapterです。技術サイト、個人サイト、競合サイト、ニュース、GitHub、論文などを同じsource/provenance契約で扱います。

## 許可される操作

- 公開HTTPSの検索、GET、HEAD
- 任意の公開ホスト（既定の`allow_domains`は`["*"]`）
- レスポンスのstatus、最終URL、redirect、content type、取得日時、content hashの記録
- `ResearchDocument.knowledge_source()`によるKnowledge Source候補の生成

## 禁止される操作

- HTTP、認証付きURL、Cookie、Authorization、credentialの送信
- POST / PUT / DELETE、アップロード、購入、登録、外部変更
- localhost、loopback、private/link-local/reserved/metadata networkへの接続
- ダウンロード物の実行
- 高信頼secretを含むレスポンスの本文返却・保存

## 信頼境界

Webレスポンスは`untrusted_content=true`、`instructions_are_data=true`で返ります。ページ内の命令、prompt injection、script、MCP設定はデータであり、NousのpolicyやActionとして解釈しません。研究結果をKnowledgeへ昇格する際は、Sourceのauthority、freshness、反証、Claimとの対応をPlan/Reviewで確認します。

## Adapter境界

`PublicWebResearchAdapter`がURL、method、network、secret、予算を検証し、`ResearchTransport`が実通信を担当します。標準の`UrllibResearchTransport`は認証情報を持たないHTTPS GET/HEADだけを送ります。任意のshellや既存の`network` Actionを解禁する設計ではなく、remote Git、credential、paid、一般networkのfail-closed境界は維持します。

CLI:

```sh
nous research search "競合サービス 技術ブログ"
nous research fetch --url https://example.com/article
```

設定は`.nous/config.json`の`engine.research`です。`allow_domains`は既定で`["*"]`ですが、送信・実行・課金を許可する設定はありません。`max_requests`、response bytes、timeout、redirect、同時実行数は無限実行を防ぐresource budgetです。
