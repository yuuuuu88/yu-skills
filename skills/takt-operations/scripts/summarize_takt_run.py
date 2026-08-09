#!/usr/bin/env python3
"""Print a concise, secret-safe summary of a TAKT JSONL run."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def latest_log(repo: Path) -> Path:
    logs = list((repo / ".takt" / "runs").glob("*/logs/*.jsonl"))
    if not logs:
        raise SystemExit("No TAKT JSONL logs found under .takt/runs")
    return max(logs, key=lambda path: path.stat().st_mtime)


def choose_log(value: str | None, repo: Path) -> Path:
    if value is None:
        return latest_log(repo)
    path = Path(value)
    if path.is_dir():
        logs = list(path.glob("logs/*.jsonl"))
        if not logs:
            raise SystemExit(f"No JSONL log found in {path}")
        return max(logs, key=lambda item: item.stat().st_mtime)
    if path.is_file():
        return path
    raise SystemExit(f"Run path does not exist: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", nargs="?", help="run directory or JSONL log; defaults to latest run")
    parser.add_argument("--repo", default=".", help="repository root when finding the latest run")
    args = parser.parse_args()

    log = choose_log(args.run, Path(args.repo).resolve())
    events: list[dict[str, object]] = []
    raw_text = log.read_text(encoding="utf-8", errors="replace")
    for line in raw_text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)

    counts = Counter(str(event.get("type", "unknown")) for event in events)
    starts = [event for event in events if event.get("type") == "step_start"]
    aborts = [event for event in events if event.get("type") == "workflow_abort"]
    last_start = starts[-1] if starts else {}
    last_abort = aborts[-1] if aborts else {}

    print(f"log: {log}")
    print(f"events: {sum(counts.values())}")
    print("event_counts: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    if last_start:
        print(
            "last_step: "
            f"{last_start.get('step', '?')} "
            f"iteration={last_start.get('iteration', '?')} "
            f"provider={last_start.get('provider', '?')} "
            f"model={last_start.get('model', '?')} "
            f"reasoning={last_start.get('providerOptions', {}).get('codex', {}).get('reasoningEffort', '?') if isinstance(last_start.get('providerOptions'), dict) else '?'}"
        )
    if last_abort:
        print(f"abort: {last_abort.get('reason', 'unknown')}")

    indicators = {
        "max_steps": "Max steps reached" in raw_text or "Reached max iterations" in raw_text,
        "provider_400": "invalid_request_error" in raw_text or '"status":400' in raw_text,
        "python_missing": "python: command not found" in raw_text,
        "command_missing": "command not found" in raw_text,
    }
    for name, present in indicators.items():
        if present:
            print(f"indicator: {name}")


if __name__ == "__main__":
    main()
