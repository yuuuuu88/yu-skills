# 構成図・ネットワーク図の作法

## 1. まず図の問いを一文にする

「誰が、何を判断するための図か」を一文で書く。例:

- 経営/利用部門: どの外部システムと連携するか
- 開発者: リクエストがどのコンポーネントを通るか
- インフラ担当: どの境界・サブネット・経路を通るか
- セキュリティ担当: どこで信頼境界を越え、何で保護されるか
- 運用担当: どこが冗長化・監視・復旧の単位か

一つの図に全ての問いを載せない。問いが変わるならスライドを分ける。

## 2. 図種と抽象度

| 図種 | 主に示すもの | 省くもの |
| --- | --- | --- |
| Context | 対象システムと利用者・外部システムの関係 | 内部コンポーネント、細かいネットワーク |
| Container / high-level | 主なアプリ、データストア、外部依存、主要な流れ | クラスや実装詳細 |
| Component | あるコンテナ内の責務と接続 | システム全体の細部 |
| Deployment | 環境、ホスト、クラスタ、ゾーン、配置 | 利用者向けの細かな業務説明 |
| Network | VPC/VNet、サブネット、FW、入口/出口、経路 | 実装責務の細部 |
| Data-flow | データの出所、変換、保存、出口、分類 | 配置情報の細部 |
| Sequence / focused flow | 一つのシナリオの時系列 | 全体トポロジー |

C4のcontext/container/component/codeは「ズームの異なる説明」であり、全レベルを必ず作る必要はない。対象読者に価値のあるレベルだけを使う。deploymentやdynamic diagramは、配置や時間順の問いがある場合の補助ビューとする。

## 3. 配置

- 16:9なら左右12列程度、または上下8行程度の仮想グリッドを置く。
- 主方向は左→右（利用者からデータ/サービス）または上→下（入口から処理）から一つ選ぶ。
- 同じ層のノードは同じ水平線に揃え、`align`と`distribute`を使える余地を残す。
- デフォルトではノードのx/y/w/hを仕様に持たせ、0.25インチ程度のグリッドに吸着する。箱の高さ/幅は通常1.45以下とし、縦長にして長文を押し込まない。
- 左→右の主経路に意図しないy座標の揺れを作らない。補助的な認証・監視層をずらす場合は、層の意味を明示し、コネクタは水平・垂直の折れ線にする。
- 外部、入口、処理、データ、監視のようにゾーンを分け、境界の見出しを付ける。
- 余白を先に確保する。タイトル、凡例、フッターで使う領域を図の配置に含める。
- 経路は最短かつ少ない曲がりで引き、線をノードの背面に置く。線が交差する場合は配置を変えるか、別スライドに分ける。
- ノード数の目安は1枚あたり6〜12個。18個を超える場合は分割を強く検討する。

### 境界の階層と余白

- 境界は「外側のコンテナ」と「その中のゾーン」を区別する。親子関係のない境界同士を重ねたり、辺を接触させたりしない。
- 同じ階層の境界には最低0.12インチの見える隙間を置く。親境界の内側に子境界を置く場合も、子の見出しとノードが親の枠線に接しないように内側余白を確保する。
- 通信線は境界を横切ってよいが、境界の外周を長くなぞらない。ゾーンへ入る線は外側から枠を一度横切り、その後はゾーン内の余白を通してノードへ接続する。
- 境界の塗りと枠線は背景レイヤーに置き、コネクタはその上に描く。ただし、枠線とコネクタが同じ位置に重なる設計は不合格とする。

### draw.io風の視覚システム

draw.ioを「そのままコピーする」のではなく、同ツールの技術図で読みやすさに寄与する作法をPowerPointへ移す。

- キャンバスは白〜ごく薄いグレー。本文は濃いチャコール、境界は淡い塗りと細い枠にする。
- ゾーンは背景の薄い塗りで先に置き、左上に短い見出しを置く。ノードの塗りつぶしと同じ濃さにしない。
- ノードは大きな意味アイコン、近接した短いタイトル、必要なら最大1行の役割説明で構成する。アイコンを小さなバッジに縮め、白い角丸カードと文字だけで表現しない。境界やゾーンが必要な場合だけ、背後に薄い塗りや細い枠を置く。
- 主経路は同じ水平基準線にそろえる。補助層だけを上下へずらし、折れ線を明示する。箱を斜めに並べない。
- 経路ラベルは白背景の独立テキストボックスにし、線の直上または線上に置く。ラベルと自分のコネクタの距離は0.32インチ以内とし、線から離れた場所に浮かせない。
- 横並びのノード間にラベルを置く場合は、ラベルをノード間の空き幅に収める。収まらない場合は、ラベルを短くするかノード間隔を広げる。ノードの上にラベルを重ねて隠す方法は採用しない。
- 凡例は右下に縦積みせず、下端に横一列で配置する。意味が多い場合は関係語彙を絞るか、図を分割する。
- 同じ抽象度のノードにベンダーロゴだけを混ぜない。実在する製品だけ公式アイコンを使い、一般概念にはgenericアイコンを使う。製品名はアイコンの近くに置く。

この方針は、draw.ioの[一貫した図](https://www.drawio.com/docs/best-practice/consistent-diagrams/)、
[ネットワーク図](https://www.drawio.com/docs/diagram-types/network-diagrams/)、
[コネクタ](https://www.drawio.com/docs/manual/connectors/)、
[配置・スナップ](https://www.drawio.com/docs/manual/editor/alignment-tools/)の考え方を、
16:9のスライドと編集可能なPowerPointオブジェクトへ適用したものである。

## 4. 線と凡例

- 通信・依存は矢印付きの単方向線にする。
- 双方向通信は`request`と`response`など2本に分ける。1本の両矢印は、意味が明確で凡例がある場合だけ使う。
- 実線・破線・太線・色を使い分けるときは、意味を凡例に書く。例: 実線=同期API、破線=非同期イベント、太線=暗号化された主要経路。
- 線ラベルは線の中央付近に置き、線と重ならない白背景を持たせる。ラベルが長い場合は短縮し、詳細は脚注に置く。
- 信頼境界と通信線を同じ線種で表さない。

## 5. ラベル、メタデータ、アクセシビリティ

各図に少なくとも次を含める:

- タイトル
- 図の目的または範囲
- 図種/抽象度（必要な場合）
- 凡例（線・色・形の意味を追加した場合）
- 更新日、版、作成者または出典（分かる範囲）

色だけで分類せず、形、枠、線種、ラベルを組み合わせる。本文を極端に小さくせず、コントラストを確保する。機密情報、IPアドレス、URL、アカウント名を含める場合は、公開範囲とマスキングを確認する。

## 6. 視覚レビューの観点

生成直後に次の順で確認する。これは「見た目の好み」ではなく、読み手が誤解しないためのゲートである。

1. 3秒でタイトルと主経路が分かるか。
2. 同じ種類の箱が同じ大きさ・同じ基準線にあるか。
3. 斜めの箱、縦長の箱、細すぎる文字、箱と線ラベルの衝突がないか。
4. 線を目で追ったときに、交差や迷路がないか。矢印方向と凡例の意味が一致するか。
5. 余白があり、ゾーン・凡例・注記が本体を押しつぶしていないか。
6. 1枚の抽象度が揃っているか。詳細が必要なら別ビューへ分割できるか。

作法に関する普遍的な単一規格があるとは扱わない。以下の実務ガイドを照合し、用途・読者に合わない慣行は採用しない。

## 7. ソース

- [Azure Well-Architected: Architecture design diagrams](https://learn.microsoft.com/en-us/azure/well-architected/architect-role/design-diagrams)
- [C4 model: Diagrams](https://c4model.com/diagrams)
- [draw.io: Create consistent diagrams](https://www.drawio.com/docs/best-practice/consistent-diagrams/)
- [draw.io: Connector styles](https://www.drawio.com/docs/manual/styles/connector-styles/)
- [draw.io: Data Flow Diagrams](https://www.drawio.com/docs/diagram-types/data-flow-diagrams/)
- [draw.io: Dependency Graphs](https://www.drawio.com/docs/diagram-types/dependency-graphs/)
- [draw.io: Network Diagrams](https://www.drawio.com/docs/diagram-types/network-diagrams/)
- [C4: Notation](https://c4model.com/diagrams/notation)
- [C4: Diagram checklist](https://c4model.com/diagrams/checklist)
- [UK NCSC: Drawing good architecture diagrams](https://www.ncsc.gov.uk/blog-post/drawing-good-architecture-diagrams)
- [Microsoft Office: Data visualization guidelines](https://learn.microsoft.com/en-us/office/dev/add-ins/design/data-visualization-guidelines)
- [The Elements of UML 2.0 Style: Diagram layout guidance](https://www.informit.com/articles/article.aspx?p=350736)

調査日: 2026-08-09。外部資料は更新され得るため、特定の規格を採用するときは原典を再確認する。
