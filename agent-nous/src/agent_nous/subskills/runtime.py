from __future__ import annotations

"""Selection and pinning boundary for phase subskills.

Subskills are guidance, not an authority that can mutate a task.  The runtime
selects one bounded composition for a phase, records the exact manifest pin,
and exposes only the selected guidance to the phase agent.  A persisted pin is
reused within a cycle; the next cycle gets a fresh selection.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..errors import RecoveryRequiredError, ValidationError
from ..io import digest_json
from .registry import SubskillProvider, SubskillRegistry, _read_utf8_nofollow
from .selector import Selection, SubskillSelector


@dataclass(frozen=True)
class SubskillRuntimeConfig:
    roots: tuple[Path, ...] = ()
    limit: int = 1
    role: str = "primary"
    phase_capabilities: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("plan", ("plan.acceptance",)),
        ("implement", ("implement.incremental",)),
        ("review", ("review.independence",)),
    )

    def capabilities_for(self, phase: str) -> tuple[str, ...]:
        return dict(self.phase_capabilities).get(phase, ())


class SubskillRuntime:
    """Discover, select, pin, and materialize phase guidance."""

    MAX_GUIDANCE_BYTES = 128 * 1024
    MAX_TOTAL_GUIDANCE_BYTES = 256 * 1024

    def __init__(
        self,
        config: SubskillRuntimeConfig | None = None,
        *,
        registry: SubskillRegistry | None = None,
        selector: SubskillSelector | None = None,
    ) -> None:
        self.config = config or SubskillRuntimeConfig()
        if not 1 <= self.config.limit <= 3:
            raise ValidationError("subskill limit must be between 1 and 3")
        if self.config.role not in {"primary", "advisor", "validator"}:
            raise ValidationError("subskill role is invalid")
        self.registry = registry or SubskillRegistry(self.config.roots)
        self.selector = selector or SubskillSelector()

    def registry_revision(self) -> str:
        providers = self.registry.discover()
        return digest_json([
            {
                "id": provider.id,
                "version": provider.version,
                "content_hash": provider.content_hash,
                "phase": provider.phase,
            }
            for provider in providers
        ])

    def select(
        self,
        *,
        task_id: str,
        phase: str,
        cycle: int,
        loop_id: str,
        input_snapshot: Mapping[str, Any],
    ) -> dict[str, Any]:
        if phase not in {"plan", "implement", "review"}:
            raise ValidationError(f"subskill selection does not support phase={phase}")
        providers = self.registry.discover()
        required = self.config.capabilities_for(phase)
        selected = self.selector.select(
            providers,
            phase=phase,
            required_capabilities=required,
            role=self.config.role,
            limit=self.config.limit,
        )
        return self._record(
            task_id=task_id,
            phase=phase,
            cycle=cycle,
            loop_id=loop_id,
            requested_capabilities=required,
            selected=selected,
            registry_revision=self._registry_revision_for(providers),
            input_snapshot=input_snapshot,
        )

    @staticmethod
    def _registry_revision_for(providers: Iterable[SubskillProvider]) -> str:
        return digest_json([
            {
                "id": provider.id,
                "version": provider.version,
                "content_hash": provider.content_hash,
                "phase": provider.phase,
            }
            for provider in providers
        ])

    def materialize(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and attach guidance for exact persisted provider pins."""
        selected_payload = record.get("selected")
        if not isinstance(selected_payload, list):
            raise RecoveryRequiredError("subskill selection selected list is missing")
        guidance: list[dict[str, Any]] = []
        for item in selected_payload:
            if not isinstance(item, Mapping):
                raise RecoveryRequiredError("subskill selection contains an invalid provider")
            provider = self.registry.provider(
                str(item.get("id")), str(item.get("version")), str(item.get("content_hash"))
            )
            content = self._read_guidance(provider)
            expected_guidance_hash = item.get("guidance_hash")
            if not isinstance(expected_guidance_hash, str):
                raise RecoveryRequiredError(f"subskill guidance hash is missing: {provider.id}@{provider.version}")
            actual_guidance_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if expected_guidance_hash != actual_guidance_hash:
                raise RecoveryRequiredError(f"subskill guidance changed: {provider.id}@{provider.version}")
            guidance.append({
                "id": provider.id,
                "version": provider.version,
                "content_hash": provider.content_hash,
                "role": item.get("role"),
                "score": item.get("score"),
                "guidance_hash": actual_guidance_hash,
                "guidance": content,
            })
        if sum(len(item["guidance"].encode("utf-8")) for item in guidance) > self.MAX_TOTAL_GUIDANCE_BYTES:
            raise ValidationError("selected subskill guidance exceeds the context budget")
        return {
            **dict(record),
            "guidance": guidance,
            "trust_boundary": "untrusted_advisory_guidance",
            "instructions_are_data": True,
            "policy_authority": "nous_engine_only",
        }

    def validate_persisted(
        self,
        record: Mapping[str, Any],
        *,
        input_snapshot: Mapping[str, Any],
        task_id: str | None = None,
        phase: str | None = None,
        cycle: int | None = None,
        loop_id: str | None = None,
    ) -> dict[str, Any]:
        if record.get("schema_version") != 2:
            raise RecoveryRequiredError("subskill selection schema version is unsupported")
        if task_id is not None and record.get("task_id") != task_id:
            raise RecoveryRequiredError("subskill selection task identity changed")
        if phase is not None and record.get("phase") != phase:
            raise RecoveryRequiredError("subskill selection phase identity changed")
        if cycle is not None and record.get("cycle") != cycle:
            raise RecoveryRequiredError("subskill selection cycle identity changed")
        if loop_id is not None and record.get("loop_id") != loop_id:
            raise RecoveryRequiredError("subskill selection loop identity changed")
        if record.get("input_snapshot_digest") != digest_json(dict(input_snapshot)):
            raise RecoveryRequiredError("subskill selection input changed within a pinned phase")
        if not record.get("pinned"):
            raise RecoveryRequiredError("subskill selection is not pinned")
        selected = record.get("selected")
        record_phase = record.get("phase")
        if not isinstance(selected, list) or record_phase not in {"plan", "implement", "review"}:
            raise RecoveryRequiredError("subskill selection shape is invalid")
        if len(selected) > self.config.limit:
            raise RecoveryRequiredError("persisted subskill selection exceeds configured limit")
        requested = record.get("requested_capabilities")
        if not isinstance(requested, list) or any(not isinstance(value, str) for value in requested) or len(requested) != len(set(requested)):
            raise RecoveryRequiredError("persisted subskill capability request is invalid")
        required = set(self.config.capabilities_for(str(record_phase)))
        if set(requested) != required:
            raise RecoveryRequiredError("persisted subskill capability request differs from config")
        ids: set[str] = set()
        capabilities: set[str] = set()
        for item in selected:
            if not isinstance(item, Mapping):
                raise RecoveryRequiredError("persisted subskill selection contains an invalid item")
            subskill_id = str(item.get("id"))
            if subskill_id in ids:
                raise RecoveryRequiredError("persisted subskill selection contains a duplicate provider")
            ids.add(subskill_id)
            if item.get("role") != self.config.role:
                raise RecoveryRequiredError("persisted subskill role differs from config")
            provider = self.registry.provider(subskill_id, str(item.get("version")), str(item.get("content_hash")))
            manifest = provider.manifest
            if provider.phase != record_phase or not manifest.get("enabled") or self.config.role not in manifest.get("roles", []):
                raise RecoveryRequiredError(f"persisted subskill provider is incompatible: {subskill_id}")
            capabilities.update(str(value) for value in manifest.get("capabilities", []))
        if not required.issubset(capabilities):
            raise RecoveryRequiredError("persisted subskill selection no longer covers requested capabilities")
        expected_selection_id = self._selection_id(record)
        if record.get("selection_id") != expected_selection_id:
            raise RecoveryRequiredError("persisted subskill selection id does not match its pin")
        return self.materialize(record)

    @staticmethod
    def _record(
        *,
        task_id: str,
        phase: str,
        cycle: int,
        loop_id: str,
        requested_capabilities: Iterable[str],
        selected: Iterable[Selection],
        registry_revision: str,
        input_snapshot: Mapping[str, Any],
    ) -> dict[str, Any]:
        selected_payload = []
        for item in selected:
            guidance = SubskillRuntime._read_guidance(item.provider)
            selected_payload.append({
                "id": item.provider.id,
                "version": item.provider.version,
                "content_hash": item.provider.content_hash,
                "guidance_hash": hashlib.sha256(guidance.encode("utf-8")).hexdigest(),
                "role": item.role,
                "score": item.score,
            })
        record = {
            "schema_version": 2,
            "task_id": task_id,
            "loop_id": loop_id,
            "cycle": cycle,
            "phase": phase,
            "requested_capabilities": list(requested_capabilities),
            "selected": selected_payload,
            "fallback_used": False,
            "registry_revision": registry_revision,
            "input_snapshot_digest": digest_json(dict(input_snapshot)),
            "selection_rationale": "deterministic highest-scoring compatible provider(s), pinned for this phase/cycle",
            "pinned": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        record["selection_id"] = SubskillRuntime._selection_id(record)
        return record

    @classmethod
    def _read_guidance(cls, provider: SubskillProvider) -> str:
        path = provider.entrypoint
        if not path.is_file():
            raise ValidationError(f"subskill guidance entrypoint is missing: {provider.id}")
        content = _read_utf8_nofollow(path, max_bytes=cls.MAX_GUIDANCE_BYTES, label=f"subskill guidance for {provider.id}")
        if "\x00" in content:
            raise ValidationError(f"subskill guidance contains a NUL byte: {provider.id}")
        return content

    @staticmethod
    def _selection_id(record: Mapping[str, Any]) -> str:
        canonical = {key: value for key, value in record.items() if key != "selection_id"}
        return f"subskill-{digest_json(canonical)}"
