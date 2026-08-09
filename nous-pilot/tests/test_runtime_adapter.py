import json
import os
from pathlib import Path

from nous_pilot.control_plane import PilotControlPlane
from nous_pilot.models import DevelopmentRequest
from nous_pilot.runtime import NousRuntimeClient, RuntimeConfig


def test_runtime_adapter_uses_explicit_home_and_extracts_task_id(tmp_path: Path) -> None:
    shim = tmp_path / "nous-shim"
    shim.write_text(
        "#!/usr/bin/python3\n"
        "import json, sys\n"
        "payload = json.load(sys.stdin) if '--input' in sys.argv else {}\n"
        "print(json.dumps({'ok': True, 'result': {'task_id': 'task-local-1'}, 'echo': {'argv': sys.argv, 'payload': payload}}))\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    client = NousRuntimeClient(RuntimeConfig(shim, tmp_path / "runtime"))
    request = {"request_id": "req-runtime", "objective": "start", "repository_root": str(tmp_path)}
    run = PilotControlPlane(client).create(request, run_id="run-1")
    assert run.task_id == "task-local-1"


def test_runtime_config_rejects_missing_binary(tmp_path: Path) -> None:
    from nous_pilot.models import ContractError
    import pytest

    with pytest.raises(ContractError):
        RuntimeConfig(tmp_path / "missing", tmp_path / "runtime")
