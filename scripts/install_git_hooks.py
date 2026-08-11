#!/usr/bin/env python3
"""Install the version-controlled hooks for this checkout."""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    subprocess.run(
        ["git", "config", "core.hooksPath", ".githooks"],
        cwd=ROOT,
        check=True,
    )
    hook = ROOT / ".githooks" / "pre-push"
    print(f"Installed {hook.relative_to(ROOT)} via core.hooksPath.")
    print("The hook runs the public release gate before every git push.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
