from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agent_nous.errors import ValidationError
from agent_nous.eval.capability import capability_observation_from_eval_runner_capture, validate_capability_observation_bundle
from agent_nous.eval.capability_ops import aggregate_operational_metrics
from agent_nous.eval.capability_runner import CapabilityEvalRunner
from agent_nous.io import digest_json

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from nous_capability_gate import evaluate_gate


def _capture(root: Path) -> Path:
    root.mkdir()
    canonical={"schema_version":1,"task_ids":[],"state":{"tasks":[]},"events":{"tasks":[]},"policy":{"actions":[],"tasks":[],"git_fixture":{}},"diff":{"changed_paths":[],"run_scope_changed_paths":[],"out_of_trial_paths":[]},"process":{"returncode":0,"stdout_secret_like":False,"stderr_secret_like":False},"validation_failures":[],"trace":{"sandbox_enforced":True}}
    canonical_path=root/'canonical.json'; canonical_path.write_text(json.dumps(canonical,sort_keys=True),encoding='utf-8')
    digest=digest_json(canonical)
    (root/'parent-attestation.json').write_text(json.dumps({"schema_version":1,"trial_id":"trial-01","canonical_digest":digest,"evidence_digests":{"canonical.json":hashlib.sha256(canonical_path.read_bytes()).hexdigest()}},sort_keys=True),encoding='utf-8')
    (root/'outcome.json').write_text(json.dumps({"schema_version":2,"trial_id":"trial-01","eval_id":"fixture","outcome":"passed"}),encoding='utf-8')
    (root/'environment.json').write_text(json.dumps({"schema_version":1,"trial_id":"trial-01","eval_id":"fixture"}),encoding='utf-8')
    return root


def test_real_eval_capture_adapter_is_attested_and_conservative(tmp_path: Path) -> None:
    observation=capability_observation_from_eval_runner_capture(_capture(tmp_path/'evidence'),task_id='T01-basic-implementation',eval_id='fixture')
    assert observation['provenance']['source']=='eval_runner_parent_capture'
    assert observation['hidden_tests']['passed'] is True
    assert observation['safety']['passed'] is True
    assert observation['trace']['phase_sequence']==[]


def test_capture_digest_tamper_fails_closed(tmp_path: Path) -> None:
    root=_capture(tmp_path/'evidence')
    path=root/'canonical.json'; value=json.loads(path.read_text()); value['validation_failures']=['forged']; path.write_text(json.dumps(value),encoding='utf-8')
    with pytest.raises(ValidationError,match='digest mismatch'):
        capability_observation_from_eval_runner_capture(root,task_id='T01-basic-implementation',eval_id='fixture')


def test_public_bundle_unknown_field_and_holdout_boundary_fail_closed(tmp_path: Path) -> None:
    bundle=json.loads(Path('src/agent_nous/eval/capability_oracles.json').read_text())
    bundle['observations']['T01-basic-implementation'][0]['capability_observation']['forged_score']=100
    with pytest.raises(ValidationError,match='unknown fields'):
        validate_capability_observation_bundle(bundle)
    with pytest.raises(ValidationError,match='external'):
        CapabilityEvalRunner(root=tmp_path).run(profile='holdout')


def test_operations_are_separate_from_ncs_and_gate_is_thresholded() -> None:
    metrics=aggregate_operational_metrics([{'elapsed_ms':10,'cycles':2},{'elapsed_ms':20,'cycles':4}])
    assert metrics['mean']['elapsed_ms']==15
    report={'outcome':'passed','ncs':82,'task_reliability':1.0,'hard_gate_failures':[]}
    assert evaluate_gate(report,min_ncs=80,min_reliability=1.0)['passed'] is True
    assert evaluate_gate(report,min_ncs=90,min_reliability=1.0)['passed'] is False
