# アイコン資産の出典と利用条件

このスキルは、**汎用概念はオープンライセンスのLucide、実在するクラウド製品は公式配布元の製品アイコン**という二層構成にする。資産の実体・SHA-256・取得元・利用条件は `assets/icons/manifest.json` を正とする。

## 同梱済みの資産

| namespace | 内容 | 出典 | 権利クラス | 用途 |
| --- | --- | --- | --- | --- |
| `generic.*` | 利用者、ブラウザ、API、サーバー、DB、キュー、ネットワーク等 | [Lucide](https://lucide.dev/) / `lucide-static@1.30.0` | ISC | ベンダーを特定しない概念 |
| `aws.*` | EC2、Lambda、API Gateway、S3、SQS、RDS、DynamoDB等 | [AWS Architecture Icons](https://aws.amazon.com/architecture/icons/) / 2026-04-30 package | official-restricted | AWS製品を実際に表す場合だけ |
| `azure.*` | App Service、API Management、SQL Database、Storage、Service Bus等 | [Azure Icons](https://learn.microsoft.com/en-us/azure/architecture/icons/) / V24 | official-restricted | Azure製品を実際に表す場合だけ |
| `gcp.*` | Compute Engine、Cloud Run、Cloud Storage、BigQuery、GKE等 | [Google Cloud product icons](https://cloud.google.com/icons/) / current core products | official-restricted | Google Cloud製品を実際に表す場合だけ |

## 選択ルール

1. `node.provider` と `node.icon` が指定された場合、`{provider}.{icon}` を最優先する。
2. providerが未指定なら `generic.{icon}` を使う。一般概念にAWS/Azure/GCPのロゴを割り当てない。
3. 見つからない場合は意味の近い汎用アイコンへフォールバックし、生成ログと `*.pptx.icons.json` に解決結果を残す。空の四角や無表示にはしない。
4. 公式製品アイコンはSVGをそのまま使う。切り抜き、反転、回転、変形、勝手な色変更をしない。
5. 製品名は公式アイコンの近くに置く。自社サービスをMicrosoft/AWS/Googleの製品アイコンで表さない。

## 利用条件上の注意

- AWS公式ページは、顧客・パートナーがAWS承認アイコンをアーキテクチャ図やプレゼンテーション等に利用できると案内している。パッケージは更新されるため、同梱版の取得日とバージョンを記録する。
- MicrosoftはAzureアイコンをアーキテクチャ図・トレーニング・文書向けに許諾しているが、Microsoft製品アイコンを自社製品の表現に使うことは禁止している。
- Google Cloudの製品アイコンは図・技術文書向けの公式資産だが、オープンソースアイコンではない。公開・顧客向け資料ではGoogleのブランドガイダンスと個別許諾要否を確認する。
- LucideのISCライセンスは `assets/licenses/lucide-1.30.0-ISC.txt` に同梱する。将来更新する場合は、バージョン固定とライセンス差分確認を行う。

## 収集コマンドの考え方

公式ZIPを丸ごと再配布せず、必要なSVGだけを選んで `providers/<provider>/` に置く。manifestに以下を必ず残す。

- 公式の案内ページと直接ダウンロードURL
- 取得日・パッケージバージョン
- `sha256`
- `rights_class` と利用可能な文脈
- `transform: preserve-artwork`

公式パッケージを更新する時は、旧版を無言で上書きせず、manifestとこの文書を同じ変更で更新する。
