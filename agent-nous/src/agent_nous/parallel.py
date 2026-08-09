from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import ValidationError
from .taskctl import TaskStore


@dataclass(frozen=True)
class ChildContract:
    """Small draft helper; TaskStore creates the immutable canonical contract."""

    loop_id: str
    objective: str
    depends_on: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()
    write_scope: tuple[str, ...] = ()

    def draft(self) -> dict[str, Any]:
        declared = self.resources or self.write_scope
        return {
            "loop_id": self.loop_id,
            "objective": self.objective,
            "scope": list(self.write_scope or declared),
            "dependencies": list(self.depends_on),
            "resources": list(declared),
            "write_scope": list(self.write_scope),
            "acceptance_criteria": [
                {
                    "criterion_id": "AC-001",
                    "statement": self.objective,
                    "validation_method": "independent child review",
                }
            ],
            "expected_outputs": ["child-result"],
            "validation_plan": ["independent child review"],
        }


class ParallelScheduler:
    """A taskctl-only scheduler facade with no in-memory reservation truth."""

    def __init__(self, store: TaskStore, max_concurrency: int = 3, max_depth: int = 2) -> None:
        if not 1 <= max_concurrency <= 3 or not 0 <= max_depth <= 2:
            raise ValidationError("parallel limits out of contract")
        self.store = store
        self.max_concurrency = max_concurrency
        self.max_depth = max_depth

    @staticmethod
    def validate(children: list[ChildContract]) -> None:
        names = {child.loop_id for child in children}
        if len(names) != len(children):
            raise ValidationError("duplicate child loop id")
        lookup = {child.loop_id: child for child in children}
        for child in children:
            if set(child.depends_on) - names:
                raise ValidationError(f"unknown dependency for {child.loop_id}")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(loop_id: str) -> None:
            if loop_id in visiting:
                raise ValidationError("child dependency cycle")
            if loop_id not in visited:
                visiting.add(loop_id)
                for dependency in lookup[loop_id].depends_on:
                    visit(dependency)
                visiting.remove(loop_id)
                visited.add(loop_id)

        for child in children:
            visit(child.loop_id)

    def ready(self, task_id: str) -> list[dict[str, Any]]:
        """Read canonical status only; callers must acquire a persisted lease."""
        _, state, _ = self.store.read_task(task_id)
        ready = [item for item in state["child_loops"] if item.get("status") == "ready"]
        return sorted(ready, key=lambda item: str(item["loop_id"]))[: self.max_concurrency]

    def request(
        self,
        task_id: str,
        expected_revision: int,
        actor: str,
        *,
        loop_id: str,
        resource: str,
        mode: str,
        input_version_ref: str | None = None,
    ) -> dict[str, Any]:
        return self.store.request_reservation(
            task_id,
            expected_revision,
            actor,
            loop_id=loop_id,
            resource=resource,
            mode=mode,
            input_version_ref=input_version_ref,
        )

    def acquire(
        self, task_id: str, expected_revision: int, actor: str, reservation_id: str, lease_seconds: int = 300
    ) -> dict[str, Any]:
        return self.store.acquire_reservation(
            task_id,
            expected_revision,
            actor,
            reservation_id=reservation_id,
            lease_seconds=lease_seconds,
        )

    def release(
        self, task_id: str, expected_revision: int, actor: str, reservation_id: str, lease_token: str, reason: str
    ) -> dict[str, Any]:
        return self.store.release_reservation(
            task_id,
            expected_revision,
            actor,
            reservation_id=reservation_id,
            lease_token=lease_token,
            reason=reason,
        )

    def renew(
        self, task_id: str, expected_revision: int, actor: str, reservation_id: str, lease_token: str, lease_seconds: int = 300
    ) -> dict[str, Any]:
        return self.store.renew_reservation(
            task_id,
            expected_revision,
            actor,
            reservation_id=reservation_id,
            lease_token=lease_token,
            lease_seconds=lease_seconds,
        )
