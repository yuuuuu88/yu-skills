---
id: OPS-003
type: operations
title: Nous Pilot distribution and compatibility
status: accepted
source_of_truth: true
created: 2026-08-09
updated: 2026-08-09
related: [PLAN-001, OPS-001, ARCH-001]
managed_by: repository-bootstrap
---
# Nous Pilot distribution and compatibility

## Supported runtimes

The package supports Python **3.11 or newer** on:

- Linux
- macOS (`Darwin` in `platform.system()`)
- Windows

The check is intentionally based on the Python version and operating-system
name rather than a distro-specific allowlist. This keeps normal Linux
distributions and Windows/macOS patch releases compatible while making an
unsupported host fail with a useful message.

```bash
PYTHONPATH=src python -c \
  "from nous_pilot.distribution import require_compatibility; print(require_compatibility().as_dict())"
```

Library or launcher code that must stop before doing work should call
`require_compatibility()`. Code that wants to display a diagnostic can call
`check_compatibility()` and inspect `supported` and `reasons` instead.

Examples of actionable failures include:

```text
Python 3.11 or newer is required; detected Python 3.10.13
Unsupported platform 'FreeBSD'; supported platforms are Linux, macOS, and Windows
```

This check does not claim support for a particular shell, init system, cloud,
or provider. Those remain separate adapters and policy boundaries.

## One-file configuration boundary

`nous-pilot.toml` remains the only user-facing project configuration file.
The ignored `.nous/config.json` is generated runtime state and must not be
copied into a wheel, checked into Git, or edited as product configuration.
Packaging metadata in `pyproject.toml` is build configuration, not a second
runtime configuration surface.

## Local build smoke test without dependency installation

The runtime dependency list is empty. A local package smoke test can therefore
use the build backend already present in the environment without installing
the project or resolving anything from the network:

```bash
python -m pip wheel \
  --no-deps \
  --no-build-isolation \
  --no-index \
  --wheel-dir "${TMPDIR:-/tmp}/nous-pilot-dist" \
  .
```

Flags are deliberate:

- `--no-deps` prevents runtime dependency resolution.
- `--no-build-isolation` reuses the existing setuptools/wheel toolchain.
- `--no-index` prevents package-index/network access.

If the existing environment does not contain a usable build backend, stop and
use a prepared build environment; do not silently install packages during a
distribution smoke check. The command is expected to create a wheel only. It
does not publish, upload, deploy, or alter `nous-pilot.toml`.

The same command is used by the build-smoke CI job. CI does not receive
secrets, provider credentials, or deployment permissions.

## Pre-release checklist

Run the following from the repository root:

```bash
python -B scripts/validate_compile.py
python -B -m pytest -q -p no:cacheprovider
python ../skills/repository-bootstrap/scripts/validate_repository_bootstrap.py . --all-docs --strict
python -m pip wheel --no-deps --no-build-isolation --no-index \
  --wheel-dir "${TMPDIR:-/tmp}/nous-pilot-dist" .
```

Review the wheel and source tree for these properties:

1. `.nous/`, task stores, request ledgers, sandboxes, and evaluation output are
   not distribution inputs.
2. No secret or provider credential is present in source, docs, tests, or
   build output.
3. Compatibility checks pass on each supported OS and Python version.
4. Remote Git, CI/PR publication, deployment, and live providers are not
   invoked by a build smoke test.

The bootstrap-validator path above is the repository's existing sibling skill
path. In a standalone checkout, provide that validator in the surrounding
repository layout before running the repository validation command; do not
fetch it as part of a release build.

## Release boundary

Building a wheel is a local artifact operation only. Publishing to an index,
creating a release, pushing Git changes, changing hosted services, or deploying
an adapter requires a separate human-approved operation and is out of scope for
this repository-local checklist.
