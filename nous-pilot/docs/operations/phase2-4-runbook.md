---
id: OPS-002
type: operations
title: Nous Pilot Phase 2–4 local runbook
status: accepted
source_of_truth: true
created: 2026-08-09
updated: 2026-08-09
related: [PLAN-001, CT-005, SEC-001, OBS-001]
managed_by: nous-loop
---
# Phase 2–4 local runbook

## Local validation

```bash
python3 scripts/validate_bootstrap.py .
python3 -B scripts/validate_compile.py
python3 -B -m pytest -q -p no:cacheprovider
```

When this repository is checked out beside the parent `skills/` tree, also run
the stricter shared validator:

```bash
python3 ../skills/repository-bootstrap/scripts/validate_repository_bootstrap.py . --all-docs --strict
```

## Durable local store

```python
from pathlib import Path
from nous_pilot.persistence import SQLiteTaskStore

store = SQLiteTaskStore(Path(".nous-pilot/tasks.db"))
store.put_task("task-1", {"objective": "local"})
store.append_event("task-1", {"type": "created"})
```

The store is a local equivalent and is not a claim of PostgreSQL production readiness. A future Postgres adapter must implement the same `TaskStore` contract and preserve the secret-rejection and organization/tenant policy required by its host.

## External adapters

- `ReadOnlyIssueAdapter` and `CIEventAdapter` normalize input only.
- `DraftPRAdapter` creates a proposal artifact only.
- `RemoteGitAdapter` is denied by default and requires a separate network/credential/Human policy.
- `BrowserVerificationAdapter` returns `blocked` unless a host capability is explicitly granted.
- `MCPHostAdapter` and `MCPHTTPGateway` are policy boundaries; the gateway does not open a listener and requires the deployment host to verify OAuth.
- `OTLPExporter` accepts a host-provided sender; the local package does not make network requests by itself.
- `TargetRepositoryAdapter.preview()` is read-only. `apply()` requires
  `human_approved=True`, a matching base revision, and a clean Git tree;
  `commit_candidate()` writes a patch outside the target repository only.

Do not add credentials to request JSON, task artifacts, event attributes, handoffs, or tests.

## Distribution checklist

1. Run compatibility and full tests on the supported Python/platform matrix.
2. Review migration files and store-backend compatibility before deployment.
3. Configure observability export separately from the local JSONL/recording adapters.
4. Review every remote, browser, OAuth, and credential policy through a Human gate.
5. Only then build a package or deploy a separately approved adapter.
