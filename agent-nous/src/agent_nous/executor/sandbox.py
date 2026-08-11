from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

from ..errors import UnsupportedActionError


class CodexSandbox:
    """OS-enforced filesystem/network containment using Codex permission profiles."""

    def __init__(self, evidence_root: Path) -> None:
        self.evidence_root = evidence_root.resolve()
        self.evidence_root.mkdir(parents=True, exist_ok=True)
        self.codex = shutil.which("codex")
        if not self.codex:
            raise UnsupportedActionError("codex CLI sandbox is unavailable")

    def _profile_home(
        self,
        writable_root: Path,
        read_roots: tuple[Path, ...] = (),
        write_roots: tuple[Path, ...] = (),
    ) -> Path:
        profile_home = Path(
            tempfile.mkdtemp(prefix="sandbox-profile-", dir=self.evidence_root)
        )
        # The npm launcher resolves to ``bin/codex.js`` but starts a native
        # binary from the package's sibling ``node_modules`` directory.  A
        # permission grant for only ``bin`` breaks after a Codex CLI upgrade.
        codex_bin_dir = Path(self.codex).resolve().parent.parent
        node = shutil.which("node")
        if not node:
            raise UnsupportedActionError("Node.js runtime required by Codex sandbox is unavailable")
        node_bin_dir = Path(node).resolve().parent
        node_root = node_bin_dir.parent
        read_entries = "\n".join(f'"{path.resolve()}" = "read"' for path in read_roots)
        write_entries = "\n".join(f'"{path.resolve()}" = "write"' for path in write_roots)
        content = f'''default_permissions = "nous-managed"

[permissions]

[permissions.nous-managed.filesystem]
":minimal" = "read"
"{codex_bin_dir}" = "read"
"{node_root}" = "read"
{read_entries}
"{writable_root.resolve()}" = "write"
{write_entries}
"{profile_home.resolve()}" = "write"

[permissions.nous-managed.network]
enabled = false
'''
        (profile_home / "config.toml").write_text(content, encoding="utf-8")
        return profile_home

    def run(
        self,
        argv: tuple[str, ...],
        *,
        writable_root: Path,
        read_roots: tuple[Path, ...] = (),
        input_text: str | None = None,
        timeout_seconds: float = 60,
        extra_env: dict[str, str] | None = None,
        write_roots: tuple[Path, ...] = (),
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        root = writable_root.resolve()
        working_directory = (cwd or root).resolve()
        if working_directory != root and root not in working_directory.parents:
            raise ValueError("sandbox cwd must remain below writable_root")
        if not working_directory.is_dir():
            raise ValueError("sandbox cwd must be an existing directory")
        resolved_write_roots = tuple(path.resolve() for path in write_roots)
        for write_root in resolved_write_roots:
            write_root.mkdir(parents=True, exist_ok=True)
        # A host-side read-only Codex is an explicit operator-approved escape
        # hatch for environments where the outer Codex sandbox cannot reach
        # the model endpoint.  The caller must still pass an inner
        # ``codex ... --sandbox read-only`` command.  This mode intentionally
        # does not claim the outer filesystem/network containment guarantees.
        host_read_only = os.environ.get("NOUS_HOST_READ_ONLY_CODEX") == "1"
        if host_read_only:
            # ``argv`` already contains the requested executable.  The normal
            # path prepends ``codex sandbox``; host mode deliberately bypasses
            # that outer wrapper and must not prepend a second Codex binary.
            command = tuple(argv)
            env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": os.environ.get("HOME", ""),
            }
            if os.environ.get("CODEX_HOME"):
                env["CODEX_HOME"] = os.environ["CODEX_HOME"]
        else:
            profile_home = self._profile_home(root, tuple(path.resolve() for path in read_roots), resolved_write_roots)
            command = (
                self.codex,
                "sandbox",
                "-P",
                "nous-managed",
                "-C",
                str(root),
                "--",
                *argv,
            )
            env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": os.environ.get("HOME", ""),
                "CODEX_HOME": str(profile_home),
            }
        if extra_env:
            env.update(extra_env)
        process = subprocess.Popen(
            command,
            cwd=working_directory,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(input=input_text, timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                stdout, stderr = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
            return subprocess.CompletedProcess(command, 124, stdout, stderr + "\nexecutor timeout")
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
