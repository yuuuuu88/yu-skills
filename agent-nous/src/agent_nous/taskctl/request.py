from __future__ import annotations

import fcntl
import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..errors import ConflictError, ValidationError
from ..io import atomic_write_json, digest_json


_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def validate_request_id(value: object) -> str:
    if not isinstance(value, str) or not _REQUEST_ID.fullmatch(value):
        raise ValidationError("request_id must be a nonempty safe identifier")
    return value


class RequestLedger:
    """Small durable request boundary for the public CLI.

    TaskStore owns task mutations.  This ledger owns the outer request
    envelope so a retried CLI invocation can replay the exact response and a
    different payload cannot reuse the same request ID.  A pending request is
    intentionally not guessed as complete; it is reported as a conflict and
    must be reconciled by a later runtime operation.
    """

    def __init__(self, home: Path) -> None:
        self.root = home.resolve() / "requests"
        self.lock_path = self.root / ".lock"

    def _path(self, request_id: str) -> Path:
        return self.root / f"{validate_request_id(request_id)}.json"

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def begin(self, request_id: str, command: str, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = validate_request_id(request_id)
        payload_hash = digest_json(request)
        path = self._path(request_id)
        with self._lock():
            if path.is_file():
                try:
                    existing = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise ConflictError("request ledger entry is unreadable; reconcile before retrying") from exc
                if existing.get("payload_hash") != payload_hash or existing.get("command") != command:
                    raise ConflictError("request_id was already used with a different payload")
                if existing.get("status") == "completed" and isinstance(existing.get("response"), dict):
                    return existing["response"]
                raise ConflictError("request_id is already pending; reconcile before retrying")
            atomic_write_json(
                path,
                {
                    "schema_version": 1,
                    "request_id": request_id,
                    "command": command,
                    "payload_hash": payload_hash,
                    "status": "pending",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        return None

    def complete(self, request_id: str, command: str, request: dict[str, Any], response: dict[str, Any]) -> None:
        request_id = validate_request_id(request_id)
        path = self._path(request_id)
        payload_hash = digest_json(request)
        with self._lock():
            if not path.is_file():
                raise ConflictError("request ledger entry disappeared before completion")
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("payload_hash") != payload_hash or existing.get("command") != command:
                raise ConflictError("request ledger payload changed before completion")
            atomic_write_json(
                path,
                {
                    **existing,
                    "status": "completed",
                    "response": response,
                },
            )
