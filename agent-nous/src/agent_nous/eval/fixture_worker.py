from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .environment import TrialEnvironment


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--environment", required=True)
    parser.add_argument("--eval-id", required=True)
    parser.add_argument("--phase", default="setup", choices=("setup", "after-ahead"))
    args = parser.parse_args(argv)
    try:
        # Fixture provisioning is intentionally separate from subject actions,
        # but it still receives the same empty credential / no-network /
        # write-root boundary as a worker.  It never creates a Task or claims
        # product evidence.
        from .runner import EvalRunner

        environment = TrialEnvironment.from_json(Path(args.environment))
        EvalRunner._prepare_fixture({"eval_id": args.eval_id, "phase": args.phase}, environment)
        return 0
    except Exception as exc:
        print(type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
