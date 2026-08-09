from pathlib import Path

import pytest

from nous_pilot.models import ContractError
from nous_pilot.verification import VerificationPolicy, VerificationRunner
from nous_pilot.workspace import DraftChange, WorkspaceManager


def test_workspace_rejects_path_escape(tmp_path: Path) -> None:
    with pytest.raises(ContractError):
        DraftChange("../escape.txt", "bad")
    manager = WorkspaceManager(tmp_path / "runtime")
    staged = manager.stage("w1", [DraftChange("src/result.txt", "ok")])
    assert staged.paths == ("src/result.txt",)
    assert (staged.root / "src/result.txt").read_text(encoding="utf-8") == "ok"
    with pytest.raises(ContractError):
        DraftChange("nested/.git/config", "bad")
    with pytest.raises(ContractError):
        DraftChange(r"..\escape.txt", "bad")


def test_verification_requires_allowlisted_argv(tmp_path: Path) -> None:
    runner = VerificationRunner(VerificationPolicy((("/usr/bin/python3", "-c"),)))
    blocked = runner.run("blocked", ("/bin/sh", "-c", "echo bad"), tmp_path)
    assert blocked.status == "blocked"
    passed = runner.run("passed", ("/usr/bin/python3", "-c", "print('ok')"), tmp_path)
    assert passed.status == "passed"
