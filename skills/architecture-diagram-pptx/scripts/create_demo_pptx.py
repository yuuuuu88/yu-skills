#!/usr/bin/env python3
"""Compatibility wrapper for the standard draw.io-inspired PPTX generator.

The previous version hand-wrote ``p:cxnSp`` PresentationML and could produce
negative connector extents.  Keep the old command name for callers, but route
all spec generation through the tested PptxGenJS implementation.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--spec", type=Path, required=True)
    args = parser.parse_args()
    candidates = [os.environ.get("NODE_BIN", ""), "node"]
    node = next((candidate for candidate in candidates if candidate and shutil.which(candidate)), None)
    if node is None:
        print("Node.js is required; run npm ci in scripts/ or set NODE_BIN", file=sys.stderr)
        return 2
    command = [node, str(HERE / "create_drawio_pptx.cjs"), "--spec", str(args.spec), str(args.output)]
    return subprocess.run(command).returncode


if __name__ == "__main__":
    raise SystemExit(main())
