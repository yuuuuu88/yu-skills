from __future__ import annotations

import json
import hashlib
import base64
import hmac
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..domain import ActionDescriptor, HumanApprovalAuthority, PolicyGateway, approval_answer_message, secret_summary
from ..executor import ExecutionResult, ManagedExecutor
from ..git_adapter import LocalGitWorktreeManager, remote_state
from ..io import atomic_write_json, digest_json
from ..knowledge import KnowledgeStore
from ..orchestrator import Orchestrator, PhaseResultDraft
from ..parallel import ChildContract
from ..subskills import SubskillRegistry, SubskillSelector
from ..taskctl import TaskStore
from ..trigger import should_start_managed_workflow
from .environment import EvalSandbox, TrialEnvironment
from .graders import events_for, grade_case
from .manifest import HANDLER_NAMES, EvalManifest, load_manifest


_SANDBOX_PYTHON = "/usr/bin/python3"
_SANDBOX_TEST_PREFIX = (_SANDBOX_PYTHON, "-B", "-m", "unittest")


def _owner_key_path(root: Path) -> Path:
    key_id = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:24]
    return root.resolve().parent / f".nous-eval-owner-{key_id}.key"


def _load_owner_key(root: Path) -> tuple[str, bytes]:
    path = _owner_key_path(root)
    try:
        key = path.read_bytes()
    except FileNotFoundError:
        key = os.urandom(32)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(path, flags, 0o600)
        try:
            os.write(descriptor, key)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    if len(key) < 32:
        raise ValueError("Eval ownership key is invalid")
    return path.name.removeprefix(".nous-eval-owner-").removesuffix(".key"), key


def _sign_owner(owner: dict[str, Any], key: bytes) -> str:
    unsigned = {name: value for name, value in owner.items() if name not in {"owner_digest", "owner_signature"}}
    payload = json.dumps(unsigned, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


class EvalRunner:
    """Manifest-driven, isolated release evaluator.

    The runner owns only a caller-selected temporary root.  It never reads a
    normal NOUS_HOME and every Git remote used by a durability case is a bare
    repository allocated below that root.
    """

    def __init__(self, manifest_path: Path | None = None, root: Path | None = None) -> None:
        self.manifest_path = manifest_path
        self.root = root.resolve() if root else None

    def manifest(self) -> EvalManifest:
        return load_manifest(self.manifest_path)

    def validate_manifest(self, stage: int | None = None) -> dict[str, Any]:
        manifest = self.manifest()
        selected = manifest.select(stage=stage, release=None) if stage is not None else list(manifest.cases)
        return {
            "valid": True,
            "manifest_digest": manifest.digest,
            "case_count": len(manifest.cases),
            "selected_case_count": len(selected),
            "fixtures": sorted(manifest.fixtures),
        }

    def list(
        self, *, stage: int | None = None, release: str | None = None, eval_ids: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        manifest = self.manifest()
        cases = manifest.select(stage=stage, release=release, eval_ids=eval_ids)
        return {
            "manifest_digest": manifest.digest,
            "stage": stage,
            "release": release,
            "cases": [
                {
                    "eval_id": item["eval_id"],
                    "category": item["category"],
                    "handler": item["handler"],
                    "selection_status": item["selection_status"],
                    "trial_repetitions": item["trial"]["repetitions"],
                }
                for item in cases
            ],
        }

    def run(
        self,
        *,
        stage: int | None = None,
        release: str | None = None,
        eval_ids: tuple[str, ...] = (),
        trials: int | None = None,
    ) -> dict[str, Any]:
        if trials is not None and trials < 1:
            raise ValueError("--trials must be at least 1")
        manifest = self.manifest()
        selected = manifest.select(stage=stage, release=release, eval_ids=eval_ids)
        # A root is a durable ownership boundary.  When callers omit one we
        # allocate it here and retain it on this runner instance so report and
        # cleanup cannot later be pointed at an arbitrary directory.
        base = self.root or Path(tempfile.mkdtemp(prefix="nous-eval-"))
        self.root = base.resolve()
        base.mkdir(parents=True, exist_ok=True)
        owner_key_id, owner_key = _load_owner_key(self.root)
        run_id = f"evalrun-{uuid.uuid4().hex[:12]}"
        run_root = base / "runs" / run_id
        run_root.mkdir(parents=True, exist_ok=False)
        run_meta = {
            "schema_version": 1,
            "run_id": run_id,
            "manifest_digest": manifest.digest,
            "stage": stage,
            "release": release,
            "started_at": _timestamp(),
            "network_policy": "deny",
            "credential_policy": "none",
            "ownership_key_id": owner_key_id,
        }
        atomic_write_json(run_root / "run.json", run_meta)
        owner = {
            "schema_version": 1,
            "run_id": run_id,
            "manifest_digest": manifest.digest,
            # The token does not authenticate an external caller.  It binds
            # the marker to the run metadata and makes accidental hand-made
            # directories fail closed before cleanup touches anything.
            "ownership_token": uuid.uuid4().hex,
            "ownership_key_id": owner_key_id,
        }
        owner["owner_signature"] = _sign_owner(owner, owner_key)
        owner["owner_digest"] = hashlib.sha256(
            json.dumps(owner, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        atomic_write_json(run_root / "owner.json", owner)
        case_results: list[dict[str, Any]] = []
        for case in selected:
            if case["selection_status"] == "registered_not_runnable":
                case_results.append(
                    {
                        "eval_id": case["eval_id"],
                        "selection_status": case["selection_status"],
                        "outcome": "not_run",
                        "trials": [],
                    }
                )
                continue
            repetitions = trials if trials is not None else int(case["trial"]["repetitions"])
            records = [
                self._run_trial(run_root, case, ordinal, requested_stage=stage, requested_release=release, owner_key=owner_key)
                for ordinal in range(1, repetitions + 1)
            ]
            failed_records = [item for item in records if item["outcome"] != "passed"]
            threshold = int(case["outcome"]["flake_threshold"])
            if not failed_records:
                outcome = "passed"
            elif len(failed_records) <= threshold:
                outcome = "flake"
            else:
                outcome = "failed"
            case_results.append(
                {
                    "eval_id": case["eval_id"],
                    "selection_status": case["selection_status"],
                    "outcome": outcome,
                    "first_failure": failed_records[0] if failed_records else None,
                    "flake_threshold": threshold,
                    "trials": records,
                }
            )
        required = [item for item in case_results if item["selection_status"] == "runnable_required"]
        required_failures = [item["eval_id"] for item in required if item["outcome"] != "passed"]
        result = {
            **run_meta,
            "finished_at": _timestamp(),
            "outcome": "passed" if not required_failures else "failed",
            "required_case_count": len(required),
            "required_failures": required_failures,
            "cases": case_results,
        }
        atomic_write_json(run_root / "report.json", result)
        return {**result, "run_path": str(run_root)}

    def report(self, run: str, fmt: str = "json") -> dict[str, Any] | str:
        path = self._run_path(run) / "report.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        if fmt == "json":
            return value
        if fmt != "markdown":
            raise ValueError("report format must be json or markdown")
        lines = [
            f"# nous Eval {value['run_id']}",
            "",
            f"Outcome: **{value['outcome']}**",
            f"Required cases: {value['required_case_count']}",
            "",
            "| Eval | Selection | Outcome | Trials | First failure |",
            "| --- | --- | --- | --- | --- |",
        ]
        for item in value["cases"]:
            first_failure = item.get("first_failure")
            failure = ""
            if isinstance(first_failure, dict):
                failure = str(first_failure.get("code") or first_failure.get("first_failure") or "")
            evidence_ref = ""
            if isinstance(first_failure, dict) and isinstance(first_failure.get("evidence_refs"), dict):
                evidence_ref = str(first_failure["evidence_refs"].get("canonical", first_failure["evidence_refs"].get("outcome", "")))
            lines.append(
                f"| {item['eval_id']} | {item['selection_status']} | {item['outcome']} | {len(item['trials'])} | {failure}{f' (evidence: {evidence_ref})' if evidence_ref else ''} |"
            )
        if value["required_failures"]:
            lines.extend(["", "Required failures: " + ", ".join(value["required_failures"])])
        return "\n".join(lines) + "\n"

    def cleanup(self, run: str) -> dict[str, Any]:
        run_root = self._run_path(run)
        removed: list[str] = []
        failures: list[str] = []
        for trial in sorted((run_root / "trials").glob("*")) if (run_root / "trials").exists() else []:
            for name in ("nous-home", "repository", "scratch", "worker-evidence"):
                path = trial / name
                if path.exists():
                    try:
                        # Allocate/cleanup uses a fixed list, and nothing in
                        # a fixture may redirect it with a symlink or a path.
                        if path.parent != trial or path.is_symlink():
                            raise ValueError("ephemeral path boundary mismatch")
                        shutil.rmtree(path)
                        if path.exists():
                            raise OSError("ephemeral directory remains after cleanup")
                        removed.append(str(path.relative_to(run_root)))
                    except (OSError, ValueError) as exc:
                        failures.append(f"{path.name}:{type(exc).__name__}")
        value = {
            "run_id": run_root.name,
            "removed": removed,
            "evidence_retained": True,
            "outcome": "passed" if not failures else "infrastructure_error",
            "failure_codes": failures,
        }
        atomic_write_json(run_root / "cleanup.json", value)
        return value

    def _run_path(self, run: str) -> Path:
        raw = Path(run)
        if not self.root:
            raise ValueError("a configured Eval --root is required for report or cleanup")
        if raw.is_absolute() or raw.exists():
            path = raw.resolve()
        else:
            path = (self.root / "runs" / run).resolve()
        if not (path / "run.json").is_file() or not (path / "owner.json").is_file():
            raise ValueError("unknown Eval run")
        if path.parent != self.root / "runs":
            raise ValueError("Eval run must remain below the configured root")
        metadata = _read_json(path / "run.json")
        owner = _read_json(path / "owner.json")
        key_id, key = _load_owner_key(self.root)
        raw_owner = {key: value for key, value in owner.items() if key != "owner_digest"}
        expected_digest = hashlib.sha256(
            json.dumps(raw_owner, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if (
            metadata.get("run_id") != path.name
            or owner.get("run_id") != path.name
            or owner.get("manifest_digest") != metadata.get("manifest_digest")
            or metadata.get("ownership_key_id") != key_id
            or owner.get("ownership_key_id") != key_id
            or not isinstance(owner.get("owner_signature"), str)
            or not hmac.compare_digest(owner["owner_signature"], _sign_owner(owner, key))
            or owner.get("owner_digest") != expected_digest
            or not isinstance(owner.get("ownership_token"), str)
        ):
            raise ValueError("Eval run ownership marker is invalid")
        return path

    def _run_trial(
        self,
        run_root: Path,
        case: dict[str, Any],
        ordinal: int,
        *,
        requested_stage: int | None,
        requested_release: str | None,
        owner_key: bytes,
    ) -> dict[str, Any]:
        environment = TrialEnvironment.allocate(run_root, str(case["eval_id"]), ordinal)
        started_at = _timestamp()
        try:
            source_root = Path(__file__).parents[2]
            fixture = EvalSandbox(environment, source_root).run_fixture(
                str(case["eval_id"]), int(case["trial"].get("timeout_seconds", 30)), phase="setup"
            )
            if fixture.returncode != 0:
                raise RuntimeError("isolated Eval fixture provisioning failed")
            fixture_steps = [("setup", fixture)]
            before = _tree_snapshot(environment.root)
            before_run_scope = _tree_snapshot(run_root / "trials")
            worker = EvalSandbox(environment, source_root).run_worker(
                str(case["handler"]), int(case["trial"].get("timeout_seconds", 30))
            )
            if worker.returncode != 0:
                raise RuntimeError("isolated Eval worker failed")
            if not isinstance(json.loads(worker.stdout), dict):
                raise RuntimeError("isolated Eval worker did not return an object")
            worker_steps = [worker]
            # The local-bare divergence is an external fixture transition,
            # never a product Git remote action.  Execute it in a separately
            # sandboxed adapter only after the managed first clone is ahead,
            # then resume the subject handler in a fresh worker process.
            if case["eval_id"] == "remote_cross_machine_resume":
                divergence = EvalSandbox(environment, source_root).run_fixture(
                    str(case["eval_id"]), int(case["trial"].get("timeout_seconds", 30)), phase="after-ahead"
                )
                if divergence.returncode != 0:
                    raise RuntimeError("isolated local-bare divergence fixture failed")
                fixture_steps.append(("after-ahead", divergence))
                resumed = EvalSandbox(environment, source_root).run_worker(
                    str(case["handler"]), int(case["trial"].get("timeout_seconds", 30))
                )
                if resumed.returncode != 0 or not isinstance(json.loads(resumed.stdout), dict):
                    raise RuntimeError("isolated Eval resume worker failed")
                worker_steps.append(resumed)
            atomic_write_json(
                environment.evidence / "fixture-process.json",
                {
                    "schema_version": 1,
                    "boundary": "isolated_fixture_adapter",
                    "steps": [_process_summary(phase, process) for phase, process in fixture_steps],
                },
            )
            worker = _combine_processes(worker_steps)
            atomic_write_json(
                environment.evidence / "worker-process.json",
                {
                    "schema_version": 1,
                    "returncode": worker.returncode,
                    "stdout_sha256": hashlib.sha256(worker.stdout.encode("utf-8")).hexdigest(),
                    "stderr_sha256": hashlib.sha256(worker.stderr.encode("utf-8")).hexdigest(),
                    "stderr_secret_like": bool(secret_summary(worker.stderr)),
                    "sandbox_enforced": True,
                },
            )
            observed = _capture_canonical_evidence(
                environment,
                before=before,
                before_run_scope=before_run_scope,
                worker=worker,
                process_workers=[_process_summary(f"worker-{index + 1}", process) for index, process in enumerate(worker_steps)],
                attestation_key=owner_key,
                run_id=run_root.name,
            )
            observed["sandbox_enforced"] = True
            grade = grade_case(case=case, observed=observed, trial_root=environment.root, attestation_key=owner_key)
            outcome = "passed" if grade["passed"] else "failed"
            failure_codes = grade["failure_codes"]
        except Exception as exc:  # Evaluation failure is evidence, not a skip.
            observed = {"trace": {"exception_type": type(exc).__name__}, "sandbox_enforced": False, "changed_paths": []}
            outcome = "infrastructure_error"
            failure_codes = [f"infrastructure:{type(exc).__name__}"]
            atomic_write_json(
                environment.evidence / "worker-process.json",
                {"schema_version": 1, "returncode": None, "error_type": type(exc).__name__, "sandbox_enforced": False},
            )
            atomic_write_json(environment.evidence / "canonical.json", observed)
        trace = _portable_trace(observed, environment)
        evidence_refs = {
            "environment": "environment.json",
            "sandbox_execution": "sandbox-execution.json",
            "fixture_process": "fixture-process.json",
            "observed_state_ref": "state.json",
            "event_trace_ref": "events.json",
            "policy_trace_ref": "policy.json",
            "diff_ref": "diff.json",
            "process_ref": "process.json",
            "canonical": "canonical.json",
            "parent_attestation": "parent-attestation.json",
            "trace": "trace.json",
            "outcome": "outcome.json",
        }
        attestation = _read_json(environment.evidence / "parent-attestation.json")
        evidence_digests = attestation.get("evidence_digests", {}) if isinstance(attestation.get("evidence_digests"), dict) else {}
        first_failure = (
            {
                "code": failure_codes[0],
                "grader_condition": failure_codes[0],
                "evidence_refs": evidence_refs,
                "evidence_digests": evidence_digests,
            }
            if failure_codes
            else None
        )
        trial = {
            "schema_version": 2,
            "eval_id": case["eval_id"],
            "trial_id": environment.trial_id,
            "stage": requested_stage,
            "release": requested_release,
            "runnable_from_stage": case["runnable_from_stage"],
            "release_targets": case["required_for_releases"],
            "outcome": outcome,
            "failure_codes": failure_codes,
            "grader_results": grade if outcome != "infrastructure_error" else {"passed": False, "failure_codes": failure_codes},
            "first_failure": first_failure,
            "environment_digest": environment.digest,
            "runtime": {"python": sys.version.split()[0], "package_source_sha256": _source_digest(Path(__file__).parents[2])},
            "started_at": started_at,
            "finished_at": _timestamp(),
            "evidence_refs": evidence_refs,
            "trace": trace,
        }
        atomic_write_json(environment.evidence / "trace.json", trace)
        atomic_write_json(environment.evidence / "outcome.json", trial)
        return {key: value for key, value in trial.items() if key != "trace"}

    @staticmethod
    def _prepare_fixture(case: dict[str, Any], environment: TrialEnvironment) -> None:
        """Materialize declared, non-product fixture input before snapshot.

        Raw Git here is only fixture provisioning inside the trial root.  The
        later subject actions (write, commit, worktree, integration) remain
        ActionDescriptor-managed and journaled by taskctl.
        """
        phase = str(case.get("phase", "setup"))
        if phase == "setup" and case["eval_id"] == "small_change_sequential":
            (environment.repository / "test_fixture.py").write_text(
                "from pathlib import Path\nimport unittest\nclass Fixture(unittest.TestCase):\n    def test_result(self):\n        self.assertEqual(Path('result.txt').read_text(), 'ok\\n')\n",
                encoding="utf-8",
            )
        if phase == "setup" and case["eval_id"] == "parallel_isolation_integration":
            base = _init_fixture_repo(environment.repository)
            atomic_write_json(environment.scratch / "parallel-fixture.json", {"base_commit": base})
        if phase == "setup" and case["eval_id"] == "full_policy_surfaces":
            _init_fixture_repo(environment.repository)
        if phase == "setup" and case["eval_id"] == "remote_cross_machine_resume":
            _provision_local_bare_fixture(environment)
        if phase == "after-ahead" and case["eval_id"] == "remote_cross_machine_resume":
            _inject_local_bare_divergence_fixture(environment)

    def _handlers(self) -> dict[str, Callable[[TrialEnvironment], dict[str, Any]]]:
        handlers = {
            "trigger_explicit_new": self._trigger_explicit_new,
            "trigger_one_off_negative": self._trigger_one_off_negative,
            "pause_fresh_process_resume": self._pause_fresh_process_resume,
            "trial_isolation_basic": self._trial_isolation_basic,
            "trigger_implicit_managed_workflow": self._trigger_implicit_managed_workflow,
            "approval_exact_red_action": self._approval_exact_red_action,
            "review_needs_plan_once": self._review_needs_plan_once,
            "managed_scope_violation": self._managed_scope_violation,
            "unbrokerable_action_denied": self._unbrokerable_action_denied,
            "small_change_sequential": self._small_change_sequential,
            "small_change_interrupt_resume": self._small_change_interrupt_resume,
            "knowledge_gate": self._knowledge_gate,
            "parallel_isolation_integration": self._parallel_isolation_integration,
            "parallel_interrupt_resume": self._parallel_interrupt_resume,
            "subskill_select_fallback": self._subskill_select_fallback,
            "local_bare_cross_clone_resume": self._local_bare_cross_clone_resume,
            "full_policy_surfaces": self._full_policy_surfaces,
        }
        if frozenset(handlers) != HANDLER_NAMES:
            raise RuntimeError("Eval manifest handler registry drift")
        return handlers

    @staticmethod
    def _store(environment: TrialEnvironment) -> TaskStore:
        store = TaskStore(environment.home)
        store.init()
        return store

    def _task(self, environment: TrialEnvironment, *, repository: bool = False) -> tuple[TaskStore, str]:
        store = self._store(environment)
        target = str(environment.repository) if repository else None
        task = store.create(
            # Task IDs are generated from title/time below each isolated home.
            # Include the allocated trial namespace so two concurrent trials
            # cannot accidentally share an identifier in their evidence.
            title=f"isolated eval fixture {environment.trial_id[-10:]}",
            objective="produce independently verifiable fixture evidence",
            target_repository=target,
            actor="eval-runner",
        )["task_id"]
        return store, str(task)

    @staticmethod
    def _observed(store: TaskStore, task_id: str, *, decisions: list[str] | None = None, kinds: list[str] | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        _, state, _ = store.read_task(task_id)
        value: dict[str, Any] = {
            "task_id": task_id,
            "task_root": str(store._path(task_id)),
            "state": state,
            "events": events_for(store._path(task_id)),
            "policy_decisions": decisions or [],
            "action_kinds": kinds or [],
            "network_used": False,
            "trace": {"task_id": task_id, "state": {key: state[key] for key in ("lifecycle_status", "execution_status", "phase", "cycle")}},
        }
        if extra:
            value.update(extra)
            value["trace"] = {**value["trace"], **extra.get("trace", {})}
        return value

    def _trigger_explicit_new(self, environment: TrialEnvironment) -> dict[str, Any]:
        if not should_start_managed_workflow("Create a durable fixture task", explicit=True):
            raise AssertionError("explicit invocation was not recognized")
        store, task_id = self._task(environment)
        return self._observed(store, task_id)

    def _trigger_one_off_negative(self, environment: TrialEnvironment) -> dict[str, Any]:
        store = self._store(environment)
        if should_start_managed_workflow("Format this one title"):
            raise AssertionError("one-off request unexpectedly became managed")
        return {
            "events": [],
            "policy_decisions": [],
            "action_kinds": [],
            "network_used": False,
            "trace": {"task_count": len(store.list()), "managed_task_created": False},
        }

    def _pause_fresh_process_resume(self, environment: TrialEnvironment) -> dict[str, Any]:
        store, task_id = self._task(environment)
        # An idle Task may be paused at its current canonical checkpoint; a
        # request/ack sequence is reserved for a running lease holder.
        store.pause(task_id, 1, "eval-runner")
        fresh = _fresh_state(environment.home, task_id, "resume")
        return self._observed(TaskStore(environment.home), task_id, extra={"state": fresh, "trace": {"fresh_process": True}})

    def _trial_isolation_basic(self, environment: TrialEnvironment) -> dict[str, Any]:
        outside = environment.run_root / "trials" / "isolation-peer-probe"
        try:
            try:
                outside.mkdir(parents=True)
            except OSError:
                outside_blocked = True
            else:
                outside.rmdir()
                outside_blocked = False
            if not outside_blocked:
                raise AssertionError("sandbox allowed a worker write outside its assigned trial")
            first_store, first_task = self._task(environment)
            peer_root = environment.scratch / "isolation-peer"
            peer_home = peer_root / "nous-home"
            peer_repository = peer_root / "repository"
            peer_home.mkdir(parents=True)
            peer_repository.mkdir(parents=True)
            second_store = TaskStore(peer_home)
            second_store.init()
            second_task = second_store.create(title="isolated peer", objective="verify nested namespace", target_repository=str(peer_repository), actor="eval-runner")["task_id"]
            if environment.home == peer_home or environment.repository == peer_repository or first_task == second_task:
                raise AssertionError("trial allocation crossed an isolation boundary")
            return {
                "events": [],
                "policy_decisions": [],
                "action_kinds": [],
                "network_used": False,
                "trace": {
                    "isolated": True,
                    "first_task": first_task,
                    "second_task": second_task,
                    "first_count": len(first_store.list()),
                    "second_count": len(second_store.list()),
                    "outside_trial_write_blocked": outside_blocked,
                },
            }
        finally:
            if outside.exists() and outside.is_dir():
                outside.rmdir()

    def _trigger_implicit_managed_workflow(self, environment: TrialEnvironment) -> dict[str, Any]:
        request = "Please plan, implement, and review this durable managed workflow."
        if not should_start_managed_workflow(request):
            raise AssertionError("durable multi-phase request was not recognized")
        return {"events": [], "policy_decisions": [], "action_kinds": [], "network_used": False, "trace": {"implicit_trigger": True}}

    def _approval_exact_red_action(self, environment: TrialEnvironment) -> dict[str, Any]:
        store, task_id = self._task(environment, repository=True)
        proposal = ActionDescriptor(
            action_id="approval-cleanup",
            task_id=task_id,
            kind="git.local",
            repository_root=str(environment.repository),
            targets=(".",),
            command=("worktree", "remove", "--force", str(environment.repository / "unused")),
            declared_risk="red",
            expected_state_revision=1,
            plan_ref="cycles/0001/01-plan-result.json",
            plan_content_hash="a" * 64,
            expected_effect="remove only an isolated clean fixture worktree",
        )
        requested = store.request_exact_approval(task_id, 1, "eval-runner", proposal)
        if requested["approval"]["status"] != "requested" or (environment.repository / "unused").exists():
            raise AssertionError("approval request was not an interrupt before effect")
        return self._observed(store, task_id, decisions=["approval_required"], kinds=["git.local"])

    def _review_needs_plan_once(self, environment: TrialEnvironment) -> dict[str, Any]:
        store, task_id = self._task(environment)
        coordinator = Orchestrator(store, "eval-runner")
        coordinator.persist_once(task_id, PhaseResultDraft("fixture plan", {"no_managed_effects": True}))
        coordinator.persist_once(task_id, PhaseResultDraft("fixture implement", {"no_managed_effects": True}))
        coordinator.persist_once(
            task_id,
            PhaseResultDraft("fixture review requires one replan", {"next_plan_input": "resolve fixture gap"}, "needs_plan"),
        )
        return self._observed(store, task_id)

    def _managed_scope_violation(self, environment: TrialEnvironment) -> dict[str, Any]:
        store, task_id = self._task(environment, repository=True)
        lease = store.acquire_lease(task_id, 1, "eval-runner")
        action = ActionDescriptor(
            action_id="scope-denied",
            task_id=task_id,
            kind="filesystem.write",
            repository_root=str(environment.repository),
            targets=(str(environment.scratch / "outside.txt"),),
            declared_risk="red",
            expected_state_revision=lease["revision"],
        )
        result = store.begin_managed_action(action, "eval-runner", PolicyGateway(), _passing_probe())
        if result["decision"].outcome != "deny" or (environment.scratch / "outside.txt").exists():
            raise AssertionError("scope violation was not denied before effect")
        return self._observed(store, task_id, decisions=["deny"], kinds=["filesystem.write"])

    def _unbrokerable_action_denied(self, environment: TrialEnvironment) -> dict[str, Any]:
        store, task_id = self._task(environment, repository=True)
        lease = store.acquire_lease(task_id, 1, "eval-runner")
        action = ActionDescriptor(
            action_id="remote-unsupported",
            task_id=task_id,
            kind="git.remote",
            repository_root=str(environment.repository),
            targets=(".",),
            command=("push", "origin", "main"),
            declared_risk="red",
            expected_state_revision=lease["revision"],
        )
        result = store.begin_managed_action(action, "eval-runner", PolicyGateway(), _passing_probe())
        if result["decision"].outcome != "unsupported":
            raise AssertionError("unbrokerable Git remote action was not unsupported")
        return self._observed(store, task_id, decisions=["unsupported"], kinds=["git.remote"])

    def _small_change_sequential(self, environment: TrialEnvironment) -> dict[str, Any]:
        store, task_id = self._task(environment, repository=True)
        coordinator = Orchestrator(store, "eval-runner")
        coordinator.persist_once(task_id, PhaseResultDraft("plan one isolated file", {"no_managed_effects": True}))
        _, state, _ = store.read_task(task_id)
        lease = store.acquire_lease(task_id, state["revision"], "eval-runner")
        gateway = PolicyGateway(allowed_processes=(_SANDBOX_TEST_PREFIX,))
        executor = ManagedExecutor(store, gateway, environment.scratch / "runtime", actor="eval-runner")
        write = executor.write_text(
            ActionDescriptor(
                action_id="sequential-write",
                task_id=task_id,
                kind="filesystem.write",
                repository_root=str(environment.repository),
                targets=("result.txt",),
                declared_risk="green",
                expected_state_revision=lease["revision"],
            ),
            "ok\n",
        )
        check = _run_eval_sandboxed_process(
            store,
            gateway,
            ActionDescriptor(
                action_id="sequential-test",
                task_id=task_id,
                kind="process.run",
                repository_root=str(environment.repository),
                targets=("result.txt",),
                command=(*_SANDBOX_TEST_PREFIX, "test_fixture"),
                declared_risk="yellow",
                expected_state_revision=write.action_state_revision,
            ),
            actor="eval-runner",
        )
        if write.returncode != 0 or check.returncode != 0:
            raise AssertionError("managed small-change fixture did not validate")
        implemented = store.record_phase(
            task_id,
            check.action_state_revision,
            "eval-runner",
            lease["active_lease"]["token"],
            "implement",
            "managed write and test completed",
            {"managed_action_refs": ["sequential-write", "sequential-test"]},
        )
        coordinator.persist_once(
            task_id,
            PhaseResultDraft(
                "independent review accepted fixture",
                _completed_outputs("AC-001", "evidence://sequential-test"),
                "completed",
            ),
        )
        fresh = _fresh_state(environment.home, task_id, "read")
        if fresh["lifecycle_status"] != "completed" or implemented["state"]["phase"] != "review":
            raise AssertionError("fresh status did not recover completed task")
        return self._observed(store, task_id, decisions=["grant"], kinds=["filesystem.write", "process.run"], extra={"trace": {"changed_paths": ["result.txt"], "fresh_process": True}})

    def _small_change_interrupt_resume(self, environment: TrialEnvironment) -> dict[str, Any]:
        store, task_id = self._task(environment, repository=True)
        coordinator = Orchestrator(store, "eval-runner")
        coordinator.persist_once(task_id, PhaseResultDraft("plan interruption fixture", {"no_managed_effects": True}))
        _, state, _ = store.read_task(task_id)
        lease = store.acquire_lease(task_id, state["revision"], "eval-runner")
        executor = ManagedExecutor(store, PolicyGateway(), environment.scratch / "runtime", actor="eval-runner")
        effect = executor.write_text(
            ActionDescriptor(
                action_id="interrupt-write",
                task_id=task_id,
                kind="filesystem.write",
                repository_root=str(environment.repository),
                targets=("result.txt",),
                declared_risk="green",
                expected_state_revision=lease["revision"],
                idempotency_key="interrupt-write-once",
            ),
            "resumed once\n",
        )
        pausing = store.request_pause(task_id, effect.action_state_revision, "eval-runner", lease["active_lease"]["token"])
        store.pause(task_id, pausing["revision"], "eval-runner", lease["active_lease"]["token"])
        resumed = _fresh_state(environment.home, task_id, "resume")
        restarted = store.acquire_lease(task_id, resumed["revision"], "eval-runner")
        store.record_phase(
            task_id,
            restarted["revision"],
            "eval-runner",
            restarted["active_lease"]["token"],
            "implement",
            "resume without duplicate effect",
            {"managed_action_refs": ["interrupt-write"]},
        )
        coordinator.persist_once(task_id, PhaseResultDraft("review resumed fixture", _completed_outputs("AC-001", "evidence://interrupt-write"), "completed"))
        actions = list((store._path(task_id) / "actions").glob("interrupt-write.json"))
        if len(actions) != 1:
            raise AssertionError("interruption duplicated managed effect")
        return self._observed(store, task_id, decisions=["grant"], kinds=["filesystem.write"], extra={"trace": {"write_action_count": 1, "fresh_process": True}})

    def _knowledge_gate(self, environment: TrialEnvironment) -> dict[str, Any]:
        store, task_id = self._task(environment)
        source = {"source_id": "fixture-source", "title": "fixture", "locator": "fixture://knowledge", "source_type": "fixture", "content_hash": "a" * 64, "retrieved_at": "2026-01-01T00:00:00+00:00"}
        claim = {"claim_id": "fixture-claim", "text": "fixture evidence is sufficient", "source_ids": ["fixture-source"], "epistemic_status": "confirmed", "lifecycle_status": "active", "checked_at": "2026-01-01T00:00:00+00:00"}
        normalized_source = store._normalize_knowledge_source(source)
        normalized_claim = store._normalize_knowledge_claim(claim)
        question = {
            "question_id": "fixture-question",
            "question": "is the fixture evidence sufficient?",
            "reason": "release gate",
            "impact": "high",
            "required_before": "implementation_step",
            "status": "answered",
            "answer_evidence": [
                {"record_type": "source", "record_id": "fixture-source", "content_hash": normalized_source["content_hash"]},
                {"record_type": "claim", "record_id": "fixture-claim", "content_hash": normalized_claim["content_hash"]},
            ],
        }
        recorded = KnowledgeStore(store).record(task_id, 1, [source], [claim], questions=[question])
        coordinator = Orchestrator(store, "eval-runner")
        coordinator.persist_once(task_id, PhaseResultDraft("knowledge-bound plan", {"no_managed_effects": True}))
        active = KnowledgeStore(store).active_claims(task_id)
        if len(active) != 1 or recorded["state"]["revision"] >= store.read_task(task_id)[1]["revision"]:
            raise AssertionError("canonical knowledge gate did not persist before plan")
        return self._observed(store, task_id, extra={"trace": {"active_claims": [item["claim_id"] for item in active]}})

    def _parallel_isolation_integration(self, environment: TrialEnvironment) -> dict[str, Any]:
        store, task_id = _parallel_worktree_e2e(environment)
        return self._observed(
            store,
            task_id,
            decisions=["grant", "approval_required"],
            kinds=["filesystem.write", "git.local"],
            extra={"trace": {"child_contracts": ["write-child", "conflict-child"], "reservation_conflict_rejected": True, "worktree_isolated": True}},
        )

    def _parallel_interrupt_resume(self, environment: TrialEnvironment) -> dict[str, Any]:
        store, task_id = self._task(environment)
        resource = "repo://fixture/recover"
        child = store.create_child_loop(task_id, 1, "eval-runner", ChildContract("recover", "recover child", resources=(resource,)).draft())
        planned = store.record_parallel_plan(task_id, child["state"]["revision"], "eval-runner", mode="sequential", rationale="lease recovery fixture", max_concurrency=1, max_depth=0, integration_strategy="review", integration_validation=["checkpoint"])
        requested = store.request_reservation(task_id, planned["state"]["revision"], "eval-runner", loop_id="recover", resource=resource, mode="exclusive_write")
        held = store.acquire_reservation(task_id, requested["state"]["revision"], "eval-runner", reservation_id=requested["reservation"]["reservation_id"], lease_seconds=1)
        started = store.start_child_loop(task_id, held["state"]["revision"], "eval-runner", loop_id="recover", lease_seconds=1)
        del started
        time.sleep(1.05)
        recovered = _fresh_child_lease_recovery(environment.home, task_id)
        if not recovered or recovered["expired_loop_ids"] != ["recover"] or recovered.get("expired_reservation_ids") != [requested["reservation"]["reservation_id"]]:
            raise AssertionError("fresh process did not recover child lease and reservation")
        return self._observed(store, task_id, extra={"trace": {"fresh_process": True, "recovered": ["recover"]}})

    def _subskill_select_fallback(self, environment: TrialEnvironment) -> dict[str, Any]:
        providers = SubskillRegistry().discover()
        if not providers:
            raise AssertionError("built-in subskill fixture is unavailable")
        provider = providers[0]
        selection = SubskillSelector().select(providers, phase=provider.phase, role="primary", limit=1)
        if not selection or selection[0].provider.id != provider.id:
            raise AssertionError("subskill selection is not deterministic")
        unavailable = False
        try:
            SubskillSelector().select(providers, phase=provider.phase, required_safety_capabilities=("nonexistent-safety-capability",))
        except Exception:
            unavailable = True
        if not unavailable:
            raise AssertionError("missing safety provider did not fail closed")
        return {"events": [], "policy_decisions": [], "action_kinds": [], "network_used": False, "trace": {"selected": provider.id, "fallback_fail_closed": True}}

    def _local_bare_cross_clone_resume(self, environment: TrialEnvironment) -> dict[str, Any]:
        first = environment.repository
        fixture = _read_json(environment.scratch / "local-bare-fixture.json")
        branch = str(fixture["branch"])
        progress_path = environment.scratch / "remote-durability-progress.json"
        progress = _read_json(progress_path)
        if not progress:
            store, task_id = self._task(environment, repository=True)
            lease = store.acquire_lease(task_id, 1, "eval-runner")
            executor = ManagedExecutor(store, PolicyGateway(), environment.scratch / "runtime", actor="eval-runner")
            write = executor.write_text(ActionDescriptor(action_id="durability-write", task_id=task_id, kind="filesystem.write", repository_root=str(first), targets=("first.txt",), declared_risk="green", expected_state_revision=lease["revision"]), "managed\n")
            add = executor.run_local_git(ActionDescriptor(action_id="durability-add", task_id=task_id, kind="git.local", repository_root=str(first), targets=("first.txt",), command=("add", "--", "first.txt"), declared_risk="yellow", expected_state_revision=write.action_state_revision))
            commit = executor.run_local_git(ActionDescriptor(action_id="durability-commit", task_id=task_id, kind="git.local", repository_root=str(first), targets=("first.txt",), command=("commit", "-m", "managed durability"), declared_risk="yellow", expected_state_revision=add.action_state_revision))
            if commit.returncode != 0:
                raise AssertionError("managed local durability commit failed")
            idle = store.release_lease(task_id, commit.action_state_revision, "eval-runner", lease["active_lease"]["token"])
            # The unsupported remote descriptor is a subject-policy check.
            # The later external-clone change is injected by a separate,
            # sandboxed fixture adapter rather than this product worker.
            policy_lease = store.acquire_lease(task_id, idle["revision"], "eval-runner")
            blocked = store.begin_managed_action(
                ActionDescriptor(
                    action_id="remote-unsupported",
                    task_id=task_id,
                    kind="git.remote",
                    repository_root=str(first),
                    targets=(".",),
                    command=("push", "origin", branch),
                    declared_risk="red",
                    expected_state_revision=policy_lease["revision"],
                ),
                "eval-runner",
                PolicyGateway(),
                _passing_probe(),
            )
            if blocked["decision"].outcome != "unsupported":
                raise AssertionError("remote fixture must remain unsupported")
            idle = store.release_lease(task_id, blocked["state"]["revision"], "eval-runner", policy_lease["active_lease"]["token"])
            ahead = remote_state(task_id, first, "origin", branch)
            if ahead["status"] != "ahead":
                raise AssertionError("local bare fixture did not establish ahead state")
            _fresh_record_durability(environment.home, task_id, ahead)
            atomic_write_json(progress_path, {"schema_version": 1, "task_id": task_id, "stage": "ahead_recorded", "fixture_kind": "local_bare_external_clone"})
            return self._observed(TaskStore(environment.home), task_id, decisions=["unsupported"], kinds=["git.remote"], extra={"trace": {"fixture_remote": "local-bare", "fresh_process": True, "awaiting_fixture_divergence": True}})
        if progress.get("stage") != "divergence_injected" or not isinstance(progress.get("task_id"), str):
            raise AssertionError("local-bare fixture resume checkpoint is invalid")
        task_id = progress["task_id"]
        divergent = remote_state(task_id, first, "origin", branch)
        if divergent["status"] != "diverged":
            raise AssertionError("local bare fixture did not establish divergence")
        state = _fresh_record_durability(environment.home, task_id, divergent)
        reconciled = TaskStore(environment.home).record_git_durability_reconciliation(task_id, state["revision"], "eval-runner", outcome="replan_required", rationale="isolated local bare fixture diverged")
        if state["revision"] >= reconciled["state"]["revision"]:
            raise AssertionError("fresh durability mutations were not canonical")
        final_store = TaskStore(environment.home)
        return self._observed(final_store, task_id, decisions=["unsupported"], kinds=["git.remote"], extra={"trace": {"fixture_remote": "local-bare", "fresh_process": True}})

    def _full_policy_surfaces(self, environment: TrialEnvironment) -> dict[str, Any]:
        store, task_id = self._task(environment, repository=True)
        root = str(environment.repository)
        lease = store.acquire_lease(task_id, 1, "eval-runner")
        executor = ManagedExecutor(store, PolicyGateway(), environment.scratch / "runtime", actor="eval-runner")
        write = executor.write_text(
            ActionDescriptor("surface-write", task_id, "filesystem.write", root, ("safe.txt",), declared_risk="green", expected_state_revision=lease["revision"]),
            "policy fixture\n",
        )
        local = executor.run_local_git(
            ActionDescriptor("surface-git", task_id, "git.local", root, (".",), ("status", "--porcelain"), declared_risk="yellow", expected_state_revision=write.action_state_revision)
        )
        if write.returncode != 0 or local.returncode != 0:
            raise AssertionError("grant surfaces did not execute through managed adapters")
        revision = local.action_state_revision
        denied_actions = (
            ActionDescriptor("surface-process", task_id, "process.run", root, ("safe.txt",), ("fixture-unallowlisted", "check"), declared_risk="red", expected_state_revision=revision),
            ActionDescriptor("surface-remote", task_id, "git.remote", root, (".",), ("push", "origin", "main"), declared_risk="red"),
            ActionDescriptor("surface-network", task_id, "network", root, (".",), network_destinations=("https://example.invalid",), declared_risk="red"),
            ActionDescriptor("surface-credential", task_id, "credential", root, (".",), declared_risk="red"),
            ActionDescriptor("surface-paid", task_id, "paid", root, (".",), estimated_cost_usd=1.0, declared_risk="red"),
        )
        outcomes: list[str] = ["grant", "grant"]
        for prototype in denied_actions:
            action = ActionDescriptor(
                prototype.action_id, prototype.task_id, prototype.kind, prototype.repository_root, prototype.targets,
                prototype.command, prototype.network_destinations, prototype.estimated_cost_usd, prototype.approval_id,
                prototype.declared_risk, prototype.loop_id, revision, prototype.request_id, prototype.idempotency_key,
                prototype.expected_effect, prototype.plan_ref, prototype.plan_content_hash, prototype.environment,
                prototype.reservation_ids, prototype.resource_ids,
            )
            result = store.begin_managed_action(action, "eval-runner", PolicyGateway(), _passing_probe())
            outcomes.append(result["decision"].outcome)
            revision = result["state"]["revision"]
        if not {"deny", "unsupported"}.issubset(set(outcomes)):
            raise AssertionError("policy matrix did not record deny and unsupported outcomes")
        return self._observed(store, task_id, decisions=outcomes, kinds=["filesystem.write", "git.local", "process.run", "git.remote", "network", "credential", "paid"])


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _process_summary(phase: str, process: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "phase": phase,
        "process_identity": getattr(process, "process_identity", None),
        "returncode": process.returncode,
        "stdout_sha256": hashlib.sha256(process.stdout.encode("utf-8")).hexdigest(),
        "stderr_sha256": hashlib.sha256(process.stderr.encode("utf-8")).hexdigest(),
        "stdout_secret_like": bool(secret_summary(process.stdout)),
        "stderr_secret_like": bool(secret_summary(process.stderr)),
    }


def _combine_processes(processes: list[subprocess.CompletedProcess[str]]) -> subprocess.CompletedProcess[str]:
    """Produce a minimal aggregate solely for parent-owned capture hashes."""
    if not processes or any(process.returncode != 0 for process in processes):
        raise ValueError("cannot combine failed Eval worker processes")
    return subprocess.CompletedProcess(
        ("isolated-eval-workers",),
        0,
        "\n".join(process.stdout for process in processes),
        "\n".join(process.stderr for process in processes),
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _source_digest(source_root: Path) -> str:
    """A portable package identity without recording host checkout paths."""
    digest = hashlib.sha256()
    for path in sorted(source_root.rglob("*.py")):
        digest.update(str(path.relative_to(source_root)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _passing_probe() -> dict[str, Any]:
    return {"result": "pass", "facts": {"fixture": True}, "redaction_summary": {}}


def _run_eval_sandboxed_process(
    store: TaskStore,
    gateway: PolicyGateway,
    action: ActionDescriptor,
    *,
    actor: str,
) -> ExecutionResult:
    """Run one fixture test under the already-enforced outer Eval sandbox.

    This is deliberately evaluator-only.  It keeps the canonical
    ActionDescriptor -> Gateway -> Grant -> Probe -> Journal lifecycle while
    avoiding an unsupported nested Linux sandbox.  Product runtime effects
    continue to use ManagedExecutor and its dedicated sandbox.
    """
    started_at = _timestamp()
    started = store.begin_managed_action(action, actor, gateway, _passing_probe())
    decision = started["decision"]
    if decision.outcome != "grant" or not decision.grant_id:
        raise AssertionError("fixture process did not receive an execution grant")
    grant = started["grant"]
    state = started["state"]
    assert isinstance(grant, dict) and isinstance(state, dict)
    completed = subprocess.run(
        action.command or (),
        cwd=action.repository_root,
        env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8"},
        text=True,
        capture_output=True,
        check=False,
    )
    postflight = {
        "result": "pass" if completed.returncode == 0 else "fail",
        "facts": {"returncode": completed.returncode, "fixture_test": "unittest"},
        "redaction_summary": {},
    }
    finished = store.finish_managed_action(
        action.task_id,
        int(state["revision"]),
        actor,
        str(grant["lease_token"]),
        action.action_id,
        str(grant["grant_id"]),
        completed.returncode,
        postflight,
    )
    return ExecutionResult(
        action.action_id,
        action.digest,
        str(grant["grant_id"]),
        completed.returncode,
        completed.stdout,
        completed.stderr,
        started_at,
        _timestamp(),
        int(finished["state"]["revision"]),
    )


def _completed_outputs(criterion_id: str, evidence: str) -> dict[str, Any]:
    return {
        "acceptance_criteria": [{"criterion_id": criterion_id, "status": "passed", "evidence_refs": [evidence]}],
        "validation_evidence": [{"criterion_id": criterion_id, "kind": "fixture", "ref": evidence, "outcome": "passed"}],
        "blocking_issues": [],
    }


def _parallel_worktree_contract(loop_id: str, *, worktree: bool) -> dict[str, Any]:
    resource = "repo://fixture/child-only.txt"
    return {
        "loop_id": loop_id,
        "objective": f"complete {loop_id} in an isolated fixture",
        "scope": [resource],
        "dependencies": [],
        "resources": [resource],
        "write_scope": [resource],
        "acceptance_criteria": [{"criterion_id": f"AC-{loop_id}", "statement": f"{loop_id} accepted", "validation_method": "independent fixture review"}],
        "expected_outputs": ["child-result"],
        "validation_plan": ["fixture validation"],
        "preferred_backend": "worktree" if worktree else "sequential",
        "resource_contracts": [{"resource_id": resource, "kind": "file", "locator": "child-only.txt", "semantic_scope": {"namespace": "repo", "entity": "fixture-child-output", "facet": "content"}, "parent_resource_ref": None, "aliases": ["repo://fixture/child-output"], "version_ref": None, "conflict_class": "exclusive", "merge_strategy": "cherry-pick", "sensitivity": "normal"}],
        "resource_requests": [{"resource_ref": resource, "requested_mode": "write", "input_version_ref": None, "required_reservation": "exclusive_write", "expected_change": "write isolated fixture output", "integration_strategy": "managed cherry-pick"}],
        "allowed_actions": ["filesystem.read", "filesystem.write", "git.local"] if worktree else [],
    }


def _init_fixture_repo(repo: Path) -> str:
    _git("init", str(repo))
    _git("-C", str(repo), "config", "user.email", "fixture@example.invalid")
    _git("-C", str(repo), "config", "user.name", "Fixture")
    (repo / "fixture.txt").write_text("base\n", encoding="utf-8")
    _git("-C", str(repo), "add", "fixture.txt")
    _git("-C", str(repo), "commit", "-m", "fixture base")
    return _git_output("-C", str(repo), "rev-parse", "HEAD").strip()


def _provision_local_bare_fixture(environment: TrialEnvironment) -> None:
    bare = environment.scratch / "fixture.git"
    seed = environment.scratch / "seed"
    first = environment.repository
    second = environment.scratch / "clone-second"
    _git("init", "--bare", str(bare))
    _git("init", str(seed))
    _git("-C", str(seed), "config", "user.email", "fixture@example.invalid")
    _git("-C", str(seed), "config", "user.name", "Fixture")
    (seed / "base.txt").write_text("base\n", encoding="utf-8")
    _git("-C", str(seed), "add", "base.txt")
    _git("-C", str(seed), "commit", "-m", "base")
    branch = _git_output("-C", str(seed), "branch", "--show-current").strip()
    _git("-C", str(seed), "remote", "add", "origin", str(bare))
    _git("-C", str(seed), "push", "-u", "origin", branch)
    _git("clone", str(bare), str(first))
    _git("clone", str(bare), str(second))
    for clone in (first, second):
        _git("-C", str(clone), "config", "user.email", "fixture@example.invalid")
        _git("-C", str(clone), "config", "user.name", "Fixture")
    atomic_write_json(
        environment.scratch / "local-bare-fixture.json",
        {
            "schema_version": 1,
            "fixture_id": f"local-bare-{environment.trial_id}",
            "fixture_kind": "local_bare_external_clone",
            "branch": branch,
            "bare": "scratch/fixture.git",
            "first_clone": "repository",
            "second_clone": "scratch/clone-second",
            "base_commit": _git_output("-C", str(first), "rev-parse", "HEAD").strip(),
            "bare_identity": _directory_identity(bare),
            "first_clone_identity": _directory_identity(first),
            "second_clone_identity": _directory_identity(second),
        },
    )


def _inject_local_bare_divergence_fixture(environment: TrialEnvironment) -> None:
    """Create a second-clone change as an explicitly isolated fixture step."""
    fixture = _read_json(environment.scratch / "local-bare-fixture.json")
    progress = _read_json(environment.scratch / "remote-durability-progress.json")
    branch = fixture.get("branch")
    second_name = fixture.get("second_clone")
    if (
        progress.get("stage") != "ahead_recorded"
        or not isinstance(progress.get("task_id"), str)
        or not isinstance(branch, str)
        or not isinstance(second_name, str)
    ):
        raise ValueError("local-bare divergence fixture has no ahead checkpoint")
    second_locator = Path(second_name)
    if second_locator.is_absolute() or ".." in second_locator.parts:
        raise ValueError("local-bare second clone locator escaped the trial")
    second = environment.root / second_locator
    (second / "second.txt").write_text("second\n", encoding="utf-8")
    _git("-C", str(second), "add", "second.txt")
    _git("-C", str(second), "commit", "-m", "independent fixture")
    _git("-C", str(second), "push", "origin", branch)
    _git("-C", str(environment.repository), "fetch", "origin")
    atomic_write_json(
        environment.scratch / "remote-durability-progress.json",
        {"schema_version": 1, "task_id": progress["task_id"], "stage": "divergence_injected", "fixture_kind": "local_bare_external_clone"},
    )
    atomic_write_json(
        environment.scratch / "local-bare-divergence.json",
        {
            "schema_version": 1,
            "fixture_id": fixture.get("fixture_id"),
            "injection": "external_fixture_adapter",
            "branch": branch,
            "second_clone": second_name,
            "second_clone_identity": _directory_identity(second),
            "second_commit": _git_output("-C", str(second), "rev-parse", "HEAD").strip(),
            "first_clone_identity": _directory_identity(environment.repository),
        },
    )


def _directory_identity(path: Path) -> str:
    stat = path.stat()
    return f"dev:{stat.st_dev}:ino:{stat.st_ino}"


def _resolve_trial_locator(environment: TrialEnvironment, locator: object) -> Path:
    if not isinstance(locator, str) or not locator or Path(locator).is_absolute() or ".." in Path(locator).parts:
        raise ValueError("fixture locator escaped the trial")
    path = (environment.root / locator).resolve()
    if environment.root not in path.parents and path != environment.root:
        raise ValueError("fixture locator escaped the trial")
    return path


def _validate_local_bare_fixture(
    environment: TrialEnvironment,
    setup: dict[str, Any],
    divergence: dict[str, Any],
    durability: list[dict[str, Any]],
) -> list[str]:
    """Re-derive the external-clone fixture identity in the parent.

    The setup JSON is worker-writable, so its claims are only useful after
    the parent checks the actual directory identities and Git object graph.
    This prevents a worker from cross-binding a plausible remote/commit
    record to a different clone or fixture.
    """
    failures: list[str] = []
    try:
        bare = _resolve_trial_locator(environment, setup["bare"])
        first = _resolve_trial_locator(environment, setup["first_clone"])
        second = _resolve_trial_locator(environment, setup["second_clone"])
        if not all(path.is_dir() for path in (bare, first, second)):
            return ["local_bare_fixture_path_missing"]
        actual_identities = {
            "bare_identity": _directory_identity(bare),
            "first_clone_identity": _directory_identity(first),
            "second_clone_identity": _directory_identity(second),
        }
        for key, actual in actual_identities.items():
            if setup.get(key) != actual:
                failures.append(f"local_bare_{key}_mismatch")
        branch = setup.get("branch")
        base_commit = setup.get("base_commit")
        if not isinstance(branch, str) or not isinstance(base_commit, str):
            return failures + ["local_bare_fixture_commit_metadata_invalid"]
        first_head = _git_output("-C", str(first), "rev-parse", "HEAD").strip()
        second_head = _git_output("-C", str(second), "rev-parse", "HEAD").strip()
        bare_head = _git_output("--git-dir", str(bare), "rev-parse", f"refs/heads/{branch}").strip()
        for commit in (base_commit, first_head, second_head, bare_head):
            if len(commit) < 40 or any(char not in "0123456789abcdef" for char in commit):
                failures.append("local_bare_fixture_commit_invalid")
        if base_commit == second_head or second_head != divergence.get("second_commit") or bare_head != divergence.get("second_commit"):
            failures.append("local_bare_fixture_commit_binding_invalid")
        if divergence.get("first_clone_identity") != actual_identities["first_clone_identity"] or divergence.get("second_clone_identity") != actual_identities["second_clone_identity"]:
            failures.append("local_bare_divergence_identity_mismatch")
        if divergence.get("branch") != branch or divergence.get("second_clone") != setup.get("second_clone"):
            failures.append("local_bare_divergence_locator_mismatch")
        for item in durability:
            for key in ("local_head", "remote_head", "artifact_commit"):
                value = item.get(key)
                if value is not None and (not isinstance(value, str) or len(value) < 40 or any(char not in "0123456789abcdef" for char in value)):
                    failures.append(f"local_bare_durability_{key}_invalid")
                elif isinstance(value, str):
                    try:
                        _git_output("-C", str(first), "cat-file", "-e", f"{value}^{{commit}}")
                    except (OSError, subprocess.CalledProcessError):
                        failures.append(f"local_bare_durability_{key}_not_in_fixture")
            if item.get("repository_identity") != "trial://repository" or item.get("branch") != branch:
                failures.append("local_bare_durability_scope_mismatch")
        if not any(item.get("status") == "ahead" and item.get("artifact_commit") == item.get("local_head") for item in durability):
            failures.append("local_bare_ahead_artifact_binding_missing")
        if not any(item.get("status") == "diverged" and item.get("local_head") == first_head and item.get("remote_head") == second_head for item in durability):
            failures.append("local_bare_diverged_head_binding_missing")
    except (KeyError, OSError, subprocess.CalledProcessError, ValueError):
        failures.append("local_bare_fixture_parent_validation_failed")
    return sorted(set(failures))


def _completed_child_outputs(criterion_id: str, evidence: str, *, no_effects: bool = True) -> dict[str, Any]:
    output = {**_completed_outputs(criterion_id, evidence), "decision": "completed", "summary": "independent fixture review accepted"}
    if no_effects:
        output["no_managed_effects"] = True
    return output


def _run_eval_exact_cleanup(
    store: TaskStore,
    task_id: str,
    repo: Path,
    *,
    action_id: str,
    command: tuple[str, ...],
    expected_effect: str,
) -> dict[str, Any]:
    """Synthetic fixture Human attestation for a Red cleanup action only."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    state = store.read_task(task_id)[1]
    proposal = ActionDescriptor(
        action_id=action_id,
        task_id=task_id,
        kind="git.local",
        repository_root=str(repo),
        targets=(".",),
        command=command,
        declared_risk="red",
        expected_state_revision=state["revision"],
        plan_ref="cycles/0001/01-parallelization-plan.json",
        plan_content_hash="a" * 64,
        expected_effect=expected_effect,
    )
    requested = store.request_exact_approval(task_id, state["revision"], "fixture-cleanup-requester", proposal)
    answer: dict[str, Any] = {"answer_type": "selected_option", "selected_option_id": "approve", "answer_text": None, "applied_scope": "action", "answer_effect": "resume", "resume_target": requested["question"]["resume_target"]}
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode("utf-8")
    signature = private.sign(approval_answer_message(task_id, requested["question"]["question_id"], "fixture-human", answer))
    answered = TaskStore(store.home, HumanApprovalAuthority(public)).answer_question(task_id, requested["state"]["revision"], "fixture-human", requested["question"]["question_id"], {**answer, "human_attestation": base64.b64encode(signature).decode("ascii")})
    answer_id = next(
        path.stem
        for path in (store._path(task_id) / "human-input" / "answers").glob("*.json")
        if _read_json(path).get("question_id") == requested["question"]["question_id"]
    )
    granted = TaskStore(store.home).grant_exact_approval(task_id, answered["revision"], "fixture-grant-boundary", requested["approval"]["approval_id"], answer_id)
    lease = TaskStore(store.home).acquire_lease(task_id, granted["state"]["revision"], "fixture-integrator")
    authorized = ActionDescriptor(
        action_id=action_id,
        task_id=task_id,
        kind="git.local",
        repository_root=str(repo),
        targets=(".",),
        command=command,
        declared_risk="red",
        expected_state_revision=lease["revision"],
        approval_id=requested["approval"]["approval_id"],
        plan_ref="cycles/0001/01-parallelization-plan.json",
        plan_content_hash="a" * 64,
        expected_effect=expected_effect,
    )
    result = ManagedExecutor(TaskStore(store.home), PolicyGateway(), store.home / "runtime", actor="fixture-integrator").run_local_git(authorized)
    if result.returncode != 0:
        raise AssertionError("approved fixture cleanup did not complete")
    return TaskStore(store.home).release_lease(task_id, result.action_state_revision, "fixture-integrator", lease["active_lease"]["token"])


def _parallel_worktree_e2e(environment: TrialEnvironment) -> tuple[TaskStore, str]:
    repo = environment.repository
    base = str(_read_json(environment.scratch / "parallel-fixture.json")["base_commit"])
    store = TaskStore(environment.home)
    task_id = store.create(title=f"parallel eval {environment.trial_id[-10:]}", objective="prove isolated worktree integration", target_repository=str(repo), actor="eval-runner")["task_id"]
    first = store.create_child_loop(task_id, 1, "eval-runner", _parallel_worktree_contract("write-child", worktree=True))
    second = store.create_child_loop(task_id, first["state"]["revision"], "eval-runner", _parallel_worktree_contract("conflict-child", worktree=False))
    # The immutable child contracts intentionally overlap.  A safe planner
    # must therefore serialize them; we then exercise the persistent
    # reservation conflict path explicitly rather than pretending these two
    # writes are safe to run concurrently.
    planned = store.record_parallel_plan(task_id, second["state"]["revision"], "eval-runner", mode="sequential", rationale="fixture verifies fail-closed overlap, reservation conflict, and worktree isolation", max_concurrency=1, max_depth=1, integration_strategy="managed cherry-pick", integration_validation=["managed Git evidence"])
    root_lease = store.acquire_lease(task_id, planned["state"]["revision"], "fixture-parent")
    executor = ManagedExecutor(store, PolicyGateway(), store.home / "runtime", actor="fixture-parent")
    manager = LocalGitWorktreeManager(store, executor)
    created = manager.create_child_worktree(task_id, root_lease["revision"], "fixture-parent", root_lease["active_lease"]["token"], loop_id="write-child", repository_root=str(repo), branch="nous-eval-child", base_commit=base, action_id="fixture-worktree-add")
    if created["result"].returncode != 0 or not created["worktree"]:
        raise AssertionError("managed child worktree was not created")
    worktree = Path(created["worktree"]["worktree"]["worktree_path"])
    root_idle = store.release_lease(task_id, created["worktree"]["state"]["revision"], "fixture-parent", root_lease["active_lease"]["token"])
    resource = "repo://fixture/child-only.txt"
    requested = store.request_reservation(task_id, root_idle["revision"], "fixture-child", loop_id="write-child", resource=resource, mode="exclusive_write")
    held = store.acquire_reservation(task_id, requested["state"]["revision"], "fixture-child", reservation_id=requested["reservation"]["reservation_id"])
    conflict_request = store.request_reservation(task_id, held["state"]["revision"], "fixture-conflict", loop_id="conflict-child", resource=resource, mode="exclusive_write")
    conflict = store.acquire_reservation(task_id, conflict_request["state"]["revision"], "fixture-conflict", reservation_id=conflict_request["reservation"]["reservation_id"])
    if conflict["outcome"] != "conflicted":
        raise AssertionError("persistent reservation conflict was not rejected")
    child = store.start_child_loop(task_id, conflict["state"]["revision"], "fixture-child", loop_id="write-child")
    write = ManagedExecutor(store, PolicyGateway(), store.home / "runtime", actor="fixture-child").write_text(ActionDescriptor(action_id="fixture-child-write", task_id=task_id, kind="filesystem.write", repository_root=str(worktree), targets=("child-only.txt",), declared_risk="green", loop_id="write-child", expected_state_revision=child["state"]["revision"], reservation_ids=(requested["reservation"]["reservation_id"],), resource_ids=(resource,)), "isolated\n")
    add = ManagedExecutor(store, PolicyGateway(), store.home / "runtime", actor="fixture-child").run_local_git(ActionDescriptor(action_id="fixture-child-add", task_id=task_id, kind="git.local", repository_root=str(worktree), targets=("child-only.txt",), command=("add", "--", "child-only.txt"), declared_risk="yellow", loop_id="write-child", expected_state_revision=write.action_state_revision, reservation_ids=(requested["reservation"]["reservation_id"],), resource_ids=(resource,)))
    commit = ManagedExecutor(store, PolicyGateway(), store.home / "runtime", actor="fixture-child").run_local_git(ActionDescriptor(action_id="fixture-child-commit", task_id=task_id, kind="git.local", repository_root=str(worktree), targets=("child-only.txt",), command=("commit", "-m", "managed fixture child"), declared_risk="yellow", loop_id="write-child", expected_state_revision=add.action_state_revision, reservation_ids=(requested["reservation"]["reservation_id"],), resource_ids=(resource,)))
    if any(result.returncode != 0 for result in (write, add, commit)):
        raise AssertionError("managed child action did not complete")
    record = json.loads((store._path(task_id) / "actions" / "fixture-child-commit.json").read_text(encoding="utf-8"))
    evidence = json.loads((store._path(task_id) / record["after_evidence_ref"]).read_text(encoding="utf-8"))
    child_commit = evidence["facts"]["after_head"]
    child_result = store.record_child_result(task_id, commit.action_state_revision, "fixture-child", loop_id="write-child", lease_token=child["lease_token"], outputs={**_completed_child_outputs("AC-write-child", "evidence://child-write", no_effects=False), "managed_action_refs": ["fixture-child-write", "fixture-child-add", "fixture-child-commit"], "durability": {"kind": "local_commit", "commit": child_commit}})
    retry = store.request_reservation(task_id, child_result["state"]["revision"], "fixture-conflict", loop_id="conflict-child", resource=resource, mode="exclusive_write")
    retry_held = store.acquire_reservation(task_id, retry["state"]["revision"], "fixture-conflict", reservation_id=retry["reservation"]["reservation_id"])
    second_child = store.start_child_loop(task_id, retry_held["state"]["revision"], "fixture-conflict", loop_id="conflict-child")
    second_result = store.record_child_result(task_id, second_child["state"]["revision"], "fixture-conflict", loop_id="conflict-child", lease_token=second_child["lease_token"], outputs=_completed_child_outputs("AC-conflict-child", "evidence://conflict-child"))
    root_lease = store.acquire_lease(task_id, second_result["state"]["revision"], "fixture-integrator")
    integrated = ManagedExecutor(store, PolicyGateway(), store.home / "runtime", actor="fixture-integrator").run_local_git(ActionDescriptor(action_id="fixture-integration-cherry-pick", task_id=task_id, kind="git.local", repository_root=str(repo), targets=(".",), command=("cherry-pick", child_commit), declared_risk="yellow", expected_state_revision=root_lease["revision"]))
    if integrated.returncode != 0:
        raise AssertionError("managed integration action did not complete")
    root_idle = store.release_lease(task_id, integrated.action_state_revision, "fixture-integrator", root_lease["active_lease"]["token"])
    root_idle = _run_eval_exact_cleanup(TaskStore(store.home), task_id, repo, action_id="fixture-cleanup-worktree", command=("worktree", "remove", "--force", str(worktree)), expected_effect="remove the clean task-owned fixture worktree")
    root_idle = _run_eval_exact_cleanup(TaskStore(store.home), task_id, repo, action_id="fixture-cleanup-branch", command=("branch", "-D", "nous-eval-child"), expected_effect="remove the integrated fixture branch")
    completed = TaskStore(store.home).record_integration_review(task_id, root_idle["revision"], "fixture-integrator", outputs={"accepted_children": ["write-child", "conflict-child"], "integrated_outputs": [{"loop_id": "write-child", "child_commit": child_commit, "integration_action_id": "fixture-integration-cherry-pick"}], "cleanup_actions": [{"loop_id": "write-child", "worktree_remove_action_id": "fixture-cleanup-worktree", "branch_delete_action_id": "fixture-cleanup-branch"}], **_completed_outputs("AC-001", "evidence://integration"), "integration_validation": ["managed integration and cleanup passed"], "blocking_issues": []})
    if completed["state"]["lifecycle_status"] != "completed" or worktree.exists() or not (repo / "child-only.txt").is_file():
        raise AssertionError("integration review did not prove isolated worktree completion")
    return TaskStore(store.home), task_id


def _fresh_state(home: Path, task_id: str, mode: str) -> dict[str, Any]:
    script = (
        "from agent_nous.taskctl import TaskStore\n"
        "from pathlib import Path\n"
        "import json, sys\n"
        "store=TaskStore(Path(sys.argv[1]))\n"
        "task=sys.argv[2]\n"
        "if sys.argv[3] == 'resume': value=store.resume_prepare(task, store.read_task(task)[1]['revision'], 'fresh-eval')['state']\n"
        "else: value=store.read_task(task)[1]\n"
        "print(json.dumps(value, sort_keys=True))\n"
    )
    return json.loads(subprocess.check_output((sys.executable, "-c", script, str(home), task_id, mode), text=True))


def _fresh_record_durability(home: Path, task_id: str, observation: dict[str, Any]) -> dict[str, Any]:
    payload = home / f"{task_id}-observation.json"
    atomic_write_json(payload, observation)
    script = (
        "from agent_nous.taskctl import TaskStore\n"
        "from pathlib import Path\n"
        "import json, sys\n"
        "home=Path(sys.argv[1]); task=sys.argv[2]; observation=json.loads(Path(sys.argv[3]).read_text())\n"
        "store=TaskStore(home)\n"
        "checkpoint=store.read_task(task)[1]\n"
        "resumed=store.resume_prepare(task, checkpoint['revision'], 'fresh-eval')['state']\n"
        "result=store.record_git_durability_observation(task, resumed['revision'], 'fresh-eval', observation)\n"
        "print(json.dumps(result['state'], sort_keys=True))\n"
    )
    return json.loads(subprocess.check_output((sys.executable, "-c", script, str(home), task_id, str(payload)), text=True))


def _fresh_child_lease_recovery(home: Path, task_id: str) -> dict[str, Any] | None:
    script = (
        "from agent_nous.taskctl import TaskStore\n"
        "from pathlib import Path\n"
        "import json, sys\n"
        "store=TaskStore(Path(sys.argv[1]))\n"
        "lease=store.recover_expired_child_leases(sys.argv[2]) or {}\n"
        "reservations=store.recover_expired_reservations(sys.argv[2]) or {}\n"
        "print(json.dumps({'expired_loop_ids': lease.get('expired_loop_ids', []), 'expired_reservation_ids': reservations.get('expired_reservation_ids', [])}, sort_keys=True))\n"
    )
    raw = subprocess.check_output(("/usr/bin/python3", "-c", script, str(home), task_id), text=True)
    value = json.loads(raw)
    return value if isinstance(value, dict) else None


def _git(*args: str) -> None:
    subprocess.run(("git", *args), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _git_output(*args: str) -> str:
    return subprocess.check_output(("git", *args), text=True)


def _tree_snapshot(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = str(path.relative_to(root))
        if relative.startswith("evidence/") or "/.git/" in f"/{relative}" or relative.startswith(".git/"):
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        result[relative] = digest.hexdigest()
    return result


def _changed_paths(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))


def _capture_canonical_evidence(
    environment: TrialEnvironment,
    *,
    before: dict[str, str],
    before_run_scope: dict[str, str] | None = None,
    worker: subprocess.CompletedProcess[str],
    process_workers: list[dict[str, Any]] | None = None,
    attestation_key: bytes | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Freeze parent-owned, portable evidence after the sandbox worker exits.

    Worker stdout is intentionally not an authority.  The parent discovers
    Tasks from the isolated store, validates canonical schemas/bindings, and
    derives the action/policy/diff views consumed by the release grader.
    """
    store = TaskStore(environment.home)
    task_paths = sorted(
        path for path in (environment.home / "tasks").iterdir()
        if path.is_dir() and (path / "task-definition.json").is_file()
    ) if (environment.home / "tasks").is_dir() else []
    task_states: list[dict[str, Any]] = []
    task_events: list[dict[str, Any]] = []
    policy_actions: list[dict[str, Any]] = []
    task_auxiliary: list[dict[str, Any]] = []
    validation_failures: list[str] = []
    for task_path in task_paths:
        task_id = task_path.name
        try:
            definition, state, _ = store.read_task(task_id)
            checkpoint = _read_json(task_path / "checkpoints" / "latest.json")
            store.schemas.validate("checkpoint", checkpoint)
            if checkpoint.get("task_id") != task_id or checkpoint.get("revision") != state.get("revision"):
                raise ValueError("checkpoint state binding mismatch")
            if checkpoint.get("state_digest") != digest_json(state):
                raise ValueError("checkpoint state digest mismatch")
            events = events_for(task_path)
            if not events or events[-1].get("revision") != state.get("revision"):
                raise ValueError("event/state revision binding mismatch")
            if events[0].get("revision") != 1:
                raise ValueError("event journal must begin at revision 1")
            previous_revision = 0
            seen_event_ids: set[str] = set()
            revision_transactions: dict[int, str] = {}
            for event in events:
                store.schemas.validate("event", event)
                event_id = event.get("event_id")
                revision = int(event.get("revision", 0))
                transaction_id = event.get("transaction_id")
                if event.get("task_id") != task_id or revision < previous_revision or not isinstance(event_id, str) or event_id in seen_event_ids:
                    raise ValueError("event journal binding mismatch")
                if not isinstance(transaction_id, str) or not transaction_id.startswith("tx-"):
                    raise ValueError("event transaction binding mismatch")
                if revision > previous_revision + 1:
                    raise ValueError("event journal revision gap")
                if revision in revision_transactions and revision_transactions[revision] != transaction_id:
                    raise ValueError("events at one revision must share a transaction")
                revision_transactions.setdefault(revision, transaction_id)
                seen_event_ids.add(event_id)
                previous_revision = revision
            task_states.append(
                {
                    "task_id": task_id,
                    "definition_digest": digest_json(definition),
                    "state": {key: state.get(key) for key in ("revision", "lifecycle_status", "execution_status", "phase", "phase_kind", "cycle", "sync_status")},
                    "checkpoint": {key: checkpoint.get(key) for key in ("checkpoint_id", "revision", "phase", "cycle", "state_digest")},
                }
            )
            task_events.append(
                {
                    "task_id": task_id,
                    "events": [
                        {
                            "event_id": event["event_id"],
                            "type": event["type"],
                            "revision": event["revision"],
                            "transaction_id": event["transaction_id"],
                            "actor": event["actor"],
                            "action_id": event.get("payload", {}).get("action_id"),
                            "action_digest": event.get("payload", {}).get("action_digest"),
                            "policy_outcome": event.get("payload", {}).get("policy_outcome"),
                            "loop_id": event.get("payload", {}).get("loop_id"),
                            "reservation_id": event.get("payload", {}).get("reservation_id"),
                            "reservation_ids": event.get("payload", {}).get("reservation_ids"),
                            "contract_ref": event.get("payload", {}).get("contract_ref"),
                            "content_hash": event.get("payload", {}).get("content_hash"),
                            "conflicts_with": event.get("payload", {}).get("conflicts_with"),
                            "observation_ref": event.get("payload", {}).get("observation_ref"),
                            "relation": event.get("payload", {}).get("relation"),
                            "outcome": event.get("payload", {}).get("outcome"),
                        }
                        for event in events
                    ],
                }
            )
            seen_idempotency_keys: set[str] = set()
            for action_path in sorted((task_path / "actions").glob("*.json")):
                record = _read_json(action_path)
                store.schemas.validate("action-record", record)
                action = record["action"]
                store.schemas.validate("action", action)
                if action_path.stem != action.get("action_id"):
                    raise ValueError("action record filename binding mismatch")
                descriptor = ActionDescriptor(
                    action_id=action["action_id"], task_id=action["task_id"], kind=action["kind"],
                    repository_root=action["repository_root"], targets=tuple(action["targets"]),
                    command=tuple(action["command"]) if action["command"] else None,
                    network_destinations=tuple(action["network_destinations"]),
                    estimated_cost_usd=action["estimated_cost_usd"], approval_id=action["approval_id"],
                    declared_risk=action["risk"], loop_id=action["loop_id"],
                    expected_state_revision=action["expected_state_revision"], request_id=action["request_id"],
                    idempotency_key=action["idempotency_key"], expected_effect=action["expected_effect"],
                    plan_ref=action["plan_ref"], plan_content_hash=action["plan_content_hash"],
                    environment=action["environment"], reservation_ids=tuple(action["reservation_ids"]), resource_ids=tuple(action["resource_ids"]),
                )
                if descriptor.digest != action["digest"] or action["task_id"] != task_id:
                    raise ValueError("action descriptor digest binding mismatch")
                idempotency_key = action.get("idempotency_key")
                if isinstance(idempotency_key, str) and idempotency_key:
                    if idempotency_key in seen_idempotency_keys:
                        raise ValueError("duplicate action idempotency key")
                    seen_idempotency_keys.add(idempotency_key)
                grants = []
                grant_scope: dict[str, Any] = {}
                child_contract_ref: str | None = None
                worktree_ref: dict[str, Any] | None = None
                if record.get("grant_id"):
                    grant_path = task_path / "grants" / f"{record['grant_id']}.json"
                    grant = _read_json(grant_path)
                    store.schemas.validate("execution-grant", grant)
                    if grant.get("action_id") != action["action_id"] or grant.get("action_digest") != action["digest"]:
                        raise ValueError("execution grant binding mismatch")
                    grants = [record["grant_id"]]
                    grant_scope = grant.get("effective_scope", {})
                    child_contract_ref = grant.get("child_contract_ref")
                    worktree_ref = grant.get("worktree_ref")
                probes: dict[str, str] = {}
                for label, ref in (("before", record.get("before_evidence_ref")), ("after", record.get("after_evidence_ref"))):
                    if ref is None:
                        continue
                    if not isinstance(ref, str) or ref.startswith("/") or ".." in Path(ref).parts:
                        raise ValueError("unsafe probe reference")
                    probe = _read_json(task_path / ref)
                    store.schemas.validate("probe-evidence", probe)
                    if probe.get("action_id") != action["action_id"]:
                        raise ValueError("probe action binding mismatch")
                    if probe.get("result") not in {"pass", "fail", "ambiguous"}:
                        raise ValueError("probe result is invalid")
                    if label == "after" and record.get("status") == "completed" and probe.get("result") != "pass":
                        raise ValueError("completed action postflight is not pass")
                    probes[label] = str(probe.get("result"))
                matching_events = [
                    event
                    for event in events
                    if event.get("payload", {}).get("action_id") == action["action_id"]
                    and event.get("payload", {}).get("action_digest") == action["digest"]
                ]
                matching_types = {event.get("type") for event in matching_events}
                if "policy_decided" not in matching_types:
                    raise ValueError("action policy event binding missing")
                if record.get("status") == "completed":
                    if not {"execution_grant_issued", "action_started", "action_completed"}.issubset(matching_types):
                        raise ValueError("completed action lifecycle binding missing")
                    if not grants or probes.get("before") != "pass" or probes.get("after") != "pass":
                        raise ValueError("completed action evidence binding missing")
                    if any(
                        event.get("payload", {}).get("execution_grant_ref") != f"grants/{grants[0]}.json"
                        for event in matching_events
                        if event.get("type") in {"execution_grant_issued", "action_completed"}
                    ):
                        raise ValueError("execution grant event reference mismatch")
                    started_events = [event for event in matching_events if event.get("type") == "action_started"]
                    completed_events = [event for event in matching_events if event.get("type") == "action_completed"]
                    if not started_events or not completed_events or any(event.get("payload", {}).get("before_probe_ref") != record.get("before_evidence_ref") for event in started_events) or any(event.get("payload", {}).get("after_probe_ref") != record.get("after_evidence_ref") for event in completed_events):
                        raise ValueError("action probe event reference mismatch")
                elif record.get("status") in {"denied", "unsupported"} and "action_started" in matching_types:
                    raise ValueError("denied action started")
                policy_actions.append(
                    {
                        "task_id": task_id,
                        "action_id": action["action_id"], "digest": action["digest"], "kind": action["kind"],
                        "status": record["status"], "policy_outcome": record["policy_outcome"],
                        "loop_id": action["loop_id"], "targets": [_portable_target(target, environment) for target in action["targets"]],
                        "reservation_ids": list(action["reservation_ids"]), "resource_ids": list(action["resource_ids"]),
                        "grant_ids": grants, "probes": probes, "idempotency_key": record["idempotency_key"],
                        "approval_bound": bool(action.get("approval_id")),
                        "repository_root": _portable_target(action["repository_root"], environment),
                        "repository_root_identity": grant_scope.get("repository_root_identity"),
                        "child_contract_ref": child_contract_ref,
                        "worktree_ref": worktree_ref,
                    }
                )
            approvals = _read_json(task_path / "approvals.json")
            if approvals:
                for approval in approvals.get("approvals", []):
                    store.schemas.validate("approval", approval)
                    request_ref = approval["requested_action_descriptor_ref"]["path"]
                    request = _read_json(task_path / request_ref)
                    store.schemas.validate("approval-request", request)
                    if request.get("approval_id") != approval.get("approval_id"):
                        raise ValueError("approval request identity mismatch")
                    requested_action = request["action"]
                    descriptor = ActionDescriptor(
                        action_id=requested_action["action_id"], task_id=requested_action["task_id"], kind=requested_action["kind"],
                        repository_root=requested_action["repository_root"], targets=tuple(requested_action["targets"]),
                        command=tuple(requested_action["command"]) if requested_action["command"] else None,
                        network_destinations=tuple(requested_action["network_destinations"]), estimated_cost_usd=requested_action["estimated_cost_usd"],
                        approval_id=requested_action["approval_id"], declared_risk=requested_action["risk"], loop_id=requested_action["loop_id"],
                        expected_state_revision=requested_action["expected_state_revision"], request_id=requested_action["request_id"],
                        idempotency_key=requested_action["idempotency_key"], expected_effect=requested_action["expected_effect"],
                        plan_ref=requested_action["plan_ref"], plan_content_hash=requested_action["plan_content_hash"],
                        environment=requested_action["environment"], reservation_ids=tuple(requested_action["reservation_ids"]), resource_ids=tuple(requested_action["resource_ids"]),
                    )
                    if descriptor.digest != requested_action["digest"] or requested_action["digest"] not in approval["approved_action_digests"]:
                        raise ValueError("approval requested action digest mismatch")
                    question_id = approval["created_from_question_id"]
                    question = _read_json(task_path / "human-input" / "questions" / f"{question_id}.json")
                    store.schemas.validate("human-question", question)
                    if question.get("approval_request_ref") != request_ref or question.get("blocking_scope") != "action":
                        raise ValueError("approval question binding mismatch")
                    deny = next((item for item in question["options"] if item.get("option_id") == "deny"), None)
                    combined = " ".join((question["question"], question["why_now"], deny.get("impact", "") if isinstance(deny, dict) else ""))
                    if (
                        question.get("recommended_option_id") != "deny"
                        or not isinstance(deny, dict)
                        or "代替" not in deny.get("impact", "")
                        or requested_action["kind"] not in combined
                        or not all(target in combined for target in requested_action["targets"])
                        or (requested_action.get("expected_effect") and requested_action["expected_effect"] not in combined)
                    ):
                        raise ValueError("approval question lacks exact target impact or safe alternative")
                    if approval.get("status") in {"active", "exhausted"}:
                        answer_ref = approval.get("granted_answer_ref")
                        if not isinstance(answer_ref, str) or answer_ref.startswith("/") or ".." in Path(answer_ref).parts:
                            raise ValueError("approval granted answer reference is unsafe")
                        answer = _read_json(task_path / answer_ref)
                        store.schemas.validate("human-answer", answer)
                        if answer.get("question_id") != question_id or answer.get("answer_type") != "selected_option" or answer.get("selected_option_id") != "approve" or answer.get("applied_scope") != "action" or answer.get("answer_effect") != "resume" or answer.get("resume_target") != question.get("resume_target"):
                            raise ValueError("approval granted answer binding mismatch")
                    approval_events = [
                        event
                        for event in events
                        if event.get("payload", {}).get("action_id") == requested_action["action_id"]
                        and event.get("payload", {}).get("action_digest") == requested_action["digest"]
                    ]
                    question_events = [
                        event
                        for event in events
                        if event.get("payload", {}).get("approval_request_ref") == request_ref
                        and event.get("payload", {}).get("question_id") == question_id
                    ]
                    if "approval_requested" not in {event.get("type") for event in approval_events} or "human_input_requested" not in {event.get("type") for event in question_events}:
                        raise ValueError("approval event binding mismatch")
                    policy_actions.append(
                        {
                            "task_id": task_id,
                            "action_id": requested_action["action_id"], "digest": requested_action["digest"],
                            "kind": requested_action["kind"], "status": "approval_requested",
                            "policy_outcome": "approval_required", "loop_id": requested_action["loop_id"],
                            "targets": [_portable_target(target, environment) for target in requested_action["targets"]], "reservation_ids": list(requested_action["reservation_ids"]),
                            "resource_ids": list(requested_action["resource_ids"]), "grant_ids": [], "probes": {},
                            "idempotency_key": requested_action["idempotency_key"], "approval_bound": True,
                            "repository_root": _portable_target(requested_action["repository_root"], environment),
                            "repository_root_identity": None,
                            "child_contract_ref": None,
                            "worktree_ref": None,
                        }
                    )
            reservations = []
            for reservation_path in sorted((task_path / "reservations").glob("*.json")) if (task_path / "reservations").is_dir() else []:
                reservation = _read_json(reservation_path)
                store.schemas.validate("reservation", reservation)
                reservations.append({key: reservation.get(key) for key in ("reservation_id", "loop_id", "contract_ref", "resource", "mode", "status", "lease_token")})
            children = []
            for entry in state.get("child_loops", []):
                loop_id = str(entry["loop_id"])
                child_path = task_path / "loops" / loop_id / "state.json"
                child_checkpoint_path = task_path / "loops" / loop_id / "checkpoints" / "latest.json"
                child_state = _read_json(child_path)
                child_checkpoint = _read_json(child_checkpoint_path)
                store.schemas.validate("task-state", child_state)
                store.schemas.validate("checkpoint", child_checkpoint)
                if child_state.get("task_id") != task_id or child_state.get("loop_id") != loop_id:
                    raise ValueError("child state identity binding mismatch")
                if child_checkpoint.get("revision") != child_state.get("revision") or child_checkpoint.get("state_digest") != digest_json(child_state):
                    raise ValueError("child checkpoint state binding mismatch")
                active_contract = child_state.get("active_contract_ref")
                if not isinstance(active_contract, dict) or active_contract.get("path") != entry.get("contract_ref") or active_contract.get("content_hash") != entry.get("content_hash"):
                    raise ValueError("child contract state binding mismatch")
                children.append(
                    {
                        "loop_id": loop_id,
                        "contract_ref": entry.get("contract_ref"),
                        "content_hash": entry.get("content_hash"),
                        "entry_status": entry.get("status"),
                        "revision": child_state.get("revision"),
                        "lifecycle_status": child_state.get("lifecycle_status"),
                        "execution_status": child_state.get("execution_status"),
                        "last_error": child_state.get("last_error"),
                        "active_lease": bool(child_state.get("active_lease")),
                        "current_action_id": child_state.get("current_action", {}).get("action_id") if isinstance(child_state.get("current_action"), dict) else None,
                        "checkpoint_revision": child_checkpoint.get("revision"),
                    }
                )
            contracts = []
            for contract_path in sorted((task_path / "loops").glob("*/contract.json")) if (task_path / "loops").is_dir() else []:
                contract = _read_json(contract_path)
                store.schemas.validate("child-contract", contract)
                if contract.get("task_id") != task_id or contract.get("content_hash") != digest_json({key: value for key, value in contract.items() if key != "content_hash"}):
                    raise ValueError("child contract binding mismatch")
                contracts.append(
                    {
                        "loop_id": contract["loop_id"],
                        "content_hash": contract["content_hash"],
                        "resources": list(contract["resources"]),
                        "write_scope": list(contract["write_scope"]),
                        "preferred_backend": contract["preferred_backend"],
                        "allowed_actions": list(contract["allowed_actions"]),
                    }
                )
            contracts_by_loop = {item["loop_id"]: item for item in contracts}
            for action_summary in policy_actions:
                if action_summary.get("task_id") != task_id:
                    continue
                contract_summary = contracts_by_loop.get(action_summary.get("loop_id"))
                if contract_summary is not None:
                    action_summary["contract_ref"] = f"loops/{action_summary['loop_id']}/contracts/rev-0001.json"
                    action_summary["contract_content_hash"] = contract_summary["content_hash"]
                else:
                    action_summary["contract_ref"] = None
                    action_summary["contract_content_hash"] = None
            worktrees = []
            for worktree_path in sorted((task_path / "loops").glob("*/worktree.json")) if (task_path / "loops").is_dir() else []:
                worktree = _read_json(worktree_path)
                store.schemas.validate("worktree", worktree)
                if worktree.get("task_id") != task_id:
                    raise ValueError("worktree task binding mismatch")
                worktrees.append({"loop_id": worktree.get("loop_id"), "action_id": worktree.get("action_id"), "branch": worktree.get("branch"), "base_commit": worktree.get("base_commit"), "contract_ref": worktree.get("contract_ref"), "worktree_path": _portable_target(str(worktree.get("worktree_path", "")), environment), "worktree_identity": worktree.get("worktree_identity")})
            durability = []
            for durability_path in sorted((task_path / "evidence").glob("git-durability-*.json")) if (task_path / "evidence").is_dir() else []:
                remote = _read_json(durability_path)
                store.schemas.validate("remote-state", remote)
                durability.append({"observation_ref": str(durability_path.relative_to(task_path)), "repository_identity": _portable_target(str(remote.get("repository_identity", "")), environment), **{key: remote.get(key) for key in ("branch", "local_head", "remote_head", "status", "artifact_commit")}})
            review = _capture_completed_review(task_path, task_id, state, store)
            task_auxiliary.append({"task_id": task_id, "children": children, "reservations": reservations, "contracts": contracts, "worktrees": worktrees, "durability": durability, "review": review})
        except Exception as exc:
            validation_failures.append(f"{task_id}:{type(exc).__name__}")
    changed_paths = _changed_paths(before, _tree_snapshot(environment.root))
    run_scope_changed_paths = (
        _changed_paths(before_run_scope, _tree_snapshot(environment.run_root / "trials"))
        if before_run_scope is not None
        else []
    )
    out_of_trial_paths = [
        path for path in run_scope_changed_paths
        if not path.startswith(f"{environment.trial_id}/")
    ]
    process = {
        "returncode": worker.returncode,
        "stdout_sha256": hashlib.sha256(worker.stdout.encode("utf-8")).hexdigest(),
        "stderr_sha256": hashlib.sha256(worker.stderr.encode("utf-8")).hexdigest(),
        "stdout_secret_like": bool(secret_summary(worker.stdout)),
        "stderr_secret_like": bool(secret_summary(worker.stderr)),
    }
    if process_workers is not None:
        process["workers"] = process_workers
    state_evidence = {"schema_version": 1, "tasks": task_states, "validation_failures": validation_failures}
    events_evidence = {"schema_version": 1, "tasks": task_events}
    git_fixture: dict[str, Any] = {}
    fixture_path = environment.scratch / "local-bare-fixture.json"
    divergence_path = environment.scratch / "local-bare-divergence.json"
    if fixture_path.is_file():
        fixture = _read_json(fixture_path)
        required_fixture_fields = {"fixture_id", "fixture_kind", "branch", "bare", "first_clone", "second_clone", "base_commit", "bare_identity", "first_clone_identity", "second_clone_identity"}
        if not required_fixture_fields.issubset(fixture):
            validation_failures.append("local_bare_fixture_identity_missing")
        else:
            git_fixture["setup"] = {key: fixture[key] for key in sorted(required_fixture_fields)}
    if divergence_path.is_file():
        divergence = _read_json(divergence_path)
        required_divergence_fields = {"fixture_id", "injection", "branch", "second_clone", "second_clone_identity", "second_commit", "first_clone_identity"}
        if not required_divergence_fields.issubset(divergence):
            validation_failures.append("local_bare_divergence_identity_missing")
        else:
            git_fixture["divergence"] = {key: divergence[key] for key in sorted(required_divergence_fields)}
    if isinstance(git_fixture.get("setup"), dict) and isinstance(git_fixture.get("divergence"), dict):
        fixture_tasks = [item for item in task_auxiliary if item.get("durability")]
        durability = fixture_tasks[0].get("durability", []) if len(fixture_tasks) == 1 else []
        validation_failures.extend(
            _validate_local_bare_fixture(
                environment,
                git_fixture["setup"],
                git_fixture["divergence"],
                durability if isinstance(durability, list) else [],
            )
        )
    policy_evidence = {"schema_version": 1, "actions": policy_actions, "tasks": task_auxiliary, "git_fixture": git_fixture}
    diff_evidence = {
        "schema_version": 1,
        "changed_paths": changed_paths,
        "run_scope_changed_paths": run_scope_changed_paths,
        "out_of_trial_paths": out_of_trial_paths,
    }
    for name, value in (("state.json", state_evidence), ("events.json", events_evidence), ("policy.json", policy_evidence), ("diff.json", diff_evidence), ("process.json", process)):
        atomic_write_json(environment.evidence / name, value)
    canonical = {
        "schema_version": 1,
        "task_ids": [item["task_id"] for item in task_states],
        "state": state_evidence,
        "events": events_evidence,
        "policy": policy_evidence,
        "diff": diff_evidence,
        "process": process,
        "validation_failures": validation_failures,
    }
    atomic_write_json(environment.evidence / "canonical.json", canonical)
    if attestation_key is not None and run_id is not None:
        evidence_digests = {
            name: hashlib.sha256((environment.evidence / name).read_bytes()).hexdigest()
            for name in ("state.json", "events.json", "policy.json", "diff.json", "process.json", "canonical.json")
        }
        attestation_unsigned = {
            "schema_version": 1,
            "run_id": run_id,
            "trial_id": environment.trial_id,
            "canonical_digest": digest_json(canonical),
            "evidence_digests": evidence_digests,
        }
        attestation = {
            **attestation_unsigned,
            "signature": hmac.new(
                attestation_key,
                json.dumps(attestation_unsigned, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                hashlib.sha256,
            ).hexdigest(),
        }
        atomic_write_json(environment.evidence / "parent-attestation.json", attestation)
        canonical["provenance"] = {
            "attestation_ref": "parent-attestation.json",
            "canonical_digest": attestation["canonical_digest"],
            "attestation_digest": digest_json(attestation),
        }
    return canonical


def _portable_target(target: str, environment: TrialEnvironment) -> str:
    """Normalize accidental trial-root absolute paths before retention."""
    path = Path(target)
    if not path.is_absolute():
        return target
    try:
        return f"trial://{path.resolve().relative_to(environment.root)}"
    except ValueError:
        # This must not normally occur: Policy Gateway rejects scope escapes.
        # Retain neither host identity nor an untrusted external locator.
        return "external://redacted"


def _capture_completed_review(task_path: Path, task_id: str, state: dict[str, Any], store: TaskStore) -> dict[str, Any] | None:
    """Verify completion evidence before publishing a portable summary."""
    if state.get("lifecycle_status") != "completed":
        return None
    result_ref = state.get("latest_review_result_ref")
    decision_ref = state.get("latest_review_decision_ref")
    if not isinstance(result_ref, dict) or not isinstance(decision_ref, dict):
        raise ValueError("completed task is missing immutable review references")
    result_path = _task_relative_path(task_path, result_ref.get("path"))
    decision_path = _task_relative_path(task_path, decision_ref.get("path"))
    result = _read_json(result_path)
    decision = _read_json(decision_path)
    store.schemas.validate("phase-result", result)
    if result.get("task_id") != task_id or result_ref.get("content_hash") != digest_json(result):
        raise ValueError("review result reference binding mismatch")
    if (
        decision.get("schema_version") != 1
        or decision.get("task_id") != task_id
        or decision.get("artifact_id") != result.get("artifact_id")
        or decision.get("decision") != "completed"
        or decision_ref.get("content_hash") != digest_json(decision)
    ):
        raise ValueError("review decision reference binding mismatch")
    acceptance = decision.get("acceptance_criteria")
    validation = decision.get("validation_evidence")
    blockers = decision.get("blocking_issues")
    if (
        not isinstance(acceptance, list)
        or not acceptance
        or any(not isinstance(item, dict) or item.get("status") != "passed" or not item.get("evidence_refs") for item in acceptance)
        or not isinstance(validation, list)
        or not validation
        or any(not isinstance(item, dict) or item.get("outcome") != "passed" or not item.get("ref") for item in validation)
        or blockers != []
    ):
        raise ValueError("completed review acceptance or validation evidence is invalid")
    return {
        "decision": "completed",
        "artifact_id": result["artifact_id"],
        "acceptance_count": len(acceptance),
        "validation_count": len(validation),
        "accepted_children": list(decision.get("accepted_children", [])),
        "cleanup_action_ids": sorted(
            action_id
            for item in decision.get("cleanup_actions", [])
            if isinstance(item, dict)
            for action_id in (item.get("worktree_remove_action_id"), item.get("branch_delete_action_id"))
            if isinstance(action_id, str)
        ),
    }


def _task_relative_path(task_path: Path, value: object) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute() or ".." in Path(value).parts:
        raise ValueError("unsafe canonical task reference")
    path = (task_path / value).resolve()
    if task_path.resolve() not in path.parents or not path.is_file():
        raise ValueError("canonical task reference is missing")
    return path


def _portable_trace(observed: dict[str, Any], environment: TrialEnvironment) -> dict[str, Any]:
    events = observed.get("events", {}).get("tasks", []) if isinstance(observed.get("events"), dict) else []
    actions = observed.get("policy", {}).get("actions", []) if isinstance(observed.get("policy"), dict) else []
    return {
        "schema_version": 1,
        "trial_id": environment.trial_id,
        "network_policy": "deny",
        "credential_policy": "none",
        "task_ids": list(observed.get("task_ids", [])),
        "event_types": [event.get("type") for task in events for event in task.get("events", [])],
        "policy_decisions": [action.get("policy_outcome") for action in actions],
        "action_kinds": [action.get("kind") for action in actions],
        "changed_paths": list(observed.get("diff", {}).get("changed_paths", [])),
        "sandbox_enforced": bool(observed.get("sandbox_enforced", False)),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--release", default="v1.0")
    parser.add_argument("--root")
    args = parser.parse_args(argv)
    result = EvalRunner(root=Path(args.root) if args.root else None).run(release=args.release)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["outcome"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
