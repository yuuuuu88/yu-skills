#!/usr/bin/env python3
"""Create the repository-local virtualenv used by the pre-push security gate."""

from __future__ import annotations

import subprocess
import sys
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SECURITY_VENV = ROOT / ".security-venv"
TOOLS = ("zizmor==1.29.0", "pip-audit==2.10.1")


def main() -> int:
    print(f"Preparing {SECURITY_VENV.relative_to(ROOT)}")
    venv.EnvBuilder(with_pip=True, clear=False, symlinks=True).create(SECURITY_VENV)
    python = SECURITY_VENV / "bin" / "python"
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            *TOOLS,
        ],
        cwd=ROOT,
        check=True,
    )
    print("Local security tools are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
