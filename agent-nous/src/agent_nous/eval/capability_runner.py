from __future__ import annotations

"""Deterministic executable runner for the NCS capability contract."""

import hashlib
import json
import tempfile
import uuid
from pathlib import Path
from typing import Any, Mapping

from ..errors import ValidationError
from ..io import atomic_write_json, digest_json
from .capability import (
    aggregate_suite,
    aggregate_trials,
    capability_observation_from_eval_runner_capture,
    capability_observation_from_eval_trial,
    compare_reports,
    grade_capability_trial,
    load_capability_manifest,
    validate_capability_observation_bundle,
)
from .capability_ops import aggregate_operational_metrics
from .runner import EvalRunner

_PROFILES = ("smoke", "full", "holdout")


class CapabilityEvalRunner:
    def __init__(self, manifest_path: Path | None = None, root: Path | None = None, oracle_path: Path | None = None) -> None:
        self.manifest_path = manifest_path
        self.root = root.resolve() if root else None
        self.oracle_path = oracle_path

    def manifest(self):
        return load_capability_manifest(self.manifest_path)

    def validate(self) -> dict[str, Any]:
        manifest = self.manifest()
        for profile in _PROFILES:
            manifest.select_profile(profile)
        oracle = self._load_observations()
        missing = sorted(set(item["task_id"] for item in manifest.tasks) - set(oracle))
        if missing:
            raise ValidationError(f"capability oracle is missing tasks: {', '.join(missing)}")
        return {
            "valid": True,
            "suite_id": manifest.value["suite_id"],
            "suite_version": manifest.value["suite_version"],
            "manifest_digest": manifest.digest,
            "task_count": len(manifest.tasks),
            "profiles": {profile: [item["task_id"] for item in manifest.select_profile(profile)] for profile in _PROFILES},
            "holdout_policy": "external_observations_required",
        }

    def list(self, profile: str = "full") -> dict[str, Any]:
        manifest = self.manifest()
        tasks = manifest.select_profile(profile)
        return {"profile": profile, "manifest_digest": manifest.digest, "tasks": [
            {"task_id": item["task_id"], "family": item["family"], "difficulty": item["difficulty"], "eval_case_ids": item.get("eval_case_ids", [])} for item in tasks
        ]}

    def run(self, *, profile: str = "smoke", observations_path: Path | None = None, trials: int | None = None, baseline_path: Path | None = None) -> dict[str, Any]:
        if profile == "holdout" and observations_path is None and self.oracle_path is None:
            raise ValidationError("holdout evaluation requires an explicit external --observations bundle")
        manifest = self.manifest()
        tasks = manifest.select_profile(profile)
        expected_trials = int(manifest.value["trial_policy"]["repetitions"])
        repetitions = expected_trials if trials is None else int(trials)
        if repetitions != expected_trials:
            raise ValidationError(f"NCS capability run requires exactly {expected_trials} trials")
        observations = self._load_observations(observations_path)
        source_path = observations_path or self.oracle_path or Path(__file__).with_name("capability_oracles.json")
        source_kind = "bundled_public_oracle" if observations_path is None and self.oracle_path is None else "external_observation_bundle"
        source_digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
        base = self.root or Path(tempfile.mkdtemp(prefix="nous-capability-"))
        base.mkdir(parents=True, exist_ok=True)
        run_id = f"caprun-{uuid.uuid4().hex[:12]}"
        run_root = base / "runs" / run_id
        run_root.mkdir(parents=True, exist_ok=False)
        task_reports: list[dict[str, Any]] = []
        operation_values: list[Mapping[str, Any] | None] = []
        for task in tasks:
            task_id = task["task_id"]
            records = observations.get(task_id)
            if not isinstance(records, list) or not records:
                raise ValidationError(f"capability observations missing task: {task_id}")
            trial_results: list[dict[str, Any]] = []
            for ordinal in range(1, repetitions + 1):
                parent_evidence = records[(ordinal - 1) % len(records)]
                observation = capability_observation_from_eval_trial(task_id, {"outcome": "passed"}, parent_evidence)
                graded = grade_capability_trial(task, observation)
                trial_results.append(graded)
                operation_values.append(observation.get("metrics"))
                trial_root = run_root / "trials" / task_id / f"{ordinal:02d}"
                trial_root.mkdir(parents=True, exist_ok=False)
                atomic_write_json(trial_root / "trial.json", {"schema_version": 1, "task_id": task_id, "trial": ordinal, "evidence_digest": observation["provenance"]["evidence_digest"], "grade": graded, "metrics": observation.get("metrics", {})})
            task_reports.append(aggregate_trials(task, trial_results, expected_trials=expected_trials, minimum_successes=int(manifest.value["trial_policy"]["minimum_successes"])))
        suite = aggregate_suite(task_reports, suite_id=manifest.value["suite_id"])
        report: dict[str, Any] = {
            **suite,
            "report_schema_version": 2,
            "run_id": run_id,
            "profile": profile,
            "suite_version": manifest.value["suite_version"],
            "manifest_digest": manifest.digest,
            "trial_policy": manifest.value["trial_policy"],
            "selected_task_ids": [item["task_id"] for item in tasks],
            "outcome": "passed" if suite["passed_tasks"] == suite["task_count"] else "failed",
            "report_ref": f"runs/{run_id}/report.json",
            "provenance": {"observation_source": source_kind, "observation_bundle_sha256": source_digest},
            "operations": aggregate_operational_metrics(operation_values),
        }
        if baseline_path is not None:
            baseline = self._load_report(baseline_path)
            report["comparison"] = compare_reports(baseline, report)
        atomic_write_json(run_root / "report.json", report)
        return self._public_report(report)

    def run_from_eval_capture(self, *, eval_root: Path, eval_run: str, profile: str = "full", baseline_path: Path | None = None) -> dict[str, Any]:
        """Build a safe observation bundle from a real EvalRunner report."""
        eval_root = eval_root.resolve()
        report = EvalRunner(root=eval_root).report(eval_run)
        run_root = eval_root / "runs" / eval_run
        by_eval_id = {str(item.get("eval_id")): item for item in report.get("cases", []) if isinstance(item, Mapping)}
        bundle: dict[str, Any] = {"schema_version": 1, "source": "eval_runner_parent_capture", "fixture_version": "eval-runner-parent-capture-v1", "observations": {}}
        for task in self.manifest().select_profile(profile):
            candidates = [str(item) for item in task.get("eval_case_ids", [])]
            case = next((by_eval_id[item] for item in candidates if item in by_eval_id), None)
            if not isinstance(case, Mapping):
                raise ValidationError(f"EvalRunner report has no mapped case for capability task: {task['task_id']}")
            records: list[dict[str, Any]] = []
            for trial in case.get("trials", []):
                trial_id = trial.get("trial_id") if isinstance(trial, Mapping) else None
                if not isinstance(trial_id, str) or "/" in trial_id or "\\" in trial_id:
                    raise ValidationError("EvalRunner trial id is invalid")
                evidence_root = run_root / "evidence" / trial_id
                observation = capability_observation_from_eval_runner_capture(evidence_root, task_id=task["task_id"], eval_id=str(case["eval_id"]), trial_outcome=str(trial.get("outcome")))
                raw = dict(observation)
                raw.pop("provenance", None)
                records.append({"source": "eval_runner_parent_capture", "evidence_digest": observation["provenance"]["evidence_digest"], "capability_observation": raw})
            if not records:
                raise ValidationError(f"EvalRunner case has no trials for capability task: {task['task_id']}")
            bundle["observations"][task["task_id"]] = records
        validate_capability_observation_bundle(bundle)
        root = self.root or Path(tempfile.mkdtemp(prefix="nous-capability-from-eval-"))
        self.root = root.resolve()
        capture_dir = self.root / "captures"
        capture_dir.mkdir(parents=True, exist_ok=True)
        capture_path = capture_dir / f"{eval_run}-{profile}.json"
        atomic_write_json(capture_path, bundle)
        return self.run(profile=profile, observations_path=capture_path, baseline_path=baseline_path)

    def report(self, run: str, fmt: str = "json") -> dict[str, Any] | str:
        path = self._run_path(run) / "report.json"
        report = self._public_report(json.loads(path.read_text(encoding="utf-8")))
        if fmt == "json":
            return report
        if fmt != "markdown":
            raise ValidationError("capability report format must be json or markdown")
        lines = [f"# Nous Capability Report {report['run_id']}", "", f"Outcome: **{report['outcome']}**", f"Profile: `{report['profile']}`", f"NCS: **{report['ncs']}**", f"Observation source: `{report['provenance']['observation_source']}`", "", "| Task | Score | Verdict | Reliability |", "| --- | ---: | --- | ---: |"]
        for task_id, score in report["task_scores"].items():
            lines.append(f"| {task_id} | {score} | {'passed' if score > 0 else 'failed'} | {report['task_reliability']} |")
        if report.get("comparison"):
            lines.extend(["", f"NCS delta: **{report['comparison']['ncs_delta']}**"])
        return "\n".join(lines) + "\n"

    def _run_path(self, run: str) -> Path:
        if not isinstance(run, str) or not run.startswith("caprun-") or "/" in run or "\\" in run:
            raise ValidationError("invalid capability run id")
        root = (self.root or Path.cwd()).resolve()
        path = (root / "runs" / run).resolve()
        if root not in path.parents or not (path / "report.json").is_file():
            raise ValidationError("capability run is not found below the configured root")
        return path

    def _load_report(self, path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or "ncs" not in value or "dimension_scores" not in value or "task_scores" not in value:
            raise ValidationError("baseline capability report is invalid")
        return value

    def _load_observations(self, path: Path | None = None) -> dict[str, list[dict[str, Any]]]:
        source = path or self.oracle_path or Path(__file__).with_name("capability_oracles.json")
        value = json.loads(source.read_text(encoding="utf-8"))
        validate_capability_observation_bundle(value)
        observations = value["observations"]
        return {str(key): [dict(item) for item in records] for key, records in observations.items()}

    @staticmethod
    def _public_report(report: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(report)
        result.pop("run_path", None)
        return result
