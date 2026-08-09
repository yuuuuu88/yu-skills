from __future__ import annotations

import json
import argparse
from pathlib import Path

import pytest

from agent_nous.cli import _prepare_engine_payload
from agent_nous.engine_config import discover_config_path, load_engine_config
from agent_nous.errors import ValidationError


def _write_config(path: Path, engine: dict[str, object]) -> None:
    path.write_text(json.dumps({"nous_home": "~/.local/share/nous", "engine": engine}), encoding="utf-8")


def test_engine_config_accepts_human_friendly_command_strings_and_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / ".nous" / "config.json"
    config_path.parent.mkdir()
    _write_config(
        config_path,
        {
            "agents": {
                "plan": "python3 agents/plan.py",
                "implement": ["python3", "agents/implement.py"],
                "review": {"command": "python3 agents/review.py"},
            },
            "allowed_processes": ["/usr/bin/python3 -m pytest"],
            "max_cycles": 5,
        },
    )

    config = load_engine_config(config_path)

    assert config.agent_commands["plan"] == ("python3", "agents/plan.py")
    assert config.agent_commands["review"] == ("python3", "agents/review.py")
    assert config.allowed_processes == (("/usr/bin/python3", "-m", "pytest"),)
    assert config.min_cycles == 1
    assert config.max_cycles == 5
    assert config.max_steps == 12
    assert config.subagents_required is True
    assert config.subagents_mode == "external"


def test_engine_config_accepts_minimum_nous_loop_cycles(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    _write_config(
        config_path,
        {
            "agents": {"plan": "plan", "implement": "implement", "review": "review"},
            "allowed_processes": ["true"],
            "min_cycles": 3,
            "max_cycles": 5,
        },
    )

    config = load_engine_config(config_path)

    assert config.min_cycles == 3
    assert config.payload()["min_cycles"] == 3


def test_cli_engine_config_passes_minimum_cycles_into_engine_payload(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    _write_config(
        config_path,
        {
            "agents": {"plan": "plan", "implement": "implement", "review": "review"},
            "allowed_processes": ["true"],
            "min_cycles": 3,
            "max_cycles": 3,
        },
    )

    args = argparse.Namespace(engine_config=None, config=str(config_path))
    payload = _prepare_engine_payload(args, {})

    assert payload["min_cycles"] == 3
    assert payload["max_cycles"] == 3
    assert payload["max_steps"] >= 9


def test_engine_config_accepts_bounded_cross_review(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    _write_config(
        config_path,
        {
            "agents": {"plan": "plan", "implement": "implement", "review": "review"},
            "allowed_processes": ["/usr/bin/python3 -m pytest"],
            "review": {
                "mode": "cross",
                "fallback": "single",
                "max_parallel": 2,
                "reviewers": [
                    {"id": "correctness", "perspective": "behavior", "command": "python3 reviewers/correctness.py"},
                    {"id": "security", "perspective": "security", "command": ["python3", "reviewers/security.py"]},
                ],
            },
        },
    )

    config = load_engine_config(config_path)

    assert config.review_mode == "cross"
    assert config.review_fallback == "single"
    assert config.review_max_parallel == 2
    assert dict(config.reviewer_commands)["correctness"] == ("python3", "reviewers/correctness.py")
    assert dict(config.reviewer_perspectives)["security"] == "security"
    assert config.payload()["reviewers"][0]["id"] == "correctness"


def test_engine_config_parses_explicit_subskill_roots_and_phase_capabilities(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    _write_config(
        config_path,
        {
            "agents": {"plan": "plan", "implement": "implement", "review": "review"},
            "allowed_processes": ["true"],
            "subskills": {
                "roots": ["vendor/subskills"],
                "limit": 2,
                "role": "advisor",
                "phase_capabilities": {"review": ["review.japanese-prose"]},
            },
        },
    )

    config = load_engine_config(config_path)

    assert config.subskill_roots == (str((tmp_path / "vendor/subskills").resolve()),)
    assert config.subskill_limit == 2
    assert config.subskill_role == "advisor"
    assert dict(config.subskill_phase_capabilities)["review"] == ("review.japanese-prose",)


def test_engine_config_is_discovered_from_project_root(tmp_path: Path) -> None:
    config_path = tmp_path / ".nous" / "config.json"
    config_path.parent.mkdir()
    _write_config(
        config_path,
        {
            "agents": {"plan": "plan", "implement": "implement", "review": "review"},
            "validation": {"allowed_processes": ["/usr/bin/python3 -m pytest"]},
        },
    )

    assert discover_config_path(cwd=tmp_path / "nested") == config_path


def test_engine_config_exposes_simple_external_subagent_switch(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    _write_config(
        config_path,
        {
            "agents": {"plan": "plan", "implement": "implement", "review": "review"},
            "allowed_processes": ["true"],
            "subagents": {"required": False, "mode": "external"},
        },
    )

    config = load_engine_config(config_path)

    assert config.subagents_required is False
    assert config.payload()["subagents"] == {"required": False, "mode": "external"}


@pytest.mark.parametrize(
    "engine, message",
    [
        ({"agents": {"plan": "plan", "implement": "implement"}}, "exactly plan"),
        ({"agents": {"plan": "plan", "implement": "implement", "review": "review"}, "unknown": True}, "unknown engine"),
        ({"agents": {"plan": "plan", "implement": "implement", "review": "review"}, "allowed_processes": ["true"], "subagents": {"unknown": True}}, "unknown engine.subagents"),
        ({"agents": {"plan": "plan", "implement": "implement", "review": "review"}}, "allowed_processes.*required"),
        ({"agents": {"plan": "plan", "implement": "implement", "review": "review"}, "allowed_processes": [""]}, "nonempty"),
        ({"agents": {"plan": "plan", "implement": "implement", "review": "review"}, "allowed_processes": ["true"], "lease_seconds": 30, "agent_timeout_seconds": 29}, "at least 5 seconds"),
        ({"agents": {"plan": "plan", "implement": "implement", "review": "review"}, "allowed_processes": ["true"], "min_cycles": 4, "max_cycles": 3}, "min_cycles"),
        ({"agents": {"plan": "plan", "implement": "implement", "review": "review"}, "allowed_processes": ["true"], "min_cycles": 3, "max_steps": 8}, "max_steps"),
    ],
)
def test_engine_config_rejects_ambiguous_or_unsafe_settings(tmp_path: Path, engine: dict[str, object], message: str) -> None:
    config_path = tmp_path / "config.json"
    _write_config(config_path, engine)

    with pytest.raises(ValidationError, match=message):
        load_engine_config(config_path)


def test_engine_config_rejects_unknown_top_level_key(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "nous_home": "~/.local/share/nous",
                "engine": {
                    "agents": {"plan": "plan", "implement": "implement", "review": "review"},
                    "allowed_processes": ["true"],
                },
                "typo": True,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="unknown nous config"):
        load_engine_config(config_path)
