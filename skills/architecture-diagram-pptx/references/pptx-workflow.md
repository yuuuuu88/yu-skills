# PPTX作成手順と編集性

## オブジェクトの分け方

| 図の要素 | 推奨するPPTXオブジェクト | 理由 |
| --- | --- | --- |
| ゾーン/境界 | native shape | 塗り、枠、角丸、サイズを編集できる。サービスノードには丸枠を置かない |
| ノード名・役割 | text boxまたはshape内text | コピー、検索、修正ができる |
| 経路 | native line shape | 線、端点、矢印を個別に編集できる。自動再配置は保証しない |
| 境界 | 透明度のあるshape + 見出し | 範囲を編集できる |
| 製品アイコン | 別のSVG/PNG image | 交換・差し替えをしやすい |
| 凡例・メタデータ | text box / small shapes | 検索・編集できる |

描画順は「背景 → ゾーン → 経路 → 経路ラベル → アイコン画像 → ノード文字 → 凡例・注記」とする。アイコン、ラベル、役割、コネクタはすべて別オブジェクトにし、ノードを一枚画像へ焼き付けない。サービスノードの周囲に丸枠を追加せず、アイコン自体を主役として見せる。
線とラベルが重なる場合はラベルの背景を白にするか、経路をずらす。折れ線の中間区間には矢印を付けず、
to側の最後の区間だけに矢印を置く。

## 生成ライブラリ

標準ジェネレーターは、このディレクトリの`package.json`に固定したPptxGenJSである。次点はpython-pptx。どちらもテキスト、図形、画像を別オブジェクトとして出力できる。PptxGenJSはOOXMLパッケージとSVG画像を扱える。PowerPointがインストールされていることを前提にしない。

```bash
npm ci --prefix skills/architecture-diagram-pptx/scripts --no-audit --no-fund
node skills/architecture-diagram-pptx/scripts/create_drawio_pptx.cjs \
  --spec skills/architecture-diagram-pptx/examples/specs/network-security-ja.json \
  /tmp/network-security.pptx
```

以前の手書きPresentationMLジェネレーターは標準経路に使わない。特に`p:cxnSp`の手動生成、
負の`a:xfrm/a:ext`、0未満の経路寸法はPowerPointの修復ダイアログにつながり得る。PptxGenJSの
標準出力を使い、生成後にCRC、XML、内部relationship target、負のtransform、編集可能オブジェクトを検査する。
PptxGenJSの水平線はOOXML上で片方の寸法が0になることがあるが、これは標準のline shapeの表現であり、
手書きの不正なconnector transformとは区別する。

- [PptxGenJS公式リポジトリ](https://github.com/gitbrent/PptxGenJS)
- [PresentationMLの構造](https://learn.microsoft.com/en-us/office/open-xml/presentation/structure-of-a-presentationml-document)
- [PowerPointのコネクタ](https://support.microsoft.com/en-US/PowerPoint/draw-or-delete-a-line-or-connector)

特定ライブラリのAPIを断定する前に、実行環境でバージョンと機能を確認する。標準ジェネレーターは固定座標の仕様JSONから線を生成するため、ノードを移動した場合の動的な再接続は保証しない。必要ならPowerPoint上で端点を再調整する。

## 16:9の標準レイアウト

- スライド: 13.333 × 7.5 inch
- タイトル: 上端0.35〜0.75 inch
- 図の本体: 上端0.95〜6.55 inch
- 凡例/メタデータ: 下端6.75〜7.25 inch
- 本文: 16〜20ptを下限の目安にする。長いラベルはノード分割かスライド分割で解決する。

テンプレートがある場合はテンプレートのマスター、フォント、余白、色を優先する。テンプレートがない場合は、背景1色、本文色1色、カテゴリ色3〜5色、線色1〜2色に抑える。

## 生成前の仕様JSON

最低限、次の形を持つJSONを作る。追加フィールドは許容するが、事実と推測を分ける。

```json
{
  "title": "注文処理の構成",
  "purpose": "注文APIからデータ保存までの同期経路を示す",
  "diagram_type": "container",
  "audience": "開発・運用",
  "nodes": [
    {"id": "client", "label": "利用者", "kind": "person", "zone": "external"},
    {"id": "api", "label": "注文API", "kind": "service", "zone": "app"},
    {"id": "db", "label": "注文DB", "kind": "database", "zone": "data"}
  ],
  "edges": [
    {"from": "client", "to": "api", "label": "HTTPS", "relation": "data_flow", "line_style": "solid"},
    {"from": "api", "to": "db", "label": "SQL", "relation": "data_flow", "line_style": "solid"}
  ],
  "boundaries": [{"id": "app", "label": "アプリ境界", "node_ids": ["api"]}],
  "legend_relations": ["data_flow"],
  "metadata": {"version": "0.1", "updated_at": "2026-08-09"}
}
```

`check_diagram_spec.py`でノード/エッジのID、重複、ラベル、過密度を確認してからPPTXを生成する。

視覚品質を優先する場合は、`layout`に`direction`、`grid`、`safe_area`を必須化し、各ノードに`position: {x, y, w, h}`を持たせる。エッジが同一基準線に乗らない場合は、直線を許さず`route`の折れ点を明示する。折れ点の各区間は水平または垂直でなければならない。長いラベルを入れるために`h`だけを増やすのではなく、短縮・折返し・別スライドの順で解決する。

## 検証

1. `scripts/validate_pptx.py output.pptx`でOOXMLの必須部品、スライド数、テキスト、図形、画像を確認する。
2. 図形・line shapeが0で画像が1枚だけなら、編集不能の可能性として失敗させる。アイコン画像は個別オブジェクトなら許容する。
3. PowerPoint/LibreOfficeで開き、警告なく表示できること、ノードを一つ動かしてテキスト・線・凡例を個別編集できることを確認する。
4. 目視できない環境では、`render_preview.cjs`でSVG/PNGを作り、`visual_qa.py`で文字・線・ノード・境界見出しの衝突を検査する。これはOffice本体のレンダリング確認とは別なので、目視確認を実施していないことを報告する。
5. PPTXと一緒に仕様JSONを保存し、後から再生成・差分確認できるようにする。

リポジトリ内のサンプル検証:

```bash
python3 scripts/run_visual_fixtures.py
```

このrunnerは正常系6件と、過密・重複・交差・矢印意味違反・コネクタのノード貫通・ノード内文字密度・経路ラベル距離・境界間隔・境界線沿いコネクタを含む負例10件を、標準PptxGenJSジェネレーターで再生成する。

`tests/fixtures/prompts.md`には、ネットワーク経路、ゾーン配置、セキュリティ境界、データフロー、過密で失敗すべき依頼を用意している。これはOfficeのレンダリングを置き換えるものではなく、崩れやすい幾何条件を生成前に落とすためのテストである。
