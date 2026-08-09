from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .errors import ValidationError

TASK_ID = re.compile(r"^[0-9]{8}-[0-9]{4}-[a-z0-9]+(?:-[a-z0-9]+)*(?:-[0-9]{2})?$")
RUNTIME_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:48] or "task"


def make_task_id(title: str, tasks: Path, now: datetime | None = None) -> str:
    timestamp = (now or datetime.now()).strftime("%Y%m%d-%H%M")
    base = f"{timestamp}-{slugify(title)}"
    candidate = base
    suffix = 1
    while (tasks / candidate).exists():
        suffix += 1
        candidate = f"{base}-{suffix:02d}"
    return candidate


def validate_task_id(task_id: str) -> str:
    if not TASK_ID.fullmatch(task_id):
        raise ValidationError(f"invalid task_id: {task_id}")
    return task_id


def validate_runtime_id(value: str, label: str = "runtime_id") -> str:
    """Validate an identifier used as a Task-relative filename."""
    if not RUNTIME_ID.fullmatch(value):
        raise ValidationError(f"{label} must be a simple canonical identifier")
    return value


def safe_child(root: Path, child: str) -> Path:
    validate_task_id(child)
    candidate = (root / child).resolve()
    if candidate.parent != root.resolve():
        raise ValidationError("task path escaped tasks root")
    return candidate
