---
id: OBS-001
type: cross-cutting
title: Nous Pilot observability and evaluation
status: accepted
source_of_truth: true
created: 2026-08-09
updated: 2026-08-09
related: [REQ-001, CT-001, CT-002, CT-004]
managed_by: repository-bootstrap
---
# Nous Pilot observability and evaluation

## Event dimensions

- task、loop、child、phase、run、provider、subskill
- actor、repository、base revision、workspace、policy grant
- start/end、status、retry、timeout、cancellation、failure code
- input/output digest、changed paths、validation、review decision

## Metrics

### Operational metrics

- phase latency、queue wait、retry count、failure rate
- token、estimated cost、tool calls、output size
- concurrency、workspace creation、integration conflict
- pause/resume recovery、Human gate wait

### Quality metrics

- acceptance criteria pass rate
- research source diversity and claim support
- defect detection by independent Review
- regression escape rate
- re-plan usefulness
- subagent planned/started/completed ratio
- role separation and review quorum
- Coordinator direct-write violations

Operational performance and cognitive capability scores are reported separately. A fast run is not automatically a good run.

## Trace privacy

Traceはsecret-free JSONLまたはhost/provider exporterへ出力する。providerのraw transcriptを正本にせず、必要な要約、artifact reference、digest、usageだけを保存する。

## Evaluation shape

評価タスクは、単純なunit testだけでなく、調査、要件の曖昧性、複数ファイル変更、失敗復旧、並列性、独立Review、security境界を含む。各Taskを複数trialで実行し、code grader、evidence grader、model grader、人間の校正を組み合わせる。
