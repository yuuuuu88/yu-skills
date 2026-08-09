---
id: CT-005
type: contract
title: Nous Pilot Phase 2–4 extension contracts
status: accepted
source_of_truth: true
created: 2026-08-09
updated: 2026-08-09
related: [CT-001, CT-002, CT-003, CT-004, SEC-001, OBS-001]
managed_by: nous-loop
---
# Phase 2–4 extension contracts

## Phase 2: development control plane

- `ProviderCapabilities` and `ProviderPolicy` are negotiated before invocation.
- retry is bounded by `RetryPolicy`; retryable failures do not silently become success.
- `CostLedger` rejects an invocation that would exceed the declared budget.
- `ChildLoopDAG.waves()` returns dependency-safe parallel waves and rejects cycles.
- `IntegrationReview` rejects overlapping write paths before integration.
- `VerificationProfile` is named, command-based, timeout-bounded, and network-disabled unless a dedicated adapter exists.
- `StructuredHandoff` carries scope, digest, assumptions, and unresolved items; secret-like fields are rejected.
- utilization and quality reports expose missing roles, review count, fallback, evidence, and blocking findings.

## Phase 3: external development boundaries

- Issue and CI adapters are read-only normalizers.
- Draft PR output is an artifact with changed paths and evidence references; it is not publication.
- Remote Git requires an allowed operation, network and credential policy, and Human approval. The default policy denies all operations.
- Browser verification requires an explicit host capability and otherwise returns a blocked result.
- The generic Agent bridge accepts only one structured JSON request/result pair and uses `shell=False` with a reduced environment.
- Target repository changes support preview, explicit human-approved local apply, and patch-only commit candidates; commit/push are not performed.

## Phase 4: durable operation and distribution

- `TaskStore` is the replaceable interface. `SQLiteTaskStore` is the local durable shared-store-equivalent implementation.
- Observability exporters accept secret-safe structured events. `JSONLExporter`, `RecordingExporter`, and the vendor-neutral `OTLPExporter` boundary are local adapters; the host supplies any network sender.
- `MCPHostAdapter` validates tool allowlists, scopes, OAuth for HTTP transport, and mutation policy. `MCPHTTPGateway` translates an already-authenticated request but does not open an HTTP server itself.
- compatibility and release manifests are data contracts; deployment, migration execution, and remote credentials remain outside this local slice.
- distribution is locally buildable as a wheel and CI exercises the supported Python matrix; publication remains a Human action.

## Safety invariant

No Phase 2–4 adapter may silently upgrade a local artifact into a remote mutation. Remote, credential-bearing, browser-host, or network behavior must be introduced as a separately configured adapter with an explicit policy and Human gate.
