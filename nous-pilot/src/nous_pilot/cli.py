from __future__ import annotations

import argparse
import json
import os
import shutil
import uuid
from pathlib import Path

from .config import PilotConfig, load_config, write_internal_engine_config, write_user_config
from .context import build_repository_context
from .control_plane import PilotControlPlane
from .distribution import require_compatibility
from .intake import normalize_request
from .runtime import NousRuntimeClient, RuntimeConfig


def _load_user_config(args: argparse.Namespace) -> PilotConfig:
    return load_config(Path(args.config))


def _runtime(args: argparse.Namespace, config: PilotConfig) -> NousRuntimeClient:
    binary_name = args.nous_binary or os.environ.get("NOUS_BINARY", "nous")
    binary = Path(binary_name).expanduser()
    if not binary.is_file():
        located = shutil.which(binary_name)
        if located is None:
            raise ValueError("agent-nous binary was not found; set NOUS_BINARY or use --nous-binary")
        binary = Path(located)
    home = Path(args.nous_home or os.environ.get("NOUS_HOME", Path.home() / ".local" / "share" / "nous-pilot"))
    timeout = args.timeout_seconds or config.timeout_seconds
    return NousRuntimeClient(RuntimeConfig(binary, home, timeout))


def _request(path: str) -> dict[str, object]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("request file must contain a JSON object")
    return value


def _objective_request(args: argparse.Namespace) -> dict[str, object]:
    repository = getattr(args, "repository_root", getattr(args, "repository", "."))
    risk = getattr(args, "risk_class", getattr(args, "risk", "green"))
    request = normalize_request(
        {
            "request_id": args.request_id or f"req-{uuid.uuid4().hex[:12]}",
            "objective": args.objective,
            "repository_root": repository,
            "constraints": getattr(args, "constraint", []),
            "non_goals": getattr(args, "non_goal", []),
            "risk_class": risk,
            "validation_profile": args.validation_profile,
            "base_revision": getattr(args, "base_revision", None),
        }
    )
    return request.as_dict()


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _task_repository_root(payload: object) -> Path | None:
    """Extract a task's target without trusting arbitrary nested values."""
    if not isinstance(payload, dict):
        return None
    candidates: list[object] = [payload.get("target_repository"), payload.get("repository_root")]
    result = payload.get("result")
    if isinstance(result, dict):
        candidates.extend((result.get("target_repository"), result.get("repository_root")))
        request = result.get("request")
        if isinstance(request, dict):
            candidates.extend((request.get("repository_root"), request.get("target_repository")))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            root = Path(candidate).expanduser().resolve()
            if root.is_dir():
                return root
    return None


def _add_config_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="nous-pilot.toml", help="optional single user config file")
    parser.add_argument("--nous-binary", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--nous-home", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--timeout-seconds", type=float, default=None, help=argparse.SUPPRESS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nous-pilot")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="build a safe repository context summary")
    inspect.add_argument("repository", nargs="?", default=".")
    init = sub.add_parser("init", help="create a small personal nous-pilot.toml")
    init.add_argument("--config", default="nous-pilot.toml")
    init.add_argument("--force", action="store_true", help="replace an existing config file")
    objective = sub.add_parser("objective", aliases=["request"], help="emit a validated request JSON object")
    objective.add_argument("objective", metavar="OBJECTIVE")
    objective.add_argument("--repository-root", "--repository", "--repo", dest="repository_root", default=".")
    objective.add_argument("--request-id", default=None)
    objective.add_argument("--constraint", action="append", default=[])
    objective.add_argument("--non-goal", action="append", default=[])
    objective.add_argument("--risk-class", choices=("green", "yellow", "red"), default="green")
    objective.add_argument("--validation-profile", default="unit")
    objective.add_argument("--base-revision", default=None)
    doctor = sub.add_parser("doctor", help="check the local agent-nous runtime")
    _add_config_options(doctor)
    status = sub.add_parser("status", help="read task status without starting work")
    status.add_argument("task_id", nargs="?", default=None)
    status.add_argument("--include-archived", action="store_true")
    _add_config_options(status)
    show = sub.add_parser("show", help="read one durable Root Task")
    show.add_argument("task_id")
    _add_config_options(show)
    create = sub.add_parser("create", help="create a durable local Root Task")
    create.add_argument("request_file")
    create.add_argument("--run-id", required=True)
    _add_config_options(create)
    run = sub.add_parser("run", help="create and execute a durable local Root Task")
    run.add_argument("request_file")
    run.add_argument("--run-id", required=True)
    run.add_argument("--engine-config", default=None, help=argparse.SUPPRESS)
    _add_config_options(run)
    start = sub.add_parser("start", help="start a task from a plain-language objective")
    start.add_argument("objective")
    start.add_argument("--repository", default=".")
    start.add_argument("--request-id", default=None)
    start.add_argument("--run-id", required=True)
    start.add_argument("--risk", choices=("green", "yellow", "red"), default="green")
    start.add_argument("--validation-profile", default="unit")
    start.add_argument("--engine-config", default=None, help=argparse.SUPPRESS)
    _add_config_options(start)
    resume = sub.add_parser("resume", help="prepare and resume a durable Root Task")
    resume.add_argument("task_id")
    resume.add_argument("--execute", "--run", dest="execute", action="store_true", help="prepare the task and start the configured loop")
    resume.add_argument("--run-id", default=None, help=argparse.SUPPRESS)
    resume.add_argument("--engine-config", default=None, help=argparse.SUPPRESS)
    _add_config_options(resume)
    args = parser.parse_args(argv)
    require_compatibility()
    if args.command == "inspect":
        context = build_repository_context(Path(args.repository))
        _print(context.as_dict())
        return 0
    if args.command == "init":
        path = write_user_config(Path(args.config), force=args.force)
        _print({"config": str(path), "created": True, "forced": args.force})
        return 0
    if args.command in {"objective", "request"}:
        _print(_objective_request(args))
        return 0
    config = _load_user_config(args)
    if args.command == "doctor":
        _print(_runtime(args, config).doctor().payload)
        return 0
    runtime = _runtime(args, config)
    if args.command == "status":
        response = runtime.show(args.task_id) if args.task_id else runtime.status(include_archived=args.include_archived)
        _print({"read_only": True, "config": str(config.path), "runtime": dict(response.payload)})
        return 0
    if args.command == "show":
        _print({"read_only": True, "config": str(config.path), "runtime": dict(runtime.show(args.task_id).payload)})
        return 0
    if args.command == "resume":
        if not args.execute:
            _print({
                "task_id": args.task_id,
                "read_only": True,
                "resume": {
                    "prepared": False,
                    "execution_started": False,
                    "next_command": f"nous-pilot resume {args.task_id} --execute",
                },
                "runtime": dict(runtime.show(args.task_id).payload),
            })
            return 0
        current = runtime.show(args.task_id)
        target_root = _task_repository_root(current.payload) or config.path.parent
        prepared = runtime.resume_prepare(args.task_id, run_id=args.run_id)
        engine_config = Path(args.engine_config).expanduser() if args.engine_config else write_internal_engine_config(config, repository_root=target_root)
        executed = runtime.engine_run(args.task_id, engine_config=engine_config)
        _print({"task_id": args.task_id, "read_only": False, "resume": dict(prepared.payload), "execution": dict(executed.payload), "config": str(config.path)})
        return 0
    control_plane = PilotControlPlane(_runtime(args, config))
    request = _request(args.request_file) if args.command in {"create", "run"} else _objective_request(args)
    result = control_plane.create(request, run_id=args.run_id)
    output: dict[str, object] = {"task_id": result.task_id, "request": result.request.as_dict(), "created": dict(result.created.payload), "config": str(config.path)}
    if args.command in {"run", "start"}:
        engine_config = Path(args.engine_config).expanduser() if args.engine_config else write_internal_engine_config(config, repository_root=result.request.repository_root)
        executed = control_plane.runtime.engine_run(result.task_id, engine_config=engine_config)
        output["execution"] = dict(executed.payload)
    _print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
