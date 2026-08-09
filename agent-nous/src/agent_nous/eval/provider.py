from __future__ import annotations

"""Host/provider boundary for live NCS capability trials.

The core evaluator remains deterministic and does not perform provider I/O.
This module is an explicit adapter for a trusted host or CI integration.  A
provider owns the agent execution and returns parent-owned EvalRunner capture
references; Nous validates and grades only those captures.
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..errors import ValidationError
from ..io import atomic_write_json, digest_json
from .capability import (
    capability_observation_from_eval_runner_capture,
    load_capability_manifest,
    validate_capability_observation_bundle,
)
from .capability_runner import CapabilityEvalRunner

_PROVIDER_CONFIG_KEYS = frozenset({
    "schema_version",
    "provider_id",
    "provider_version",
    "command",
    "environment_keys",
    "timeout_seconds",
    "max_output_bytes",
    "network_policy",
})
_REQUIRED_CAPTURE_FILES = ("canonical.json", "outcome.json", "environment.json", "parent-attestation.json")
_ALLOWED_NETWORK_POLICIES = frozenset({"host_managed", "deny"})


def _safe_identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 128 or any(ch in value for ch in "/\\\x00\r\n"):
        raise ValidationError(f"{label} must be a safe nonempty identifier")
    return value


def _safe_relative(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValidationError(f"{label} must be a relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path == Path("."):
        raise ValidationError(f"{label} must stay below the provider workspace")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class ProviderEvalConfig:
    """Validated host adapter configuration.

    ``command`` is argv, never a shell string.  Environment values are not
    stored in this object; only names explicitly allowed by the host are
    copied into the child process at execution time.
    """

    provider_id: str
    provider_version: str
    command: tuple[str, ...]
    environment_keys: tuple[str, ...] = ()
    timeout_seconds: int = 900
    max_output_bytes: int = 1_048_576
    network_policy: str = "host_managed"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ProviderEvalConfig":
        if not isinstance(value, Mapping):
            raise ValidationError("provider config must be an object")
        unknown = sorted(set(value) - _PROVIDER_CONFIG_KEYS)
        if unknown:
            raise ValidationError("provider config has unknown keys: " + ", ".join(str(item) for item in unknown))
        if value.get("schema_version", 1) != 1:
            raise ValidationError("provider config schema_version must be 1")
        command = value.get("command")
        if not isinstance(command, list) or not command or any(not isinstance(item, str) or not item for item in command):
            raise ValidationError("provider command must be a nonempty argv list")
        if any(";" in item or "|" in item or "&&" in item or "\n" in item for item in command):
            raise ValidationError("provider command must not contain shell syntax")
        executable = Path(command[0]).name.lower()
        if executable in {"sh", "bash", "zsh", "fish", "cmd", "powershell", "pwsh"} or "-c" in command or "/c" in command:
            raise ValidationError("provider command must be a direct executable, not a shell wrapper")
        environment_keys = value.get("environment_keys", [])
        if not isinstance(environment_keys, list) or any(not isinstance(item, str) or not item or "=" in item for item in environment_keys):
            raise ValidationError("environment_keys must contain variable names only")
        if len(set(environment_keys)) != len(environment_keys):
            raise ValidationError("environment_keys must not repeat names")
        timeout = value.get("timeout_seconds", 900)
        maximum = value.get("max_output_bytes", 1_048_576)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 86_400:
            raise ValidationError("timeout_seconds must be between 1 and 86400")
        if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1_024 <= maximum <= 16 * 1024 * 1024:
            raise ValidationError("max_output_bytes must be between 1024 and 16777216")
        policy = value.get("network_policy", "host_managed")
        if policy not in _ALLOWED_NETWORK_POLICIES:
            raise ValidationError("network_policy must be host_managed or deny")
        # Provider identity and version are provenance, not executable input.
        provider_id = _safe_identifier(value.get("provider_id"), "provider_id")
        provider_version = _safe_identifier(value.get("provider_version"), "provider_version")
        for item in command:
            if len(item) > 4096:
                raise ValidationError("provider command argument is too long")
            if _looks_like_secret(item):
                raise ValidationError("provider command contains a secret-like value")
        return cls(provider_id, provider_version, tuple(command), tuple(environment_keys), timeout, maximum, policy)

    def public(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "command_argv_sha256": hashlib.sha256(json.dumps(self.command, ensure_ascii=True).encode()).hexdigest(),
            "environment_key_names": list(self.environment_keys),
            "timeout_seconds": self.timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
            "network_policy": self.network_policy,
            "containment_claim": False,
        }


def _looks_like_secret(value: str) -> bool:
    lowered = value.lower()
    if "-----begin " in lowered or "sk-" in lowered or "ghp_" in lowered or "xoxb-" in lowered:
        return True
    return any(token in lowered for token in ("api" + "_key" + "=", "token" + "=", "secret" + "=", "pass" + "word" + "="))


class ProviderEvalRunner:
    """Run a host-governed provider and grade its parent-owned captures.

    The provider must write a strict ``provider-result.json`` at the path
    supplied by ``NOUS_PROVIDER_OUTPUT_FILE``.  Its ``captures`` entries may
    reference only directories below the adapter workspace.  The adapter
    copies no raw provider output into reports and passes each capture through
    the existing digest-bound EvalRunner capture converter.
    """

    def __init__(self, manifest_path: Path | None = None, root: Path | None = None) -> None:
        self.manifest_path = manifest_path
        self.root = root.resolve() if root else None

    def manifest(self):
        return load_capability_manifest(self.manifest_path)

    def validate_config(self, config: ProviderEvalConfig | Mapping[str, Any]) -> dict[str, Any]:
        parsed = config if isinstance(config, ProviderEvalConfig) else ProviderEvalConfig.from_mapping(config)
        return {"valid": True, "provider": parsed.public(), "contract": self.contract()}

    @staticmethod
    def contract() -> dict[str, Any]:
        return {
            "schema_version": 1,
            "result_file": "provider-result.json",
            "required_capture_files": list(_REQUIRED_CAPTURE_FILES),
            "required_trials": 3,
            "minimum_successes": 2,
            "provider_output_is_untrusted": True,
            "holdout_answers_must_remain_external": True,
        }

    def run(
        self,
        *,
        config: ProviderEvalConfig | Mapping[str, Any],
        profile: str = "smoke",
        allow_host_provider: bool = False,
        baseline_path: Path | None = None,
    ) -> dict[str, Any]:
        parsed = config if isinstance(config, ProviderEvalConfig) else ProviderEvalConfig.from_mapping(config)
        if not allow_host_provider:
            raise ValidationError("provider execution requires explicit --allow-host-provider")
        if profile not in {"smoke", "full", "holdout"}:
            raise ValidationError("provider profile must be smoke, full, or holdout")
        tasks = self.manifest().select_profile(profile)
        root = self.root or Path(tempfile.mkdtemp(prefix="nous-provider-eval-"))
        root.mkdir(parents=True, exist_ok=True)
        run_id = f"providerrun-{uuid.uuid4().hex[:12]}"
        run_root = root / "runs" / run_id
        workspace = run_root / "provider-workspace"
        workspace.mkdir(parents=True, exist_ok=False)
        request_path = run_root / "provider-request.json"
        output_path = workspace / "provider-result.json"
        request = {
            "schema_version": 1,
            "run_id": run_id,
            "profile": profile,
            "manifest_digest": self.manifest().digest,
            "task_ids": [item["task_id"] for item in tasks],
            "trial_repetitions": int(self.manifest().value["trial_policy"]["repetitions"]),
            "minimum_successes": int(self.manifest().value["trial_policy"]["minimum_successes"]),
            "workspace_contract": {"capture_root": "provider-workspace", "result_file": "provider-result.json"},
            "holdout_policy": "host_resolves_hidden_cases; Nous receives captures only",
        }
        atomic_write_json(request_path, request)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", ""),
            "LANG": "C.UTF-8",
            "NOUS_PROVIDER_REQUEST_FILE": str(request_path),
            "NOUS_PROVIDER_OUTPUT_FILE": str(output_path),
        }
        missing_env = []
        for name in parsed.environment_keys:
            value = os.environ.get(name)
            if value is None:
                missing_env.append(name)
            else:
                env[name] = value
        if missing_env:
            raise ValidationError("approved provider environment variables are missing: " + ", ".join(missing_env))
        started = time.monotonic()
        try:
            completed = subprocess.run(
                parsed.command,
                cwd=workspace,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=parsed.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValidationError("provider execution timed out") from exc
        if len(completed.stdout.encode()) > parsed.max_output_bytes or len(completed.stderr.encode()) > parsed.max_output_bytes:
            raise ValidationError("provider process output exceeded configured bound")
        if completed.returncode != 0:
            raise ValidationError("provider execution failed; raw provider output is not retained")
        if not output_path.is_file() or output_path.is_symlink():
            raise ValidationError("provider result file is missing")
        result = self._load_result(output_path, parsed, run_id, profile, tasks, workspace)
        observations_path = run_root / "observations.json"
        bundle = self._capture_bundle(result, tasks, workspace)
        validate_capability_observation_bundle(bundle)
        atomic_write_json(observations_path, bundle)
        capability = CapabilityEvalRunner(manifest_path=self.manifest_path, root=root)
        report = capability.run(profile=profile, observations_path=observations_path, baseline_path=baseline_path)
        report["provider"] = parsed.public()
        report["provider_run_id"] = run_id
        report["provider_elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
        report["provider_execution"] = {
            "network_policy": parsed.network_policy,
            "containment_claim": False,
            "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
            "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
            "raw_output_retained": False,
        }
        atomic_write_json(run_root / "report.json", report)
        return report

    def _load_result(
        self,
        path: Path,
        config: ProviderEvalConfig,
        run_id: str,
        profile: str,
        tasks: tuple[dict[str, Any], ...],
        workspace: Path,
    ) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationError("provider result is not valid JSON") from exc
        if not isinstance(value, Mapping):
            raise ValidationError("provider result must be an object")
        allowed = {"schema_version", "run_id", "profile", "provider", "manifest_digest", "captures"}
        if set(value) - allowed or value.get("schema_version") != 1 or value.get("run_id") != run_id or value.get("profile") != profile:
            raise ValidationError("provider result envelope is invalid")
        provider = value.get("provider")
        if not isinstance(provider, Mapping) or provider.get("provider_id") != config.provider_id or provider.get("provider_version") != config.provider_version:
            raise ValidationError("provider result identity does not match config")
        if value.get("manifest_digest") != self.manifest().digest:
            raise ValidationError("provider result manifest digest does not match")
        captures = value.get("captures")
        if not isinstance(captures, list):
            raise ValidationError("provider result captures must be a list")
        expected_tasks = {item["task_id"] for item in tasks}
        seen: set[tuple[str, int]] = set()
        for item in captures:
            if not isinstance(item, Mapping):
                raise ValidationError("provider capture entry must be an object")
            task_id = item.get("task_id")
            trial = item.get("trial")
            if task_id not in expected_tasks or isinstance(trial, bool) or not isinstance(trial, int) or not 1 <= trial <= 3:
                raise ValidationError("provider capture task/trial is invalid")
            key = (str(task_id), trial)
            if key in seen:
                raise ValidationError("provider result repeats a task/trial capture")
            seen.add(key)
            relative = _safe_relative(item.get("evidence_dir"), "provider capture evidence_dir")
            evidence = (workspace / relative).resolve()
            if workspace.resolve() not in evidence.parents:
                raise ValidationError("provider capture escaped workspace")
            if evidence.is_symlink() or not evidence.is_dir():
                raise ValidationError("provider capture evidence directory is missing")
            for name in _REQUIRED_CAPTURE_FILES:
                candidate = evidence / name
                if candidate.is_symlink() or not candidate.is_file():
                    raise ValidationError(f"provider capture is missing {name}")
        expected = {(task_id, trial) for task_id in expected_tasks for trial in range(1, 4)}
        if seen != expected:
            raise ValidationError("provider result must contain exactly three captures for every selected task")
        return dict(value)

    def _capture_bundle(self, result: Mapping[str, Any], tasks: tuple[dict[str, Any], ...], workspace: Path) -> dict[str, Any]:
        by_task: dict[str, list[dict[str, Any]]] = {item["task_id"]: [] for item in tasks}
        for item in result["captures"]:
            evidence = (workspace / _safe_relative(item["evidence_dir"], "provider capture evidence_dir")).resolve()
            observation = capability_observation_from_eval_runner_capture(
                evidence,
                task_id=str(item["task_id"]),
                eval_id=str(item.get("eval_id")) if item.get("eval_id") else None,
                trial_outcome=str(item.get("outcome")) if item.get("outcome") else None,
            )
            raw = dict(observation)
            raw.pop("provenance", None)
            by_task[str(item["task_id"])].append({
                "source": "eval_runner_parent_capture",
                "evidence_digest": observation["provenance"]["evidence_digest"],
                "capability_observation": raw,
            })
        return {
            "schema_version": 1,
            "source": "eval_runner_parent_capture",
            "fixture_version": "provider-parent-capture-v1",
            "observations": by_task,
        }
