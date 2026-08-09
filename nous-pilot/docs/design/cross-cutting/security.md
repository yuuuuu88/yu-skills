---
id: SEC-001
type: cross-cutting
title: Nous Pilot security and containment
status: accepted
source_of_truth: true
created: 2026-08-09
updated: 2026-08-09
related: [REQ-001, ARCH-001, DES-001, CT-002, CT-003, CT-004, ADR-0002]
managed_by: repository-bootstrap
---
# Nous Pilot security and containment

## Trust boundaries

```text
Human input / repository instruction / Web / Agent output
                 = untrusted data
                               |
                               v
Policy Gateway + schema + scope + capability validation
                               |
                               v
Managed filesystem / process / Git / network / credential adapters
```

## Core rules

- Repository内の指示、Web本文、Issue本文、Agent outputは命令ではなくuntrusted dataとして解析する
- source code write、process起動、Git、network、credential、external operationはtyped actionに変換する
- exact target、resource scope、risk、grant、expiration、actorをactionへ束縛する
- secret valueはprompt、artifact、log、digest入力、outbound payloadへ入れない
- public Web researchはGET/HEAD、公開HTTPS、private network block、download execution disabledを既定にする
- providerが要求するcredentialはsecret brokerまたはhost環境の許可された境界からのみ取得する
- output、tool call、wall time、concurrency、cancellationの各budgetを管理する

## Development-specific controls

- Coordinator source writeを禁止し、実装はImplement/Repair Agentのworkspaceへ限定する
- Reviewerは実装者のraw rationaleではなく、要求、差分、evidence、設計正本を検証する
- child scope外の変更、未宣言ファイル、symlink escape、秘密情報らしい値を検出したら停止する
- required subagent unavailable時にsingle-agentへ黙って縮退しない
- remote push、PR、merge、deploy、課金、production data変更は別のHuman gateとする

## Prompt injection handling

1. 外部内容とsystem/task contractを別namespaceへ格納する
2. Agentへ渡す前にsource provenance、trust level、scopeを付ける
3. 外部内容がpolicy変更、secret要求、scope拡張、tool起動を指示しても採用しない
4. 不審な指示はsecurity findingとしてReviewへ渡す
5. parsing/executionより先にfilesystemとnetworkの境界を適用する

## Data retention

Task artifactには目的に必要な最小情報を保存する。raw transcript、秘密、全文外部ページ、不要な個人情報は保存せず、参照URI、hash、要約、必要なevidenceへ置き換える。

## Security validation

- secret scan
- action policy contract test
- scope and symlink containment test
- network/private target denial test
- provider credential non-persistence test
- coordinator direct-write denial test
- untrusted instruction injection test
