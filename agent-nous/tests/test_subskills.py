from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from agent_nous.errors import RecoveryRequiredError, ValidationError
from agent_nous.subskills import SubskillRegistry, SubskillRuntime, SubskillSelector
from agent_nous.subskills.registry import manifest_digest


def test_builtin_registry_has_five_per_phase() -> None:
    providers = SubskillRegistry().discover()
    assert SubskillRegistry().validate() == []
    for phase in ("knowledge", "plan", "implement", "review"):
        assert len([item for item in providers if item.phase == phase]) >= 5
    assert all(item.manifest.get("guidance_hash") for item in providers)
    assert all(item.manifest.get("provenance", {}).get("transformation") for item in providers)


def test_selector_is_deterministic_and_fail_closed() -> None:
    providers = SubskillRegistry().discover()
    result = SubskillSelector().select(
        providers,
        phase="implement",
        required_capabilities=("architecture.boundaries",),
    )
    assert result[0].provider.id == "implement.architecture.clean-architecture"
    with pytest.raises(ValidationError):
        SubskillSelector().select(
            providers,
            phase="implement",
            required_safety_capabilities=("safety.nonexistent",),
        )


def test_external_provider_hash_is_verified(tmp_path: Path) -> None:
    folder = tmp_path / "sample"
    folder.mkdir()
    (folder / "guidance.md").write_text("# Sample\n")
    value = {
        "schema_version": 1,
        "id": "plan.sample",
        "version": "1.0.0",
        "phase": "plan",
        "core": {
            "name": "Sample",
            "originator": "Example",
            "source_url": "https://example.com",
            "operational_rule": "Create a verifiable plan.",
        },
        "capabilities": ["plan.sample"],
        "roles": ["primary"],
        "priority": 1,
        "enabled": True,
        "entrypoint": "guidance.md",
        "content_hash": "",
    }
    value["content_hash"] = manifest_digest(value)
    (folder / "manifest.json").write_text(json.dumps(value))
    assert any(item.id == "plan.sample" for item in SubskillRegistry((tmp_path,)).discover())


def test_guidance_hash_is_verified_before_selection(tmp_path: Path) -> None:
    folder = tmp_path / "tamper"
    folder.mkdir()
    guidance = "# pinned guidance\n"
    (folder / "guidance.md").write_text(guidance, encoding="utf-8")
    value = {
        "schema_version": 1,
        "id": "plan.tamper",
        "version": "1.0.0",
        "phase": "plan",
        "core": {
            "name": "Tamper",
            "originator": "Example",
            "source_url": "https://example.com",
            "operational_rule": "Keep guidance pinned.",
        },
        "capabilities": ["plan.sample"],
        "roles": ["primary"],
        "priority": 1,
        "enabled": True,
        "entrypoint": "guidance.md",
        "guidance_hash": hashlib.sha256(guidance.encode()).hexdigest(),
        "content_hash": "",
    }
    value["content_hash"] = manifest_digest(value)
    (folder / "manifest.json").write_text(json.dumps(value), encoding="utf-8")
    assert any(item.id == "plan.tamper" for item in SubskillRegistry((tmp_path,)).discover())
    (folder / "guidance.md").write_text("# replaced\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="guidance hash mismatch"):
        SubskillRegistry((tmp_path,)).discover()


def test_runtime_materializes_exact_pins_and_rejects_changed_guidance(tmp_path: Path) -> None:
    runtime = SubskillRuntime()
    snapshot = {"objective": "test pinning", "phase": "review", "cycle": 1}
    record = runtime.select(task_id="task-pin", phase="review", cycle=1, loop_id="root", input_snapshot=snapshot)
    materialized = runtime.validate_persisted(record, input_snapshot=snapshot)
    assert materialized["guidance"]
    assert materialized["selected"][0]["guidance_hash"]

    tampered = dict(record)
    tampered["selected"] = [dict(item) for item in record["selected"]]
    tampered["selected"][0]["content_hash"] = "0" * 64
    with pytest.raises(ValidationError, match="content hash"):
        runtime.validate_persisted(tampered, input_snapshot=snapshot)


def test_runtime_rejects_tampered_selection_metadata(tmp_path: Path) -> None:
    runtime = SubskillRuntime()
    snapshot = {"objective": "metadata pin", "phase": "review", "cycle": 1}
    record = runtime.select(task_id="task-pin", phase="review", cycle=1, loop_id="root", input_snapshot=snapshot)

    for field, value in (("schema_version", 1), ("phase", "plan"), ("guidance_hash", "0" * 64)):
        tampered = json.loads(json.dumps(record))
        if field == "guidance_hash":
            tampered["selected"][0][field] = value
        else:
            tampered[field] = value
        with pytest.raises((ValidationError, RecoveryRequiredError)):
            runtime.validate_persisted(tampered, input_snapshot=snapshot)


def test_registry_uses_semver_and_rejects_missing_entrypoint(tmp_path: Path) -> None:
    def manifest(folder: Path, version: str, *, entrypoint: str = "guidance.md") -> None:
        folder.mkdir()
        value = {
            "schema_version": 1,
            "id": "plan.versioned",
            "version": version,
            "phase": "plan",
            "core": {
                "name": "Versioned",
                "originator": "Example",
                "source_url": "https://example.com",
                "operational_rule": "Prefer the highest compatible version.",
            },
            "capabilities": ["plan.sample"],
            "roles": ["primary"],
            "priority": 1,
            "enabled": True,
            "entrypoint": entrypoint,
            "content_hash": "",
        }
        value["content_hash"] = manifest_digest(value)
        (folder / "manifest.json").write_text(json.dumps(value), encoding="utf-8")
        if entrypoint == "guidance.md":
            (folder / entrypoint).write_text("# guidance\n", encoding="utf-8")

    manifest(tmp_path / "v19", "1.9.0")
    manifest(tmp_path / "v110", "1.10.0")
    assert SubskillRegistry((tmp_path,)).provider("plan.versioned", "1.10.0", json.loads((tmp_path / "v110" / "manifest.json").read_text())["content_hash"]).version == "1.10.0"

    missing = tmp_path / "missing"
    manifest(missing, "2.0.0", entrypoint="missing.md")
    with pytest.raises(ValidationError, match="entrypoint"):
        SubskillRegistry((tmp_path,)).discover()
