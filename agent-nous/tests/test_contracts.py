from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from agent_nous.contracts import SchemaCatalog
from agent_nous.config import resolve_nous_home
from agent_nous.errors import ValidationError
from agent_nous.ids import make_task_id, validate_task_id


def test_all_schemas_are_well_formed_and_refs_resolve() -> None:
    assert SchemaCatalog().check_all() == []


def test_task_definition_rejects_unknown_fields() -> None:
    value = {
        "schema_version": 1,
        "task_id": "20260719-1200-example",
        "title": "Example",
        "objective": "Do the work",
        "acceptance_criteria": [{"criterion_id": "AC-001", "statement": "Do the work", "validation_method": "review"}],
        "created_at": "2026-07-19T12:00:00+00:00",
        "created_by": "human",
        "target_repository": None,
        "derived_from_task_id": None,
        "unexpected": True,
    }
    with pytest.raises(ValidationError):
        SchemaCatalog().validate("task-definition", value)


def test_task_id_matches_contract(tmp_path) -> None:
    task_id = make_task_id(
        "Example Task", tmp_path, datetime(2026, 7, 19, 12, 34, tzinfo=timezone.utc)
    )
    assert task_id == "20260719-1234-example-task"
    assert validate_task_id(task_id) == task_id


def test_nous_home_resolution_fails_closed_and_accepts_project_config(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("NOUS_HOME", raising=False)
    monkeypatch.setattr("agent_nous.config.Path.home", lambda: tmp_path / "empty-home")
    with pytest.raises(ValidationError, match="NOUS_HOME is not configured"):
        resolve_nous_home(cwd=tmp_path)

    project = tmp_path / "project"
    (project / ".nous").mkdir(parents=True)
    configured = tmp_path / "runtime"
    (project / ".nous" / "config.json").write_text(f'{{"schema_version": 1, "nous_home": "{configured}"}}')
    assert resolve_nous_home(cwd=project) == configured.resolve()


def test_project_config_precedes_user_config(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("NOUS_HOME", raising=False)
    user_home = tmp_path / "user-home"
    monkeypatch.setattr("agent_nous.config.Path.home", lambda: user_home)
    (user_home / ".config" / "nous").mkdir(parents=True)
    (user_home / ".config" / "nous" / "config.json").write_text(
        json.dumps({"nous_home": str(tmp_path / "user-runtime")}), encoding="utf-8"
    )
    project = tmp_path / "project"
    (project / ".nous").mkdir(parents=True)
    project_runtime = tmp_path / "project-runtime"
    (project / ".nous" / "config.json").write_text(
        json.dumps({"nous_home": str(project_runtime)}), encoding="utf-8"
    )

    assert resolve_nous_home(cwd=project) == project_runtime.resolve()
