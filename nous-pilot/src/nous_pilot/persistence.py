from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .handoff import _assert_safe
from .models import ContractError, safe_identifier


class TaskStore(Protocol):
    def put_task(self, task_id: str, payload: Mapping[str, Any]) -> None: ...
    def get_task(self, task_id: str) -> dict[str, Any] | None: ...
    def append_event(self, task_id: str, event: Mapping[str, Any]) -> None: ...
    def list_events(self, task_id: str) -> tuple[dict[str, Any], ...]: ...


class SQLiteTaskStore:
    """Durable shared-store equivalent for local and single-host deployments."""

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS task_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, event TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            """)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def put_task(self, task_id: str, payload: Mapping[str, Any]) -> None:
        safe_identifier(task_id, "task_id")
        _assert_safe(payload)
        encoded = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            connection.execute("INSERT INTO tasks(task_id, payload) VALUES (?, ?) ON CONFLICT(task_id) DO UPDATE SET payload=excluded.payload, updated_at=CURRENT_TIMESTAMP", (task_id, encoded))

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def append_event(self, task_id: str, event: Mapping[str, Any]) -> None:
        safe_identifier(task_id, "task_id")
        _assert_safe(event)
        with self._connect() as connection:
            connection.execute("INSERT INTO task_events(task_id, event) VALUES (?, ?)", (task_id, json.dumps(dict(event), ensure_ascii=False, sort_keys=True)))

    def list_events(self, task_id: str) -> tuple[dict[str, Any], ...]:
        with self._connect() as connection:
            rows = connection.execute("SELECT event FROM task_events WHERE task_id = ? ORDER BY event_id", (task_id,)).fetchall()
        return tuple(json.loads(row[0]) for row in rows)
