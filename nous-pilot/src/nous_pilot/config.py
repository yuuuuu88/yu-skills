from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .models import ContractError


class ConfigError(ContractError):
    """Raised when the small user-facing config is invalid."""


_SECRET_ARGUMENT = re.compile(r"(?:api[_-]?key|password|secret|token|private[_-]?key|credential)", re.IGNORECASE)


@dataclass(frozen=True)
class PilotConfig:
    path: Path
    mode: str = "personal"
    cycles: int = 3
    lease_seconds: int = 600
    timeout_seconds: int = 300
    agent_mode: str = "external"
    agent_command: tuple[str, ...] = ()
    reviewers: int = 2
    web_research: bool = True
    remote_mode: str = "ask"

    def __post_init__(self) -> None:
        if self.mode not in {"personal"}:
            raise ConfigError("project.mode must be personal")
        if self.cycles < 1 or self.cycles > 10:
            raise ConfigError("run.cycles must be between 1 and 10")
        if self.lease_seconds < 60 or self.timeout_seconds < 1:
            raise ConfigError("run lease and timeout values are invalid")
        if self.agent_mode not in {"external"}:
            raise ConfigError("agents.mode must be external")
        if any(not item.strip() for item in self.agent_command):
            raise ConfigError("agents.command must contain non-empty strings")
        if self.reviewers < 2 or self.reviewers > 2:
            raise ConfigError("agents.reviewers must be exactly 2 for cross-review")
        if self.remote_mode not in {"ask", "allow", "deny"}:
            raise ConfigError("remote.mode must be ask, allow, or deny")


def _section(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, Mapping):
        raise ConfigError(f"[{name}] must be a table")
    return value


def load_config(path: Path | None = None) -> PilotConfig:
    config_path = (path or Path.cwd() / "nous-pilot.toml").expanduser().resolve()
    data: dict[str, Any] = {}
    if config_path.is_file():
        try:
            data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"could not read {config_path.name}") from exc
    project = _section(data, "project")
    run = _section(data, "run")
    agents = _section(data, "agents")
    research = _section(data, "research")
    remote = _section(data, "remote")
    return PilotConfig(
        path=config_path,
        mode=str(project.get("mode", "personal")),
        cycles=int(run.get("cycles", 3)),
        lease_seconds=int(run.get("lease_seconds", 600)),
        timeout_seconds=int(run.get("timeout_seconds", 300)),
        agent_mode=str(agents.get("mode", "external")),
        agent_command=_command_value(agents.get("command", ())),
        reviewers=int(agents.get("reviewers", 2)),
        web_research=_boolean_value(research.get("web", True), "research.web"),
        remote_mode=str(remote.get("mode", "ask")),
    )


def _command_value(value: Any) -> tuple[str, ...]:
    if value in (None, "", ()):
        return ()
    if isinstance(value, str):
        items = (value,)
    elif isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value):
        items = tuple(item.strip() for item in value)
    else:
        raise ConfigError("agents.command must be a string or a list of strings")
    previous_secret_flag = False
    for item in items:
        if previous_secret_flag:
            raise ConfigError("agents.command must not contain credentials or secret arguments")
        if _SECRET_ARGUMENT.search(item) and (item.startswith("-") or "=" in item):
            raise ConfigError("agents.command must not contain credentials or secret arguments")
        previous_secret_flag = item.lstrip("-").split("=", 1)[0].lower() in {"token", "password", "secret", "api-key", "api_key", "private-key", "private_key", "credential"}
    return items


def _boolean_value(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{label} must be a TOML boolean")
    return value


def write_user_config(path: Path, *, overwrite: bool = False, force: bool = False) -> Path:
    """Create the only user-facing config file with conservative defaults."""
    target = path.expanduser().resolve()
    if target.exists() and not (overwrite or force):
        raise ConfigError(f"configuration already exists: {target.name}")
    if target.exists() and target.is_dir():
        raise ConfigError(f"configuration path is a directory: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        """[project]\nmode = \"personal\"\n\n[run]\ncycles = 3\nlease_seconds = 600\ntimeout_seconds = 300\n\n[agents]\nmode = \"external\"\nreviewers = 2\n# Optional: command that receives a JSON request on stdin and returns a JSON result.\n# command = [\"/absolute/path/to/your-agent\"]\n\n[research]\nweb = true\n\n[remote]\nmode = \"ask\"\n""",
        encoding="utf-8",
    )
    return target


def _absolute_tool(name: str) -> str:
    candidate = Path(name).expanduser()
    if candidate.is_file():
        return str(candidate.resolve())
    for directory in os.environ.get("PATH", "/usr/bin:/bin").split(os.pathsep):
        resolved = Path(directory) / name
        if resolved.is_file():
            return str(resolved.resolve())
    raise ConfigError(f"agent-nous binary was not found: {name}")


def write_internal_engine_config(config: PilotConfig, *, repository_root: Path, path: Path | None = None) -> Path:
    """Generate hidden runtime configuration from the single user config file."""
    root = repository_root.expanduser().resolve()
    target = (path or root / ".nous" / "config.json").expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    agent_dir = root / "scripts" / "agents"
    bridge_script = agent_dir / "external_bridge.py"
    bridge = [sys.executable, str(bridge_script)] if bridge_script.is_file() else [sys.executable, "-m", "nous_pilot.external_bridge"]
    command_json = json.dumps(list(config.agent_command), ensure_ascii=False)
    reviewers = [{"id": f"reviewer-{index}", "perspective": "security and quality" if index == 1 else "requirements and regression"} for index in range(1, config.reviewers + 1)]
    payload = {
        "nous_home": os.environ.get("NOUS_HOME", str(Path.home() / ".local" / "share" / "nous-pilot")),
        "engine": {
            "agents": {role: [*bridge, role, "--command-json", command_json] for role in ("plan", "implement", "review")},
            "review": {"mode": "cross", "fallback": "none", "max_parallel": config.reviewers, "reviewers": [{**item, "command": [*bridge, "review", "--command-json", command_json]} for item in reviewers]},
            "validation": {"allowed_processes": [[sys.executable, "-B", "scripts/validate_compile.py"], [sys.executable, "-B", "-m", "pytest"]]},
            "min_cycles": config.cycles,
            "max_cycles": 10,
            "max_steps": 80,
            "lease_seconds": config.lease_seconds,
            "agent_timeout_seconds": config.timeout_seconds,
            "resources": {"max_concurrent_agents": max(3, config.reviewers + 1), "max_output_bytes": 4194304, "max_tool_calls": 500, "max_wall_seconds": 900},
            "durability": {"mode": "local"},
            "subagents": {"required": True, "mode": config.agent_mode},
            "research": {"enabled": config.web_research, "mode": "open_web_read_only", "public_https": config.web_research, "allow_domains": ["*"], "blocked_private_networks": True, "allow_credentials": False, "allow_external_writes": False, "execute_downloads": False, "max_requests": 20, "max_response_bytes": 10485760, "max_total_bytes": 52428800, "max_concurrent_requests": 2, "timeout_seconds": 30, "max_redirects": 5, "max_results": 10, "search_endpoint_template": "https://html.duckduckgo.com/html/?q={query}", "user_agent": "nous-pilot-research/0.1"},
        },
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target
