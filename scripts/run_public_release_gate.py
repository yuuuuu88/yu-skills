#!/usr/bin/env python3
"""Run all security checks that can be executed before a remote push.

GitHub-only checks such as CodeQL upload and pull-request Dependency Review are
kept in `.github/workflows/security-gate.yml`; this command is their local,
fail-closed companion.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SECURITY_VENV = ROOT / ".security-venv"


def _tool(name: str) -> str | None:
    local = SECURITY_VENV / "bin" / name
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    return shutil.which(name)


def _security_python() -> str:
    local = SECURITY_VENV / "bin" / "python"
    if local.is_file():
        return str(local)
    return sys.executable


def _run(label: str, command: list[str], *, cwd: Path = ROOT) -> None:
    print(f"\n== {label} ==")
    print("$ " + " ".join(command))
    completed = subprocess.run(command, cwd=cwd, check=False)
    if completed.returncode:
        raise SystemExit(f"{label} failed with exit code {completed.returncode}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pre-push", action="store_true", help=argparse.SUPPRESS)
    parser.parse_args()

    _run("Git diff hygiene", ["git", "diff", "--check"])
    _run("Repository and history security scan", [sys.executable, "scripts/security_scan.py"])

    zizmor = _tool("zizmor")
    if zizmor is None:
        print(
            "zizmor is not installed. Run "
            "python3 scripts/bootstrap_security_tools.py first.",
            file=sys.stderr,
        )
        return 2
    _run("GitHub Actions workflow audit", [zizmor, "--offline", ".github/workflows"])

    audit_python = _security_python()
    if audit_python == sys.executable and importlib.util.find_spec("pip_audit") is None:
        print(
            "pip-audit is not installed. Run "
            "python3 scripts/bootstrap_security_tools.py first.",
            file=sys.stderr,
        )
        return 2
    _run("agent-nous dependency audit", [audit_python, "-m", "pip_audit", "--strict", "./agent-nous"])
    _run("nous-pilot dependency audit", [audit_python, "-m", "pip_audit", "--strict", "./nous-pilot"])

    npm = shutil.which("npm")
    if npm is None:
        print("npm is not installed; refusing to bypass the Node dependency audit.", file=sys.stderr)
        return 2
    node_dir = ROOT / "skills" / "architecture-diagram-pptx" / "scripts"
    _run("Node dependency installation", [npm, "ci", "--ignore-scripts", "--no-audit"], cwd=node_dir)
    _run("Node dependency audit", [npm, "audit", "--audit-level=high"], cwd=node_dir)

    print("\nPublic release gate passed locally.")
    print("GitHub-side CodeQL and Dependency Review must still pass on the pull request.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
