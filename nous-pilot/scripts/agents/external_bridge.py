"""Checkout-friendly entry point for the packaged agent-nous bridge."""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))

from nous_pilot.external_bridge import main


if __name__ == "__main__":
    raise SystemExit(main())
