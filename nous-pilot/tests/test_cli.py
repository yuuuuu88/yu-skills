from pathlib import Path

from nous_pilot.cli import main


def test_init_creates_one_user_config(tmp_path: Path, capsys) -> None:
    assert main(["init", "--config", str(tmp_path / "nous-pilot.toml")]) == 0
    output = capsys.readouterr().out
    assert "nous-pilot.toml" in output
    assert "cycles = 3" in (tmp_path / "nous-pilot.toml").read_text(encoding="utf-8")


def test_init_does_not_overwrite_without_force(tmp_path: Path) -> None:
    path = tmp_path / "nous-pilot.toml"
    path.write_text("custom = true\n", encoding="utf-8")
    try:
        main(["init", "--config", str(path)])
    except Exception as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("init unexpectedly overwrote an existing config")
    assert path.read_text(encoding="utf-8") == "custom = true\n"


def test_objective_helper_emits_validated_request_without_running(tmp_path: Path, capsys) -> None:
    import json
    assert main(["objective", "add a focused test", "--repository-root", str(tmp_path), "--request-id", "req-cli", "--constraint", "keep it local"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["request_id"] == "req-cli"
    assert payload["objective"] == "add a focused test"
    assert payload["constraints"] == ["keep it local"]


def test_status_show_and_resume_are_read_only_by_default(tmp_path: Path, capsys) -> None:
    import json
    shim = tmp_path / "nous-shim"
    shim.write_text(
        "#!/usr/bin/python3\n"
        "import json, sys\n"
        "payload = json.load(sys.stdin) if '--input' in sys.argv else {}\n"
        "command = 'show' if 'show' in sys.argv else ('list' if 'list' in sys.argv else '')\n"
        "result = {'state': {'revision': 7}} if command == 'show' else {'tasks': []}\n"
        "print(json.dumps({'ok': True, 'result': result, 'command': command, 'payload': payload}))\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    config = tmp_path / "nous-pilot.toml"
    config.write_text("[project]\nmode='personal'\n", encoding="utf-8")
    assert main(["status", "--config", str(config), "--nous-binary", str(shim)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["read_only"] is True
    assert status["runtime"]["result"]["tasks"] == []
    assert main(["show", "task-1", "--config", str(config), "--nous-binary", str(shim)]) == 0
    show = json.loads(capsys.readouterr().out)
    assert show["read_only"] is True
    assert show["runtime"]["result"]["state"]["revision"] == 7
    assert main(["resume", "task-1", "--config", str(config), "--nous-binary", str(shim)]) == 0
    resume = json.loads(capsys.readouterr().out)
    assert resume["read_only"] is True
    assert resume["resume"]["execution_started"] is False


def test_resume_execute_requires_an_explicit_execution_flag(tmp_path: Path, capsys) -> None:
    import json
    shim = tmp_path / "nous-shim"
    shim.write_text(
        "#!/usr/bin/python3\n"
        "import json, sys\n"
        "command = 'show' if 'show' in sys.argv else 'other'\n"
        "result = {'state': {'revision': 2}} if command == 'show' else {'resumed': True}\n"
        "print(json.dumps({'ok': True, 'result': result}))\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    config = tmp_path / "nous-pilot.toml"
    config.write_text("[project]\nmode='personal'\n", encoding="utf-8")
    engine = tmp_path / "engine.json"
    engine.write_text("{}", encoding="utf-8")
    assert main(["resume", "task-1", "--execute", "--engine-config", str(engine), "--config", str(config), "--nous-binary", str(shim)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["read_only"] is False
    assert output["execution"]["result"]["resumed"] is True


def test_cli_checks_runtime_compatibility_before_commands(monkeypatch) -> None:
    from nous_pilot import cli
    from nous_pilot.distribution import CompatibilityReport, UnsupportedRuntimeError

    report = CompatibilityReport(False, "3.10.0", "Plan 9", ("unsupported",))
    monkeypatch.setattr(cli, "require_compatibility", lambda: (_ for _ in ()).throw(UnsupportedRuntimeError(report)))
    try:
        cli.main(["objective", "noop"])
    except UnsupportedRuntimeError as exc:
        assert "Plan 9" in str(exc)
    else:
        raise AssertionError("CLI did not enforce compatibility")
