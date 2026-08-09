from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Any

from .errors import ValidationError


def _config_nous_home(path: Path) -> str | None:
    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"invalid nous config: {path}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("nous_home"), str) or not value["nous_home"].strip():
        raise ValidationError(f"nous config must contain a nonempty nous_home: {path}")
    return value["nous_home"]


def _project_config(cwd: Path) -> Path | None:
    current = cwd.resolve()
    for directory in (current, *current.parents):
        for candidate in (directory / ".nous" / "config.json", directory / "nous.config.json"):
            if candidate.is_file():
                return candidate
    return None


def resolve_nous_home(
    explicit: str | None = None,
    config_path: str | None = None,
    *,
    cwd: Path | None = None,
) -> Path:
    """Resolve the runtime home without inventing a host-specific fallback.

    The installed Skill must not silently create ``~/.local/share/nous`` or
    place Task state in the current directory.  A caller may still provide a
    user/project config containing an environment-specific absolute path.
    """
    raw = explicit or os.environ.get("NOUS_HOME")
    if not raw and config_path:
        raw = _config_nous_home(Path(config_path).expanduser().resolve())
    if not raw:
        project_config = _project_config(cwd or Path.cwd())
        if project_config:
            raw = _config_nous_home(project_config)
    if not raw:
        user_config = Path.home() / ".config" / "nous" / "config.json"
        raw = _config_nous_home(user_config)
    if not raw:
        raise ValidationError(
            "NOUS_HOME is not configured; set NOUS_HOME, pass --nous-home, or provide a nous config"
        )
    path = Path(raw).expanduser().resolve()
    if path == Path("/"):
        raise ValidationError("NOUS_HOME cannot be the filesystem root")
    return path


def tasks_dir(home: Path) -> Path:
    return home / "tasks"
