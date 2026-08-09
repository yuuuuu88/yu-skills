import sys
import json
import os
import subprocess
from pathlib import Path

import pytest

from nous_pilot.models import ContractError
from nous_pilot.provider import SubprocessAgentProvider, result_from_mapping


def test_subprocess_provider_uses_structured_json_and_minimal_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agent = tmp_path / "agent.py"
    agent.write_text(
        "import json, os, sys\n"
        "payload = json.load(sys.stdin)\n"
        "print(json.dumps({'status': 'success', 'summary': os.environ['NOUS_PILOT_ROLE'], 'outputs': {'echo': payload['request']['objective'], 'host_present': 'NOUS_TEST_HOST_SECRET' in os.environ, 'python_path_present': 'PYTHONPATH' in os.environ}}))\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NOUS_TEST_HOST_SECRET", "must-not-be-inherited")
    provider = SubprocessAgentProvider((sys.executable, str(agent)), cwd=tmp_path)
    result = provider.invoke(
        "plan",
        {"request": {"repository_root": str(tmp_path), "objective": "make a safe change"}},
    )
    assert result.status == "success"
    assert result.summary == "plan"
    assert result.outputs["echo"] == "make a safe change"
    assert result.outputs["host_present"] is False
    assert result.outputs["python_path_present"] is False


def test_subprocess_provider_rejects_malformed_output_without_leaking_stderr(tmp_path: Path) -> None:
    agent = tmp_path / "agent.py"
    agent.write_text("import sys\nsys.stderr.write('secret-looking detail')\nprint('not json')\n", encoding="utf-8")
    with pytest.raises(ContractError, match="malformed JSON") as error:
        SubprocessAgentProvider((sys.executable, str(agent)), cwd=tmp_path).invoke(
            "review", {"request": {"repository_root": str(tmp_path)}}
        )
    assert "secret" not in str(error.value)


def test_subprocess_provider_rejects_secret_like_result_fields(tmp_path: Path) -> None:
    agent = tmp_path / "agent.py"
    agent.write_text(
        "import json\n"
        "print(json.dumps({'status': 'success', 'summary': 'bad', 'outputs': {'api_key': 'value'}}))\n",
        encoding="utf-8",
    )
    with pytest.raises(ContractError, match="secret-like"):
        SubprocessAgentProvider((sys.executable, str(agent)), cwd=tmp_path).invoke(
            "plan", {"request": {"repository_root": str(tmp_path)}}
        )


def test_subprocess_provider_rejects_secret_environment_keys(tmp_path: Path) -> None:
    with pytest.raises(ContractError, match="secret-like"):
        SubprocessAgentProvider(
            (sys.executable, str(tmp_path / "agent.py")),
            cwd=tmp_path,
            extra_environment={"NOUS_PILOT_SECRET_KEY": "do-not-accept"},
        )


def test_subprocess_provider_enforces_max_output(tmp_path: Path) -> None:
    agent = tmp_path / "agent.py"
    agent.write_text("print('x' * 200)\n", encoding="utf-8")
    result = SubprocessAgentProvider((sys.executable, str(agent)), cwd=tmp_path, max_output_bytes=32).invoke(
        "test", {"request": {"repository_root": str(tmp_path)}}
    )
    assert result.status == "failed"
    assert result.summary == "external Agent output exceeded the configured limit"


def test_subprocess_provider_timeout_is_a_failed_result(tmp_path: Path) -> None:
    agent = tmp_path / "agent.py"
    agent.write_text("import time\ntime.sleep(1)\n", encoding="utf-8")
    result = SubprocessAgentProvider((sys.executable, str(agent)), cwd=tmp_path, timeout_seconds=0.01).invoke(
        "review", {"request": {"repository_root": str(tmp_path)}}
    )
    assert result.status == "failed"
    assert result.summary == "external Agent timed out"


def test_result_from_mapping_returns_detached_safe_outputs() -> None:
    source = {"status": "success", "summary": "ok", "outputs": {"items": ["one"]}}
    result = result_from_mapping("plan", source)
    source["outputs"]["items"].append("mutated")
    assert result.outputs == {"items": ["one"]}


def test_result_from_mapping_rejects_non_object_and_secret_keys() -> None:
    with pytest.raises(ContractError):
        result_from_mapping("plan", [])  # type: ignore[arg-type]
    with pytest.raises(ContractError, match="secret-like"):
        result_from_mapping("plan", {"summary": "unsafe", "outputs": {"privateKey": "no"}})
    with pytest.raises(ContractError, match="secret-like"):
        result_from_mapping("plan", {"summary": "sk-live-looking-value", "outputs": {}})
    with pytest.raises(ContractError, match="unknown"):
        result_from_mapping("plan", {"summary": "ok", "unexpected": True})


def test_subprocess_provider_uses_an_isolated_copy_by_default(tmp_path: Path) -> None:
    agent = tmp_path / "writer.py"
    agent.write_text(
        "from pathlib import Path\nimport json\nPath('side-effect.txt').write_text('changed')\nprint(json.dumps({'status':'success','summary':'ok','outputs':{}}))\n",
        encoding="utf-8",
    )
    provider = SubprocessAgentProvider((sys.executable, str(agent)), cwd=tmp_path)
    result = provider.invoke("implement", {"request": {"repository_root": str(tmp_path)}})
    assert result.status == "success"
    assert not (tmp_path / "side-effect.txt").exists()


def test_subprocess_provider_rejects_shell_commands() -> None:
    with pytest.raises(ContractError, match="shell"):
        SubprocessAgentProvider(("/bin/sh", "-c", "echo unsafe"))


def test_packaged_external_bridge_returns_a_blocked_structured_result_without_command(tmp_path: Path) -> None:
    context = tmp_path / "context.json"
    context.write_text(json.dumps({"repository_root": str(tmp_path)}), encoding="utf-8")
    environment = {"PATH": os.defpath, "PYTHONPATH": "src", "NOUS_AGENT_CONTEXT_FILE": str(context)}
    completed = subprocess.run(
        [sys.executable, "-m", "nous_pilot.external_bridge", "plan"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["status"] == "blocked"
    assert payload["outputs"] == {}


def test_result_from_mapping_preserves_review_decision() -> None:
    result = result_from_mapping(
        "review",
        {"status": "success", "summary": "needs another plan", "decision": "needs_plan", "outputs": {"findings": []}},
    )
    assert result.decision == "needs_plan"
