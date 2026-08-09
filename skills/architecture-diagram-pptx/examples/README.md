# サンプル図

このディレクトリには、`architecture-diagram-pptx` Skillの使い方を示す、架空の日本語構成図を置く。すべてのPPTXは、draw.ioの一貫したゾーン・アイコン・直交経路・凡例の考え方を参考にし、ノードのSVGアイコン・境界・テキスト・line shapeを個別のPowerPointオブジェクトとして生成している。アイコンは四角の中の小さな飾りではなく、ノードの主役である。

## サンプル一覧

| サンプル | 図の問い | 主な読者 | 仕様JSON |
|---|---|---|---|
| コンテナ構成 | 利用者の操作がアプリとデータストアをどう通るか | 開発・運用担当者 | `specs/container-overview-ja.json` |
| ネットワーク／セキュリティ | 主経路がどのゾーンと認証境界を通るか | 情シス・セキュリティ担当者 | `specs/network-security-ja.json` |
| データフロー | 申請データがどこで検証・保存・監査されるか | 業務・開発担当者 | `specs/data-flow-ja.json` |
| 配置 | 環境・ゾーンごとにサービスをどこへ配置するか | クラウド・運用担当者 | `specs/deployment-ja.json` |
| 公式アイコン比較 | AWS / Azure / Google Cloudの実在製品を公式アイコンで比較する | クラウド設計担当者 | `specs/provider-icon-showcase.json` |
| AWS全体構成（例） | 入口から非同期処理・データ・監視までのAWS構成をどう通るか | クラウド設計・開発・運用担当者 | `specs/aws-overview-ja.json` |
| 高密度コンテナ | 多数のサービス、非同期処理、監視を含むC4 Container相当の全体像を読めるか | 開発・運用担当者 | `specs/complex-container-ja.json` |
| 高密度ネットワーク | 公開入口、複数サブネット、VPC接続、運用経路の境界を追えるか | 情シス・セキュリティ担当者 | `specs/complex-network-ja.json` |
| 高密度データフロー | 複数入力、検証、イベント、保存、監査、通知の流れを追えるか | 業務・開発・運用担当者 | `specs/complex-data-flow-ja.json` |

PPTX、SVG/PNGプレビュー、機械判定レポートは、仕様からローカルで
再生成します。これらの生成物は公開リポジトリには含めません。

高密度サンプルは、通常の推奨範囲を意図的に超えるストレスケースである。`complex-container-ja` は17ノード/8境界、`complex-network-ja` は16ノード/10境界、`complex-data-flow-ja` は15ノード/4境界を持つ。いずれも `review_diagram.py --strict`、PNGレビュー、編集可能PPTXの構造検証を通す。高密度図ではノードの役割説明を省き、短いタイトルと大きなアイコンを優先する。詳細な役割は別のコンポーネント図へ分ける。

検証条件と結果の一覧は [`references/complexity-validation.md`](../references/complexity-validation.md) にまとめている。

## 再生成

リポジトリルートから、次のコマンドで仕様検証・PPTX生成・構造検証を行う。

```bash
npm ci --prefix skills/architecture-diagram-pptx/scripts --no-audit --no-fund
for spec in skills/architecture-diagram-pptx/examples/specs/*.json; do
  name="$(basename "$spec" .json)"
  python3 skills/architecture-diagram-pptx/scripts/check_diagram_spec.py "$spec" --layout --semantics
  node skills/architecture-diagram-pptx/scripts/create_drawio_pptx.cjs \
    --spec "$spec" \
    "skills/architecture-diagram-pptx/examples/pptx/$name.pptx"
  python3 skills/architecture-diagram-pptx/scripts/validate_pptx.py \
    "skills/architecture-diagram-pptx/examples/pptx/$name.pptx" \
    --spec "$spec"
done
python3 skills/architecture-diagram-pptx/scripts/validate_icon_manifest.py
```

1枚を作成しながら評価用画像・レポートも生成する場合は、次を使う。

```bash
npm ci --prefix skills/architecture-diagram-pptx/scripts --no-audit --no-fund
PREVIEW_FONT_DIR=/path/to/japanese/fonts \
  python3 skills/architecture-diagram-pptx/scripts/review_diagram.py \
    skills/architecture-diagram-pptx/examples/specs/aws-overview-ja.json \
    --strict
```

`review_diagram.py` は、仕様・レイアウト・矢印意味、コネクタと文字の衝突、SVG/PNGプレビュー、PPTX構造を一度に検証する。`PREVIEW_FONT_DIR` は日本語フォントが標準配置されていない環境だけ指定する。PowerPoint/LibreOfficeの画面を撮影したものではなく、同じJSON座標とアイコンmanifestから描く再現可能なレビュー用プレビューである。

既存の成功・失敗fixtureを含む幾何検証は、次で確認する。

```bash
python3 skills/architecture-diagram-pptx/scripts/run_visual_fixtures.py
```

この環境ではPowerPoint/LibreOfficeのレンダラーを使用していないため、PPTXの目視確認済みとは扱わない。納品先で開いた後に、文字切れ、線の接続、ラベルの重なり、ノード移動後の編集性を確認する。

## 使い分け

- `container-overview-ja`: C4 Container相当の全体構成。内部実装の細部は省く。
- `network-security-ja`: ネットワーク／信頼境界の説明。詳細なサブネットやFWルールは別図に分ける。
- `data-flow-ja`: データの出所・変換・保存・監査を説明する。配置情報は載せない。
- `deployment-ja`: 環境・ゾーン・配置の説明。業務処理の順序は載せない。
- `aws-overview-ja`: 実在するAWSサービスを使った、入口・非同期処理・データ・監視の少し複雑な全体例。実運用のサブネットやIAMポリシーを確定する設計書ではない。

サンプルは汎用名と架空データだけを使う。provider-icon-showcaseだけは公式配布元から収集したAWS/Azure/Google Cloudアイコンを使い、出典と利用条件をmanifestに残している。IPアドレス、認証情報は含めない。線の意味は[矢印意味の契約](../references/arrow-semantics.md)で固定している。
