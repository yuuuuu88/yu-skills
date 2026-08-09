from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from .handoff import _assert_safe


@dataclass(frozen=True)
class ObservationEvent:
    event_type: str
    trace_id: str
    attributes: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        _assert_safe(self.attributes)
        return {"event_type": self.event_type, "trace_id": self.trace_id, "attributes": dict(self.attributes)}


class EventExporter(Protocol):
    def export(self, event: ObservationEvent) -> None: ...


class JSONLExporter:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, event: ObservationEvent) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.as_dict(), ensure_ascii=False, sort_keys=True) + "\n")


class RecordingExporter:
    def __init__(self) -> None:
        self.events: list[ObservationEvent] = []

    def export(self, event: ObservationEvent) -> None:
        self.events.append(event)


class OTLPExporter:
    """Vendor-neutral OTLP boundary; the host supplies the network sender."""

    def __init__(self, sender: Callable[[Mapping[str, Any]], None]) -> None:
        self.sender = sender

    def export(self, event: ObservationEvent) -> None:
        self.sender(event.as_dict())


def digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
