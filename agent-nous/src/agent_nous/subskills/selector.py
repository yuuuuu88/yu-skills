from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..errors import ValidationError
from .registry import SubskillProvider


@dataclass(frozen=True)
class Selection:
    provider: SubskillProvider
    role: str
    score: int


class SubskillSelector:
    def select(
        self,
        providers: Iterable[SubskillProvider],
        *,
        phase: str,
        required_capabilities: Iterable[str] = (),
        role: str = "primary",
        limit: int = 1,
        required_safety_capabilities: Iterable[str] = (),
    ) -> tuple[Selection, ...]:
        required = frozenset(required_capabilities)
        safety = frozenset(required_safety_capabilities)
        candidates: list[Selection] = []
        for provider in providers:
            manifest = provider.manifest
            capabilities = frozenset(str(item) for item in manifest["capabilities"])
            if not manifest["enabled"] or manifest["phase"] != phase:
                continue
            if role not in manifest["roles"] or not required.issubset(capabilities):
                continue
            score = int(manifest["priority"]) + 100 * len(required & capabilities)
            candidates.append(Selection(provider, role, score))
        candidates.sort(key=lambda item: (-item.score, item.provider.id))
        selected = tuple(candidates[:limit])
        selected_capabilities = frozenset(
            capability
            for item in selected
            for capability in item.provider.manifest["capabilities"]
        )
        missing_safety = safety - selected_capabilities
        if missing_safety:
            raise ValidationError(
                "required safety capabilities unavailable: "
                + ", ".join(sorted(missing_safety))
            )
        if not selected and (required or safety):
            raise ValidationError(f"no compatible subskill for phase={phase}")
        return selected
