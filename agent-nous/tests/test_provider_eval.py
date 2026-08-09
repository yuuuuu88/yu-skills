from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agent_nous.errors import ValidationError
from agent_nous.eval.provider import ProviderEvalConfig, ProviderEvalRunner


def _write_fake_provider(tmp_path: Path) -> Path:
    oracle = Path("src/agent_nous/eval/capability_oracles.json").resolve()
    script = tmp_path / "fake_provider.py"
    provider_source = """import hashlib, json, os
from pathlib import Path

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

request = json.loads(Path(os.environ['NOUS_PROVIDER_REQUEST_FILE']).read_text())
oracles = json.loads(Path(__ORACLE_PATH__).read_text())['observations']
workspace = Path(os.environ['NOUS_PROVIDER_OUTPUT_FILE']).parent
captures = []
for task_id in request['task_ids']:
    for trial in range(1, 4):
        raw = dict(oracles[task_id][(trial - 1) % len(oracles[task_id])]['capability_observation'])
        signals = {
            'phase_sequence': raw['trace']['phase_sequence'],
            'external_subagent_phases': raw['trace']['external_subagent_phases'],
            'selected_capabilities': raw['selected_capabilities'],
            'research': raw['research'],
            'recovery': raw['recovery'],
            'parallel': raw['parallel'],
            'review': raw['review'],
            'scores': raw['scores'],
        }
        if 'replans' in raw:
            signals['replans'] = raw['replans']
        canonical = {
            'trace': {'sandbox_enforced': True},
            'process': {'stdout_secret_like': False, 'stderr_secret_like': False},
            'diff': {'out_of_trial_paths': []},
            'validation_failures': [],
            'capability_signals': signals,
        }
        trial_id = f'{task_id}-{trial:02d}'
        relative = Path('captures') / task_id / f'{trial:02d}'
        evidence = workspace / relative
        evidence.mkdir(parents=True)
        (evidence / 'canonical.json').write_text(json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
        (evidence / 'environment.json').write_text(json.dumps({'schema_version': 1, 'trial_id': trial_id, 'eval_id': task_id}))
        (evidence / 'outcome.json').write_text(json.dumps({'schema_version': 2, 'trial_id': trial_id, 'eval_id': task_id, 'outcome': 'passed'}))
        canonical_bytes = (evidence / 'canonical.json').read_bytes()
        (evidence / 'parent-attestation.json').write_text(json.dumps({'schema_version': 1, 'trial_id': trial_id, 'canonical_digest': digest(canonical), 'evidence_digests': {'canonical.json': hashlib.sha256(canonical_bytes).hexdigest()}}))
        captures.append({'task_id': task_id, 'trial': trial, 'evidence_dir': str(relative), 'outcome': 'passed'})
Path(os.environ['NOUS_PROVIDER_OUTPUT_FILE']).write_text(json.dumps({'schema_version': 1, 'run_id': request['run_id'], 'profile': request['profile'], 'manifest_digest': request['manifest_digest'], 'provider': {'provider_id': 'fake-provider', 'provider_version': '1.0.0'}, 'captures': captures}))
"""
    script.write_text(provider_source.replace("__ORACLE_PATH__", json.dumps(str(oracle))), encoding="utf-8")
    return script


def test_provider_config_is_explicit_and_secret_free() -> None:
    parsed = ProviderEvalConfig.from_mapping(
        {
            "schema_version": 1,
            "provider_id": "codex-host",
            "provider_version": "2026.08",
            "command": ["/usr/bin/python3", "adapter.py"],
            "environment_keys": ["PROVIDER_TOKEN"],
            "network_policy": "host_managed",
        }
    )
    public = parsed.public()
    assert public["provider_id"] == "codex-host"
    assert "PROVIDER_TOKEN" in public["environment_key_names"]
    assert "PROVIDER_TOKEN=" not in json.dumps(public)
    with pytest.raises(ValidationError):
        ProviderEvalConfig.from_mapping({"provider_id": "x", "provider_version": "1", "command": "python adapter.py"})
    with pytest.raises(ValidationError):
        ProviderEvalConfig.from_mapping({"provider_id": "x", "provider_version": "1", "command": ["/bin/sh", "-c", "echo ok"]})
    with pytest.raises(ValidationError):
        ProviderEvalConfig.from_mapping({"provider_id": "x", "provider_version": "1", "command": ["/usr/bin/python3", "sk-" + "live-token"]})


def test_provider_runner_requires_human_host_opt_in(tmp_path: Path) -> None:
    runner = ProviderEvalRunner(root=tmp_path)
    config = {"provider_id": "fake-provider", "provider_version": "1.0.0", "command": ["/bin/false"]}
    with pytest.raises(ValidationError, match="explicit"):
        runner.run(config=config, profile="smoke")


def test_provider_runner_ingests_three_parent_captures_and_grades(tmp_path: Path) -> None:
    script = _write_fake_provider(tmp_path)
    runner = ProviderEvalRunner(root=tmp_path / "runs")
    config = {
        "provider_id": "fake-provider",
        "provider_version": "1.0.0",
        "command": ["/usr/bin/python3", str(script)],
        "network_policy": "deny",
    }
    report = runner.run(config=config, profile="smoke", allow_host_provider=True)
    assert report["outcome"] == "passed"
    assert report["ncs"] > 0
    assert report["provider"]["provider_id"] == "fake-provider"
    assert report["provider_execution"]["raw_output_retained"] is False
    assert report["provenance"]["observation_source"] == "external_observation_bundle"
