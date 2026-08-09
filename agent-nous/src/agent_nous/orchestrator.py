from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import UnsupportedActionError, ValidationError
from .taskctl import TaskStore


@dataclass(frozen=True)
class PhaseResultDraft:
    """Non-canonical data returned by an already-managed phase backend."""

    summary: str
    outputs: dict[str, Any]
    decision: str | None = None
    managed_action_refs: tuple[str, ...] = ()


@dataclass
class Orchestrator:
    """State coordinator, never an arbitrary-code execution path.

    Phase backends may produce a draft only after their effects were brokered by
    managed adapters.  The coordinator acquires the worker lease and asks
    taskctl to persist the draft.  It deliberately refuses Python callables:
    invoking one here would create an unobservable side-effect bypass.
    """

    store: TaskStore
    actor: str = "nous-orchestrator"

    def persist_once(self, task_id: str, draft: PhaseResultDraft | dict[str, Any]) -> dict[str, Any]:
        definition, state, context = self.store.read_task(task_id)
        del definition, context
        if state["lifecycle_status"] != "active" or state["execution_status"] != "idle":
            raise ValidationError("task is not ready for a phase result")
        if isinstance(draft, dict):
            draft = PhaseResultDraft(
                str(draft.get("summary", "")),
                dict(draft.get("outputs", {})),
                draft.get("decision"),
                tuple(draft.get("managed_action_refs", ())),
            )
        if not isinstance(draft, PhaseResultDraft) or not draft.summary.strip():
            raise ValidationError("phase draft must have a nonempty summary")
        phase = state["phase"]
        if phase != "review" and draft.decision is not None:
            raise ValidationError("only review may decide lifecycle")
        outputs = dict(draft.outputs)
        if draft.managed_action_refs:
            outputs["managed_action_refs"] = list(draft.managed_action_refs)
        leased = self.store.acquire_lease(task_id, state["revision"], self.actor)
        return self.store.record_phase(task_id, leased["revision"], self.actor, str(leased["active_lease"]["token"]), phase, draft.summary, outputs, draft.decision)

    def run_once(self, task_id: str, workers: dict[str, object]) -> dict[str, Any]:
        """Compatibility guard for the unsafe pre-v1 callable runner."""
        del task_id, workers
        raise UnsupportedActionError("Orchestrator does not execute arbitrary phase callables; use managed adapters then persist_once")

    def run_sequential(self, task_id: str, drafts: dict[str, PhaseResultDraft | dict[str, Any]], max_steps: int = 3) -> dict[str, Any]:
        last: dict[str, Any] = {}
        for _ in range(max_steps):
            _, state, _ = self.store.read_task(task_id)
            phase = state["phase"]
            if phase not in drafts:
                raise ValidationError(f"no phase draft for phase={phase}")
            last = self.persist_once(task_id, drafts[phase])
            state = last["state"]
            if state["lifecycle_status"] != "active" or state["phase"] == "plan" or state["execution_status"] != "idle":
                break
        return last
