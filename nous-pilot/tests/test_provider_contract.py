from pathlib import Path

from nous_pilot.models import AgentResult
from nous_pilot.orchestrator import PilotEngine, PilotEngineConfig
from nous_pilot.provider import RecordingProvider
from nous_pilot.verification import VerificationPolicy, VerificationRunner


def test_engine_runs_research_implementation_test_and_two_independent_reviews(tmp_path: Path) -> None:
    provider = RecordingProvider()
    request = {"request_id": "req-1", "objective": "inspect safely", "repository_root": str(tmp_path)}
    engine = PilotEngine(
        provider,
        VerificationRunner(VerificationPolicy((("/usr/bin/python3", "-c"),))),
        PilotEngineConfig(min_cycles=1, max_cycles=1, runtime_root=tmp_path / "runtime"),
    )
    report = engine.run(request)
    assert report.outcome == "completed"
    assert [call[0] for call in provider.calls] == ["research", "plan", "implement", "test", "review", "review"]
    assert {call[1]["reviewer_id"] for call in provider.calls if call[0] == "review"} == {"reviewer-1", "reviewer-2"}


def test_engine_replans_after_blocking_review(tmp_path: Path) -> None:
    provider = RecordingProvider(responses={
        "review": [
            AgentResult("review", "success", "blocking", {"findings": [{"finding_id": "f1", "severity": "major", "summary": "missing evidence"}]}),
            AgentResult("review", "success", "blocking", {"findings": [{"finding_id": "f2", "severity": "major", "summary": "missing evidence"}]}),
            AgentResult("review", "success", "pass", {"findings": []}),
            AgentResult("review", "success", "pass", {"findings": []}),
        ]
    })
    engine = PilotEngine(
        provider,
        VerificationRunner(VerificationPolicy((("/usr/bin/python3", "-c"),))),
        PilotEngineConfig(min_cycles=2, max_cycles=2, runtime_root=tmp_path / "runtime"),
    )
    report = engine.run({"request_id": "req-2", "objective": "replan", "repository_root": str(tmp_path)})
    assert report.outcome == "completed"
    assert report.cycles == 2
    assert report.diagnostics[0] == "cycle 1 requested re-plan"


def test_engine_blocks_when_test_agent_fails(tmp_path: Path) -> None:
    provider = RecordingProvider(responses={
        "test": [AgentResult("test", "failed", "test agent unavailable", {})],
    })
    engine = PilotEngine(
        provider,
        VerificationRunner(VerificationPolicy((('/usr/bin/python3', '-c'),))),
        PilotEngineConfig(min_cycles=1, max_cycles=1, runtime_root=tmp_path / "runtime"),
    )
    report = engine.run({"request_id": "req-test-failure", "objective": "block", "repository_root": str(tmp_path)})
    assert report.outcome == "blocked"
    assert "test provider blocked" in report.diagnostics[0]
