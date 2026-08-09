from pathlib import Path

import pytest

from nous_pilot.distribution import check_compatibility
from nous_pilot.models import ContractError
from nous_pilot.observability import JSONLExporter, ObservationEvent, RecordingExporter
from nous_pilot.persistence import SQLiteTaskStore


def test_sqlite_store_is_durable_and_secret_safe(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "tasks.db")
    store.put_task("task-1", {"objective": "test"})
    store.append_event("task-1", {"type": "created"})
    assert store.get_task("task-1")["objective"] == "test"
    assert store.list_events("task-1")[0]["type"] == "created"
    with pytest.raises(ContractError):
        store.put_task("task-2", {"token": "secret"})


def test_observability_and_compatibility_are_local() -> None:
    event = ObservationEvent("phase.completed", "trace-1", {"role": "review"})
    recorder = RecordingExporter()
    recorder.export(event)
    assert recorder.events[0].as_dict()["trace_id"] == "trace-1"
    assert check_compatibility().supported
