---
id: RULES-ROOT-001
type: agent-rules
title: yu-skills public repository agent rules
status: accepted
source_of_truth: true
created: 2026-08-12
updated: 2026-08-12
related: [SECURITY-001]
managed_by: repository-maintainer
---

# yu-skills repository agent rules

## Read first

- [README.md](./README.md)
- [SECURITY.md](./SECURITY.md)
- [agent-nous/README.md](./agent-nous/README.md)
- [nous-pilot/AGENTS.md](./nous-pilot/AGENTS.md)

## Public repository boundary

`yu-skills` is a public repository. Never add credentials, API keys, private
keys, environment files, customer data, personal information, local home
directory paths, private URLs, or generated runtime state.

Do not treat a passing test as permission to publish. Check the complete diff,
including untracked files, before committing or pushing.

## Mandatory pre-push workflow

Before every commit intended for publication and before every `git push`, run
the local release gate from the repository root:

```bash
python3 scripts/run_public_release_gate.py
```

If the local security tools have not been installed yet, bootstrap them once:

```bash
python3 scripts/bootstrap_security_tools.py
python3 scripts/install_git_hooks.py
```

The installed pre-push hook runs the same local gate automatically. Do not use
`git push --no-verify` to bypass it. If the gate fails, stop, inspect the
finding, and repair or explicitly escalate it; do not suppress a finding just
to make the push green.

The local gate covers repository hygiene, reachable Git history, GitHub Actions
workflow auditing, Python dependency auditing, and Node dependency auditing.
CodeQL and pull-request Dependency Review remain GitHub-side checks and must
also pass before merging to `main`.

## Branch and remote rules

- Direct pushes to `main` are allowed only after the local public release gate
  passes.
- Never force-push or rewrite public history.
- Do not push a branch containing unresolved security-gate failures.
- The GitHub workflow is a post-push secondary audit; it cannot undo the
  visibility of a commit that was already pushed to this public repository.

## Development workflow

1. Read the relevant design and implementation plan.
2. Use the Nous Loop: Research/Plan → Implement → Test/Review → re-plan.
3. Keep changes scoped and update design or security documents when behavior
   changes.
4. Run project tests in addition to the public release gate.
5. Report created, modified, moved, and deleted files plus validation evidence.
