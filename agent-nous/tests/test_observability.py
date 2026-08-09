from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agent_nous.observability import InMemoryTraceExporter, JsonlTraceExporter

def test_trace_exporter_sanitizes_and_writes_secret_free_metadata():
    event = {"trace_id":"a"*32,"span_id":"b"*16,"phase":"review","duration_ms":3.0,"prompt":"must not be exported"}
    memory = InMemoryTraceExporter(); memory.export(event)
    assert memory.events[0]["phase"] == "review"
    assert "prompt" not in memory.events[0]
    import tempfile
    with tempfile.NamedTemporaryFile() as handle:
        JsonlTraceExporter(Path(handle.name)).export(event)
