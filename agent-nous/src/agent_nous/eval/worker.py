from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from .environment import TrialEnvironment
from ..io import atomic_write_json


def _write_sandbox_probe(environment: TrialEnvironment, raw_environment: dict[str, object]) -> None:
    try:
        current_namespace = hashlib.sha256(os.readlink("/proc/self/ns/net").encode("utf-8")).hexdigest()
    except OSError:
        current_namespace = None
    names = sorted(os.environ)
    suspicious = [
        name
        for name in names
        if any(token in name.upper() for token in ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AWS_", "OPENAI", "ANTHROPIC", "PROXY"))
    ]
    home = Path(os.environ["HOME"])
    atomic_write_json(
        environment.worker_evidence / "sandbox-probe.json",
        {
            "schema_version": 1,
            "network_namespace_isolated": bool(
                current_namespace
                and raw_environment.get("parent_network_namespace_digest")
                and current_namespace != raw_environment.get("parent_network_namespace_digest")
            ),
            "credential_like_env_keys": suspicious,
            "credential_home_empty": home.is_dir() and not any(home.iterdir()),
            "environment_keys": names,
        },
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--environment", required=True)
    parser.add_argument("--handler", required=True)
    args = parser.parse_args(argv)
    try:
        # Import here so the worker has no manifest/grader information beyond
        # its declared scenario name.  Expected state and grader rules remain
        # outside the isolated workload.
        from .runner import EvalRunner

        raw_environment = json.loads(Path(args.environment).read_text(encoding="utf-8"))
        if not isinstance(raw_environment, dict):
            raise ValueError("invalid Eval environment evidence")
        environment = TrialEnvironment.from_json(Path(args.environment))
        _write_sandbox_probe(environment, raw_environment)
        handler = EvalRunner(root=environment.run_root)._handlers().get(args.handler)
        if handler is None:
            raise ValueError("unknown Eval handler")
        observed = handler(environment)
        print(json.dumps(observed, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        # Keep exception detail outside stdout and never persist it verbatim;
        # the parent classifies this as an infrastructure failure by type.
        print(type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
