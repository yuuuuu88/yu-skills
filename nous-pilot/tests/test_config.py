import json
from pathlib import Path
import pytest

from nous_pilot.config import ConfigError, load_config, write_internal_engine_config, write_user_config


def test_one_user_config_has_small_defaults_and_generates_internal_runtime(tmp_path: Path) -> None:
    config_file = tmp_path / "nous-pilot.toml"
    config_file.write_text("[run]\ncycles = 4\n\n[remote]\nmode = 'allow'\n", encoding="utf-8")
    config = load_config(config_file)
    assert config.cycles == 4
    assert config.lease_seconds == 600
    assert config.timeout_seconds == 300
    internal = write_internal_engine_config(config, repository_root=tmp_path)
    payload = json.loads(internal.read_text(encoding="utf-8"))
    assert payload["engine"]["min_cycles"] == 4
    assert payload["engine"]["lease_seconds"] == 600
    assert payload["engine"]["agent_timeout_seconds"] == 300
    assert "nous_pilot.external_bridge" in payload["engine"]["agents"]["plan"]


def test_missing_config_uses_personal_defaults(tmp_path: Path) -> None:
    config = load_config(tmp_path / "missing.toml")
    assert config.mode == "personal"
    assert config.cycles == 3
    assert config.remote_mode == "ask"


def test_user_config_creator_is_non_destructive(tmp_path: Path) -> None:
    path = write_user_config(tmp_path / "nous-pilot.toml")
    assert load_config(path).cycles == 3


def test_config_rejects_ambiguous_booleans_extra_reviewers_and_secret_args(tmp_path: Path) -> None:
    path = tmp_path / "nous-pilot.toml"
    path.write_text("[research]\nweb = 'false'\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="TOML boolean"):
        load_config(path)
    path.write_text("[agents]\nreviewers = 3\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="exactly 2"):
        load_config(path)
    path.write_text('[agents]\ncommand = ["agent", "--token", "value"]\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="credentials"):
        load_config(path)
