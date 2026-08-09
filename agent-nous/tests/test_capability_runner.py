from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_nous.cli import main
from agent_nous.errors import ValidationError
from agent_nous.eval.capability import capability_observation_from_eval_trial
from agent_nous.eval.capability_runner import CapabilityEvalRunner


def test_capability_profiles_and_full_runner_are_deterministic(tmp_path: Path) -> None:
    runner = CapabilityEvalRunner(root=tmp_path)
    validation = runner.validate()
    assert validation["task_count"] == 10
    assert len(validation["profiles"]["smoke"]) == 3
    result = runner.run(profile="full")
    assert result["outcome"] == "passed"
    assert result["provenance"]["observation_source"] == "bundled_public_oracle"
    assert "operations" in result
    assert result["task_count"] == 10
    assert result["selected_task_ids"] == validation["profiles"]["full"]
    assert result["trial_policy"]["repetitions"] == 3
    assert not result["report_ref"].startswith("/")
    assert str(tmp_path) not in json.dumps(result)


def test_capability_runner_supports_baseline_comparison_and_markdown(tmp_path: Path) -> None:
    runner = CapabilityEvalRunner(root=tmp_path)
    baseline = runner.run(profile="smoke")
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    candidate = runner.run(profile="smoke", baseline_path=baseline_path)
    assert candidate["comparison"]["ncs_delta"] == pytest.approx(0)
    markdown = runner.report(candidate["run_id"], "markdown")
    assert "Nous Capability Report" in markdown
    assert str(tmp_path) not in markdown


def test_parent_capture_adapter_rejects_forged_or_nonportable_input() -> None:
    with pytest.raises(ValidationError, match="canonical parent capture"):
        capability_observation_from_eval_trial("T01-basic-implementation", {"outcome":"passed"}, {"source":"worker"})
    fixture = {
        "source":"eval_runner_parent_capture",
        "evidence_digest":"a" * 64,
        "capability_observation": {"task_id":"T01-basic-implementation", "raw_output":"do not accept"},
    }
    with pytest.raises(ValidationError, match="raw_output|missing"):
        capability_observation_from_eval_trial("T01-basic-implementation", {"outcome":"passed"}, fixture)


def test_capability_cli_smoke_and_validation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["eval", "capability-validate"]) == 0
    assert main(["eval", "capability-smoke", "--root", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert '"outcome": "passed"' in output


def test_holdout_requires_external_observations(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="external"):
        CapabilityEvalRunner(root=tmp_path).run(profile="holdout")
