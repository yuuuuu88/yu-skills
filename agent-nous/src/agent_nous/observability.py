from __future__ import annotations

import fcntl
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from .domain.secrets import secret_summary
from .errors import ValidationError


_TRACE_KEYS = {
    "trace_id", "span_id", "parent_span_id", "name", "phase", "cycle", "loop_id",
    "status", "started_at", "finished_at", "duration_ms", "provider", "external_subagent",
    "agent_run_id", "reviewer_count", "subagent_count", "error",
}


def _primitive(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def sanitize_trace_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Keep trace export metadata bounded and reject secret-like values."""
    candidate = {key: _primitive(event.get(key)) for key in _TRACE_KEYS if key in event}
    candidate["schema_version"] = 1
    if secret_summary(candidate):
        raise ValidationError("trace export contains a high-confidence secret-like value")
    return candidate


class TraceExporter(Protocol):
    def export(self, event: Mapping[str, Any]) -> None:
        ...


class NullTraceExporter:
    def export(self, event: Mapping[str, Any]) -> None:
        return None


class InMemoryTraceExporter:
    """Deterministic test exporter; it stores only sanitized event metadata."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def export(self, event: Mapping[str, Any]) -> None:
        value = sanitize_trace_event(event)
        with self._lock:
            self.events.append(value)


@dataclass
class JsonlTraceExporter:
    """Append secret-free trace events to a local JSONL sink.

    This is an OpenTelemetry-compatible correlation sink, not a vendor SDK.
    A later adapter may translate the same sanitized events to OTLP. The file
    is protected by an advisory lock and each event has a size bound.
    """

    path: Path
    max_event_bytes: int = 256 * 1024

    def __post_init__(self) -> None:
        self.path = self.path.expanduser().resolve()
        if self.max_event_bytes < 1024 or self.max_event_bytes > 4 * 1024 * 1024:
            raise ValidationError("trace max_event_bytes must be between 1024 and 4194304")
        if self.path.exists() and self.path.is_symlink():
            raise ValidationError("trace export path must not be a symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, event: Mapping[str, Any]) -> None:
        value = sanitize_trace_event(event)
        line = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        if len(line) > self.max_event_bytes:
            raise ValidationError("trace event exceeds max_event_bytes")
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(self.path, flags, 0o600)
        try:
            with os.fdopen(fd, "a", encoding="utf-8", closefd=True) as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                handle.write(line.decode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            raise


def export_execution_event(exporter: TraceExporter | None, event: Mapping[str, Any]) -> None:
    if exporter is not None:
        exporter.export(event)
