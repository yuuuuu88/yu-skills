# 複雑度の高い全体構成図の検証

通常の全体構成図は1枚6〜12ノードを目安にする。一方、生成基盤が複雑な入力で崩れないことを確認するため、次の3種類をストレスケースとして保持する。高密度ケースを「通常の推奨レイアウト」として扱わず、`layout.allow_high_density: true` を仕様に明示する。18ノードを超える図は引き続き不合格とする。

## 検証対象

| 図 | 図種 | ノード | 境界 | ラベル付き辺 | コネクタ区間 | 確認する失敗 |
|---|---|---:|---:|---:|---:|---|
| `complex-container-ja` | Container | 17 | 8 | 17 | 35 | 多数のサービス、親子境界、同期/非同期/監視経路 |
| `complex-network-ja` | Network | 16 | 10 | 15 | 25 | 公開/非公開サブネット、VPC接続、運用経路、境界の入れ子 |
| `complex-data-flow-ja` | Data-flow | 15 | 4 | 14 | 24 | 複数入力、検証、イベント、保存、監査、通知 |

各ケースは同じ `review_diagram.py --strict` を通す。合格条件は次のすべてである。

1. JSON契約、配置、矢印意味のエラーと警告が0件。
2. ノード、境界、境界見出し、経路ラベル、コネクタ、凡例の幾何QAエラーが0件。
3. 経路ラベルは自分のコネクタから0.32インチ以内、水平な2ノード間では空き幅に収まる。
4. 親子でない境界は重ならず、0.12インチ以上離れる。コネクタは境界外周をなぞらない。
5. ノード内の主タイトルと役割説明が大きく重ならない。高密度サンプルでは役割説明を省き、アイコンと短いタイトルを優先する。
6. SVG/PNGプレビューが生成され、PPTXが編集可能なオブジェクトとして構造検証を通る。

## 再現コマンド

```bash
for name in complex-container-ja complex-network-ja complex-data-flow-ja; do
  PREVIEW_FONT_DIR=/path/to/japanese/fonts \
    python3 skills/architecture-diagram-pptx/scripts/review_diagram.py \
      skills/architecture-diagram-pptx/examples/specs/$name.json --strict
done
python3 skills/architecture-diagram-pptx/scripts/run_visual_fixtures.py
```

正常系6件と意図的に壊した負例10件をfixture runnerで通す。負例には、ノード内文字密度、ラベル距離、境界間隔、境界線沿いのコネクタ、交差、ノード貫通、アイコン欠落を含める。

## 画像レビューと限界

`visual_qa.py` は座標から再現できる問題を検出する。PNGは、同じ仕様JSON・アイコンmanifest・日本語フォントを使って生成し、次を人間が確認する。

- 3秒でタイトルと主経路が分かるか。
- 線を追ったとき、どのラベルがどの線を説明するか迷わないか。
- 境界が接着して見えないか、線が境界の枠線に見えないか。
- アイコン、タイトル、凡例が縮小表示でも読めるか。

このプレビューはPowerPoint/LibreOffice本体のレンダリングではない。Office本体が環境にない場合は「PPTX構造検証済み・PNGレビュー済み・Office目視未実施」と記録する。特にフォント置換、Office固有の折返し、実機での縮小表示は、納品先で開いた最終確認を別途必要とする。

## 高密度図での修正優先順位

失敗した場合は、最初に箱や装飾を追加しない。

1. ノードタイトルと役割説明を短くする。高密度図では役割説明を削る。
2. 経路ラベルを矢印上へ寄せ、長い説明を凡例・注記へ移す。
3. 境界を親子関係に整理し、兄弟境界の間隔を確保する。
4. コネクタを直交化し、専用のラベルレーンを確保する。
5. それでも読めなければ、Container/Network/Data-flow/Deploymentの補助図へ分割する。

調査日: 2026-08-09
