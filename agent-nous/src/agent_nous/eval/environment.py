from __future__ import annotations

import json
import os
import hashlib
import shutil
import signal
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import UnsupportedActionError
from ..io import atomic_write_json, digest_json


@dataclass(frozen=True)
class TrialEnvironment:
    """A single evaluator-owned filesystem and process boundary.

    All mutable fixture material belongs below scratch.  The runner retains
    evidence but never lets cleanup operate outside this allocated directory.
    """

    run_root: Path
    eval_id: str
    trial_id: str
    root: Path
    home: Path
    repository: Path
    scratch: Path
    worker_evidence: Path
    evidence: Path

    @classmethod
    def allocate(cls, run_root: Path, eval_id: str, ordinal: int) -> "TrialEnvironment":
        trial_id = f"{eval_id}-{ordinal:02d}-{uuid.uuid4().hex[:10]}"
        root = run_root / "trials" / trial_id
        home = root / "nous-home"
        repository = root / "repository"
        scratch = root / "scratch"
        worker_evidence = root / "worker-evidence"
        # Parent evidence is a sibling of the sandbox-writeable trial root.
        # A worker can report observations, but cannot alter the evidence that
        # controls a release decision after it exits.
        evidence = run_root / "evidence" / trial_id
        for directory in (home, repository, scratch, worker_evidence, evidence):
            directory.mkdir(parents=True, exist_ok=False)
        value = cls(
            run_root.resolve(),
            eval_id,
            trial_id,
            root.resolve(),
            home.resolve(),
            repository.resolve(),
            scratch.resolve(),
            worker_evidence.resolve(),
            evidence.resolve(),
        )
        atomic_write_json(
            evidence / "environment.json",
            {
                "schema_version": 1,
                "trial_id": trial_id,
                "eval_id": eval_id,
                "ephemeral_nous_home": "nous-home",
                "isolated_repository": "repository",
                "fixture_scratch": "scratch",
                "worker_evidence": "trials/<trial>/worker-evidence",
                "parent_evidence": f"evidence/{trial_id}",
                "network_policy": "deny",
                "credential_policy": "none",
                "execution_boundary": "codex_sandbox",
                "parent_network_namespace_digest": _network_namespace_digest(),
                "environment_digest": value.digest,
            },
        )
        atomic_write_json(
            root / "worker-environment.json",
            {
                "schema_version": 1,
                "trial_id": trial_id,
                "eval_id": eval_id,
                "parent_network_namespace_digest": _network_namespace_digest(),
            },
        )
        return value

    @classmethod
    def from_json(cls, path: Path) -> "TrialEnvironment":
        value = json.loads(path.read_text(encoding="utf-8"))
        root = path.parent.resolve()
        # worker-environment.json lives directly in a trial root; retain
        # compatibility with the old worker-evidence placement for recovery.
        if path.name != "worker-environment.json":
            root = path.parent.parent.resolve()
        run_root = root.parents[1]
        return cls(
            run_root,
            str(value["eval_id"]),
            str(value["trial_id"]),
            root,
            root / "nous-home",
            root / "repository",
            root / "scratch",
            root / "worker-evidence",
            run_root / "evidence" / str(value["trial_id"]),
        )

    @property
    def digest(self) -> str:
        return digest_json(
            {
                "trial_id": self.trial_id,
                "eval_id": self.eval_id,
                "home": "nous-home",
                "repository": "repository",
                "scratch": "scratch",
                "network_policy": "deny",
                "credential_policy": "none",
                "execution_boundary": "codex_sandbox",
            }
        )

    def cleanup_ephemeral(self) -> None:
        """Delete only paths that allocation created below this Trial root."""
        for directory in (self.home, self.repository, self.scratch, self.worker_evidence):
            if directory.parent != self.root:
                raise ValueError("trial cleanup path escaped its allocated root")
            shutil.rmtree(directory, ignore_errors=True)


class EvalSandbox:
    """Codex-enforced trial executor with empty credentials and no network."""

    def __init__(self, environment: TrialEnvironment, source_root: Path) -> None:
        self.environment = environment
        self.source_root = source_root.resolve()
        self.codex = shutil.which("codex")
        if not self.codex:
            raise UnsupportedActionError("codex CLI sandbox is unavailable for Eval isolation")

    def run_worker(self, handler: str, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
        return self._run_module("agent_nous.eval.worker", ("--environment", str(self.environment.root / "worker-environment.json"), "--handler", handler), timeout_seconds)

    def run_fixture(self, eval_id: str, timeout_seconds: int, *, phase: str = "setup") -> subprocess.CompletedProcess[str]:
        return self._run_module(
            "agent_nous.eval.fixture_worker",
            ("--environment", str(self.environment.root / "worker-environment.json"), "--eval-id", eval_id, "--phase", phase),
            timeout_seconds,
        )

    def _run_module(self, module: str, module_args: tuple[str, ...], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
        profile = Path(tempfile.mkdtemp(prefix="eval-profile-", dir=self.environment.scratch))
        empty_home = self.environment.scratch / "empty-home"
        empty_home.mkdir(exist_ok=True)
        codex_bin_dir = Path(self.codex).resolve().parent.parent
        node = shutil.which("node")
        if not node:
            raise UnsupportedActionError("Node.js runtime required by Codex sandbox is unavailable")
        node_bin_dir = Path(node).resolve().parent
        node_root = node_bin_dir.parent
        trials_root = self.environment.run_root / "trials"
        config = (
            'default_permissions = "nous-eval"\n\n'
            '[permissions]\n\n'
            '[permissions.nous-eval.filesystem]\n'
            '":minimal" = "read"\n'
            f'"{codex_bin_dir}" = "read"\n'
            f'"{node_root}" = "read"\n'
            f'"{self.source_root}" = "read"\n'
            f'"{trials_root}" = "read"\n'
            f'"{self.environment.root}" = "write"\n\n'
            '[permissions.nous-eval.network]\n'
            'enabled = false\n'
        )
        (profile / "config.toml").write_text(config, encoding="utf-8")
        environment_file = self.environment.root / "worker-environment.json"
        parent_environment_file = self.environment.evidence / "environment.json"
        evidence = json.loads(parent_environment_file.read_text(encoding="utf-8"))
        evidence["sandbox_profile_sha256"] = hashlib.sha256(config.encode("utf-8")).hexdigest()
        atomic_write_json(parent_environment_file, evidence)
        atomic_write_json(
            self.environment.evidence / "sandbox-execution.json",
            {
                "schema_version": 1,
                "backend": "codex_sandbox",
                "network_requested_disabled": True,
                "credential_environment_replaced": True,
                "sanitized_environment_keys": sorted(env_key for env_key in ("PATH", "HOME", "CODEX_HOME", "PYTHONPATH", "NOUS_HOME", "PYTHONDONTWRITEBYTECODE", "LANG")),
                "allowed_read_roots": ["runtime-source", "codex-bin", "minimal-system"],
                "allowed_write_roots": ["trial-root"],
                "sandbox_profile_sha256": evidence["sandbox_profile_sha256"],
            },
        )
        command = (
            self.codex,
            "sandbox",
            "-P",
            "nous-eval",
            "-C",
            str(self.environment.root),
            "--",
            "/usr/bin/python3",
            "-m",
            module,
            *module_args,
        )
        # Deliberately replace, rather than copy, the parent environment.  A
        # caller's proxy, token, SSH agent, NOUS_HOME, and credential-related
        # variables never cross this boundary.
        env = {
            "PATH": f"{node_bin_dir}:{codex_bin_dir}:/usr/bin:/bin",
            "HOME": str(empty_home),
            "CODEX_HOME": str(profile),
            "PYTHONPATH": str(self.source_root),
            "NOUS_HOME": str(self.environment.home),
            "PYTHONDONTWRITEBYTECODE": "1",
            "LANG": "C.UTF-8",
        }
        process = subprocess.Popen(
            command,
            cwd=self.environment.root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        process_identity = f"spawn:{uuid.uuid4().hex}"
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                stdout, stderr = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
            completed = subprocess.CompletedProcess(command, 124, stdout, stderr + "\neval worker timeout")
            completed.process_identity = process_identity  # type: ignore[attr-defined]
            return completed
        completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        completed.process_identity = process_identity  # type: ignore[attr-defined]
        return completed


def _network_namespace_digest() -> str | None:
    """Return a non-portable namespace identity only as a one-way probe.

    The identifier itself is never surfaced in a report; the worker stores a
    hash and proves it differs after the Codex/bwrap network namespace split.
    """
    try:
        return hashlib.sha256(os.readlink("/proc/self/ns/net").encode("utf-8")).hexdigest()
    except OSError:
        return None
