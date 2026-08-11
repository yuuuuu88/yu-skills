# Security and public-release policy

## Before code becomes public

This repository is public. Direct pushes to `main` are allowed by the project
workflow, but only after the local release gate passes. Each checkout can
install a version-controlled local `pre-push` hook. Run
`python3 scripts/bootstrap_security_tools.py` once, followed by
`python3 scripts/install_git_hooks.py`. The hook runs
`python3 scripts/run_public_release_gate.py` and fails closed when a required
tool or check is unavailable.
The following checks are also run by GitHub on push (and on pull requests when
used):

- `Security / repository hygiene`
- `Security / workflow audit`
- `Security / Python dependencies`
- `Security / Node dependencies`
- `Security / dependency review`
- `Security / CodeQL (python)`
- `Security / CodeQL (actions)`

The workflow also runs on every push. It is a secondary audit: a push workflow
cannot make a direct push private after the push. The local pre-push hook is the
actual pre-publication gate for this repository's direct-to-`main` workflow.

## What the gate checks

- local home-directory paths and non-example email addresses
- private-key files and common credential/token formats
- reachable Git history, not only the current checkout
- GitHub Actions workflow risks
- Python and Node dependency vulnerabilities
- CodeQL findings in Python and GitHub Actions workflows
- newly introduced dependency vulnerabilities on pull requests

The repository-specific scan intentionally does not print matching secret
values in CI logs.

The local hook can technically be bypassed, so do not use
`git push --no-verify`. The GitHub workflow is the secondary audit; a remote
ruleset is optional for this direct-to-`main` workflow, but can be enabled for
teams that want an additional merge gate.

## Optional GitHub repository settings

For an additional remote-side safety net, enable these settings in the
repository's `Settings` page:

1. Require the checks listed above when using pull requests.
2. Enable Secret scanning and Push protection.
3. Enable Dependabot alerts and security updates.

## Reporting a vulnerability

Do not open a public issue containing an exploit or credential. Use GitHub's
private vulnerability reporting channel when it is enabled for this
repository.
