# 調査根拠

調査日: **2026-08-09（JST）**

以下は設計書作成Skillの方針を決めるために参照した資料である。標準の記述と、そこから導いたこのSkillの運用ルールを分けて扱う。

| 資料 | 適用範囲 | 調査日 | このSkillで参照する点 |
|---|---|---|---|
| [ISO/IEC/IEEE 29148:2018](https://www.iso.org/standard/72089.html) | システム・ソフトウェアの要件工学 | 2026-08-09 | 要件エンジニアリングと要件情報の入口。要件ID、品質、検証、追跡性の基礎 |
| [ISO/IEC/IEEE 42010:2022](https://www.iso.org/standard/74393.html) | ソフトウェア、システム、企業、製品等のアーキテクチャ記述 | 2026-08-09 | 関係者の関心事、ビュー、ビューポイント、モデル種別によるアーキテクチャ記述 |
| [ISO/IEC/IEEE 15289:2019](https://www.iso.org/standard/74909.html) | システム・ソフトウェアのライフサイクル文書 | 2026-08-09 | ライフサイクル文書の情報項目。文書の統合・分割・選択をプロジェクトに合わせる考え方 |
| [C4 Model - Diagrams](https://c4model.com/diagrams) | ソフトウェアアーキテクチャの視覚化 | 2026-08-09 | Context、Container、Component、Codeと補助図。価値のある図だけを作る方針 |
| [arc42 Documentation](https://docs.arc42.org/home/) | 実務向けアーキテクチャ文書 | 2026-08-09 | 目的、制約、コンテキスト、構造、実行時、配置、品質、リスク、用語の実務テンプレート |
| [Architecture Decision Record guidance](https://adr.github.io/) | アーキテクチャ・設計判断 | 2026-08-09 | 重要な判断の背景、選択肢、理由、結果を小さく記録する方針 |
| [NASA Requirements Writing Guide](https://www.nasa.gov/reference/appendix-c-how-to-write-a-good-requirement/) | 高信頼システムの要求記述 | 2026-08-09 | 明確、簡潔、検証可能、一文一要件の要求記述 |
| [Google Technical Writing - Active voice](https://developers.google.com/tech-writing/one/active-voice) | 技術文書の文体 | 2026-08-09 | 主語と責任主体を明確にする文体 |
| [OMG UML](https://www.omg.org/spec/UML/) | ソフトウェアモデル・図 | 2026-08-09 | 状態、シーケンス、構造など、必要時に選ぶモデルの候補 |
| [OMG BPMN](https://www.omg.org/bpmn/) | 業務プロセス・ワークフロー | 2026-08-09 | 人・部署・システムを含む業務プロセス表現の候補 |
| [OWASP Threat Modeling](https://owasp.org/www-project-threat-modeling/) | セキュリティ設計・脅威分析 | 2026-08-09 | 対象分解、脅威、緩和策、検証を行うセキュリティ設計の候補 |
| [OpenAPI Specification](https://spec.openapis.org/oas/) | APIの機械可読契約 | 2026-08-09 | API契約を機械可読の正本にする場合の候補 |

## 適用上の注意

- ISO/IEC/IEEE規格は、プロダクトごとに同じファイル名・ディレクトリ構造を強制するものではない。ここでの番号付き構造や `not-applicable` 記録は、このSkillの実務上の提案である。
- C4、arc42、ADRは有力な実務手法だが、全図・全章を必須にしない。関係者の問い、リスク、変更頻度に基づいて選ぶ。
- 外部資料の内容を長く転載せず、設計判断に必要な要約とリンクだけを残す。
