from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from .models import ContractError, safe_identifier


@dataclass(frozen=True)
class ChildLoop:
    loop_id: str
    dependencies: tuple[str, ...] = ()
    read_scope: tuple[str, ...] = ()
    write_scope: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        safe_identifier(self.loop_id, "loop_id")
        if self.loop_id in self.dependencies:
            raise ContractError("child loop cannot depend on itself")


class ChildLoopDAG:
    def __init__(self) -> None:
        self._nodes: dict[str, ChildLoop] = {}

    def add(self, node: ChildLoop) -> None:
        if node.loop_id in self._nodes:
            raise ContractError(f"duplicate child loop: {node.loop_id}")
        self._nodes[node.loop_id] = node

    def waves(self) -> tuple[tuple[str, ...], ...]:
        remaining = dict(self._nodes)
        completed: set[str] = set()
        waves: list[tuple[str, ...]] = []
        while remaining:
            ready = tuple(sorted(loop_id for loop_id, node in remaining.items() if set(node.dependencies) <= completed))
            if not ready:
                raise ContractError("child loop dependencies contain a cycle or missing node")
            waves.append(ready)
            completed.update(ready)
            for loop_id in ready:
                remaining.pop(loop_id)
        return tuple(waves)


@dataclass(frozen=True)
class IntegrationReview:
    accepted: bool
    conflicts: tuple[tuple[str, str, str], ...] = ()

    @classmethod
    def from_write_sets(cls, write_sets: Mapping[str, Sequence[str]]) -> "IntegrationReview":
        conflicts: list[tuple[str, str, str]] = []
        items = sorted(write_sets.items())
        for index, (left_id, left_paths) in enumerate(items):
            for right_id, right_paths in items[index + 1:]:
                for path in sorted(set(left_paths) & set(right_paths)):
                    conflicts.append((left_id, right_id, path))
        return cls(not conflicts, tuple(conflicts))
