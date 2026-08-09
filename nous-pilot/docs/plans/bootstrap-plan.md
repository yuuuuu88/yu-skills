---
id: PLAN-001
type: plan
title: Nous Pilot bootstrap and implementation plan
status: active
source_of_truth: true
created: 2026-08-09
updated: 2026-08-09
related: [REQ-001, ARCH-001, DES-001, CT-001, CT-002, CT-003, CT-004, ADR-0001, ADR-0002, ADR-0003]
managed_by: repository-bootstrap
---
# Nous Pilot bootstrap and implementation plan

## Objective

Nous Pilotを、現行`agent-nous`のruntime境界を再利用しながら、Research-firstかつSubagent-firstのソフトウェア開発制御プレーンとして実装する。

## Current checkpoint

- Directory `nous-pilot/` created under the `yu-skills` parent repository
- repository-bootstrap required structure created
- current requirements, architecture, contracts, inherited design, and external research recorded
- Phase 1–4 local implementation is complete and cross-reviewed through the external Nous runtime
- Distribution hardening for Phase 4 is complete locally: explicit runtime checks, no-network build smoke guidance, and cross-platform CI are documented and added
- remote Git, PR publication, deploy, live provider, and paid service effects remain disabled by default and require separately deployed policy-approved adapters
- the personal-development hardening slice now provides a generic structured-JSON Agent bridge, human-gated target apply/patch artifacts, operator CLI commands, and local packaging/CI contracts

## Ordered tasks

### Phase 0: Bootstrap and design packet

- [x] Create sibling project directory `nous-pilot`
- [x] Add AI rules, Codex handoff, README, and folder responsibilities
- [x] Write product brief and acceptance criteria
- [x] Write architecture and runtime boundary
- [x] Write initial issue-to-draft-change slice
- [x] Write control-plane, subagent, provider, and verification contracts
- [x] Write security and observability constraints
- [x] Record inherited Nous design and external agentic-development research
- [x] Record ADRs for project separation, subagent-first execution, and Research-first design
- [x] Run strict bootstrap validation

### Phase 1: Safe local vertical slice

- [x] Define Python package and runtime adapter boundary
- [x] Implement local request intake and Root Task creation through agent-nous
- [x] Implement repository context map and Knowledge packet
- [x] Implement Research/Plan/Implement/Test/Review fake-provider integration
- [x] Implement required-agent and review-quorum hard gates
- [x] Implement a local staging workspace and write-containment adapter (Git worktree remains Phase 2)
- [x] Implement evidence-backed acceptance and bounded re-plan
- [x] Add contract, integration, failure-recovery, and security-boundary tests

### Phase 2: Development control plane

- [x] Add provider capability negotiation, retry, cancellation, and cost limits
- [x] Add adaptive Child Loop DAG and Integration Review
- [x] Add project verification profiles beyond unit tests
- [x] Add context reset, structured handoff, and resume diagnostics
- [x] Add subagent utilization and quality evaluation reports

### Phase 3: External development integrations

- [x] Add read-only Issue intake adapter
- [x] Add CI event adapter and feedback-to-replan conversion
- [x] Add draft PR proposal artifact behind an explicit human gate
- [x] Add remote Git/PR operation adapter with credential and network policy
- [x] Add browser/product verification adapter

### Phase 4: Durable operation and distribution

- [x] Add SQLite durable shared-store equivalent (Postgres adapter contract remains replaceable)
- [x] Add local JSONL and exporter observability boundary
- [x] Add MCP HTTP/OAuth host policy adapter without opening a listener by default
- [x] Add cross-platform compatibility and release manifest contracts
- [x] Add migration, compatibility, security, and operations documentation

### Phase 4 follow-up: packaging and CI hardening (2026-08-09)

- [x] Add explicit Python 3.11+ and Linux/macOS/Windows compatibility checks with clear unsupported-runtime errors
- [x] Add focused distribution tests and stable release-manifest serialization
- [x] Add no-dependency-resolution, no-network wheel build smoke guidance
- [x] Add GitHub Actions coverage for supported OS/Python combinations, compile, tests, bootstrap validation, and build smoke
- [x] Preserve `nous-pilot.toml` as the only user-facing runtime configuration file

## Validation commands

Bootstrap validation:

```bash
python3 ../skills/repository-bootstrap/scripts/validate_repository_bootstrap.py . --all-docs --strict
```

Implementation validation is local and dependency-light:

```bash
python3 scripts/validate_bootstrap.py .
python3 -B scripts/validate_compile.py
python3 -B -m pytest -q -p no:cacheprovider
```

The GitHub Actions matrix additionally exercises the supported operating
systems/Python versions and a no-index wheel build smoke test.

## Checkpoints

- Checkpoint A: required repository structure and source-of-truth documents exist
- Checkpoint B: fake-provider local vertical slice passes without external side effects — **passed**
- Checkpoint C: subagent-first hard gates and independent-review metrics pass — **passed**
- Checkpoint D: failed Test Agent and bounded re-plan scenarios pass; pause/resume remains delegated to the durable agent-nous runtime — **passed for Phase 1 scope**
- Checkpoint E: external adapters are separately policy-gated and tested — **passed**

## Blockers and decision gates

- provider selection and credentials
- richer local Git worktree lifecycle beyond the safe preview/apply/patch boundary
- remote GitHub/CI/PR scope
- host resource and network enforcement
- browser test environment
- shared TaskStore choice

## Implementation entry point

Use the existing [`agent-nous` Nous Skill](../../../agent-nous/skill/nous/SKILL.md) for further hardening or for separately deployed remote adapters. Phase 1–4 were completed in managed tasks with three Nous Loop cycles per task, external Plan/Implement/Test agents, and two independent cross-reviewers per cycle.

## Phase 1 evidence

- Final hardening Root Task: `20260809-0252-harden-nous-pilot-phase-1-with-runtime-adapter-a` — `completed`
- CLI entry Root Task: `20260809-0255-add-a-safe-nous-pilot-cli-entry-point` — `completed`
- Phase 2–4 Root Task: `20260809-0308-complete-nous-pilot-phase-2-through-phase-4` — `completed`
- Phase 4 boundary hardening Root Task: `20260809-0312-harden-phase-4-otlp-and-mcp-http-boundaries` — `completed`
- Personal config Root Task: `20260809-0344-simplify-nous-pilot-configuration-for-personal-d` — `completed`
- Each task used cycles 1–3, with Plan → Implement → Cross-review in every cycle
- Each task used 12 external subagent invocations
- Each task used 2 independent cross-review perspectives per cycle
- Validation: `scripts/validate_compile.py` and `python3 -B -m pytest -q -p no:cacheprovider`
- Final suite: 18 tests passed; bootstrap validator passed for 34 documents
- User-facing configuration: one `nous-pilot.toml`; detailed runtime JSON is generated under ignored `.nous/`

## Distribution hardening evidence

- Added `src/nous_pilot/distribution.py` compatibility checks for Python 3.11+
  and Linux/macOS/Windows, plus `require_compatibility()` fail-fast errors.
- Added `tests/test_distribution.py` for supported, unsupported, and manifest
  serialization cases.
- Added `.github/workflows/ci.yml` for the supported matrix and local-only
  build smoke command.
- Added [distribution operations guidance](../operations/distribution.md).
- Full-suite validation after the hardening slice passes locally; the focused
  provider, target-repository, CLI, distribution, and compatibility tests are
  included in the suite.

## Personal-development hardening evidence

- Generic external provider boundary: `SubprocessAgentProvider` and `scripts/agents/external_bridge.py`
- Target repository boundary: preview, explicit human-approved apply, and patch-only commit candidate in `TargetRepositoryAdapter`
- Operator commands: `init`, `start`, `status`/`show`, and `resume`
- Distribution: Python 3.11–3.13 CI workflow, wheel build smoke, compatibility and release runbooks
- Remaining boundary: a real Agent command must be supplied in `[agents].command`; no live provider or remote mutation is bundled by default
