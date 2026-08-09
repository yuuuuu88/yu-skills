from __future__ import annotations

from typing import Any

from .taskctl import TaskStore


class KnowledgeStore:
    """Read facade over TaskStore's transactional canonical Knowledge API.

    Phase agents may use this convenience class, but it intentionally owns no
    filesystem path and has no writer capable of bypassing taskctl.
    """

    def __init__(self, store: TaskStore) -> None:
        self.store = store

    def record(
        self,
        task_id: str,
        expected_revision: int,
        sources: list[dict[str, Any]],
        claims: list[dict[str, Any]],
        *,
        actor: str = "knowledge-store",
        questions: list[dict[str, Any]] | None = None,
        loop_id: str = "root",
    ) -> dict[str, Any]:
        return self.store.record_knowledge(
            task_id,
            expected_revision,
            actor,
            questions=questions,
            sources=sources,
            claims=claims,
            loop_id=loop_id,
        )

    def active_claims(self, task_id: str, loop_id: str = "root") -> list[dict[str, Any]]:
        claims = self.store.read_knowledge(task_id, loop_id)["claims"]
        superseded = {identifier for claim in claims for identifier in claim.get("supersedes", [])}
        return [
            claim
            for claim in claims
            if claim["lifecycle_status"] == "active" and claim["claim_id"] not in superseded
        ]
