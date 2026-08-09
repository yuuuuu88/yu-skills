# 矢印の意味を固定する契約

矢印は装飾ではなく、`from` から `to` へ読める一文で設計する。たとえば
`業務API → 認証基盤` は「業務APIが認証基盤を利用する」であり、認証基盤から
業務APIへ流れるデータを意味しない。意味が異なる経路は、別の線と別のラベルに分ける。

## 標準の関係語彙

| relation | 読み方 | 線 | 矢印 | 典型的なラベル |
| --- | --- | --- | --- | --- |
| `data_flow` | from が to へデータを渡す | 青の実線 | to 側の三角 | `申請データ`、`SQL / 結果` |
| `dependency` | from が to を利用・参照する | 灰の破線 | to 側の三角 | `認証基盤を利用`、`設定を参照` |
| `control` | from が to を操作・命令する | 紫の実線 | to 側の三角 | `デプロイを開始`、`停止` |
| `monitoring` | from が to へログ等を送る | 緑の点線 | to 側の三角 | `ログ・メトリクスを送信` |
| `message` | from が to へイベントを発行する | ティールの実線 | to 側の三角 | `申請イベント`、`ジョブを取得` |
| `response` | from が to へ応答を返す | 青の破線 | to 側の三角 | `結果を返却`、`応答` |
| `association` | 互いに関連があるだけ | 灰の実線 | なし | `関連`、`所属` |

`relation` と線種の組み合わせは、`scripts/semantic_policy.py` が検証する。`style`
だけを指定して、破線を「非同期」、実線を「データ」と推測してはならない。

## 図種ごとの使い分け

- **Data-flow**: データの出所、変換、保存、出口を `data_flow` でつなぐ。DFDの矢印は
  制御フローではなくデータの受け渡しとして読む。
- **Network / deployment**: 通信する経路を `data_flow`、依存するだけの呼び出しを
  `dependency`、ログの送り先を `monitoring` として分ける。ゾーン境界そのものには矢印を付けない。
- **Container / component**: C4の関係ラベルに加え、必要ならプロトコルや技術を短く添える。
- **Queue / event**: 発行側からキューまたは購読側へ `message` を使う。処理の実行命令は
  `control` として別にする。
- **双方向**: 両矢印で省略せず、要求と応答など、意味の異なる2本の片方向エッジに分ける。

## 出典と採用理由

- [draw.io Data Flow Diagrams](https://www.drawio.com/docs/diagram-types/data-flow-diagrams/): DFDの矢印をデータの受け渡しとして扱う。
- [draw.io Dependency Graphs](https://www.drawio.com/docs/diagram-types/dependency-graphs/): 依存の向きを利用側から依存先へ置く。
- [draw.io Connector Styles](https://www.drawio.com/docs/manual/styles/connector-styles/): 線種、端点、矢印、直交経路を意図的に選ぶ。
- [C4 Notation](https://c4model.com/diagrams/notation): 一方向の関係、線ごとのラベル、必要に応じたプロトコル表示を採用する。

この語彙はdraw.ioやC4の図をそのまま規格化したものではなく、PowerPointで一貫して再現・検証するためのSkill内契約である。
