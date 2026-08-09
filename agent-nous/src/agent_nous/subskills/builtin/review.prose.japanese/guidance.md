# Japanese Prose Review

目的に合う日本語かを確認し、原文の事実・技術用語・コード・著者の声を保全する。文書の読者、媒体、目的、変更可能範囲を先に確認する。

## 観点

1. 組織の用語集と正式名称を最優先し、表記ゆれ、全半角、単位、固有名詞を確認する。
2. 誤字脱字、助詞、主述関係、括弧、文体混在、二重否定を確認する。
3. 文長、読点、冗長表現、曖昧さ、情報の飛躍を確認する。ただし経験的な閾値を絶対基準にしない。
4. 敬語は相手・場面・内外関係を見て判断し、一律に丁寧化しない。
5. 技術文書では前提、主体、手順、期待結果、例外、エラー、用語定義を確認する。
6. 数値、日付、責任主体、引用、コマンド、API名の変更は推測で行わず「要確認」とする。

## 出力

指摘ごとに、箇所、種別、短い引用、問題、修正案または確認事項、重要度（必須/推奨/任意）、適用した根拠を示す。依頼がない限り全文リライトをしない。

## 参照

- 文化庁「公用文作成の考え方」: https://www.bunka.go.jp/seisaku/kokugo_nihongo/kokugo_shisaku/94336802.html
- 文化庁「敬語の指針」: https://www.bunka.go.jp/seisaku/bunkashingikai/kokugo/hokoku/pdf/keigo_tosin.pdf
- JTCA 日本語スタイルガイド: https://jtca.org/learn-tc/publication/guide_jsg/
- textlint: https://github.com/textlint/textlint
