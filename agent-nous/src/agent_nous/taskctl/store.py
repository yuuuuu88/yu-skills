from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from ..config import tasks_dir
from ..contracts import SchemaCatalog
from ..domain import ActionDescriptor, Approval, HumanApprovalAuthority, PolicyDecision, PolicyGateway, secret_summary
from ..errors import ConflictError, NotFoundError, RecoveryRequiredError, ValidationError
from ..ids import make_task_id, safe_child, validate_runtime_id, validate_task_id
from ..io import append_jsonl, atomic_write_json, atomic_write_text, digest_json, read_json, read_text_nofollow
from .request import validate_request_id


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _safe_runtime_id(value: str, label: str) -> str:
    """Accept IDs only when safe to use as a single Task-relative filename."""
    return validate_runtime_id(value, label)


def _directory_identity(path: Path) -> str:
    metadata = os.stat(path, follow_symlinks=False)
    if not path.is_dir():
        raise ValidationError("target repository must be a directory")
    return f"{metadata.st_dev}:{metadata.st_ino}"


def _event(
    task_id: str,
    revision: int,
    type_: str,
    actor: str,
    payload: dict[str, Any],
    transaction_id: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "event_id": f"evt-{uuid.uuid4().hex}",
        "task_id": task_id,
        "revision": revision,
        "transaction_id": transaction_id,
        "type": type_,
        "occurred_at": now(),
        "actor": actor,
        "payload": payload,
    }


class TaskStore:
    """The only canonical local Task State mutation boundary.

    A mutation is written as a recoverable multi-file transaction while the
    store lock is held.  Callers never edit snapshots, journals, checkpoints,
    questions, or answers directly.
    """

    def __init__(
        self,
        home: Path,
        human_approval_authority: HumanApprovalAuthority | None = None,
        request_id: str | None = None,
    ) -> None:
        self.home = home.resolve()
        self.root = tasks_dir(self.home)
        self.schemas = SchemaCatalog()
        self.human_approval_authority = human_approval_authority or HumanApprovalAuthority()
        self.request_id = validate_request_id(request_id) if request_id is not None else None

    def _event_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Bind the public request ID to every canonical event from this store."""
        bound = dict(payload)
        if self.request_id is None:
            return bound
        existing = bound.get("request_id")
        if existing is not None and existing != self.request_id:
            raise ValidationError("event payload request_id conflicts with the active request")
        bound["request_id"] = self.request_id
        return bound

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / ".transactions").mkdir(exist_ok=True)
        if not (self.root / "registry.json").exists():
            atomic_write_json(
                self.root / "registry.json",
                {"schema_version": 1, "generated_at": now(), "tasks": []},
            )
        if not (self.root / "STATUS.md").exists():
            atomic_write_text(self.root / "STATUS.md", "# nous tasks\n\nNo tasks.\n")

    @contextmanager
    def lock(self) -> Iterator[None]:
        self.init()
        with (self.root / ".lock").open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                self.recover()
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _path(self, task_id: str) -> Path:
        return safe_child(self.root, validate_task_id(task_id))

    def _read_task_unlocked(self, task_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        path = self._path(task_id)
        if not path.is_dir():
            raise NotFoundError(f"task not found: {task_id}")
        definition, state, context = (
            read_json(path / name)
            for name in ("task-definition.json", "state.json", "stable-context.json")
        )
        self.schemas.validate("task-definition", definition)
        self.schemas.validate("task-state", state)
        self.schemas.validate("stable-context", context)
        self._validate_state_contract(state)
        return definition, state, context

    def read_task(self, task_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        return self._read_task_unlocked(task_id)

    @staticmethod
    def _state_defaults(task_id: str, timestamp: str) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "task_id": task_id,
            "root_task_id": task_id,
            "loop_id": "root",
            "parent_loop_id": None,
            "revision": 1,
            "lifecycle_status": "active",
            "execution_status": "idle",
            "phase": "plan",
            "cycle": 1,
            "phase_kind": "standard",
            "visibility": "visible",
            "last_checkpoint_id": None,
            "waiting_question_ids": [],
            "active_lease": None,
            "updated_at": timestamp,
            "last_error": None,
            "active_plan_ref": None,
            "active_parallelization_plan_ref": None,
            "active_implement_ref": None,
            "latest_review_result_ref": None,
            "latest_review_decision_ref": None,
            "blocking_reasons": [],
            "sync_status": "clean",
            "target_artifact_durability": "none",
            "current_action": None,
            "active_contract_ref": None,
            "latest_checkpoint_ref": None,
            "pending_human_questions": [],
            "active_approval_refs": [],
            "pending_approval_request_refs": [],
            "active_secret_exception_refs": [],
            "child_loops": [],
            "run_id": None,
            "lease_expires_at": None,
        }

    def _validate_state_contract(self, state: dict[str, Any]) -> None:
        lifecycle = state["lifecycle_status"]
        execution = state["execution_status"]
        if lifecycle == "completed" and execution != "idle":
            raise ValidationError("completed task must be idle")
        if lifecycle == "waiting_human":
            if execution != "idle" or not state["waiting_question_ids"] or not state["pending_human_questions"]:
                raise ValidationError("waiting_human task must be idle with a pending question")
        elif state["waiting_question_ids"] or state["pending_human_questions"]:
            raise ValidationError("only waiting_human task may have pending questions")
        lease = state["active_lease"]
        if execution in {"running", "pausing"} and not lease:
            raise ValidationError("running task must hold a lease")
        if execution not in {"running", "pausing"} and lease is not None:
            raise ValidationError("only running or pausing task may hold a lease")
        if execution == "paused" and lifecycle not in {"active", "blocked"}:
            raise ValidationError("paused execution requires a nonterminal task")
        if lease and (state["run_id"] != lease["run_id"] or state["lease_expires_at"] != lease["expires_at"]):
            raise ValidationError("lease cache must match active lease")
        if not lease and (state["run_id"] is not None or state["lease_expires_at"] is not None):
            raise ValidationError("lease cache must be empty when no lease is active")

    def create(
        self,
        *,
        title: str,
        objective: str,
        target_repository: str | None,
        actor: str,
        acceptance_criteria: list[dict[str, Any]] | None = None,
        derived_from_task_id: str | None = None,
    ) -> dict[str, Any]:
        if not title.strip() or not objective.strip():
            raise ValidationError("title and objective are required")
        if secret_summary({"title": title, "objective": objective, "acceptance_criteria": acceptance_criteria or []}):
            raise ValidationError("high-confidence secret blocked from canonical task definition")
        with self.lock():
            task_id = make_task_id(title, self.root)
            timestamp = now()
            criteria = acceptance_criteria or [{"criterion_id": "AC-001", "statement": objective.strip(), "validation_method": "independent review evidence"}]
            canonical_target = str(Path(target_repository).resolve()) if target_repository else None
            target_identity = _directory_identity(Path(canonical_target)) if canonical_target else None
            definition = {
                "schema_version": 1,
                "task_id": task_id,
                "title": title.strip(),
                "objective": objective.strip(),
                "acceptance_criteria": criteria,
                "created_at": timestamp,
                "created_by": actor,
                "target_repository": canonical_target,
                "target_repository_identity": target_identity,
                "derived_from_task_id": derived_from_task_id,
            }
            context = {
                "schema_version": 1,
                "task_id": task_id,
                "revision": 1,
                "display_title": title.strip(),
                "confirmed_requirements": [objective.strip()],
                "decisions": [],
                "open_questions": [],
                "risks": [],
                "knowledge_claim_refs": [],
                "human_question_refs": [],
                "human_answer_refs": [],
                "updated_at": timestamp,
            }
            state = self._state_defaults(task_id, timestamp)
            for name, value in (
                ("task-definition", definition),
                ("stable-context", context),
                ("task-state", state),
            ):
                self.schemas.validate(name, value)
            temporary = self.root / f".{task_id}.create-{uuid.uuid4().hex}"
            task_dir = self._path(task_id)
            temporary.mkdir()
            try:
                (temporary / "checkpoints").mkdir()
                (temporary / "human-input" / "questions").mkdir(parents=True)
                (temporary / "human-input" / "answers").mkdir(parents=True)
                atomic_write_json(temporary / "task-definition.json", definition)
                atomic_write_json(temporary / "stable-context.json", context)
                checkpoint_id = f"ckpt-{uuid.uuid4().hex}"
                state = {**state, "last_checkpoint_id": checkpoint_id, "latest_checkpoint_ref": {"path": "checkpoints/latest.json", "revision": 1}}
                atomic_write_json(temporary / "state.json", state)
                txid = f"tx-{uuid.uuid4().hex}"
                event = _event(task_id, 1, "task_created", actor, self._event_payload({}), txid)
                self.schemas.validate("event", event)
                atomic_write_text(
                    temporary / "events.jsonl",
                    json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n",
                )
                self._checkpoint_to(temporary, state, "created", "run plan", checkpoint_id)
                os.replace(temporary, task_dir)
            except Exception:
                shutil.rmtree(temporary, ignore_errors=True)
                raise
            self.render_all()
            return {"task_id": task_id, "state": state}

    def rename(self, task_id: str, expected_revision: int, actor: str, title: str) -> dict[str, Any]:
        if not title.strip():
            raise ValidationError("title is required")
        with self.lock():
            definition, state, context = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            updated_definition = {**definition, "title": title.strip()}
            updated_context = {**context, "display_title": title.strip(), "updated_at": now()}
            self.schemas.validate("task-definition", updated_definition)
            self.schemas.validate("stable-context", updated_context)
            new = self._mutate_locked(
                task_id,
                expected_revision=expected_revision,
                actor=actor,
                event_type="task_renamed",
                patch={},
                reason="task title renamed",
                next_action=f"run {state['phase']}",
                additional_files={"task-definition.json": updated_definition, "stable-context.json": updated_context},
                event_payload={"previous_title": definition["title"], "title": title.strip()},
            )
            return {"state": new, "title": title.strip()}

    def duplicate(self, task_id: str, actor: str, *, title: str | None = None, objective: str | None = None) -> dict[str, Any]:
        with self.lock():
            definition, state, _ = self._read_task_unlocked(task_id)
            if state["lifecycle_status"] == "cancelled":
                raise ValidationError("cancelled task cannot be duplicated")
            source = {
                "title": title or f"Follow-up: {definition['title']}",
                "objective": objective or definition["objective"],
                "target_repository": definition["target_repository"],
                "acceptance_criteria": list(definition["acceptance_criteria"]),
            }
        return self.create(actor=actor, derived_from_task_id=task_id, **source)

    def cancel(self, task_id: str, expected_revision: int, actor: str, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise ValidationError("cancel reason is required")
        with self.lock():
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            if state["lifecycle_status"] in {"completed", "cancelled"}:
                raise ValidationError("completed or cancelled task cannot be cancelled")
            if state["execution_status"] in {"running", "pausing"} or state["current_action"] is not None:
                raise ValidationError("running task must reach a safe checkpoint before cancellation")
            if any(item.get("status") in {"created", "ready", "running", "waiting_human", "integration_ready"} for item in state.get("child_loops", [])):
                raise ValidationError("uncompleted child loops must be reconciled before cancellation")
            if state["pending_approval_request_refs"] or state["active_approval_refs"]:
                raise ValidationError("approval state must be reconciled before cancellation")
            patch = {
                "lifecycle_status": "cancelled",
                "execution_status": "idle",
                "active_lease": None,
                "run_id": None,
                "lease_expires_at": None,
                "waiting_question_ids": [],
                "pending_human_questions": [],
            }
            return self._mutate_locked(
                task_id,
                expected_revision=expected_revision,
                actor=actor,
                event_type="task_cancelled",
                patch=patch,
                reason=reason,
                next_action="follow-up task or explicit reopen",
                event_payload={"reason": reason},
            )

    def reopen(self, task_id: str, expected_revision: int, actor: str, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise ValidationError("reopen reason is required")
        with self.lock():
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            if state["lifecycle_status"] != "cancelled":
                raise ValidationError("only cancelled tasks can be reopened; completed tasks require a follow-up task")
            return self._mutate_locked(
                task_id,
                expected_revision=expected_revision,
                actor=actor,
                event_type="task_reopened",
                patch={
                    "lifecycle_status": "active",
                    "execution_status": "idle",
                    "phase": "plan",
                    "cycle": state["cycle"] + 1,
                    "active_lease": None,
                    "run_id": None,
                    "lease_expires_at": None,
                    "last_error": None,
                    "active_plan_ref": None,
                    "active_parallelization_plan_ref": None,
                    "active_implement_ref": None,
                    "child_loops": [],
                    "latest_review_result_ref": None,
                    "latest_review_decision_ref": None,
                },
                reason=reason,
                next_action="create a new plan",
                event_payload={"reason": reason, "previous_cycle": state["cycle"]},
            )

    def set_visibility(self, task_id: str, expected_revision: int, actor: str, visibility: str) -> dict[str, Any]:
        if visibility not in {"visible", "archived"}:
            raise ValidationError("visibility must be visible or archived")
        with self.lock():
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            if state["visibility"] == visibility:
                raise ConflictError(f"task is already {visibility}")
            return self._mutate_locked(
                task_id,
                expected_revision=expected_revision,
                actor=actor,
                event_type="task_archived" if visibility == "archived" else "task_unarchived",
                patch={"visibility": visibility},
                reason=f"task visibility changed to {visibility}",
                next_action=f"run {state['phase']}",
                event_payload={"visibility": visibility},
            )

    def _checkpoint_to(
        self,
        directory: Path,
        state: dict[str, Any],
        reason: str,
        next_action: str,
        checkpoint_id: str | None = None,
    ) -> dict[str, Any]:
        checkpoint = {
            "schema_version": 1,
            "checkpoint_id": checkpoint_id or f"ckpt-{uuid.uuid4().hex}",
            "task_id": state["task_id"],
            "revision": state["revision"],
            "created_at": now(),
            "phase": state["phase"],
            "cycle": state["cycle"],
            "reason": reason,
            "state_digest": digest_json(state),
            "next_safe_action": next_action,
        }
        self.schemas.validate("checkpoint", checkpoint)
        atomic_write_json(directory / "checkpoints" / "latest.json", checkpoint)
        return checkpoint

    def _transaction(
        self,
        task_id: str,
        files: dict[str, dict[str, Any]],
        event: dict[str, Any],
        reason: str,
        next_action: str,
        additional_events: list[dict[str, Any]] | None = None,
    ) -> None:
        task = self._path(task_id)
        checkpoint_id = f"ckpt-{uuid.uuid4().hex}"
        copied = dict(files)
        if "state.json" in copied:
            copied["state.json"] = {**copied["state.json"], "last_checkpoint_id": checkpoint_id, "latest_checkpoint_ref": {"path": "checkpoints/latest.json", "revision": copied["state.json"]["revision"]}}
            self.schemas.validate("task-state", copied["state.json"])
            self._validate_state_contract(copied["state.json"])
        events = [event, *(additional_events or [])]
        for journal_event in events:
            self.schemas.validate("event", journal_event)
            if journal_event["transaction_id"] != event["transaction_id"]:
                raise ValidationError("all transaction events must share a transaction ID")
            if journal_event["task_id"] != task_id:
                raise ValidationError("transaction event task does not match the mutated task")
        tx = self.root / ".transactions" / f"{event['transaction_id']}.json"
        prepared = {
            "transaction_id": event["transaction_id"],
            "task_id": task_id,
            "files": copied,
            # Keep the singular field for recovery compatibility with an
            # interrupted transaction written by the earlier implementation.
            # New transactions use the complete ordered event set.
            "event": event,
            "events": events,
            "reason": reason,
            "next_action": next_action,
            "status": "prepared",
        }
        if self.request_id is not None:
            prepared["request_id"] = self.request_id
        atomic_write_json(tx, prepared)
        for relative, value in copied.items():
            target = self._safe_task_relative_path(task_id, relative)
            atomic_write_json(target, value)
        for journal_event in events:
            append_jsonl(task / "events.jsonl", journal_event)
        state = copied.get("state.json") or read_json(task / "state.json")
        self._checkpoint_to(task, state, reason, next_action, checkpoint_id)
        prepared["status"] = "committed"
        atomic_write_json(tx, prepared)
        tx.unlink(missing_ok=True)

    def recover(self) -> list[str]:
        recovered: list[str] = []
        tx_root = self.root / ".transactions"
        if not tx_root.exists():
            return recovered
        for marker in tx_root.glob("tx-*.json"):
            value = read_json(marker)
            if value.get("status") != "prepared":
                marker.unlink(missing_ok=True)
                continue
            task = self._path(str(value["task_id"]))
            if not task.exists():
                raise RecoveryRequiredError(f"transaction references missing task: {value['task_id']}")
            for relative, data in value["files"].items():
                target = self._safe_task_relative_path(str(value["task_id"]), relative)
                atomic_write_json(target, data)
            events = value.get("events") or [value["event"]]
            journal = task / "events.jsonl"
            existing = read_text_nofollow(journal) if journal.exists() else ""
            missing = [journal_event for journal_event in events if journal_event["event_id"] not in existing]
            if missing:
                with journal.open("a", encoding="utf-8") as handle:
                    for journal_event in missing:
                        handle.write(json.dumps(journal_event, ensure_ascii=False, sort_keys=True) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            state = value["files"].get("state.json") or read_json(task / "state.json")
            self._checkpoint_to(task, state, value["reason"], value["next_action"], state.get("last_checkpoint_id"))
            marker.unlink(missing_ok=True)
            recovered.append(marker.name)
        return recovered

    def _mutate_locked(
        self,
        task_id: str,
        *,
        expected_revision: int,
        actor: str,
        event_type: str,
        patch: dict[str, Any],
        reason: str,
        next_action: str,
        additional_files: dict[str, dict[str, Any]] | None = None,
        event_payload: dict[str, Any] | None = None,
        additional_events: list[tuple[str, dict[str, Any]]] | None = None,
        transaction_id: str | None = None,
    ) -> dict[str, Any]:
        _, state, _ = self._read_task_unlocked(task_id)
        if state["revision"] != expected_revision:
            raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
        primary_payload = self._event_payload(event_payload or {"reason": reason})
        sibling_payloads = [
            (type_, self._event_payload(payload))
            for type_, payload in (additional_events or [])
        ]
        candidate = {
            "patch": patch,
            "event_payload": primary_payload,
            "additional_files": additional_files or {},
        }
        if secret_summary(candidate):
            raise ValidationError("high-confidence secret blocked from canonical task persistence")
        new_state = {**state, **patch, "revision": state["revision"] + 1, "updated_at": now()}
        self.schemas.validate("task-state", new_state)
        self._validate_state_contract(new_state)
        txid = transaction_id or f"tx-{uuid.uuid4().hex}"
        if not txid.startswith("tx-"):
            raise ValidationError("transaction_id must use the canonical tx- prefix")
        event = _event(task_id, new_state["revision"], event_type, actor, primary_payload, txid)
        self.schemas.validate("event", event)
        sibling_events = [
            _event(task_id, new_state["revision"], type_, actor, payload, txid)
            for type_, payload in sibling_payloads
        ]
        files = {"state.json": new_state, **(additional_files or {})}
        self._transaction(task_id, files, event, reason, next_action, sibling_events)
        self.render_all()
        return read_json(self._path(task_id) / "state.json")

    def mutate(
        self,
        task_id: str,
        *,
        expected_revision: int,
        actor: str,
        event_type: str,
        patch: dict[str, Any],
        reason: str,
        next_action: str,
    ) -> dict[str, Any]:
        with self.lock():
            return self._mutate_locked(
                task_id,
                expected_revision=expected_revision,
                actor=actor,
                event_type=event_type,
                patch=patch,
                reason=reason,
                next_action=next_action,
            )

    def checkpoint(self, task_id: str, expected_revision: int, actor: str, reason: str, next_action: str) -> dict[str, Any]:
        return self.mutate(task_id, expected_revision=expected_revision, actor=actor, event_type="checkpoint_saved", patch={}, reason=reason, next_action=next_action)

    def request_pause(self, task_id: str, expected_revision: int, actor: str, token: str | None = None) -> dict[str, Any]:
        with self.lock():
            self._recover_expired_lease_locked(task_id)
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            if state["execution_status"] == "idle":
                return self._mutate_locked(task_id, expected_revision=expected_revision, actor=actor, event_type="pause_requested", patch={"execution_status": "paused"}, reason="idle task paused at current checkpoint", next_action="resume-prepare")
            lease = state["active_lease"]
            if state["execution_status"] != "running" or not lease:
                raise ConflictError("pause request requires a running managed worker")
            return self._mutate_locked(task_id, expected_revision=expected_revision, actor=actor, event_type="pause_requested", patch={"execution_status": "pausing"}, reason="pause requested; worker must acknowledge a safe checkpoint", next_action="worker pause acknowledgement")

    def pause(self, task_id: str, expected_revision: int, actor: str, token: str | None = None) -> dict[str, Any]:
        with self.lock():
            self._recover_expired_lease_locked(task_id)
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            if state["lifecycle_status"] not in {"active", "blocked"}:
                raise ValidationError("only nonterminal non-waiting task can pause")
            if state["execution_status"] not in {"idle", "pausing", "interrupted"}:
                raise ValidationError("task is not pausable")
            lease = state["active_lease"]
            if state["execution_status"] == "pausing" and (not lease or lease["owner"] != actor or lease["token"] != token):
                raise ConflictError("safe pause acknowledgement requires the running worker's lease token")
            if state["current_action"] is not None:
                raise ConflictError("cannot acknowledge pause while a managed action is live")
            return self._mutate_locked(task_id, expected_revision=expected_revision, actor=actor, event_type="task_paused", patch={"execution_status": "paused", "active_lease": None, "run_id": None, "lease_expires_at": None}, reason="safe pause", next_action="resume-prepare")

    def _recover_expired_lease_locked(self, task_id: str, actor: str = "taskctl-recovery") -> dict[str, Any] | None:
        _, state, _ = self._read_task_unlocked(task_id)
        lease = state["active_lease"]
        if not lease or _parse_time(lease["expires_at"]) > datetime.now(timezone.utc):
            return None
        return self._mutate_locked(
            task_id,
            expected_revision=state["revision"],
            actor=actor,
            event_type="lease_expired",
            patch={"execution_status": "interrupted", "active_lease": None, "run_id": None, "lease_expires_at": None},
            reason="lease expired; fresh-process reconciliation is required",
            next_action="resume-prepare",
            event_payload={"expired_lease_owner": lease["owner"], "expired_lease_token": lease["token"]},
        )

    def recover_expired_lease(self, task_id: str, actor: str = "taskctl-recovery") -> dict[str, Any] | None:
        with self.lock():
            return self._recover_expired_lease_locked(task_id, actor)

    def resume_prepare(self, task_id: str, expected_revision: int, actor: str) -> dict[str, Any]:
        with self.lock():
            self._recover_expired_lease_locked(task_id)
            self._expire_approvals_locked(task_id)
            definition, state, context = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            if state["lifecycle_status"] == "waiting_human" or state["waiting_question_ids"]:
                raise ValidationError("all human answers are required before resume")
            if state["current_action"] is not None:
                raise ValidationError("a live or interrupted managed action must be reconciled before resume")
            if state["lifecycle_status"] not in {"active", "blocked"}:
                raise ValidationError("only nonterminal tasks can resume")
            if state["execution_status"] not in {"paused", "interrupted", "idle"}:
                raise ValidationError("task is not at a resumable checkpoint")
            new = self._mutate_locked(task_id, expected_revision=expected_revision, actor=actor, event_type="task_resumed", patch={"lifecycle_status": "active", "execution_status": "idle", "active_lease": None, "run_id": None, "lease_expires_at": None}, reason="fresh process resume", next_action=f"run {state['phase']}")
            return {"definition": definition, "state": new, "context": context, "checkpoint": read_json(self._path(task_id) / "checkpoints/latest.json")}

    def acquire_lease(self, task_id: str, expected_revision: int, actor: str, seconds: int = 300) -> dict[str, Any]:
        with self.lock():
            self._recover_expired_lease_locked(task_id)
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            if state["sync_status"] == "conflicted":
                raise ConflictError("cannot acquire an execution lease while Git durability is conflicted; record a reconciliation/replan outcome")
            if state["lifecycle_status"] != "active" or state["execution_status"] != "idle":
                raise ValidationError("only active idle task can acquire a lease")
            token = f"lease-{uuid.uuid4().hex}"
            run_id = f"run-{uuid.uuid4().hex}"
            expiry = (datetime.now(timezone.utc) + timedelta(seconds=max(1, min(seconds, 3600)))).isoformat()
            return self._mutate_locked(task_id, expected_revision=expected_revision, actor=actor, event_type="lease_acquired", patch={"active_lease": {"owner": actor, "token": token, "expires_at": expiry, "run_id": run_id}, "execution_status": "running", "run_id": run_id, "lease_expires_at": expiry}, reason="lease acquired", next_action="complete current phase")

    # Backward-compatible internal name; callers should use acquire_lease.
    lease = acquire_lease

    def renew_lease(self, task_id: str, expected_revision: int, actor: str, token: str, seconds: int = 300) -> dict[str, Any]:
        with self.lock():
            self._recover_expired_lease_locked(task_id)
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            if state["sync_status"] == "conflicted":
                raise ConflictError("phase progression is blocked while Git durability is conflicted; record a reconciliation/replan outcome")
            lease = state["active_lease"]
            if not lease or lease["owner"] != actor or lease["token"] != token:
                raise ConflictError("lease token or owner does not match")
            expiry = (datetime.now(timezone.utc) + timedelta(seconds=max(1, min(seconds, 3600)))).isoformat()
            return self._mutate_locked(task_id, expected_revision=expected_revision, actor=actor, event_type="lease_renewed", patch={"active_lease": {**lease, "expires_at": expiry}, "lease_expires_at": expiry}, reason="lease renewed", next_action="complete current phase")

    def release_lease(self, task_id: str, expected_revision: int, actor: str, token: str) -> dict[str, Any]:
        with self.lock():
            self._recover_expired_lease_locked(task_id)
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            lease = state["active_lease"]
            if not lease or lease["owner"] != actor or lease["token"] != token:
                raise ConflictError("lease token or owner does not match")
            if state["current_action"] is not None:
                raise ConflictError("cannot release a lease while a managed action is live; reconcile or finish it")
            return self._mutate_locked(task_id, expected_revision=expected_revision, actor=actor, event_type="lease_released", patch={"execution_status": "idle", "active_lease": None, "run_id": None, "lease_expires_at": None}, reason="lease released", next_action=f"run {state['phase']}")

    def _normalize_question(self, task_id: str, question: dict[str, Any]) -> dict[str, Any]:
        result = {**question, "schema_version": 1, "task_id": task_id, "loop_id": "root", "created_at": now()}
        result.setdefault("question_id", f"q-{uuid.uuid4().hex}")
        _safe_runtime_id(str(result["question_id"]), "question_id")
        target = result.get("resume_target")
        if not isinstance(target, dict) or target.get("resume_phase") not in {"plan", "implement", "review"}:
            raise ValidationError("question resume_target must name a core phase and transition")
        option_ids = [item.get("option_id") for item in result.get("options", []) if isinstance(item, dict)]
        if len(option_ids) != len(set(option_ids)):
            raise ValidationError("question options must have unique IDs")
        recommended = result.get("recommended_option_id")
        if recommended is not None and recommended not in option_ids:
            raise ValidationError("recommended option must be one of the question options")
        if not result.get("options") and not result.get("free_text_allowed"):
            raise ValidationError("question without options must allow free text")
        self.schemas.validate("human-question", result)
        return result

    def record_question(self, task_id: str, expected_revision: int, actor: str, question: dict[str, Any]) -> dict[str, Any]:
        with self.lock():
            _, state, context = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            if state["lifecycle_status"] != "active":
                raise ValidationError("only active task can record a human question")
            canonical = self._normalize_question(task_id, question)
            relative = f"human-input/questions/{canonical['question_id']}.json"
            question_ref = {"artifact_id": canonical["question_id"], "artifact_type": "human_question", "ref": relative}
            context = {**context, "revision": state["revision"] + 1, "open_questions": [*context["open_questions"], relative], "human_question_refs": [*context["human_question_refs"], question_ref], "updated_at": now()}
            self.schemas.validate("stable-context", context)
            new = self._mutate_locked(
                task_id,
                expected_revision=expected_revision,
                actor=actor,
                event_type="human_input_requested",
                patch={"lifecycle_status": "waiting_human", "execution_status": "idle", "active_lease": None, "run_id": None, "lease_expires_at": None, "waiting_question_ids": [*state["waiting_question_ids"], canonical["question_id"]], "pending_human_questions": [*state["pending_human_questions"], question_ref]},
                reason="human question",
                next_action=canonical["resume_target"]["resume_phase"],
                additional_files={relative: canonical, "stable-context.json": context},
                event_payload={"question_id": canonical["question_id"], "resume_target": canonical["resume_target"]},
            )
            return {"question": canonical, "state": new}

    def answer_question(self, task_id: str, expected_revision: int, actor: str, question_id: str, answer: dict[str, Any]) -> dict[str, Any]:
        with self.lock():
            _safe_runtime_id(question_id, "question_id")
            _, state, context = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            if question_id not in state["waiting_question_ids"]:
                raise ConflictError("question is not pending")
            qpath = self._path(task_id) / "human-input" / "questions" / f"{question_id}.json"
            question = read_json(qpath)
            self.schemas.validate("human-question", question)
            answer_type = answer.get("answer_type")
            option_id = answer.get("selected_option_id")
            if answer_type == "selected_option" and option_id not in {item["option_id"] for item in question["options"]}:
                raise ValidationError("unknown question option")
            if answer_type == "free_text" and not question["free_text_allowed"]:
                raise ValidationError("free-text answer is not allowed")
            if answer.get("applied_scope") != question["blocking_scope"]:
                raise ValidationError("answer scope must match the blocked scope")
            effect = answer.get("answer_effect")
            if effect not in {"resume", "replan", "remain_blocked", "cancel"}:
                raise ValidationError("unknown answer effect")
            if effect == "resume" and answer.get("resume_target") != question["resume_target"]:
                raise ValidationError("resume answer must use the exact question resume target")
            if effect != "resume" and answer.get("resume_target") is not None:
                raise ValidationError("only resume answer may carry a resume target")
            if question["approval_request_ref"]:
                # A phase agent may propose the question but cannot turn an
                # arbitrary actor string into a Red approval.  The host must
                # verify a detached Human signature over this exact answer;
                # the signature is transient and never enters Task files.
                if not self.human_approval_authority.verify(
                    task_id,
                    question_id,
                    actor,
                    answer,
                    answer.get("human_attestation"),
                ):
                    raise ValidationError("approval-bearing Human answer lacks a valid trusted-host attestation")
            answer_id = f"a-{uuid.uuid4().hex}"
            _safe_runtime_id(answer_id, "answer_id")
            canonical_answer = {"schema_version": 1, "answer_id": answer_id, "task_id": task_id, "loop_id": question["loop_id"], "question_id": question_id, "answer_type": answer_type, "selected_option_id": answer.get("selected_option_id"), "answer_text": answer.get("answer_text"), "applied_scope": answer.get("applied_scope"), "answer_effect": effect, "resume_target": answer.get("resume_target"), "answered_by": actor, "answered_at": now()}
            self.schemas.validate("human-answer", canonical_answer)
            answer_relative = f"human-input/answers/{answer_id}.json"
            remaining = [item for item in state["waiting_question_ids"] if item != question_id]
            # The question itself is never rewritten.  The immutable answer is
            # the sole canonical record of a Human decision.
            pending_refs = [item for item in state["pending_human_questions"] if item.get("artifact_id") != question_id]
            patch: dict[str, Any] = {"waiting_question_ids": remaining, "pending_human_questions": pending_refs}
            if not remaining:
                if effect == "resume":
                    patch.update({"lifecycle_status": "active", "execution_status": "idle", "phase": question["resume_target"]["resume_phase"], "active_lease": None, "run_id": None, "lease_expires_at": None})
                elif effect == "replan":
                    patch.update({"lifecycle_status": "active", "execution_status": "idle", "phase": "plan", "phase_kind": "standard", "cycle": state["cycle"] + 1, "active_lease": None, "run_id": None, "lease_expires_at": None, "active_plan_ref": None, "active_parallelization_plan_ref": None, "active_implement_ref": None, "child_loops": []})
                elif effect == "remain_blocked":
                    patch.update({"lifecycle_status": "blocked", "execution_status": "idle", "active_lease": None, "run_id": None, "lease_expires_at": None})
                else:
                    patch.update({"lifecycle_status": "cancelled", "execution_status": "idle", "active_lease": None, "run_id": None, "lease_expires_at": None})
            answer_ref = {"artifact_id": answer_id, "artifact_type": "human_answer", "ref": answer_relative}
            context = {**context, "revision": state["revision"] + 1, "open_questions": [item for item in context["open_questions"] if item != f"human-input/questions/{question_id}.json"], "human_answer_refs": [*context["human_answer_refs"], answer_ref], "updated_at": now()}
            self.schemas.validate("stable-context", context)
            files: dict[str, dict[str, Any]] = {answer_relative: canonical_answer, "stable-context.json": context}
            extra_events: list[tuple[str, dict[str, Any]]] = []
            approval_ref = question["approval_request_ref"]
            if approval_ref and option_id == "deny":
                approvals = self._approvals_store_unlocked(task_id)
                records: list[dict[str, Any]] = []
                denied_id: str | None = None
                for record in approvals["approvals"]:
                    if record["requested_action_descriptor_ref"]["path"] == approval_ref:
                        if record["status"] != "requested" or record["created_from_question_id"] != question_id:
                            raise ConflictError("approval request is not awaiting this Human answer")
                        denied_id = record["approval_id"]
                        record = {
                            **record,
                            "status": "denied",
                            "revoked_at": now(),
                            "revoke_reason": "human selected deny",
                        }
                        self.schemas.validate("approval", record)
                    records.append(record)
                if denied_id is None:
                    raise RecoveryRequiredError("approval question has no canonical approval request")
                files["approvals.json"] = {**approvals, "approvals": records}
                patch["pending_approval_request_refs"] = [item for item in patch["pending_approval_request_refs"] if item.get("approval_id") != denied_id]
                extra_events.append(("approval_denied", {"approval_id": denied_id, "answer_ref": answer_relative, "reason": "human selected deny"}))
            new = self._mutate_locked(
                task_id,
                expected_revision=expected_revision,
                actor=actor,
                event_type="human_input_received",
                patch=patch,
                reason="human answer",
                next_action=f"run {question['resume_target']['resume_phase']}" if not remaining and effect == "resume" else "await explicit resume decision",
                additional_files=files,
                event_payload={"question_id": question_id, "answer_id": answer_id},
                additional_events=extra_events,
            )
            return new

    @staticmethod
    def _review_completion_is_valid(definition: dict[str, Any], outputs: dict[str, Any]) -> bool:
        criteria = outputs.get("acceptance_criteria")
        evidence = outputs.get("validation_evidence")
        blockers = outputs.get("blocking_issues")
        if not isinstance(criteria, list) or not isinstance(evidence, list):
            return False
        required_ids = {item["criterion_id"] for item in definition["acceptance_criteria"]}
        if {item.get("criterion_id") for item in criteria if isinstance(item, dict)} != required_ids:
            return False
        evidence_by_criterion: dict[str, set[str]] = {}
        for item in evidence:
            if not isinstance(item, dict) or item.get("outcome") != "passed" or item.get("criterion_id") not in required_ids or not isinstance(item.get("ref"), str):
                return False
            evidence_by_criterion.setdefault(item["criterion_id"], set()).add(item["ref"])
        return (
            bool(criteria)
            and all(isinstance(item, dict) and item.get("status") == "passed" and item.get("evidence_refs") for item in criteria)
            and all(set(item["evidence_refs"]).issubset(evidence_by_criterion.get(item["criterion_id"], set())) for item in criteria)
            and all(evidence_by_criterion.get(criterion_id) for criterion_id in required_ids)
            and isinstance(blockers, list)
            and not blockers
        )

    def _validate_managed_action_refs(self, task_id: str, phase: str, outputs: dict[str, Any], loop_id: str = "root") -> None:
        refs = outputs.get("managed_action_refs", [])
        if not isinstance(refs, list) or any(not isinstance(item, str) or not item for item in refs):
            raise ValidationError("managed_action_refs must be a list of completed action IDs")
        if phase == "implement" and not refs and outputs.get("no_managed_effects") is not True:
            raise ValidationError("implement output requires completed managed action evidence or explicit no_managed_effects")
        if outputs.get("changed_artifacts") and not refs:
            raise ValidationError("changed artifacts require managed completed-action evidence")
        seen: set[str] = set()
        for action_id in refs:
            _safe_runtime_id(action_id, "managed action ID")
            if action_id in seen:
                raise ValidationError("managed_action_refs must not repeat an action")
            seen.add(action_id)
            action_path = self._path(task_id) / "actions" / f"{action_id}.json"
            if not action_path.exists():
                raise ValidationError("phase references an unknown managed action")
            record = read_json(action_path)
            self.schemas.validate("action-record", record)
            if record["status"] != "completed" or not record["after_evidence_ref"]:
                raise ValidationError("phase references an action without completed postflight evidence")
            if record["action"].get("loop_id") != loop_id:
                raise ValidationError("phase references an action owned by another loop")

    def _knowledge_gate_allows_implementation_unlocked(self, task_id: str) -> bool:
        knowledge = self._read_knowledge_unlocked(task_id)
        superseded = {identifier for question in knowledge["questions"] for identifier in question.get("supersedes", [])}
        return not any(
            question["impact"] == "high"
            and question["required_before"] == "plan_complete"
            and question["question_id"] not in superseded
            # v1 deliberately has no scope-amendment artifact that could
            # prove a high-impact question no longer matters.  Therefore a
            # defer/cancel lifecycle cannot silently open the Plan->Implement
            # gate; only an evidence-backed immutable answer can.
            and (question["status"] != "answered" or not question["answer_evidence"])
            for question in knowledge["questions"]
        )

    def record_phase(self, task_id: str, expected_revision: int, actor: str, lease_token: str, phase: str, summary: str, outputs: dict[str, Any], decision: str | None = None) -> dict[str, Any]:
        with self.lock():
            self._recover_expired_lease_locked(task_id)
            definition, state, context = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            if state["sync_status"] == "conflicted":
                raise ConflictError("phase progression is blocked while Git durability is conflicted; record a reconciliation/replan outcome")
            lease = state["active_lease"]
            if state["phase"] != phase or state["lifecycle_status"] != "active" or state["execution_status"] not in {"running", "pausing"} or not lease or lease["owner"] != actor or lease["token"] != lease_token:
                raise ValidationError("phase result requires the current actor's exact active lease token")
            if state["current_action"] is not None:
                raise ConflictError("cannot record a phase while a managed action is live")
            if phase != "review" and decision is not None:
                raise ValidationError("only review has a decision")
            if phase == "review" and decision not in {"completed", "needs_plan", "needs_human_input"}:
                raise ValidationError("review decision is required")
            self._validate_managed_action_refs(task_id, phase, outputs)
            if phase == "plan" and not self._knowledge_gate_allows_implementation_unlocked(task_id):
                raise ValidationError("high-impact Knowledge Questions must be answered, deferred, or superseded before implementation")
            if phase == "review" and decision == "completed" and not self._review_completion_is_valid(definition, outputs):
                raise ValidationError("completed review requires passed acceptance criteria, validation evidence, and no blockers")
            if phase == "review" and decision == "completed":
                has_children = bool(state["child_loops"])
                if has_children:
                    raise ValidationError("materialized child loops require record_integration_review; ordinary review cannot complete the root")
                if state["phase_kind"] != "standard":
                    raise ValidationError("ordinary review completion requires standard phase kind")
                if state["pending_human_questions"] or state["pending_approval_request_refs"]:
                    raise ValidationError("review completion is blocked by pending human input or approval")
            question: dict[str, Any] | None = None
            if phase == "review" and decision == "needs_human_input":
                raw_question = outputs.get("human_question")
                if not isinstance(raw_question, dict):
                    raise ValidationError("review needs_human_input requires outputs.human_question")
                question = self._normalize_question(task_id, raw_question)
            artifact = {"schema_version": 1, "artifact_id": f"{phase}-{state['cycle']:02d}-{uuid.uuid4().hex[:8]}", "task_id": task_id, "phase": phase, "cycle": state["cycle"], "created_at": now(), "summary": summary, "inputs": [], "outputs": outputs, "decision": decision}
            self.schemas.validate("phase-result", artifact)
            prefix = {"plan": "01-plan", "implement": "02-implement", "review": "03-review"}[phase]
            artifact_path = f"cycles/{state['cycle']:04d}/{prefix}-result.json"
            artifact_ref = {"artifact_id": artifact["artifact_id"], "path": artifact_path, "content_hash": digest_json(artifact), "revision": state["revision"] + 1}
            safe_paused = state["execution_status"] == "pausing"
            patch: dict[str, Any] = {"execution_status": "paused" if safe_paused else "idle", "active_lease": None, "run_id": None, "lease_expires_at": None}
            files: dict[str, dict[str, Any]] = {artifact_path: artifact}
            if phase == "plan":
                patch.update({"phase": "implement", "phase_kind": "standard", "active_plan_ref": artifact_ref})
            elif phase == "implement":
                patch.update({"phase": "review", "phase_kind": "standard", "active_implement_ref": artifact_ref})
            elif decision == "completed":
                decision_path = f"cycles/{state['cycle']:04d}/03-review-decision.json"
                review_decision = {"schema_version": 1, "task_id": task_id, "artifact_id": artifact["artifact_id"], "decision": decision, "acceptance_criteria": outputs["acceptance_criteria"], "validation_evidence": outputs["validation_evidence"], "blocking_issues": outputs["blocking_issues"], "created_at": now()}
                files[decision_path] = review_decision
                patch.update({"lifecycle_status": "completed", "phase": "review", "latest_review_result_ref": artifact_ref, "latest_review_decision_ref": {"path": decision_path, "content_hash": digest_json(review_decision), "revision": state["revision"] + 1}})
            elif decision == "needs_plan":
                patch.update({"phase": "plan", "phase_kind": "standard", "cycle": state["cycle"] + 1, "active_plan_ref": None, "active_parallelization_plan_ref": None, "active_implement_ref": None, "latest_review_result_ref": artifact_ref})
            elif decision == "needs_human_input" and question:
                qrelative = f"human-input/questions/{question['question_id']}.json"
                files[qrelative] = question
                question_ref = {"artifact_id": question["question_id"], "artifact_type": "human_question", "ref": qrelative}
                context = {**context, "revision": state["revision"] + 1, "open_questions": [*context["open_questions"], qrelative], "human_question_refs": [*context["human_question_refs"], question_ref], "updated_at": now()}
                self.schemas.validate("stable-context", context)
                files["stable-context.json"] = context
                if safe_paused:
                    raise ValidationError("a pause acknowledgement cannot create a new human wait")
                patch.update({"lifecycle_status": "waiting_human", "execution_status": "idle", "waiting_question_ids": [*state["waiting_question_ids"], question["question_id"]], "pending_human_questions": [*state["pending_human_questions"], question_ref], "latest_review_result_ref": artifact_ref})
            event_payload = {"phase": phase, "artifact_id": artifact["artifact_id"], "decision": decision}
            if question:
                event_payload["question_id"] = question["question_id"]
            new = self._mutate_locked(task_id, expected_revision=expected_revision, actor=actor, event_type="phase_recorded", patch=patch, reason=f"{phase} recorded", next_action=f"run {patch.get('phase', state['phase'])}", additional_files=files, event_payload=event_payload)
            return {"artifact": artifact, "state": new}

    def read_review_observations(self, task_id: str, cycle: int, loop_id: str = "root") -> dict[str, dict[str, Any]]:
        """Read durable per-reviewer observations without changing Task state."""
        _safe_runtime_id(loop_id, "loop_id")
        root = self._path(task_id).resolve()
        base = root if loop_id == "root" else root / "loops" / loop_id
        directory = base / "cycles" / f"{cycle:04d}" / "reviewers"
        if not directory.is_dir():
            return {}
        observations: dict[str, dict[str, Any]] = {}
        for path in sorted(directory.glob("*.json")):
            relative = str(path.relative_to(self._path(task_id)))
            value = self._read_task_json_nofollow(task_id, relative)
            self.schemas.validate("review-observation", value)
            if value.get("task_id") != task_id or value.get("loop_id") != loop_id or value.get("cycle") != cycle or value.get("phase") != "review":
                raise RecoveryRequiredError("review observation identity mismatch")
            reviewer_id = value.get("reviewer_id")
            if not isinstance(reviewer_id, str) or reviewer_id in observations:
                raise RecoveryRequiredError("duplicate or invalid reviewer observation")
            observations[reviewer_id] = value
        return observations

    def _subskill_selection_path(self, task_id: str, cycle: int, phase: str, loop_id: str = "root") -> Path:
        _safe_runtime_id(loop_id, "loop_id")
        if phase not in {"plan", "implement", "review"}:
            raise ValidationError("subskill selection phase is invalid")
        base = "cycles" if loop_id == "root" else f"loops/{loop_id}/cycles"
        return self._path(task_id) / base / f"{cycle:04d}" / "subskills" / f"{phase}-selection.json"

    def _safe_subskill_selection_path(self, task_id: str, cycle: int, phase: str, loop_id: str = "root") -> Path:
        path = self._subskill_selection_path(task_id, cycle, phase, loop_id)
        relative = str(path.relative_to(self._path(task_id)))
        return self._safe_task_relative_path(task_id, relative)

    def _safe_task_relative_path(self, task_id: str, relative: str) -> Path:
        """Resolve a Task file while rejecting traversal and symlink components."""
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise RecoveryRequiredError("Task-relative path escapes the Task root")
        task_root = self._path(task_id).resolve()
        lexical = task_root.joinpath(*relative_path.parts)
        cursor = lexical.parent
        while cursor != task_root:
            if cursor.is_symlink():
                raise RecoveryRequiredError("Task-relative path contains a symlink parent")
            cursor = cursor.parent
        if lexical.is_symlink():
            raise RecoveryRequiredError("Task-relative path points to a symlink")
        resolved = lexical.resolve(strict=False)
        if resolved != task_root and task_root not in resolved.parents:
            raise RecoveryRequiredError("Task-relative path escapes the Task root")
        return lexical

    def _read_task_json_nofollow(self, task_id: str, relative: str) -> dict[str, Any]:
        """Read a Task JSON object through O_NOFOLLOW directory descriptors."""
        path = self._safe_task_relative_path(task_id, relative)
        parts = Path(relative).parts
        if not parts:
            raise RecoveryRequiredError("Task JSON path is empty")
        directory_fd = os.open(self._path(task_id).resolve(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened_directories = [directory_fd]
        try:
            for part in parts[:-1]:
                directory_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
                opened_directories.append(directory_fd)
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
            with os.fdopen(file_fd, "r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, ValueError) as exc:
            raise RecoveryRequiredError(f"Task JSON cannot be safely read: {path}") from exc
        finally:
            for descriptor in reversed(opened_directories):
                os.close(descriptor)
        if not isinstance(value, dict):
            raise RecoveryRequiredError(f"Task JSON must be an object: {path}")
        return value

    def _read_child_state(self, task_id: str, loop_id: str) -> dict[str, Any]:
        safe_loop_id = _safe_runtime_id(loop_id, "loop_id")
        return self._read_task_json_nofollow(task_id, f"loops/{safe_loop_id}/state.json")

    def _validate_subskill_selection_anchor(self, task_id: str, relative: str, value: dict[str, Any]) -> None:
        """Require the append-only event anchor for every v2 selection pin."""
        events_path = self._safe_task_relative_path(task_id, "events.jsonl")
        if not events_path.is_file():
            raise RecoveryRequiredError("subskill selection event journal is missing")
        try:
            events = [json.loads(line) for line in read_text_nofollow(events_path).splitlines() if line.strip()]
        except (OSError, ValueError) as exc:
            raise RecoveryRequiredError("subskill selection event journal cannot be safely read") from exc
        expected_digest = digest_json(value)
        anchors: list[dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict) or not isinstance(event.get("payload"), dict):
                continue
            payload = event["payload"]
            if payload.get("selection_path") == relative and payload.get("selection_id") == value.get("selection_id"):
                anchors.append(payload)
        if not anchors or any(anchor.get("selection_digest") != expected_digest for anchor in anchors):
            raise RecoveryRequiredError("subskill selection does not match its append-only event anchor")

    def _validate_subskill_selection(self, value: dict[str, Any]) -> None:
        schema = "selection-record-v1" if value.get("schema_version") == 1 else "selection-record"
        self.schemas.validate(schema, value)

    def read_subskill_selection(self, task_id: str, cycle: int, phase: str, loop_id: str = "root") -> dict[str, Any] | None:
        """Read the exact phase/cycle pin without changing canonical state."""
        if not isinstance(cycle, int) or cycle < 1:
            raise ValidationError("subskill selection cycle must be positive")
        path = self._safe_subskill_selection_path(task_id, cycle, phase, loop_id)
        if not path.is_file():
            return None
        value = self._read_task_json_nofollow(task_id, str(path.relative_to(self._path(task_id))))
        self._validate_subskill_selection(value)
        if value.get("task_id") != task_id or value.get("phase") != phase:
            raise RecoveryRequiredError("subskill selection identity mismatch")
        if value.get("schema_version") == 1:
            if loop_id != "root":
                raise RecoveryRequiredError("legacy subskill selection cannot be migrated for a child loop")
            return value
        if value.get("loop_id") != loop_id or value.get("cycle") != cycle:
            raise RecoveryRequiredError("subskill selection identity mismatch")
        self._validate_subskill_selection_anchor(task_id, str(path.relative_to(self._path(task_id))), value)
        return value

    def record_subskill_selection(
        self,
        task_id: str,
        expected_revision: int,
        actor: str,
        lease_token: str,
        *,
        selection: dict[str, Any],
        loop_id: str = "root",
    ) -> dict[str, Any]:
        """Persist one deterministic Subskill pin while its phase lease is live."""
        _safe_runtime_id(loop_id, "loop_id")
        self.schemas.validate("selection-record", selection)
        if selection.get("task_id") != task_id or selection.get("loop_id") != loop_id:
            raise ValidationError("subskill selection identity does not match the Task")
        phase = selection.get("phase")
        cycle = selection.get("cycle")
        if not isinstance(phase, str) or not isinstance(cycle, int):
            raise ValidationError("subskill selection phase and cycle are required")
        with self.lock():
            if loop_id == "root":
                self._recover_expired_lease_locked(task_id)
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            observed_state = state
            if loop_id != "root":
                if state["lifecycle_status"] != "active" or state["execution_status"] != "idle" or state.get("current_action") is not None:
                    raise ValidationError("child subskill selection requires an active idle parent checkpoint")
                child_path = self._path(task_id) / "loops" / loop_id / "state.json"
                if not child_path.is_file():
                    raise RecoveryRequiredError(f"child state is missing for subskill selection: {loop_id}")
                observed_state = self._read_child_state(task_id, loop_id)
                self.schemas.validate("task-state", observed_state)
            lease = observed_state.get("active_lease")
            if (
                observed_state.get("phase") != phase
                or observed_state.get("cycle") != cycle
                or observed_state.get("lifecycle_status") != "active"
                or observed_state.get("execution_status") not in {"running", "pausing"}
                or not lease
                or lease.get("owner") != actor
                or lease.get("token") != lease_token
            ):
                raise ValidationError("subskill selection requires the current phase lease")
            if observed_state.get("current_action") is not None:
                raise ConflictError("cannot record a subskill selection while a managed action is live")
            if _parse_time(str(lease["expires_at"])) <= datetime.now(timezone.utc):
                raise RecoveryRequiredError("subskill selection lease has expired")
            path = self._safe_subskill_selection_path(task_id, cycle, phase, loop_id)
            if path.is_file():
                existing = self._read_task_json_nofollow(task_id, str(path.relative_to(self._path(task_id))))
                self._validate_subskill_selection(existing)
                if existing.get("schema_version") == 1:
                    if loop_id != "root":
                        raise RecoveryRequiredError("legacy child subskill selection requires explicit migration")
                    relative = str(path.relative_to(self._path(task_id)))
                    new = self._mutate_locked(
                        task_id,
                        expected_revision=expected_revision,
                        actor=actor,
                        event_type="subskill_selection_migrated",
                        patch={},
                        reason=f"{phase} legacy subskill selection migrated for cycle {cycle}",
                        next_action=f"run {phase} with migrated subskill pin",
                        additional_files={relative: selection},
                        event_payload={
                            "loop_id": loop_id,
                            "phase": phase,
                            "cycle": cycle,
                            "selection_id": selection["selection_id"],
                            "selection_digest": digest_json(selection),
                            "previous_selection_digest": digest_json(existing),
                            "selection_path": relative,
                        },
                    )
                    return {"selection": selection, "state": new}
                if digest_json(existing) != digest_json(selection):
                    raise ConflictError(f"subskill selection already exists with different content: {phase}/{cycle}")
                return {"selection": existing, "state": state}
            relative = str(path.relative_to(self._path(task_id)))
            new = self._mutate_locked(
                task_id,
                expected_revision=expected_revision,
                actor=actor,
                event_type="subskill_selection_recorded",
                patch={},
                reason=f"{phase} subskill pinned for cycle {cycle}",
                next_action=f"run {phase} with pinned subskill",
                additional_files={relative: selection},
                event_payload={"loop_id": loop_id, "phase": phase, "cycle": cycle, "selection_id": selection["selection_id"], "selection_digest": digest_json(selection), "selection_path": relative},
            )
            return {"selection": selection, "state": new}

    def record_review_observation(
        self,
        task_id: str,
        expected_revision: int,
        actor: str,
        lease_token: str,
        *,
        reviewer_id: str,
        observation: dict[str, Any],
        loop_id: str = "root",
    ) -> dict[str, Any]:
        """Persist one cross-reviewer result while the Review lease remains live."""
        _safe_runtime_id(reviewer_id, "reviewer_id")
        _safe_runtime_id(loop_id, "loop_id")
        self.schemas.validate("review-observation", observation)
        if observation.get("task_id") != task_id or observation.get("loop_id") != loop_id or observation.get("reviewer_id") != reviewer_id or observation.get("phase") != "review":
            raise ValidationError("review observation identity does not match the Task")
        with self.lock():
            if loop_id == "root":
                self._recover_expired_lease_locked(task_id)
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            observed_state = state
            if loop_id != "root":
                child_path = self._path(task_id) / "loops" / loop_id / "state.json"
                if not child_path.is_file():
                    raise RecoveryRequiredError(f"child state is missing for review observation: {loop_id}")
                observed_state = self._read_child_state(task_id, loop_id)
                self.schemas.validate("task-state", observed_state)
            lease = observed_state.get("active_lease")
            if observed_state["phase"] != "review" or observed_state["lifecycle_status"] != "active" or observed_state["execution_status"] not in {"running", "pausing"} or not lease or lease.get("owner") != actor or lease.get("token") != lease_token:
                raise ValidationError("review observation requires the current actor's exact active lease token")
            if observed_state.get("current_action") is not None:
                raise ConflictError("cannot record a review observation while a managed action is live")
            if _parse_time(str(lease["expires_at"])) <= datetime.now(timezone.utc):
                raise RecoveryRequiredError("review observation lease has expired")
            relative_base = "cycles" if loop_id == "root" else f"loops/{loop_id}/cycles"
            relative = f"{relative_base}/{observed_state['cycle']:04d}/reviewers/{reviewer_id}.json"
            path = self._path(task_id) / relative
            if path.is_file():
                existing = read_json(path)
                self.schemas.validate("review-observation", existing)
                if digest_json(existing) != digest_json(observation):
                    raise ConflictError(f"review observation already exists with different content: {reviewer_id}")
                return {"observation": existing, "state": state}
            new = self._mutate_locked(
                task_id,
                expected_revision=expected_revision,
                actor=actor,
                event_type="review_observation_recorded",
                patch={},
                reason=f"cross-reviewer {reviewer_id} recorded",
                next_action="complete cross-review aggregation",
                additional_files={relative: observation},
                event_payload={"reviewer_id": reviewer_id, "status": observation["status"], "observation_path": relative},
            )
            return {"observation": observation, "state": new}

    def record_runtime_diagnostic(self, diagnostic: dict[str, Any]) -> dict[str, Any]:
        """Append a redacted provider/validation diagnostic without revision churn.

        Diagnostics are operational evidence, not a phase transition.  They
        therefore live in an append-only task-local JSONL file and do not alter
        the Task State CAS revision.  The schema and secret gate still apply,
        so a malformed or sensitive diagnostic is rejected before persistence.
        """
        if not isinstance(diagnostic, dict):
            raise ValidationError("runtime diagnostic must be an object")
        value = {**diagnostic, "schema_version": 1, "recorded_at": diagnostic.get("recorded_at", now())}
        self.schemas.validate("runtime-diagnostic", value)
        if secret_summary(value):
            raise ValidationError("high-confidence secret blocked from runtime diagnostic")
        task_id = value.get("task_id")
        # ProviderDiagnostic intentionally does not carry task_id in its public
        # shape.  The caller can bind it through the active store path.
        with self.lock():
            if isinstance(task_id, str):
                self._read_task_unlocked(task_id)
            # A store is scoped to one task only when the caller adds task_id;
            # engine diagnostics use the task id as a private envelope field.
            if not isinstance(task_id, str):
                raise ValidationError("runtime diagnostic requires task_id")
            path = self._path(task_id) / "diagnostics.jsonl"
            append_jsonl(path, value)
        return value

    @staticmethod
    def _gateway_outcome(outcome: str) -> str:
        return {"grant": "allow", "deny": "deny", "approval_required": "interrupt_for_approval", "unsupported": "unsupported"}[outcome]

    @staticmethod
    def _action_secret_candidate(action: ActionDescriptor) -> dict[str, Any]:
        """Fields an adapter may propose for canonical action persistence.

        Do not call ``canonical_record`` here: it computes a digest and can
        make a future refactor accidentally regard a credential-bearing value
        as safe to persist.  The gate sees the original proposed values first.
        """
        return {
            "repository_root": action.repository_root,
            "targets": action.targets,
            "command": action.command,
            "network_destinations": action.network_destinations,
            "expected_effect": action.expected_effect,
            "plan_ref": action.plan_ref,
            "plan_content_hash": action.plan_content_hash,
            "environment": action.environment,
            "reservation_ids": action.reservation_ids,
            "resource_ids": action.resource_ids,
            "validation": action.validation,
        }

    def _record_secret_violation_locked(
        self,
        task_id: str,
        state: dict[str, Any],
        actor: str,
        location: str,
        summary: dict[str, object],
        *,
        current_action: dict[str, Any] | None = None,
    ) -> None:
        """Persist only a value-free violation summary, then stop the caller."""
        patch: dict[str, Any] = {}
        if current_action is not None:
            patch = {
                "current_action": None,
                "execution_status": "interrupted",
                "active_lease": None,
                "run_id": None,
                "lease_expires_at": None,
                "last_error": "secret gate blocked action evidence; reconcile before retry",
            }
        self._mutate_locked(
            task_id,
            expected_revision=state["revision"],
            actor=actor,
            event_type="policy_violation",
            patch=patch,
            reason="secret gate blocked canonical persistence",
            next_action="reconcile action" if current_action else "replan without secret-bearing input",
            event_payload={
                "violation": "secret_detected_before_persistence",
                "location_class": location,
                "redaction_summary": summary,
                "action_id": current_action.get("action_id") if current_action else None,
            },
        )

    @staticmethod
    def _require_no_secret(value: Any) -> dict[str, object]:
        return secret_summary(value)

    def _probe_record(self, action_id: str, kind: str, actor: str, probe: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        summary = self._require_no_secret(probe)
        if summary:
            # The caller records a value-free policy_violation under its
            # current State revision.  Never create the evidence artifact.
            raise ValidationError("high-confidence secret blocked from probe evidence")
        evidence_id = f"evidence-{uuid.uuid4().hex}"
        reconcile = kind == "reconcile"
        result = {
            "schema_version": 1,
            "evidence_id": evidence_id,
            "action_id": action_id,
            "original_action_id": action_id if reconcile else None,
            "probe_kind": kind,
            "observed_at": now(),
            "observer_identity": actor,
            "target_fingerprint": str(probe.get("target_fingerprint", "")),
            "result": probe.get("result", "ambiguous"),
            "reconcile_outcome": probe.get("reconcile_outcome") if reconcile else None,
            "facts": dict(probe.get("facts", {})),
            "artifact_refs": list(probe.get("artifact_refs", [])),
            "redaction_summary": dict(probe.get("redaction_summary", {})),
        }
        self.schemas.validate("probe-evidence", result)
        return f"evidence/{evidence_id}.json", result

    def _active_approvals_unlocked(self, task_id: str) -> tuple[Approval, ...]:
        """Read only canonical approvals; never trust adapter-supplied objects."""
        path = self._path(task_id) / "approvals.json"
        if not path.exists():
            return ()
        store = read_json(path)
        records = store.get("approvals", [])
        if not isinstance(records, list):
            raise RecoveryRequiredError("approvals store is malformed")
        active: list[Approval] = []
        for record in records:
            self.schemas.validate("approval", record)
            expiry = _parse_time(record["expires_at"]) if record["expires_at"] else None
            # Gateway receives a small policy view.  Exact scope is checked
            # again against the full canonical record at action-start.
            active.append(
                Approval(
                    record["approval_id"],
                    record["task_id"],
                    record["requested_action_descriptor_ref"]["action_digest"],
                    record["status"],
                    int(record["max_count"]),
                    int(record["consumed_count"]),
                    expiry,
                )
            )
        return tuple(active)

    def _approvals_store_unlocked(self, task_id: str) -> dict[str, Any]:
        path = self._path(task_id) / "approvals.json"
        if not path.exists():
            return {"schema_version": 1, "task_id": task_id, "approvals": []}
        value = read_json(path)
        if value.get("schema_version") != 1 or value.get("task_id") != task_id or not isinstance(value.get("approvals"), list):
            raise RecoveryRequiredError("approvals store is malformed")
        return value

    def _expire_approvals_locked(self, task_id: str, actor: str = "taskctl-recovery") -> dict[str, Any] | None:
        """Mark elapsed requests/approvals expired exactly once before use."""
        _, state, _ = self._read_task_unlocked(task_id)
        approvals = self._approvals_store_unlocked(task_id)
        expired_ids: list[str] = []
        records: list[dict[str, Any]] = []
        current = datetime.now(timezone.utc)
        for record in approvals["approvals"]:
            self.schemas.validate("approval", record)
            if record["status"] in {"requested", "active"} and _parse_time(record["expires_at"]) <= current:
                record = {**record, "status": "expired", "revoked_at": now(), "revoke_reason": "approval expired before action start"}
                expired_ids.append(record["approval_id"])
            records.append(record)
        if not expired_ids:
            return None
        updated = {**approvals, "approvals": records}
        patch = {
            "pending_approval_request_refs": [item for item in state["pending_approval_request_refs"] if item.get("approval_id") not in expired_ids],
            "active_approval_refs": [item for item in state["active_approval_refs"] if item.get("approval_id") not in expired_ids],
        }
        return self._mutate_locked(
            task_id,
            expected_revision=state["revision"],
            actor=actor,
            event_type="approval_expired",
            patch=patch,
            reason="canonical approval expired",
            next_action="request a new exact approval",
            additional_files={"approvals.json": updated},
            event_payload={"approval_ids": expired_ids},
        )

    def expire_approvals(self, task_id: str, actor: str = "taskctl-recovery") -> dict[str, Any] | None:
        with self.lock():
            return self._expire_approvals_locked(task_id, actor)

    def list_approvals(self, task_id: str) -> dict[str, Any]:
        with self.lock():
            self._read_task_unlocked(task_id)
            return self._approvals_store_unlocked(task_id)

    def _exact_approval_matches(self, record: dict[str, Any], action: ActionDescriptor) -> bool:
        """Check the complete immutable exact-action contract before use."""
        if record["approval_mode"] != "exact_action" or record["status"] != "active":
            return False
        if _parse_time(record["expires_at"]) <= datetime.now(timezone.utc):
            return False
        if record["consumed_count"] >= record["max_count"]:
            return False
        snapshot = record["requested_action_descriptor_ref"]
        scope = record["scope"]
        return (
            snapshot["action_id"] == action.action_id
            and snapshot["action_digest"] == action.digest
            and record["task_id"] == action.task_id
            and scope["task_id"] == action.task_id
            and action.loop_id in scope["loop_ids"]
            and action.repository_root in scope["repository_identities"]
            and action.environment in scope["environments"]
            and list(action.targets) == scope["normalized_targets"]
            and action.kind in scope["allowed_action_types"]
            and action.kind not in scope["excluded_action_types"]
            and action.digest in record["approved_action_digests"]
            and action.plan_ref == record["approved_plan_ref"]
            and action.plan_content_hash == record["approved_plan_content_hash"]
            and action.expected_effect == record["expected_effect"]
        )

    def _canonical_approval_for_action_unlocked(self, task_id: str, action: ActionDescriptor) -> dict[str, Any] | None:
        if not action.approval_id:
            return None
        approvals = self._approvals_store_unlocked(task_id)
        for record in approvals["approvals"]:
            self.schemas.validate("approval", record)
            if record["approval_id"] == action.approval_id:
                return record if self._exact_approval_matches(record, action) else None
        return None

    def request_exact_approval(self, task_id: str, expected_revision: int, actor: str, action: ActionDescriptor, expires_at: str | None = None) -> dict[str, Any]:
        """Create a canonical exact-action request; it is not executable yet."""
        with self.lock():
            self._expire_approvals_locked(task_id)
            _safe_runtime_id(action.action_id, "action_id")
            definition, state, _ = self._read_task_unlocked(task_id)
            if action.task_id != task_id or action.expected_state_revision != expected_revision:
                raise ValidationError("approval request must bind the exact task and observed action revision")
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            if state["lifecycle_status"] != "active" or state["execution_status"] != "idle" or state["active_lease"] is not None:
                raise ValidationError("approval request must be created from an active idle checkpoint, not by a running worker")
            secret = self._require_no_secret(self._action_secret_candidate(action))
            if secret:
                self._record_secret_violation_locked(task_id, state, actor, "approval_request", secret)
                raise ValidationError("high-confidence secret blocked from approval request")
            if not action.repository_root or not action.targets:
                raise ValidationError("exact approval requires an in-scope repository root and target")
            if not definition["target_repository"] or Path(definition["target_repository"]).resolve() != Path(action.repository_root).resolve():
                raise ValidationError("approval request repository root must exactly match the canonical task target repository")
            if not definition["target_repository_identity"] or _directory_identity(Path(definition["target_repository"])) != definition["target_repository_identity"]:
                raise ValidationError("approval request target repository identity changed")
            if not action.plan_ref or not action.plan_content_hash:
                raise ValidationError("exact approval requires an immutable approved plan reference and content hash")
            expiry = expires_at or (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
            if _parse_time(expiry) <= datetime.now(timezone.utc):
                raise ValidationError("approval expiry must be in the future")
            approval_id = f"approval-{uuid.uuid4().hex}"
            risk, _ = PolicyGateway().assess_risk(action)
            descriptor = action.canonical_record(risk)
            self.schemas.validate("action", descriptor)
            request_path = f"approval-requests/{approval_id}.json"
            snapshot = {
                "schema_version": 1,
                "approval_id": approval_id,
                "requested_at": now(),
                "action": descriptor,
            }
            self.schemas.validate("approval-request", snapshot)
            question = self._normalize_question(
                task_id,
                {
                    "question": (
                        f"次の操作を実行してよいですか？ 操作: {action.kind}。"
                        f"対象: {', '.join(action.targets)}。想定される影響: {action.expected_effect or '指定された対象だけを変更します。'}"
                    ),
                    "why_now": (
                        f"{action.kind} は対象 {', '.join(action.targets)} に {action.expected_effect or '変更を加える'}ため、"
                        "操作単位の明示的な承認が必要です。"
                    ),
                    "blocking_scope": "action",
                    "blocked_refs": [request_path],
                    "options": [
                        {"option_id": "approve", "label": "この操作だけを承認する", "impact": "この1回・この対象・この計画に限って実行可能にします。想定される影響を受け入れます。"},
                        {"option_id": "deny", "label": "承認せず安全な代替を選ぶ", "impact": "この操作は実行せず、対象や計画を見直す安全な代替を選びます。"},
                    ],
                    "recommended_option_id": "deny",
                    "free_text_allowed": False,
                    "affected_loop_ids": [action.loop_id],
                    "resume_target": {
                        "resume_phase": state["phase"],
                        "resume_transition": "approval_granted",
                        "blocked_action_ref": request_path,
                        "blocked_artifact_ref": None,
                    },
                    "approval_request_ref": request_path,
                },
            )
            question_path = f"human-input/questions/{question['question_id']}.json"
            question_ref = {"artifact_id": question["question_id"], "artifact_type": "human_question", "ref": question_path}
            record = {
                "schema_version": 1,
                "approval_id": approval_id,
                "approval_mode": "exact_action",
                "task_id": task_id,
                "status": "requested",
                "requested_action_descriptor_ref": {
                    "path": request_path,
                    "action_id": action.action_id,
                    "action_digest": action.digest,
                },
                "granted_by": None,
                "granted_at": None,
                "granted_answer_ref": None,
                "expires_at": expiry,
                "revoked_at": None,
                "revoke_reason": None,
                "scope": {
                    "task_id": task_id,
                    "loop_ids": [action.loop_id],
                    "repository_identities": [action.repository_root],
                    "environments": [action.environment],
                    "normalized_targets": list(action.targets),
                    "allowed_action_types": [action.kind],
                    "excluded_action_types": [],
                },
                "max_count": 1,
                "consumed_count": 0,
                "approved_plan_ref": action.plan_ref,
                "approved_plan_content_hash": action.plan_content_hash,
                "approved_action_digests": [action.digest],
                "expected_effect": action.expected_effect,
                "paid_action_bound": None,
                "action_constraints": None,
                "created_from_question_id": question["question_id"],
                "created_at": now(),
            }
            self.schemas.validate("approval", record)
            approvals = self._approvals_store_unlocked(task_id)
            approvals = {**approvals, "approvals": [*approvals["approvals"], record]}
            context = self._read_task_unlocked(task_id)[2]
            context = {
                **context,
                "revision": state["revision"] + 1,
                "open_questions": [*context["open_questions"], question_path],
                "human_question_refs": [*context["human_question_refs"], question_ref],
                "updated_at": now(),
            }
            self.schemas.validate("stable-context", context)
            new = self._mutate_locked(
                task_id,
                expected_revision=expected_revision,
                actor=actor,
                event_type="approval_requested",
                patch={
                    "lifecycle_status": "waiting_human",
                    "execution_status": "idle",
                    "active_lease": None,
                    "run_id": None,
                    "lease_expires_at": None,
                    "waiting_question_ids": [*state["waiting_question_ids"], question["question_id"]],
                    "pending_human_questions": [*state["pending_human_questions"], question_ref],
                    "pending_approval_request_refs": [*state["pending_approval_request_refs"], {"approval_id": approval_id, "path": "approvals.json"}],
                },
                reason="exact action approval requested",
                next_action="wait for immutable human approval answer",
                additional_files={"approvals.json": approvals, request_path: snapshot, question_path: question, "stable-context.json": context},
                event_payload={"approval_id": approval_id, "action_id": action.action_id, "action_digest": action.digest, "approval_request_ref": request_path, "question_ref": question_path},
                additional_events=[("human_input_requested", {"question_id": question["question_id"], "approval_request_ref": request_path})],
            )
            return {"approval": record, "question": question, "state": new}

    def grant_exact_approval(self, task_id: str, expected_revision: int, actor: str, approval_id: str, answer_id: str) -> dict[str, Any]:
        with self.lock():
            self._expire_approvals_locked(task_id)
            _safe_runtime_id(answer_id, "answer_id")
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            answer_path = self._path(task_id) / "human-input" / "answers" / f"{answer_id}.json"
            approvals = self._approvals_store_unlocked(task_id)
            updated_records: list[dict[str, Any]] = []
            selected: dict[str, Any] | None = None
            for record in approvals["approvals"]:
                if record["approval_id"] == approval_id:
                    if record["status"] != "requested":
                        raise ConflictError("approval is not requested")
                    if _parse_time(record["expires_at"]) <= datetime.now(timezone.utc):
                        raise ConflictError("approval request has expired")
                    if not answer_path.exists():
                        raise NotFoundError("approval grant requires an immutable human answer")
                    answer = read_json(answer_path)
                    self.schemas.validate("human-answer", answer)
                    question_id = record["created_from_question_id"]
                    request_path = record["requested_action_descriptor_ref"]["path"]
                    question_path = self._path(task_id) / "human-input" / "questions" / f"{question_id}.json"
                    if not question_id or not question_path.exists():
                        raise RecoveryRequiredError("approval request is missing its immutable human question")
                    question = read_json(question_path)
                    self.schemas.validate("human-question", question)
                    if (
                        answer["question_id"] != question_id
                        or question["approval_request_ref"] != request_path
                        or answer["answer_type"] != "selected_option"
                        or answer["selected_option_id"] != "approve"
                        or answer["answer_effect"] != "resume"
                        or answer["resume_target"] != question["resume_target"]
                        or answer["applied_scope"] != "action"
                    ):
                        raise ConflictError("approval grant requires the matching immutable Human approval answer")
                    selected = {
                        **record,
                        "status": "active",
                        "granted_by": answer["answered_by"],
                        "granted_at": now(),
                        "granted_answer_ref": f"human-input/answers/{answer_id}.json",
                    }
                    self.schemas.validate("approval", selected)
                    record = selected
                updated_records.append(record)
            if not selected:
                raise NotFoundError(f"approval not found: {approval_id}")
            updated = {**approvals, "approvals": updated_records}
            new = self._mutate_locked(task_id, expected_revision=expected_revision, actor=actor, event_type="approval_granted", patch={"pending_approval_request_refs": [item for item in state["pending_approval_request_refs"] if item.get("approval_id") != approval_id], "active_approval_refs": [*state["active_approval_refs"], {"approval_id": approval_id, "path": "approvals.json"}]}, reason="exact action approval granted", next_action=f"re-evaluate action {approval_id}", additional_files={"approvals.json": updated}, event_payload={"approval_id": approval_id, "granted_by": selected["granted_by"], "granted_answer_ref": selected["granted_answer_ref"], "expires_at": selected["expires_at"]})
            return {"approval": selected, "state": new}

    def _set_approval_status(
        self,
        task_id: str,
        expected_revision: int,
        actor: str,
        approval_id: str,
        *,
        from_status: str,
        to_status: str,
        reason: str,
        event_type: str,
    ) -> dict[str, Any]:
        with self.lock():
            self._expire_approvals_locked(task_id)
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            approvals = self._approvals_store_unlocked(task_id)
            selected: dict[str, Any] | None = None
            records: list[dict[str, Any]] = []
            for record in approvals["approvals"]:
                if record["approval_id"] == approval_id:
                    if record["status"] != from_status:
                        raise ConflictError(f"approval is not {from_status}")
                    selected = {
                        **record,
                        "status": to_status,
                        "revoked_at": now(),
                        "revoke_reason": reason,
                    }
                    record = selected
                records.append(record)
            if selected is None:
                raise NotFoundError(f"approval not found: {approval_id}")
            updated = {**approvals, "approvals": records}
            patch = {
                "pending_approval_request_refs": [item for item in state["pending_approval_request_refs"] if item.get("approval_id") != approval_id],
                "active_approval_refs": [item for item in state["active_approval_refs"] if item.get("approval_id") != approval_id],
            }
            new = self._mutate_locked(
                task_id,
                expected_revision=expected_revision,
                actor=actor,
                event_type=event_type,
                patch=patch,
                reason=reason,
                next_action="replan or request a new approval",
                additional_files={"approvals.json": updated},
                event_payload={"approval_id": approval_id, "reason": reason},
            )
            return {"approval": selected, "state": new}

    def deny_exact_approval(self, task_id: str, expected_revision: int, actor: str, approval_id: str, reason: str = "human denied approval") -> dict[str, Any]:
        return self._set_approval_status(task_id, expected_revision, actor, approval_id, from_status="requested", to_status="denied", reason=reason, event_type="approval_denied")

    def revoke_exact_approval(self, task_id: str, expected_revision: int, actor: str, approval_id: str, reason: str = "human revoked approval") -> dict[str, Any]:
        return self._set_approval_status(task_id, expected_revision, actor, approval_id, from_status="active", to_status="revoked", reason=reason, event_type="approval_revoked")

    def _managed_loop_context_unlocked(
        self, definition: dict[str, Any], root_state: dict[str, Any], action: ActionDescriptor, actor: str
    ) -> dict[str, Any]:
        """Resolve the only State/lease/scope allowed for one Action loop."""
        if action.loop_id == "root":
            if root_state["lifecycle_status"] != "active" or root_state["execution_status"] not in {"running", "pausing"}:
                raise ValidationError("managed action requires an active leased task")
            lease = root_state["active_lease"]
            if not lease or lease["owner"] != actor:
                raise ConflictError("managed action actor must own the current task lease")
            declared_root = definition["target_repository"]
            if not declared_root or not action.repository_root or Path(declared_root).resolve() != Path(action.repository_root).resolve():
                raise ValidationError("managed action repository root must exactly match the canonical task target repository")
            if not definition["target_repository_identity"] or _directory_identity(Path(declared_root)) != definition["target_repository_identity"]:
                raise ValidationError("canonical target repository identity changed; replan and create a fresh task boundary")
            cleanup_target = self._validate_root_local_git_cleanup_scope(root_state, action)
            return {
                "loop_state": root_state,
                "loop_state_path": "state.json",
                "lease": lease,
                "repository_root": str(Path(declared_root).resolve()),
                "repository_identity": definition["target_repository_identity"],
                "child_contract_ref": None,
                "reservation_refs": [],
                "resource_ids": [],
                "worktree_ref": None,
                "cleanup_target": cleanup_target,
            }
        entry = next((item for item in root_state["child_loops"] if item.get("loop_id") == action.loop_id), None)
        if not entry or entry.get("status") != "running":
            raise ValidationError("managed child action requires a running materialized child loop")
        child_path = self._path(action.task_id) / "loops" / action.loop_id / "state.json"
        child_state = self._read_child_state(action.task_id, action.loop_id)
        self.schemas.validate("task-state", child_state)
        lease = child_state["active_lease"]
        if child_state["lifecycle_status"] != "active" or child_state["execution_status"] not in {"running", "pausing"} or not lease or lease["owner"] != actor:
            raise ConflictError("managed child action actor must own the current child lease")
        if action.lease_token is not None and action.lease_token != lease["token"]:
            raise ConflictError("managed child action is fenced by a different lease token")
        if _parse_time(lease["expires_at"]) <= datetime.now(timezone.utc):
            raise ConflictError("managed child lease expired; recover before an effect")
        if not entry.get("worktree_ref"):
            # A read-only validation process runs against the disposable
            # ManagedExecutor snapshot.  It does not receive a persistent
            # repository capability, so a worktree is unnecessary.  Every
            # other child effect remains fail-closed until the parent binds
            # an isolated canonical worktree and reservations.
            contract_ref, contract = self._read_child_contract_unlocked(action.task_id, action.loop_id, str(entry["contract_ref"]))
            if action.kind != "process.run" or contract["write_scope"]:
                raise ValidationError("child effects require a canonical isolated worktree binding")
            declared_root = definition["target_repository"]
            if not declared_root or not action.repository_root or Path(declared_root).resolve() != Path(action.repository_root).resolve():
                raise ValidationError("read-only child validation must use the canonical task target repository")
            if not definition["target_repository_identity"] or _directory_identity(Path(declared_root)) != definition["target_repository_identity"]:
                raise ValidationError("canonical target repository identity changed; replan and create a fresh task boundary")
            if action.resource_ids or action.reservation_ids:
                raise ValidationError("read-only child validation cannot bind write resources or reservations")
            return {
                "loop_state": child_state,
                "loop_state_path": str(child_path.relative_to(self._path(action.task_id))),
                "lease": lease,
                "repository_root": str(Path(declared_root).resolve()),
                "repository_identity": definition["target_repository_identity"],
                "child_contract_ref": contract_ref,
                "reservation_refs": [],
                "resource_ids": [],
                "worktree_ref": None,
                "cleanup_target": None,
            }
        worktree_ref = entry["worktree_ref"]
        worktree_path = self._path(action.task_id) / str(worktree_ref["path"])
        worktree = read_json(worktree_path)
        self.schemas.validate("worktree", worktree)
        root = Path(str(worktree["worktree_path"])).resolve()
        if not root.is_dir() or _directory_identity(root) != worktree["worktree_identity"]:
            raise ConflictError("child worktree identity changed; reconcile before retry")
        if not action.repository_root or Path(action.repository_root).resolve() != root:
            raise ValidationError("managed child action must use exactly its canonical worktree root")
        contract_ref, contract = self._read_child_contract_unlocked(action.task_id, action.loop_id, str(entry["contract_ref"]))
        resource_ids = self._validate_child_action_resource_scope(action, contract, root)
        if set(action.resource_ids) != set(resource_ids):
            raise ValidationError("child action must explicitly bind exactly the semantic resources derived from its targets")
        records = {record["reservation_id"]: record for _, record in self._reservation_records_unlocked(action.task_id)}
        required_ids = {
            record["reservation_id"]
            for record in records.values()
            if record["loop_id"] == action.loop_id
            and record["status"] == "held"
            and record["lease_owner"] == actor
            and record["expires_at"]
            and _parse_time(record["expires_at"]) > datetime.now(timezone.utc)
            and record["resource"] in set(contract["write_scope"])
        }
        declared_ids = set(action.reservation_ids)
        if required_ids != declared_ids:
            raise ValidationError("child action must bind exactly every live declared write-resource reservation")
        for reservation_id in declared_ids:
            record = records.get(reservation_id)
            if not record or record["loop_id"] != action.loop_id or record["status"] != "held" or record["lease_owner"] != actor:
                raise ConflictError("child action reservation is not a live lease owned by this worker")
        return {
            "loop_state": child_state,
            "loop_state_path": str(child_path.relative_to(self._path(action.task_id))),
            "lease": lease,
            "repository_root": str(root),
            "repository_identity": worktree["worktree_identity"],
            "child_contract_ref": contract_ref,
            "reservation_refs": [f"reservations/{reservation_id}.json" for reservation_id in sorted(declared_ids)],
            "resource_ids": resource_ids,
            "worktree_ref": worktree_ref,
            "cleanup_target": None,
        }

    def _validate_root_local_git_cleanup_scope(self, state: dict[str, Any], action: ActionDescriptor) -> dict[str, str] | None:
        """Fail closed for cleanup commands that carry paths outside Git's root.

        The generic adapter may expose the narrow local-Git cleanup verbs, but
        a Root action may remove only a worktree/branch that taskctl previously
        bound to this exact task.  This prevents a descriptor from using a
        target-root grant to delete an arbitrary sibling worktree or branch.
        Exact Human approval is still required by PolicyGateway for these Red
        actions.
        """
        if action.kind != "git.local" or not action.command:
            return None
        command = action.command
        worktrees: list[dict[str, Any]] = []
        for entry in state["child_loops"]:
            ref = entry.get("worktree_ref")
            if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
                continue
            path = self._path(action.task_id) / ref["path"]
            if not path.is_file():
                raise RecoveryRequiredError("child worktree binding is missing")
            worktree = read_json(path)
            self.schemas.validate("worktree", worktree)
            worktrees.append(worktree)
        if command[:2] == ("worktree", "remove"):
            if len(command) not in {3, 4} or (len(command) == 4 and command[2] != "--force"):
                raise ValidationError("managed worktree cleanup requires exactly worktree remove [--force] <task worktree>")
            raw_path = command[-1]
            selected = next((item for item in worktrees if Path(raw_path).resolve() == Path(item["worktree_path"]).resolve()), None)
            if selected is None:
                raise ValidationError("managed worktree cleanup path is not a canonical task child worktree")
            worktree_path = Path(selected["worktree_path"])
            if not worktree_path.is_dir() or _directory_identity(worktree_path) != selected["worktree_identity"]:
                raise ConflictError("managed worktree cleanup identity changed; reconcile before deleting")
            return {
                "loop_id": str(selected["loop_id"]),
                "worktree_path": str(worktree_path),
                "worktree_identity": str(selected["worktree_identity"]),
                "branch": str(selected["branch"]),
            }
        if command[:2] in {("branch", "--delete"), ("branch", "-d"), ("branch", "-D")}:
            if len(command) != 3 or not any(command[2] == item["branch"] for item in worktrees):
                raise ValidationError("managed branch cleanup may delete only a canonical task child branch")
        return None

    @staticmethod
    def _relative_child_target(root: Path, target: str) -> str:
        """Normalize one child effect target without allowing a path escape."""
        raw = Path(target)
        candidate = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
        try:
            return candidate.relative_to(root).as_posix() or "."
        except ValueError as exc:
            raise ValidationError("child action target escapes its canonical worktree") from exc

    @staticmethod
    def _resource_matches_child_target(resource: dict[str, Any], target: str) -> bool:
        """Match a concrete repository target to a typed semantic resource.

        A URI alone is deliberately not enough authority to edit a file.  A
        local effect must also resolve to the resource's relative locator and
        the resource must carry a non-ambiguous semantic namespace/entity.
        """
        scope = resource["semantic_scope"]
        if not str(scope["namespace"]).strip() or not str(scope["entity"]).strip():
            return False
        locator = Path(str(resource["locator"]))
        if locator.is_absolute() or ".." in locator.parts:
            return False
        normalized = locator.as_posix() or "."
        kind = resource["kind"]
        if kind == "directory":
            return normalized == "." or target == normalized or target.startswith(normalized.rstrip("/") + "/")
        # File-like contracts must be exact.  Non-filesystem semantic
        # resources cannot authorize a local filesystem/Git mutation.
        return kind in {"file", "schema", "api_contract", "config", "lockfile"} and target == normalized

    def _child_resource_ids_for_targets(
        self, *, kind: str, targets: tuple[str, ...] | list[str], contract: dict[str, Any], root: Path
    ) -> list[str]:
        if kind not in set(contract["allowed_actions"]):
            raise ValidationError("child action kind is not allowed by its immutable contract")
        if kind in set(contract["forbidden_actions"]):
            raise ValidationError("child action kind is forbidden by its immutable contract")
        if not targets:
            raise ValidationError("child action requires at least one declared target")
        write_ids = set(contract["write_scope"])
        resources = {
            item["resource_id"]: item
            for item in contract["resource_contracts"]
            if item["resource_id"] in write_ids
        }
        # A child process in v1 receives only a disposable snapshot, so its
        # repository effects are already fail-closed.  All persistent child
        # effects (filesystem and local Git) must resolve to a write resource.
        if kind not in {"filesystem.write", "git.local"}:
            return []
        if not resources:
            raise ValidationError("child persistent effect requires a declared write resource")
        matched: set[str] = set()
        for raw_target in targets:
            target = self._relative_child_target(root, raw_target)
            candidates = [resource_id for resource_id, resource in resources.items() if self._resource_matches_child_target(resource, target)]
            if len(candidates) != 1:
                raise ValidationError("child action target is not uniquely covered by a declared semantic write resource")
            matched.add(candidates[0])
        return sorted(matched)

    def _validate_child_action_resource_scope(self, action: ActionDescriptor, contract: dict[str, Any], root: Path) -> list[str]:
        return self._child_resource_ids_for_targets(kind=action.kind, targets=action.targets, contract=contract, root=root)

    def begin_managed_action(self, action: ActionDescriptor, actor: str, gateway: PolicyGateway, preflight: dict[str, Any], approvals: tuple[Approval, ...] = ()) -> dict[str, Any]:
        """Persist decision, probe, descriptor, grant, and action_started before effect.

        This is intentionally the only store API an adapter may use to start a
        target-repository side effect.  A duplicate or uncertain action is not
        re-run; it must be reconciled by a separate operation.
        """
        with self.lock():
            self._expire_approvals_locked(action.task_id)
            _safe_runtime_id(action.action_id, "action_id")
            definition, state, _ = self._read_task_unlocked(action.task_id)
            if action.expected_state_revision is None or action.expected_state_revision != state["revision"]:
                raise ConflictError("managed action requires the exact current state revision")
            if state["sync_status"] == "conflicted":
                raise ConflictError("managed effects are blocked while Git durability is conflicted; record a reconciliation/replan outcome")
            loop_context = self._managed_loop_context_unlocked(definition, state, action, actor)
            action_secret_summary = self._require_no_secret(self._action_secret_candidate(action))
            if action_secret_summary:
                self._record_secret_violation_locked(
                    action.task_id,
                    state,
                    actor,
                    "action_descriptor",
                    action_secret_summary,
                )
                raise ValidationError("high-confidence secret blocked from action descriptor")
            action_path = self._path(action.task_id) / "actions" / f"{action.action_id}.json"
            if action_path.exists():
                prior = read_json(action_path)
                raise ConflictError(f"action_id is already durable with status={prior['status']}; reconcile instead of re-executing")
            # Approval records are resolved from the canonical Task store; an
            # adapter must not smuggle an in-memory approval across the trust
            # boundary.  The parameter remains only for transition callers.
            del approvals
            canonical_approval = self._canonical_approval_for_action_unlocked(action.task_id, action)
            decision = gateway.evaluate(action, self._active_approvals_unlocked(action.task_id))
            risk, _ = gateway.assess_risk(action)
            if action.approval_id and canonical_approval is None:
                decision = PolicyDecision(
                    "approval_required",
                    risk,
                    "no active exact canonical approval matches this action",
                    action.digest,
                )
            descriptor = action.canonical_record(risk)
            self.schemas.validate("action", descriptor)
            preflight_secret_summary = self._require_no_secret(preflight)
            if preflight_secret_summary:
                self._record_secret_violation_locked(
                    action.task_id,
                    state,
                    actor,
                    "preflight_probe",
                    preflight_secret_summary,
                )
                raise ValidationError("high-confidence secret blocked from probe evidence")
            preflight_ref, evidence = self._probe_record(action.action_id, "preflight", actor, preflight)
            files: dict[str, dict[str, Any]] = {preflight_ref: evidence}
            if evidence["result"] != "pass":
                decision = PolicyDecision("deny", risk, "required preflight probe did not pass", action.digest)
            record = {"schema_version": 1, "action": descriptor, "status": "started" if decision.outcome == "grant" else "denied", "actor": actor, "expected_state_revision": state["revision"], "grant_id": decision.grant_id, "policy_outcome": decision.outcome, "gateway_outcome": self._gateway_outcome(decision.outcome), "before_evidence_ref": preflight_ref, "after_evidence_ref": None, "idempotency_key": action.idempotency_key, "created_at": now(), "updated_at": now()}
            self.schemas.validate("action-record", record)
            files[f"actions/{action.action_id}.json"] = record
            if decision.outcome != "grant" or not decision.grant_id:
                decision_payload = {"action_id": action.action_id, "action_digest": action.digest, "policy_outcome": decision.outcome, "gateway_outcome": self._gateway_outcome(decision.outcome), "before_probe_ref": preflight_ref, "reason": decision.reason}
                new = self._mutate_locked(
                    action.task_id,
                    expected_revision=state["revision"],
                    actor=actor,
                    event_type="policy_decided",
                    patch={},
                    reason="policy denied managed action",
                    next_action="replan or request approval",
                    additional_files=files,
                    event_payload=decision_payload,
                    additional_events=[("action_denied", decision_payload)],
                )
                return {"decision": decision, "state": new, "action_record": record}
            grant = {
                "schema_version": 1,
                "grant_id": decision.grant_id,
                "request_id": action.request_id,
                "action_id": action.action_id,
                "action_digest": action.digest,
                "expected_state_revision": state["revision"],
                "actor": actor,
                "lease_token": loop_context["lease"]["token"],
                "issued_at": now(),
                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                "single_use": True,
                "consumed": False,
                "consumed_at": None,
                "revocation_reason": None,
                "policy_revision": "v1",
                "loop_id": action.loop_id,
                "child_contract_ref": loop_context["child_contract_ref"],
                "reservation_refs": loop_context["reservation_refs"],
                "worktree_ref": loop_context["worktree_ref"],
                "cleanup_target": loop_context["cleanup_target"],
                "effective_scope": {
                    "repository_root": loop_context["repository_root"],
                    "repository_root_identity": loop_context["repository_identity"],
                    "targets": list(action.targets),
                    "resource_ids": loop_context["resource_ids"],
                    "command": list(action.command) if action.command else None,
                    "environment": action.environment,
                    "network": False,
                },
                "approval_refs": [f"approvals.json#{action.approval_id}"] if action.approval_id else [],
                "policy_reason": decision.reason,
            }
            self.schemas.validate("execution-grant", grant)
            files[f"grants/{decision.grant_id}.json"] = grant
            approval_ref: str | None = None
            if action.approval_id:
                approvals_store = self._approvals_store_unlocked(action.task_id)
                records: list[dict[str, Any]] = []
                found = False
                for approval in approvals_store["approvals"]:
                    if approval["approval_id"] == action.approval_id:
                        if not self._exact_approval_matches(approval, action):
                            raise ConflictError("canonical approval is not active for this exact action")
                        consumed = approval["consumed_count"] + 1
                        approval = {**approval, "consumed_count": consumed, "status": "exhausted" if consumed >= approval["max_count"] else "active"}
                        self.schemas.validate("approval", approval)
                        found = True
                    records.append(approval)
                if not found:
                    raise ConflictError("referenced canonical approval does not exist")
                files["approvals.json"] = {**approvals_store, "approvals": records}
                approval_ref = f"approvals.json#{action.approval_id}"
            start_payload = {"action_id": action.action_id, "action_digest": action.digest, "policy_outcome": decision.outcome, "gateway_outcome": "allow", "execution_grant_ref": f"grants/{decision.grant_id}.json", "approval_ref": approval_ref, "before_probe_ref": preflight_ref}
            action_events: list[tuple[str, dict[str, Any]]] = [
                ("execution_grant_issued", start_payload),
            ]
            if approval_ref:
                action_events.append(("approval_consumed", {"approval_ref": approval_ref, "action_id": action.action_id, "action_digest": action.digest}))
            action_events.append(("action_started", start_payload))
            patch: dict[str, Any] = {}
            if action.loop_id == "root":
                patch["current_action"] = {"action_id": action.action_id, "action_digest": action.digest, "grant_id": decision.grant_id}
            else:
                child_state = {
                    **loop_context["loop_state"],
                    "revision": loop_context["loop_state"]["revision"] + 1,
                    "updated_at": now(),
                    "current_action": {"action_id": action.action_id, "action_digest": action.digest, "grant_id": decision.grant_id},
                }
                child_state, child_checkpoint_path, child_checkpoint = self._checkpoint_child_state(action.task_id, action.loop_id, child_state, "managed child action started", "finish or reconcile child action")
                files[str(loop_context["loop_state_path"])] = child_state
                files[child_checkpoint_path] = child_checkpoint
            new = self._mutate_locked(
                action.task_id,
                expected_revision=state["revision"],
                actor=actor,
                event_type="policy_decided",
                patch=patch,
                reason="managed action started",
                next_action="await managed action completion",
                additional_files=files,
                event_payload=start_payload,
                additional_events=action_events,
            )
            return {"decision": decision, "state": new, "action_record": record, "grant": grant}

    def verify_execution_grant(
        self,
        task_id: str,
        expected_revision: int,
        actor: str,
        lease_token: str,
        action_id: str,
        grant_id: str,
    ) -> dict[str, Any]:
        """Re-check a live grant immediately before an adapter effect.

        This is deliberately a read-only control-plane operation.  Lease
        release, phase recording, and pause acknowledgement all reject a
        live action, so a successful check cannot be invalidated by a normal
        Task State transition before a short atomic adapter operation starts.
        """
        with self.lock():
            self._recover_expired_lease_locked(task_id)
            _safe_runtime_id(action_id, "action_id")
            _, state, _ = self._read_task_unlocked(task_id)
            action_path = self._path(task_id) / "actions" / f"{action_id}.json"
            grant_path = self._path(task_id) / "grants" / f"{grant_id}.json"
            if not action_path.exists() or not grant_path.exists():
                raise RecoveryRequiredError("live action or execution grant is missing")
            record = read_json(action_path)
            grant = read_json(grant_path)
            self.schemas.validate("action-record", record)
            self.schemas.validate("execution-grant", grant)
            loop_id = grant["loop_id"]
            if loop_id == "root":
                if state["revision"] != expected_revision:
                    raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
                lease = state["active_lease"]
                if (
                    state["lifecycle_status"] != "active"
                    or state["execution_status"] not in {"running", "pausing"}
                    or not lease
                    or lease["owner"] != actor
                    or lease["token"] != lease_token
                ):
                    raise ConflictError("live lease does not authorize this managed effect")
                if state["current_action"] is None or state["current_action"].get("action_id") != action_id or state["current_action"].get("grant_id") != grant_id:
                    raise ConflictError("Task State does not identify this as the live managed action")
            else:
                child_path = self._path(task_id) / "loops" / _safe_runtime_id(loop_id, "loop_id") / "state.json"
                child_state = self._read_child_state(task_id, loop_id)
                self.schemas.validate("task-state", child_state)
                lease = child_state["active_lease"]
                if (
                    child_state["lifecycle_status"] != "active"
                    or child_state["execution_status"] not in {"running", "pausing"}
                    or not lease
                    or lease["owner"] != actor
                    or lease["token"] != lease_token
                    or child_state["current_action"] is None
                    or child_state["current_action"].get("action_id") != action_id
                    or child_state["current_action"].get("grant_id") != grant_id
                ):
                    raise ConflictError("live child lease does not authorize this managed effect")
                if _parse_time(lease["expires_at"]) <= datetime.now(timezone.utc):
                    raise ConflictError("live child lease expired before managed effect verification")
                for reservation_ref in grant["reservation_refs"]:
                    reservation = read_json(self._path(task_id) / reservation_ref)
                    self.schemas.validate("reservation", reservation)
                    if reservation["loop_id"] != loop_id or reservation["status"] != "held" or reservation["lease_owner"] != actor or not reservation["expires_at"] or _parse_time(reservation["expires_at"]) <= datetime.now(timezone.utc):
                        raise ConflictError("execution grant child reservation is no longer live")
                entry = next((item for item in state["child_loops"] if item.get("loop_id") == loop_id), None)
                if not entry or str(entry.get("contract_ref")) != str(grant["child_contract_ref"]):
                    raise ConflictError("execution grant child contract is no longer current")
                _, contract = self._read_child_contract_unlocked(task_id, loop_id, str(grant["child_contract_ref"]))
                action_value = record["action"]
                resource_ids = self._child_resource_ids_for_targets(
                    kind=str(action_value["kind"]),
                    targets=list(action_value["targets"]),
                    contract=contract,
                    root=Path(str(grant["effective_scope"]["repository_root"])).resolve(),
                )
                if resource_ids != list(grant["effective_scope"]["resource_ids"]):
                    raise ConflictError("execution grant semantic resource scope no longer matches its action targets")
            if (
                record["status"] != "started"
                or record["actor"] != actor
                or record["grant_id"] != grant_id
                or grant["consumed"]
                or grant["actor"] != actor
                or grant["lease_token"] != lease_token
                or _parse_time(grant["expires_at"]) <= datetime.now(timezone.utc)
            ):
                raise ConflictError("execution grant is no longer live")
            scope = grant["effective_scope"]
            if _directory_identity(Path(scope["repository_root"])) != scope["repository_root_identity"]:
                raise ConflictError("execution grant repository identity is no longer live")
            return {"state": state, "action_record": record, "grant": grant}

    def finish_managed_action(self, task_id: str, expected_revision: int, actor: str, lease_token: str, action_id: str, grant_id: str, returncode: int, postflight: dict[str, Any]) -> dict[str, Any]:
        with self.lock():
            self._recover_expired_lease_locked(task_id)
            _safe_runtime_id(action_id, "action_id")
            _, state, _ = self._read_task_unlocked(task_id)
            action_path = self._path(task_id) / "actions" / f"{action_id}.json"
            if not action_path.exists():
                raise NotFoundError(f"action record not found: {action_id}")
            record = read_json(action_path)
            self.schemas.validate("action-record", record)
            if record["status"] != "started" or record["grant_id"] != grant_id:
                raise ConflictError("action is not a live grant-bound action")
            grant_path = self._path(task_id) / "grants" / f"{grant_id}.json"
            if not grant_path.exists():
                raise RecoveryRequiredError("execution grant is missing")
            grant = read_json(grant_path)
            self.schemas.validate("execution-grant", grant)
            loop_id = grant["loop_id"]
            child_state: dict[str, Any] | None = None
            child_path: Path | None = None
            if loop_id == "root":
                if state["revision"] != expected_revision:
                    raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
                if record["actor"] != actor or state["execution_status"] not in {"running", "pausing"} or not state["active_lease"] or state["active_lease"]["owner"] != actor or state["active_lease"]["token"] != lease_token:
                    raise ConflictError("only the live lease-owning action actor may finish an action")
                if state["current_action"] != {"action_id": action_id, "action_digest": record["action"]["digest"], "grant_id": grant_id}:
                    raise ConflictError("current action does not match the grant")
            else:
                child_path = self._path(task_id) / "loops" / _safe_runtime_id(loop_id, "loop_id") / "state.json"
                child_state = self._read_child_state(task_id, loop_id)
                self.schemas.validate("task-state", child_state)
                if record["actor"] != actor or child_state["execution_status"] not in {"running", "pausing"} or not child_state["active_lease"] or child_state["active_lease"]["owner"] != actor or child_state["active_lease"]["token"] != lease_token:
                    raise ConflictError("only the live child lease-owning action actor may finish an action")
                if _parse_time(child_state["active_lease"]["expires_at"]) <= datetime.now(timezone.utc):
                    raise ConflictError("child lease expired before managed action completion")
                if child_state["current_action"] != {"action_id": action_id, "action_digest": record["action"]["digest"], "grant_id": grant_id}:
                    raise ConflictError("child current action does not match the grant")
                for reservation_ref in grant["reservation_refs"]:
                    reservation = read_json(self._path(task_id) / reservation_ref)
                    self.schemas.validate("reservation", reservation)
                    if reservation["loop_id"] != loop_id or reservation["status"] != "held" or reservation["lease_owner"] != actor or not reservation["expires_at"] or _parse_time(reservation["expires_at"]) <= datetime.now(timezone.utc):
                        raise ConflictError("child reservation expired or changed before action completion")
            scope = grant["effective_scope"]
            if returncode == 0 and _directory_identity(Path(scope["repository_root"])) != scope["repository_root_identity"]:
                raise ConflictError("execution grant repository identity changed; reconcile the started action")
            if grant.get("consumed") or grant.get("actor") != actor or grant.get("lease_token") != lease_token or _parse_time(str(grant["expires_at"])) <= datetime.now(timezone.utc):
                raise ConflictError("execution grant is expired, consumed, or bound to another actor/lease")
            postflight_secret_summary = self._require_no_secret(postflight)
            if postflight_secret_summary:
                reconciled = {
                    **record,
                    "status": "reconciliation_required",
                    "after_evidence_ref": None,
                    "updated_at": now(),
                }
                self.schemas.validate("action-record", reconciled)
                consumed_grant = {**grant, "consumed": True, "consumed_at": now(), "revocation_reason": "secret evidence blocked"}
                self.schemas.validate("execution-grant", consumed_grant)
                if loop_id != "root":
                    # A Child has its own live-action state.  Do not use the
                    # Root-only secret helper here: it would leave the Child
                    # running and make the required reconciliation
                    # unreachable after its grant was revoked.
                    assert child_state is not None and child_path is not None
                    interrupted_child = {
                        **child_state,
                        "revision": child_state["revision"] + 1,
                        "updated_at": now(),
                        "execution_status": "interrupted",
                        "active_lease": None,
                        "run_id": None,
                        "lease_expires_at": None,
                        "last_error": "secret gate blocked child action evidence; reconcile before retry",
                    }
                    interrupted_child, checkpoint_path, checkpoint = self._checkpoint_child_state(
                        task_id,
                        loop_id,
                        interrupted_child,
                        "secret-blocked child action requires reconciliation",
                        "reconcile child action",
                    )
                    self._mutate_locked(
                        task_id,
                        expected_revision=state["revision"],
                        actor=actor,
                        event_type="policy_violation",
                        patch={},
                        reason="secret-blocked child action requires reconciliation",
                        next_action="reconcile child action",
                        additional_files={
                            f"actions/{action_id}.json": reconciled,
                            f"grants/{grant_id}.json": consumed_grant,
                            str(child_path.relative_to(self._path(task_id))): interrupted_child,
                            checkpoint_path: checkpoint,
                        },
                        event_payload={
                            "violation": "secret_detected_before_persistence",
                            "location_class": "postflight_probe",
                            "redaction_summary": postflight_secret_summary,
                            "action_id": action_id,
                        },
                        additional_events=[
                            (
                                "execution_grant_revoked",
                                {
                                    "action_id": action_id,
                                    "action_digest": record["action"]["digest"],
                                    "execution_grant_ref": f"grants/{grant_id}.json",
                                    "reason": "secret evidence blocked",
                                },
                            )
                        ],
                    )
                    raise ValidationError("high-confidence secret blocked from child postflight evidence; action requires reconciliation")
                self._record_secret_violation_locked(
                    task_id,
                    state,
                    actor,
                    "postflight_probe",
                    postflight_secret_summary,
                    current_action=state["current_action"],
                )
                # The policy event above has durably made the action
                # interrupted.  Persist the now-ambiguous action and revoke
                # its grant in a second recovery-safe transaction; the raw
                # evidence itself is never written.
                latest = self._read_task_unlocked(task_id)[1]
                self._mutate_locked(
                    task_id,
                    expected_revision=latest["revision"],
                    actor=actor,
                    event_type="execution_grant_revoked",
                    patch={},
                    reason="secret-blocked action requires reconciliation",
                    next_action="reconcile action",
                    additional_files={
                        f"actions/{action_id}.json": reconciled,
                        f"grants/{grant_id}.json": consumed_grant,
                    },
                    event_payload={
                        "action_id": action_id,
                        "action_digest": record["action"]["digest"],
                        "execution_grant_ref": f"grants/{grant_id}.json",
                        "reason": "secret evidence blocked",
                    },
                )
                raise ValidationError("high-confidence secret blocked from postflight evidence; action requires reconciliation")
            postflight_ref, evidence = self._probe_record(action_id, "postcheck", actor, postflight)
            outcome = "completed" if returncode == 0 and evidence["result"] == "pass" else "failed"
            updated = {**record, "status": outcome, "after_evidence_ref": postflight_ref, "updated_at": now()}
            self.schemas.validate("action-record", updated)
            consumed_grant = {**grant, "consumed": True, "consumed_at": now()}
            self.schemas.validate("execution-grant", consumed_grant)
            completion_payload = {"action_id": action_id, "action_digest": record["action"]["digest"], "execution_grant_ref": f"grants/{grant_id}.json", "after_probe_ref": postflight_ref, "returncode": returncode}
            additional_events: list[tuple[str, dict[str, Any]]] = [
                ("execution_grant_revoked", {**completion_payload, "reason": "action finished"}),
            ]
            if evidence["facts"].get("reason") == "process wrote inside isolated workspace":
                additional_events.append(("policy_violation", {**completion_payload, "violation": "runtime_write_scope_escape"}))
            files: dict[str, dict[str, Any]] = {f"actions/{action_id}.json": updated, postflight_ref: evidence, f"grants/{grant_id}.json": consumed_grant}
            patch: dict[str, Any] = {"current_action": None} if loop_id == "root" else {}
            if child_state is not None and child_path is not None:
                child_state = {**child_state, "revision": child_state["revision"] + 1, "updated_at": now(), "current_action": None}
                child_state, checkpoint_path, checkpoint = self._checkpoint_child_state(task_id, loop_id, child_state, f"managed child action {outcome}", "continue child contract" if outcome == "completed" else "reconcile child action")
                files[str(child_path.relative_to(self._path(task_id)))] = child_state
                files[checkpoint_path] = checkpoint
            new = self._mutate_locked(task_id, expected_revision=state["revision"], actor=actor, event_type="action_completed" if outcome == "completed" else "action_failed", patch=patch, reason=f"managed action {outcome}", next_action=f"run {state['phase']}" if outcome == "completed" else "reconcile or replan", additional_files=files, event_payload=completion_payload, additional_events=additional_events)
            return {"state": new, "action_record": updated, "postflight_ref": postflight_ref}

    def reconcile_managed_action(
        self,
        task_id: str,
        expected_revision: int,
        actor: str,
        action_id: str,
        reconciliation: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve a started action after interruption without re-executing it.

        A reconciliation is an observation, not permission to retry an
        uncertain effect.  ``not_started_safe_to_retry`` is recorded as a
        retryable state for a later fresh Action request; all other uncertain
        outcomes block or repair-plan the Task instead of silently rerunning.
        """
        with self.lock():
            self._recover_expired_lease_locked(task_id)
            _safe_runtime_id(action_id, "action_id")
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            action_path = self._path(task_id) / "actions" / f"{action_id}.json"
            if not action_path.exists():
                raise NotFoundError(f"action record not found: {action_id}")
            record = read_json(action_path)
            self.schemas.validate("action-record", record)
            if record["status"] not in {"started", "reconciliation_required"}:
                raise ConflictError("only an incomplete action can be reconciled")
            loop_id = record["action"]["loop_id"]
            child_state: dict[str, Any] | None = None
            child_path: Path | None = None
            if loop_id == "root":
                if state["execution_status"] != "interrupted":
                    raise ValidationError("root action reconciliation requires an interrupted task")
            else:
                child_path = self._path(task_id) / "loops" / _safe_runtime_id(loop_id, "loop_id") / "state.json"
                child_state = self._read_child_state(task_id, loop_id)
                self.schemas.validate("task-state", child_state)
                if child_state["execution_status"] != "interrupted" or not child_state["current_action"] or child_state["current_action"].get("action_id") != action_id:
                    raise ValidationError("child action reconciliation requires its interrupted current action")
            summary = self._require_no_secret(reconciliation)
            if summary:
                self._record_secret_violation_locked(task_id, state, actor, "reconcile_probe", summary, current_action=state["current_action"] if loop_id == "root" else None)
                raise ValidationError("high-confidence secret blocked from reconciliation evidence")
            evidence_ref, evidence = self._probe_record(action_id, "reconcile", actor, reconciliation)
            outcome = evidence["reconcile_outcome"]
            if outcome not in {"completed_verified", "not_started_safe_to_retry", "partial_requires_repair", "ambiguous_requires_human"}:
                raise ValidationError("reconciliation evidence requires a typed reconcile outcome")
            if outcome == "completed_verified" and evidence["result"] != "pass":
                raise ValidationError("completed_verified reconciliation requires a passing probe")
            if outcome == "not_started_safe_to_retry" and evidence["result"] != "pass":
                raise ValidationError("safe retry reconciliation requires a passing probe")
            status = "completed" if outcome == "completed_verified" else "reconciled"
            updated = {**record, "status": status, "after_evidence_ref": evidence_ref, "updated_at": now()}
            self.schemas.validate("action-record", updated)
            files: dict[str, dict[str, Any]] = {f"actions/{action_id}.json": updated, evidence_ref: evidence}
            grant_id = record["grant_id"]
            if grant_id:
                grant_path = self._path(task_id) / "grants" / f"{grant_id}.json"
                if grant_path.exists():
                    grant = read_json(grant_path)
                    self.schemas.validate("execution-grant", grant)
                    files[f"grants/{grant_id}.json"] = {**grant, "consumed": True, "consumed_at": grant.get("consumed_at") or now(), "revocation_reason": f"reconciled:{outcome}"}
                    self.schemas.validate("execution-grant", files[f"grants/{grant_id}.json"])
            patch: dict[str, Any] = {"current_action": None, "execution_status": "idle", "last_error": None} if loop_id == "root" else {}
            next_action = f"run {state['phase']}"
            if outcome == "partial_requires_repair":
                if loop_id == "root":
                    patch.update({"lifecycle_status": "blocked", "last_error": "action partially applied; repair plan required"})
                next_action = "create repair plan"
            elif outcome == "ambiguous_requires_human":
                if loop_id == "root":
                    patch.update({"lifecycle_status": "blocked", "last_error": "action outcome ambiguous; human decision required"})
                next_action = "request human reconciliation decision"
            event_payload = {
                "action_id": action_id,
                "action_digest": record["action"]["digest"],
                "reconcile_evidence_ref": evidence_ref,
                "reconcile_outcome": outcome,
            }
            additional = [("execution_grant_revoked", {**event_payload, "reason": f"reconciled:{outcome}"})] if grant_id else []
            if child_state is not None and child_path is not None:
                child_patch = {"current_action": None, "execution_status": "idle", "active_lease": None, "run_id": None, "lease_expires_at": None, "last_error": None}
                child_status = "ready"
                if outcome in {"partial_requires_repair", "ambiguous_requires_human"}:
                    child_patch.update({"lifecycle_status": "blocked", "last_error": "child action reconciliation requires repair or Human decision"})
                    child_status = "failed"
                child_state = {**child_state, **child_patch, "revision": child_state["revision"] + 1, "updated_at": now()}
                child_state, checkpoint_path, checkpoint = self._checkpoint_child_state(task_id, loop_id, child_state, f"child action reconciled: {outcome}", next_action)
                files[str(child_path.relative_to(self._path(task_id)))] = child_state
                files[checkpoint_path] = checkpoint
                patch["child_loops"] = [({**item, "status": child_status} if item.get("loop_id") == loop_id else item) for item in state["child_loops"]]
            new = self._mutate_locked(
                task_id,
                expected_revision=state["revision"],
                actor=actor,
                event_type="action_reconciled",
                patch=patch,
                reason=f"managed action reconciled: {outcome}",
                next_action=next_action,
                additional_files=files,
                event_payload=event_payload,
                additional_events=additional,
            )
            return {"state": new, "action_record": updated, "reconcile_evidence_ref": evidence_ref}

    @staticmethod
    def _knowledge_content_hash(record: dict[str, Any]) -> str:
        """Hash the immutable semantic record, never a mutable container."""
        return digest_json({key: value for key, value in record.items() if key != "content_hash"})

    @staticmethod
    def _knowledge_paths(loop_id: str) -> tuple[str, str, str]:
        if loop_id == "root":
            prefix = "knowledge"
        else:
            _safe_runtime_id(loop_id, "loop_id")
            prefix = f"loops/{loop_id}/knowledge"
        return (f"{prefix}/questions.json", f"{prefix}/sources.json", f"{prefix}/claims.json")

    def _read_knowledge_unlocked(self, task_id: str, loop_id: str = "root") -> dict[str, list[dict[str, Any]]]:
        paths = self._knowledge_paths(loop_id)
        names = ("knowledge-questions", "knowledge-sources", "knowledge-claims")
        keys = ("questions", "sources", "claims")
        result: dict[str, list[dict[str, Any]]] = {}
        for relative, name, key in zip(paths, names, keys, strict=True):
            path = self._path(task_id) / relative
            if not path.exists():
                result[key] = []
                continue
            value = read_json(path)
            self.schemas.validate(name, value)
            if value["task_id"] != task_id or value["loop_id"] != loop_id:
                raise RecoveryRequiredError("knowledge store identity does not match task/loop")
            result[key] = list(value[key])
        return result

    def read_knowledge(self, task_id: str, loop_id: str = "root") -> dict[str, list[dict[str, Any]]]:
        with self.lock():
            self._read_task_unlocked(task_id)
            return self._read_knowledge_unlocked(task_id, loop_id)

    @staticmethod
    def _normalize_knowledge_question(value: dict[str, Any]) -> dict[str, Any]:
        record = {
            "question_id": str(value.get("question_id", "")),
            "question": str(value.get("question", "")),
            "reason": str(value.get("reason", "")),
            "impact": value.get("impact", "medium"),
            "required_before": value.get("required_before", "plan_complete"),
            "status": value.get("status", "open"),
            # An answered Knowledge Question is a claim about the world, not
            # an unchecked status switch.  Pin the canonical source/claim
            # records that justify it so a fresh process can re-evaluate the
            # gate without parsing a Markdown explanation.
            "answer_evidence": list(value.get("answer_evidence", value.get("answer_evidence_refs", []))),
            "record_revision": value.get("record_revision", 1),
            "supersedes": list(value.get("supersedes", [])),
        }
        return {**record, "content_hash": TaskStore._knowledge_content_hash(record)}

    @staticmethod
    def _normalize_knowledge_source(value: dict[str, Any]) -> dict[str, Any]:
        record = {
            "source_id": str(value.get("source_id", "")),
            "title": str(value.get("title", "")),
            "locator": str(value.get("locator", "")),
            "type": str(value.get("type", value.get("source_type", "other"))),
            "authority": str(value.get("authority", "")),
            "version_or_commit": value.get("version_or_commit"),
            "section_or_lines": value.get("section_or_lines"),
            "published_at": value.get("published_at"),
            "retrieved_at": value.get("retrieved_at"),
            # Keep the observed source bytes and the immutable source-record
            # envelope distinct.  Knowledge references pin the latter, while
            # provenance can still prove exactly which collected content was
            # evaluated.
            "source_content_hash": value.get("source_content_hash", value.get("content_hash")),
            "volatility": value.get("volatility", "medium"),
            "supports": list(value.get("supports", [])),
            "contradicts": list(value.get("contradicts", [])),
            "reliability_notes": str(value.get("reliability_notes", "")),
            "record_revision": value.get("record_revision", 1),
            "supersedes": list(value.get("supersedes", [])),
        }
        return {**record, "content_hash": TaskStore._knowledge_content_hash(record)}

    @staticmethod
    def _normalize_knowledge_claim(value: dict[str, Any]) -> dict[str, Any]:
        legacy_status = {"confirmed": "verified", "probable": "inferred", "uncertain": "hypothesis"}
        record = {
            "claim_id": str(value.get("claim_id", "")),
            "claim": str(value.get("claim", value.get("text", ""))),
            "kind": value.get("kind", "fact"),
            "epistemic_status": legacy_status.get(value.get("epistemic_status"), value.get("epistemic_status", "hypothesis")),
            "lifecycle_status": value.get("lifecycle_status", "active"),
            "scope": dict(value.get("scope", {})),
            "as_of": value.get("as_of", value.get("checked_at")),
            "supports": list(value.get("supports", value.get("source_ids", []))),
            "contradicts": list(value.get("contradicts", [])),
            "derived_from": list(value.get("derived_from", [])),
            "confidence": value.get("confidence", "medium"),
            "owner": value.get("owner", "plan"),
            "revalidate_when": value.get("revalidate_when"),
            "notes": str(value.get("notes", "")),
            "record_revision": value.get("record_revision", 1),
            "supersedes": list(value.get("supersedes", [])),
        }
        return {**record, "content_hash": TaskStore._knowledge_content_hash(record)}

    @staticmethod
    def _merge_immutable_records(
        existing: list[dict[str, Any]], incoming: list[dict[str, Any]], identifier: str
    ) -> list[dict[str, Any]]:
        merged = {str(item[identifier]): item for item in existing}
        for item in incoming:
            item_id = str(item[identifier])
            if item_id in merged and merged[item_id] != item:
                raise ConflictError(f"knowledge {identifier} is immutable; create a superseding record instead")
            merged[item_id] = item
        if len(merged) != len(existing) + len([item for item in incoming if item[identifier] not in {old[identifier] for old in existing}]):
            # A repeated new ID in one request is never an idempotent rewrite:
            # rejecting it makes producer mistakes visible before persistence.
            seen = [str(item[identifier]) for item in incoming]
            if len(seen) != len(set(seen)):
                raise ValidationError(f"knowledge request repeats {identifier}")
        return [merged[key] for key in sorted(merged)]

    def record_knowledge(
        self,
        task_id: str,
        expected_revision: int,
        actor: str,
        *,
        questions: list[dict[str, Any]] | None = None,
        sources: list[dict[str, Any]] | None = None,
        claims: list[dict[str, Any]] | None = None,
        loop_id: str = "root",
        transaction_id: str | None = None,
    ) -> dict[str, Any]:
        """Commit the three canonical Knowledge stores in one Task transaction.

        Producers may submit only new immutable records.  They never write a
        canonical JSON file themselves, and a stale state revision fails before
        any Knowledge view can be generated.
        """
        with self.lock():
            _, state, context = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            if state["lifecycle_status"] in {"completed", "cancelled"}:
                raise ValidationError("cannot record knowledge for a terminal task")
            if loop_id != "root" and not any(item.get("loop_id") == loop_id for item in state["child_loops"]):
                raise ValidationError("knowledge loop is not a materialized child loop")
            candidate = {"questions": questions or [], "sources": sources or [], "claims": claims or []}
            if secret_summary(candidate):
                self._record_secret_violation_locked(task_id, state, actor, "knowledge", secret_summary(candidate))
                raise ValidationError("high-confidence secret blocked from canonical knowledge")
            current = self._read_knowledge_unlocked(task_id, loop_id)
            new_questions = [self._normalize_knowledge_question(item) for item in (questions or [])]
            new_sources = [self._normalize_knowledge_source(item) for item in (sources or [])]
            new_claims = [self._normalize_knowledge_claim(item) for item in (claims or [])]
            merged_questions = self._merge_immutable_records(current["questions"], new_questions, "question_id")
            merged_sources = self._merge_immutable_records(current["sources"], new_sources, "source_id")
            merged_claims = self._merge_immutable_records(current["claims"], new_claims, "claim_id")
            source_ids = {item["source_id"] for item in merged_sources}
            claim_ids = {item["claim_id"] for item in merged_claims}
            question_ids = {item["question_id"] for item in merged_questions}
            for question in merged_questions:
                if not set(question["supersedes"]).issubset(question_ids - {question["question_id"]}):
                    raise ValidationError("knowledge question supersedes relation references an unknown question")
                evidence = question["answer_evidence"]
                if question["status"] == "answered" and not evidence:
                    raise ValidationError("answered Knowledge Question requires pinned source or claim evidence")
                seen_evidence: set[tuple[str, str]] = set()
                for reference in evidence:
                    if not isinstance(reference, dict):
                        raise ValidationError("Knowledge Question answer evidence must be typed")
                    record_type = reference.get("record_type")
                    record_id = reference.get("record_id")
                    content_hash = reference.get("content_hash")
                    key = (str(record_type), str(record_id))
                    if key in seen_evidence:
                        raise ValidationError("Knowledge Question answer evidence must not repeat a record")
                    seen_evidence.add(key)
                    if record_type == "source":
                        record = next((item for item in merged_sources if item["source_id"] == record_id), None)
                    elif record_type == "claim":
                        record = next((item for item in merged_claims if item["claim_id"] == record_id), None)
                    else:
                        raise ValidationError("Knowledge Question answer evidence must reference a source or claim")
                    if not record or record.get("content_hash") != content_hash:
                        raise ValidationError("Knowledge Question answer evidence does not pin a canonical record hash")
                if question["status"] == "answered":
                    source_refs = {str(reference["record_id"]) for reference in evidence if reference["record_type"] == "source"}
                    claim_refs = {str(reference["record_id"]) for reference in evidence if reference["record_type"] == "claim"}
                    if not source_refs or not claim_refs:
                        raise ValidationError("answered Knowledge Question requires both a pinned Claim and its Source evidence")
                    if not any(
                        set(claim["supports"]) & source_refs
                        for claim in merged_claims
                        if claim["claim_id"] in claim_refs
                    ):
                        raise ValidationError("answered Knowledge Question Claim evidence must be supported by its pinned Source")
            for source in merged_sources:
                if not set(source["supersedes"]).issubset(source_ids - {source["source_id"]}):
                    raise ValidationError("knowledge source supersedes relation references an unknown source")
            for claim in merged_claims:
                if not set(claim["supports"]).issubset(source_ids):
                    raise ValidationError("knowledge claim support references an unknown source")
                if not set(claim["contradicts"]).issubset(claim_ids) or not set(claim["derived_from"]).issubset(claim_ids) or not set(claim["supersedes"]).issubset(claim_ids - {claim["claim_id"]}):
                    raise ValidationError("knowledge claim relation references an unknown claim")
            txid = transaction_id or f"tx-{uuid.uuid4().hex}"
            if not txid.startswith("tx-"):
                raise ValidationError("transaction_id must use the canonical tx- prefix")
            revision = state["revision"] + 1
            timestamp = now()
            qpath, spath, cpath = self._knowledge_paths(loop_id)
            stores = {
                qpath: {"schema_version": 1, "task_id": task_id, "loop_id": loop_id, "revision": revision, "transaction_id": txid, "questions": merged_questions, "updated_at": timestamp},
                spath: {"schema_version": 1, "task_id": task_id, "loop_id": loop_id, "revision": revision, "transaction_id": txid, "sources": merged_sources, "updated_at": timestamp},
                cpath: {"schema_version": 1, "task_id": task_id, "loop_id": loop_id, "revision": revision, "transaction_id": txid, "claims": merged_claims, "updated_at": timestamp},
            }
            for name, value in zip(("knowledge-questions", "knowledge-sources", "knowledge-claims"), stores.values(), strict=True):
                self.schemas.validate(name, value)
            if loop_id == "root":
                superseded_claim_ids = {identifier for claim in merged_claims for identifier in claim["supersedes"]}
                active_refs = [f"knowledge/claims.json#{claim['claim_id']}:{claim['content_hash']}" for claim in merged_claims if claim["lifecycle_status"] == "active" and claim["claim_id"] not in superseded_claim_ids]
                context = {**context, "revision": revision, "knowledge_claim_refs": active_refs, "updated_at": timestamp}
                self.schemas.validate("stable-context", context)
                stores["stable-context.json"] = context
            new = self._mutate_locked(
                task_id,
                expected_revision=expected_revision,
                actor=actor,
                event_type="knowledge_recorded",
                patch={},
                reason="canonical knowledge records committed",
                next_action=f"run {state['phase']}",
                additional_files=stores,
                event_payload={"loop_id": loop_id, "question_count": len(merged_questions), "source_count": len(merged_sources), "claim_count": len(merged_claims)},
                transaction_id=txid,
            )
            return {"state": new, "knowledge": {"questions": merged_questions, "sources": merged_sources, "claims": merged_claims}}

    @staticmethod
    def _resource_overlaps(first: str, second: str) -> bool:
        """Conservative canonical resource collision check.

        Resource strings are deliberately URI-only.  If the scheduler cannot
        prove separation (same scheme plus equal/prefix locator), it reserves
        sequentially rather than guessing that a concurrent write is safe.
        """
        if "://" not in first or "://" not in second:
            return True
        first_scheme, first_locator = first.split("://", 1)
        second_scheme, second_locator = second.split("://", 1)
        if first_scheme != second_scheme:
            return False
        first_locator = first_locator.rstrip("/")
        second_locator = second_locator.rstrip("/")
        return (
            first_locator == second_locator
            or first_locator.startswith(second_locator + "/")
            or second_locator.startswith(first_locator + "/")
        )

    @staticmethod
    def _reservations_conflict(first: dict[str, Any], second: dict[str, Any]) -> bool:
        if not TaskStore._resource_overlaps(str(first["resource"]), str(second["resource"])):
            return False
        modes = {str(first["mode"]), str(second["mode"])}
        if "advisory" in modes:
            return False
        if modes == {"shared_read"}:
            return False
        if "shared_read" in modes:
            reader = first if first["mode"] == "shared_read" else second
            # A pinned immutable input can coexist with a later write.  An
            # unpinned read is an ambiguity and is held back conservatively.
            return not bool(reader["input_version_ref"])
        return True

    def _read_child_contract_unlocked(self, task_id: str, loop_id: str, contract_ref: str | None = None) -> tuple[str, dict[str, Any]]:
        _safe_runtime_id(loop_id, "loop_id")
        if contract_ref is None:
            state_path = self._path(task_id) / "loops" / loop_id / "state.json"
            if not state_path.exists():
                raise NotFoundError(f"child loop not found: {loop_id}")
            child_state = self._read_child_state(task_id, loop_id)
            self.schemas.validate("task-state", child_state)
            ref = child_state.get("active_contract_ref")
            if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
                raise RecoveryRequiredError("child loop has no active immutable contract")
            contract_ref = ref["path"]
        path = self._path(task_id) / contract_ref
        if not path.is_file() or self._path(task_id) not in path.resolve().parents:
            raise RecoveryRequiredError("child contract ref escaped task storage")
        contract = read_json(path)
        self.schemas.validate("child-contract", contract)
        if contract["task_id"] != task_id or contract["loop_id"] != loop_id:
            raise RecoveryRequiredError("child contract identity mismatch")
        return contract_ref, contract

    def read_child_contract(
        self, task_id: str, loop_id: str, contract_ref: str | None = None
    ) -> tuple[str, dict[str, Any]]:
        """Read an immutable child contract through the public store boundary."""
        with self.lock():
            self._read_task_unlocked(task_id)
            return self._read_child_contract_unlocked(task_id, loop_id, contract_ref)

    def _checkpoint_child_state(
        self, task_id: str, loop_id: str, state: dict[str, Any], reason: str, next_action: str
    ) -> tuple[dict[str, Any], str, dict[str, Any]]:
        """Return a child State and checkpoint that can be committed together.

        Child snapshots are never handwritten after the parent transaction.
        This keeps a fresh-process Child resume packet tied to the same atomic
        root journal transaction that changed its scheduler lifecycle.
        """
        checkpoint_id = f"ckpt-{uuid.uuid4().hex}"
        relative = f"loops/{loop_id}/checkpoints/latest.json"
        updated = {
            **state,
            "last_checkpoint_id": checkpoint_id,
            "latest_checkpoint_ref": {"path": relative, "revision": state["revision"]},
        }
        self.schemas.validate("task-state", updated)
        checkpoint = {
            "schema_version": 1,
            "checkpoint_id": checkpoint_id,
            "task_id": task_id,
            "revision": updated["revision"],
            "created_at": now(),
            "phase": updated["phase"],
            "cycle": updated["cycle"],
            "reason": reason,
            "state_digest": digest_json(updated),
            "next_safe_action": next_action,
        }
        self.schemas.validate("checkpoint", checkpoint)
        return updated, relative, checkpoint

    def _validate_knowledge_references_unlocked(self, task_id: str, references: list[dict[str, Any]]) -> None:
        knowledge = self._read_knowledge_unlocked(task_id)
        collections = {"question": ("questions", "question_id"), "source": ("sources", "source_id"), "claim": ("claims", "claim_id")}
        for reference in references:
            if not isinstance(reference, dict) or reference.get("record_type") not in collections:
                raise ValidationError("Knowledge Reference must identify a typed canonical record")
            collection, identifier = collections[reference["record_type"]]
            record = next((item for item in knowledge[collection] if item[identifier] == reference.get("record_id")), None)
            if not record or record.get("content_hash") != reference.get("content_hash"):
                raise ValidationError("Knowledge Reference does not pin a current canonical record hash")

    @staticmethod
    def _child_contract_payload(task_id: str, raw: dict[str, Any], revision: int, previous_contract_ref: str | None) -> dict[str, Any]:
        loop_id = _safe_runtime_id(str(raw.get("loop_id", "")), "loop_id")
        if loop_id == "root":
            raise ValidationError("root is not a child loop identifier")
        timestamp = now()
        resources = list(raw.get("resources", []))
        write_scope = list(raw.get("write_scope", []))
        def derived_resource(resource_id: str) -> dict[str, Any]:
            scheme, locator = resource_id.split("://", 1) if "://" in resource_id else ("invalid", resource_id)
            kind = {"repo": "file", "db-schema": "schema", "api": "api_contract", "config": "config", "service": "external_service", "git": "git_ref"}.get(scheme, "file")
            return {"resource_id": resource_id, "kind": kind, "locator": locator, "semantic_scope": {"namespace": scheme, "entity": locator, "facet": ""}, "parent_resource_ref": None, "aliases": [], "version_ref": None, "conflict_class": "exclusive" if resource_id in write_scope else "advisory", "merge_strategy": "sequential", "sensitivity": "normal"}
        resource_contracts = list(raw.get("resource_contracts", [])) or [derived_resource(resource) for resource in resources]
        resource_requests = list(raw.get("resource_requests", [])) or [
            {"resource_ref": resource, "requested_mode": "write" if resource in write_scope else "read", "input_version_ref": None, "required_reservation": "exclusive_write" if resource in write_scope else "shared_read", "expected_change": "declared child change" if resource in write_scope else "read-only input", "integration_strategy": str(raw.get("integration_strategy", "record output for parent integration review"))}
            for resource in resources
        ]
        allowed_actions = list(raw.get("allowed_actions", ["filesystem.read"]))
        if write_scope and "filesystem.write" not in allowed_actions:
            allowed_actions.append("filesystem.write")
        if write_scope and "git.local" not in allowed_actions:
            allowed_actions.append("git.local")
        value = {
            "schema_version": 1,
            "contract_id": str(raw.get("contract_id") or f"contract-{loop_id}-{revision:04d}"),
            "task_id": task_id,
            "loop_id": loop_id,
            "parent_loop_id": str(raw.get("parent_loop_id", "root")),
            "revision": revision,
            "previous_contract_ref": previous_contract_ref,
            "objective": str(raw.get("objective", "")),
            "scope": list(raw.get("scope", raw.get("write_scope") or raw.get("resources") or [])),
            "non_goals": list(raw.get("non_goals", [])),
            "dependencies": list(raw.get("dependencies", raw.get("depends_on", []))),
            "required_inputs": list(raw.get("required_inputs", [])),
            "input_versions": list(raw.get("input_versions", [])),
            "acceptance_criteria": list(raw.get("acceptance_criteria", [])),
            "expected_outputs": list(raw.get("expected_outputs", [])),
            "resources": resources,
            "write_scope": write_scope,
            "resource_contracts": resource_contracts,
            "resource_requests": resource_requests,
            "risk_class": raw.get("risk_class", "green"),
            "allowed_actions": allowed_actions,
            "forbidden_actions": list(raw.get("forbidden_actions", ["git.remote", "network", "credential", "paid"])),
            "preferred_backend": raw.get("preferred_backend", "sequential"),
            "integration_strategy": str(raw.get("integration_strategy", "record output for parent integration review")),
            "validation_plan": list(raw.get("validation_plan", [])),
            "knowledge_references": list(raw.get("knowledge_references", [])),
            "created_at": timestamp,
        }
        if not value["acceptance_criteria"]:
            value["acceptance_criteria"] = [{"criterion_id": "AC-001", "statement": value["objective"], "validation_method": "child independent review"}]
        if not value["expected_outputs"]:
            value["expected_outputs"] = ["child-result"]
        if not value["validation_plan"]:
            value["validation_plan"] = ["independent child review"]
        resource_ids = {item.get("resource_id") for item in resource_contracts if isinstance(item, dict)}
        if resource_ids != set(resources) or not set(write_scope).issubset(resource_ids):
            raise ValidationError("typed resource contracts must cover every declared resource and write scope")
        for resource in resource_contracts:
            if resource.get("resource_id") not in set(write_scope):
                continue
            scope = resource.get("semantic_scope")
            locator = resource.get("locator")
            if not isinstance(scope, dict) or not str(scope.get("namespace", "")).strip() or not str(scope.get("entity", "")).strip():
                raise ValidationError("write resource requires an unambiguous semantic namespace and entity")
            if not isinstance(locator, str) or not locator.strip() or Path(locator).is_absolute() or ".." in Path(locator).parts:
                raise ValidationError("write resource locator must be a safe relative repository path")
        if {item.get("resource_ref") for item in resource_requests if isinstance(item, dict)} != set(resources):
            raise ValidationError("typed resource requests must cover every declared resource exactly once")
        content = digest_json(value)
        return {**value, "content_hash": content}

    def create_child_loop(
        self, task_id: str, expected_revision: int, actor: str, contract_draft: dict[str, Any]
    ) -> dict[str, Any]:
        """Materialize a logical Child Loop and immutable contract revision 1."""
        with self.lock():
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            if state["lifecycle_status"] != "active" or state["execution_status"] != "idle":
                raise ValidationError("child loops may be created only from an active idle parent checkpoint")
            contract = self._child_contract_payload(task_id, contract_draft, 1, None)
            self.schemas.validate("child-contract", contract)
            self._validate_knowledge_references_unlocked(task_id, contract["knowledge_references"])
            loop_id = contract["loop_id"]
            if any(item.get("loop_id") == loop_id for item in state["child_loops"]):
                raise ConflictError("child loop ID already exists")
            known = {item.get("loop_id") for item in state["child_loops"]}
            if not set(contract["dependencies"]).issubset(known):
                raise ValidationError("child dependencies must reference already materialized loops")
            if loop_id in contract["dependencies"]:
                raise ValidationError("child loop cannot depend on itself")
            contract_ref = f"loops/{loop_id}/contracts/rev-0001.json"
            child_checkpoint_id = f"ckpt-{uuid.uuid4().hex}"
            child_state = self._state_defaults(task_id, now())
            child_state.update({
                "root_task_id": task_id,
                "loop_id": loop_id,
                "parent_loop_id": contract["parent_loop_id"],
                "active_contract_ref": {"path": contract_ref, "content_hash": contract["content_hash"], "revision": 1},
                "last_checkpoint_id": child_checkpoint_id,
                "latest_checkpoint_ref": {"path": f"loops/{loop_id}/checkpoints/latest.json", "revision": 1},
            })
            self.schemas.validate("task-state", child_state)
            child_checkpoint = {
                "schema_version": 1,
                "checkpoint_id": child_checkpoint_id,
                "task_id": task_id,
                "revision": child_state["revision"],
                "created_at": now(),
                "phase": child_state["phase"],
                "cycle": child_state["cycle"],
                "reason": "child loop created",
                "state_digest": digest_json(child_state),
                "next_safe_action": "run child plan",
            }
            self.schemas.validate("checkpoint", child_checkpoint)
            child_ref = {"loop_id": loop_id, "contract_ref": contract_ref, "content_hash": contract["content_hash"], "status": "created", "dependencies": contract["dependencies"], "preferred_backend": contract["preferred_backend"], "worktree_ref": None, "result_ref": None}
            new = self._mutate_locked(
                task_id,
                expected_revision=expected_revision,
                actor=actor,
                event_type="child_loop_created",
                patch={"child_loops": [*state["child_loops"], child_ref]},
                reason="logical child loop materialized",
                next_action="record parallel plan or schedule ready child",
                additional_files={contract_ref: contract, f"loops/{loop_id}/contract.json": contract, f"loops/{loop_id}/state.json": child_state, f"loops/{loop_id}/checkpoints/latest.json": child_checkpoint},
                event_payload={"loop_id": loop_id, "contract_ref": contract_ref, "content_hash": contract["content_hash"]},
            )
            return {"state": new, "contract": contract, "contract_ref": contract_ref}

    def revise_child_contract(
        self, task_id: str, expected_revision: int, actor: str, loop_id: str, contract_draft: dict[str, Any]
    ) -> dict[str, Any]:
        with self.lock():
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            entry = next((item for item in state["child_loops"] if item.get("loop_id") == loop_id), None)
            if not entry or entry.get("status") not in {"created", "ready"}:
                raise ValidationError("only an unscheduled child loop contract may be revised")
            old_ref, old = self._read_child_contract_unlocked(task_id, loop_id, str(entry["contract_ref"]))
            revision = int(old["revision"]) + 1
            amended = {**contract_draft, "loop_id": loop_id, "parent_loop_id": old["parent_loop_id"]}
            contract = self._child_contract_payload(task_id, amended, revision, old_ref)
            self.schemas.validate("child-contract", contract)
            self._validate_knowledge_references_unlocked(task_id, contract["knowledge_references"])
            if not set(contract["dependencies"]).issubset({item.get("loop_id") for item in state["child_loops"]} - {loop_id}):
                raise ValidationError("revised child dependencies are not materialized")
            ref = f"loops/{loop_id}/contracts/rev-{revision:04d}.json"
            child_state_path = self._path(task_id) / "loops" / loop_id / "state.json"
            child_state = self._read_child_state(task_id, loop_id)
            child_state = {**child_state, "revision": child_state["revision"] + 1, "updated_at": now(), "active_contract_ref": {"path": ref, "content_hash": contract["content_hash"], "revision": revision}}
            child_state, checkpoint_path, checkpoint = self._checkpoint_child_state(task_id, loop_id, child_state, "child contract revision selected", "revalidate child plan")
            dependent_ids: set[str] = set()
            changed = True
            while changed:
                changed = False
                for item in state["child_loops"]:
                    candidate = str(item["loop_id"])
                    if candidate == loop_id or candidate in dependent_ids:
                        continue
                    if set(item.get("dependencies", [])) & ({loop_id} | dependent_ids):
                        dependent_ids.add(candidate)
                        changed = True
            files: dict[str, dict[str, Any]] = {ref: contract, f"loops/{loop_id}/contract.json": contract, f"loops/{loop_id}/state.json": child_state, checkpoint_path: checkpoint}
            children: list[dict[str, Any]] = []
            for item in state["child_loops"]:
                candidate = str(item["loop_id"])
                if candidate == loop_id:
                    children.append({**item, "contract_ref": ref, "content_hash": contract["content_hash"], "status": "created", "result_ref": None})
                    continue
                if candidate not in dependent_ids:
                    children.append(item)
                    continue
                dependent_path = self._path(task_id) / "loops" / candidate / "state.json"
                dependent_state = self._read_child_state(task_id, candidate)
                self.schemas.validate("task-state", dependent_state)
                if dependent_state["active_lease"] is not None:
                    raise ConflictError("cannot revise a contract while a dependent child lease is live")
                dependent_state = {**dependent_state, "revision": dependent_state["revision"] + 1, "updated_at": now(), "lifecycle_status": "active", "execution_status": "idle", "phase": "plan", "phase_kind": "standard", "active_plan_ref": None, "active_implement_ref": None, "latest_review_result_ref": None, "latest_review_decision_ref": None, "last_error": "upstream child contract changed; replan required"}
                dependent_state, dependent_checkpoint_path, dependent_checkpoint = self._checkpoint_child_state(task_id, candidate, dependent_state, "upstream contract revised", "replan dependent child")
                files[str(dependent_path.relative_to(self._path(task_id)))] = dependent_state
                files[dependent_checkpoint_path] = dependent_checkpoint
                children.append({**item, "status": "created", "result_ref": None})
            new = self._mutate_locked(task_id, expected_revision=expected_revision, actor=actor, event_type="child_contract_revised", patch={"child_loops": children, "phase": "plan", "phase_kind": "standard", "active_parallelization_plan_ref": None}, reason="new immutable child contract revision selected and downstream loops invalidated", next_action="revalidate parallel plan", additional_files=files, event_payload={"loop_id": loop_id, "previous_contract_ref": old_ref, "contract_ref": ref, "invalidated_dependent_loop_ids": sorted(dependent_ids)})
            return {"state": new, "contract": contract, "contract_ref": ref}

    def _build_parallel_plan_unlocked(
        self,
        task_id: str,
        state: dict[str, Any],
        children: list[dict[str, Any]],
        contracts: dict[str, dict[str, Any]],
        contract_refs: dict[str, str],
        *,
        mode: str,
        rationale: str,
        max_concurrency: int,
        max_depth: int,
        integration_strategy: str,
        integration_validation: list[str],
        knowledge_references: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, list[str]]]:
        if not children:
            raise ValidationError("parallel plan requires at least one child loop")
        self._validate_knowledge_references_unlocked(task_id, knowledge_references)
        ids = {str(item["loop_id"]) for item in children}
        if ids != set(contracts) or ids != set(contract_refs):
            raise ValidationError("parallel plan child contract set is inconsistent")
        dependencies: dict[str, list[str]] = {}
        refs: list[dict[str, Any]] = []
        for child in children:
            loop_id = str(child["loop_id"])
            contract = contracts[loop_id]
            deps = list(contract["dependencies"])
            if not set(deps).issubset(ids) or loop_id in deps:
                raise ValidationError("parallel plan contains invalid child dependency")
            dependencies[loop_id] = deps
            refs.append({"loop_id": loop_id, "contract_ref": contract_refs[loop_id], "content_hash": contract["content_hash"]})
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(loop: str) -> None:
            if loop in visiting:
                raise ValidationError("parallel plan dependency graph contains a cycle")
            if loop not in visited:
                visiting.add(loop)
                for dependency in dependencies[loop]:
                    visit(dependency)
                visiting.remove(loop)
                visited.add(loop)

        for loop in sorted(ids):
            visit(loop)
        ordered: list[str] = []
        emitted: set[str] = set()

        def emit(loop: str) -> None:
            if loop in emitted:
                return
            for dependency in sorted(dependencies[loop]):
                emit(dependency)
            emitted.add(loop)
            ordered.append(loop)

        for loop in sorted(ids):
            emit(loop)
        if mode == "parallel":
            def depends_on(left: str, right: str) -> bool:
                return right in dependencies[left] or any(depends_on(dependency, right) for dependency in dependencies[left])

            def write_resources(contract: dict[str, Any]) -> list[dict[str, Any]]:
                return [resource for resource in contract["resource_contracts"] if resource["resource_id"] in set(contract["write_scope"])]

            def resource_collision(first: dict[str, Any], second: dict[str, Any]) -> bool:
                if self._resource_overlaps(first["resource_id"], second["resource_id"]):
                    return True
                if set(first["aliases"]) & ({second["resource_id"]} | set(second["aliases"])):
                    return True
                if set(second["aliases"]) & ({first["resource_id"]} | set(first["aliases"])):
                    return True
                left_scope = first["semantic_scope"]
                right_scope = second["semantic_scope"]
                return bool(left_scope["namespace"] and left_scope["entity"] and left_scope["namespace"] == right_scope["namespace"] and left_scope["entity"] == right_scope["entity"])

            for left in sorted(ids):
                for right in sorted(ids):
                    if left >= right or depends_on(left, right) or depends_on(right, left):
                        continue
                    if any(resource_collision(first, second) for first in write_resources(contracts[left]) for second in write_resources(contracts[right])):
                        raise ValidationError("parallel plan contains an unreserved overlapping exclusive write resource; split or serialize the child loops")
        plan = {
            "schema_version": 1,
            "plan_id": f"parallel-{state['cycle']:04d}-{uuid.uuid4().hex[:8]}",
            "task_id": task_id,
            "cycle": state["cycle"],
            "mode": mode,
            "rationale": rationale,
            "max_concurrency": max_concurrency,
            "max_depth": max_depth,
            "child_contract_refs": refs,
            "integration_order": ordered,
            "integration_strategy": integration_strategy,
            "integration_validation": integration_validation,
            "knowledge_references": knowledge_references,
            "created_at": now(),
        }
        self.schemas.validate("parallel-plan", plan)
        return plan, dependencies

    def materialize_parallel_plan(
        self,
        task_id: str,
        expected_revision: int,
        actor: str,
        *,
        child_contracts: list[dict[str, Any]],
        mode: str,
        rationale: str,
        max_concurrency: int,
        max_depth: int,
        integration_strategy: str,
        integration_validation: list[str],
        knowledge_references: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Atomically commit child contracts, checkpoints, and the DAG plan."""
        with self.lock():
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            if state["lifecycle_status"] != "active" or state["execution_status"] != "idle":
                raise ValidationError("parallel materialization requires an active idle parent checkpoint")
            if state["child_loops"] or state.get("active_parallelization_plan_ref") is not None:
                raise ConflictError("parallel child graph already exists")
            if not child_contracts:
                raise ValidationError("parallel plan requires at least one child contract")
            children: list[dict[str, Any]] = []
            contracts: dict[str, dict[str, Any]] = {}
            contract_refs: dict[str, str] = {}
            files: dict[str, dict[str, Any]] = {}
            for raw_contract in child_contracts:
                if not isinstance(raw_contract, dict):
                    raise ValidationError("child_contracts must contain objects")
                contract = self._child_contract_payload(task_id, raw_contract, 1, None)
                self.schemas.validate("child-contract", contract)
                self._validate_knowledge_references_unlocked(task_id, contract["knowledge_references"])
                loop_id = contract["loop_id"]
                if loop_id in contracts:
                    raise ConflictError("child loop ID already exists")
                contract_ref = f"loops/{loop_id}/contracts/rev-0001.json"
                child_state = self._state_defaults(task_id, now())
                child_state.update({
                    "root_task_id": task_id,
                    "loop_id": loop_id,
                    "parent_loop_id": contract["parent_loop_id"],
                    "active_contract_ref": {"path": contract_ref, "content_hash": contract["content_hash"], "revision": 1},
                    "last_checkpoint_id": f"ckpt-{uuid.uuid4().hex}",
                    "latest_checkpoint_ref": {"path": f"loops/{loop_id}/checkpoints/latest.json", "revision": 1},
                })
                self.schemas.validate("task-state", child_state)
                child_state, checkpoint_path, checkpoint = self._checkpoint_child_state(task_id, loop_id, child_state, "child loop created", "run child plan")
                child_ref = {"loop_id": loop_id, "contract_ref": contract_ref, "content_hash": contract["content_hash"], "status": "created", "dependencies": contract["dependencies"], "preferred_backend": contract["preferred_backend"], "worktree_ref": None, "result_ref": None}
                children.append(child_ref)
                contracts[loop_id] = contract
                contract_refs[loop_id] = contract_ref
                files.update({contract_ref: contract, f"loops/{loop_id}/contract.json": contract, f"loops/{loop_id}/state.json": child_state, checkpoint_path: checkpoint})
            plan, dependencies = self._build_parallel_plan_unlocked(
                task_id,
                state,
                children,
                contracts,
                contract_refs,
                mode=mode,
                rationale=rationale,
                max_concurrency=max_concurrency,
                max_depth=max_depth,
                integration_strategy=integration_strategy,
                integration_validation=integration_validation,
                knowledge_references=list(knowledge_references or []),
            )
            plan_path = f"cycles/{state['cycle']:04d}/01-parallelization-plan.json"
            plan_ref = {"path": plan_path, "content_hash": digest_json(plan), "revision": state["revision"] + 1}
            files[plan_path] = plan
            ready_children = [({**child, "status": "ready"} if not dependencies[str(child["loop_id"])] else child) for child in children]
            new = self._mutate_locked(
                task_id,
                expected_revision=expected_revision,
                actor=actor,
                event_type="parallel_plan_materialized",
                patch={"active_parallelization_plan_ref": plan_ref, "child_loops": ready_children},
                reason="parallel child graph and immutable contracts committed atomically",
                next_action="acquire reservations for ready children",
                additional_files=files,
                event_payload={"plan_id": plan["plan_id"], "child_loop_ids": plan["integration_order"]},
            )
            return {"state": new, "plan": plan, "plan_ref": plan_ref}

    def record_parallel_plan(
        self,
        task_id: str,
        expected_revision: int,
        actor: str,
        *,
        mode: str,
        rationale: str,
        max_concurrency: int,
        max_depth: int,
        integration_strategy: str,
        integration_validation: list[str],
        knowledge_references: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        with self.lock():
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            if state["lifecycle_status"] != "active" or state["execution_status"] != "idle":
                raise ValidationError("parallel plan requires an active idle parent checkpoint")
            children = list(state["child_loops"])
            contracts: dict[str, dict[str, Any]] = {}
            contract_refs: dict[str, str] = {}
            for child in children:
                ref, contract = self._read_child_contract_unlocked(task_id, str(child["loop_id"]), str(child["contract_ref"]))
                contracts[str(child["loop_id"])] = contract
                contract_refs[str(child["loop_id"])] = ref
            plan, dependencies = self._build_parallel_plan_unlocked(task_id, state, children, contracts, contract_refs, mode=mode, rationale=rationale, max_concurrency=max_concurrency, max_depth=max_depth, integration_strategy=integration_strategy, integration_validation=integration_validation, knowledge_references=list(knowledge_references or []))
            path = f"cycles/{state['cycle']:04d}/01-parallelization-plan.json"
            ref = {"path": path, "content_hash": digest_json(plan), "revision": state["revision"] + 1}
            ready_children = [({**child, "status": "ready"} if not dependencies[str(child["loop_id"])] and child.get("status") == "created" else child) for child in children]
            new = self._mutate_locked(task_id, expected_revision=expected_revision, actor=actor, event_type="parallel_plan_recorded", patch={"active_parallelization_plan_ref": ref, "child_loops": ready_children}, reason="parallel DAG and immutable child references committed", next_action="acquire reservations for ready children", additional_files={path: plan}, event_payload={"plan_id": plan["plan_id"], "child_loop_ids": plan["integration_order"]})
            return {"state": new, "plan": plan, "plan_ref": ref}

    def record_child_worktree(
        self,
        task_id: str,
        expected_revision: int,
        actor: str,
        *,
        loop_id: str,
        action_id: str,
        branch: str,
        base_commit: str,
        worktree_path: str,
    ) -> dict[str, Any]:
        """Bind a completed managed local-Git action to one child workspace."""
        with self.lock():
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            entry = next((item for item in state["child_loops"] if item.get("loop_id") == loop_id), None)
            if not entry or entry.get("status") not in {"created", "ready"}:
                raise ValidationError("worktree may be bound only to an unscheduled child loop")
            action_path = self._path(task_id) / "actions" / f"{_safe_runtime_id(action_id, 'action_id')}.json"
            if not action_path.exists():
                raise ValidationError("worktree requires a completed managed git action")
            action_record = read_json(action_path)
            self.schemas.validate("action-record", action_record)
            action = action_record["action"]
            if action_record["status"] != "completed" or action["kind"] != "git.local" or action["loop_id"] != "root":
                raise ValidationError("worktree action is not a completed parent managed Git action")
            if not isinstance(action.get("command"), list) or len(action["command"]) != 6 or action["command"][:3] != ["worktree", "add", "-b"]:
                raise ValidationError("worktree action command is not the narrow managed worktree-add operation")
            if action["command"][3] != branch or action["command"][4] != worktree_path or action["command"][5] != base_commit:
                raise ValidationError("worktree metadata does not exactly match the managed action descriptor")
            try:
                target = Path(worktree_path).resolve(strict=True)
                allowed_root = (self.home / "runtime" / "worktrees" / task_id / loop_id).resolve()
                if target != allowed_root or not target.is_dir() or not (target / ".git").exists():
                    raise ValidationError("worktree path is not the task-owned isolated child worktree")
                identity = _directory_identity(target)
            except OSError as exc:
                raise ValidationError("worktree path cannot be verified") from exc
            contract_ref = str(entry["contract_ref"])
            record = {"schema_version": 1, "task_id": task_id, "loop_id": loop_id, "contract_ref": contract_ref, "action_id": action_id, "branch": branch, "base_commit": base_commit, "worktree_path": str(target), "worktree_identity": identity, "created_at": now()}
            self.schemas.validate("worktree", record)
            path = f"loops/{loop_id}/worktree.json"
            ref = {"path": path, "content_hash": digest_json(record), "action_id": action_id}
            children = [({**item, "worktree_ref": ref, "status": "ready"} if item.get("loop_id") == loop_id else item) for item in state["child_loops"]]
            new = self._mutate_locked(task_id, expected_revision=expected_revision, actor=actor, event_type="child_worktree_bound", patch={"child_loops": children}, reason="managed local Git worktree bound to child contract", next_action="run child in isolated worktree", additional_files={path: record}, event_payload={"loop_id": loop_id, "action_id": action_id, "worktree_ref": ref})
            return {"state": new, "worktree": record, "worktree_ref": ref}

    def _reservation_records_unlocked(self, task_id: str) -> list[tuple[str, dict[str, Any]]]:
        directory = self._path(task_id) / "reservations"
        if not directory.exists():
            return []
        records: list[tuple[str, dict[str, Any]]] = []
        for path in sorted(directory.glob("res-*.json")):
            value = read_json(path)
            self.schemas.validate("reservation", value)
            records.append((str(path.relative_to(self._path(task_id))), value))
        return records

    def request_reservation(
        self, task_id: str, expected_revision: int, actor: str, *, loop_id: str, resource: str, mode: str, input_version_ref: str | None = None
    ) -> dict[str, Any]:
        with self.lock():
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            entry = next((item for item in state["child_loops"] if item.get("loop_id") == loop_id), None)
            if not entry or entry.get("status") not in {"created", "ready"}:
                raise ValidationError("reservation requires a created or ready child loop")
            contract_ref, contract = self._read_child_contract_unlocked(task_id, loop_id, str(entry["contract_ref"]))
            if resource not in set(contract["resources"]) | set(contract["write_scope"]):
                raise ValidationError("reservation resource is not declared by the immutable child contract")
            reservation = {"schema_version": 1, "reservation_id": f"res-{uuid.uuid4().hex}", "task_id": task_id, "loop_id": loop_id, "contract_ref": contract_ref, "resource": resource, "mode": mode, "input_version_ref": input_version_ref, "status": "requested", "lease_owner": None, "lease_token": None, "acquired_at": None, "expires_at": None, "release_reason": None, "created_at": now(), "updated_at": now()}
            self.schemas.validate("reservation", reservation)
            path = f"reservations/{reservation['reservation_id']}.json"
            new = self._mutate_locked(task_id, expected_revision=expected_revision, actor=actor, event_type="reservation_requested", patch={}, reason="child resource reservation requested", next_action="acquire reservation after conflict check", additional_files={path: reservation}, event_payload={"reservation_id": reservation["reservation_id"], "loop_id": loop_id, "resource": resource, "mode": mode})
            return {"state": new, "reservation": reservation, "reservation_ref": path}

    def acquire_reservation(
        self, task_id: str, expected_revision: int, actor: str, *, reservation_id: str, lease_seconds: int = 300
    ) -> dict[str, Any]:
        if lease_seconds <= 0 or lease_seconds > 3600:
            raise ValidationError("reservation lease_seconds must be within 1..3600")
        with self.lock():
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            records = self._reservation_records_unlocked(task_id)
            path, requested = next(((path, record) for path, record in records if record["reservation_id"] == reservation_id), (None, None))
            if path is None or requested is None:
                raise NotFoundError("reservation not found")
            if requested["status"] != "requested":
                raise ConflictError("only a requested reservation can be acquired")
            timestamp = now()
            current = datetime.now(timezone.utc)
            files: dict[str, dict[str, Any]] = {}
            expired: list[str] = []
            held: list[dict[str, Any]] = []
            for other_path, record in records:
                if record["status"] == "held" and record["expires_at"] and _parse_time(record["expires_at"]) <= current:
                    updated = {**record, "status": "expired", "lease_owner": None, "lease_token": None, "release_reason": "lease expired before scheduler acquisition", "updated_at": timestamp}
                    self.schemas.validate("reservation", updated)
                    files[other_path] = updated
                    expired.append(record["reservation_id"])
                elif record["status"] == "held":
                    held.append(record)
            conflict = next((record for record in held if record["loop_id"] != requested["loop_id"] and self._reservations_conflict(requested, record)), None)
            if conflict:
                conflicted = {**requested, "status": "conflicted", "release_reason": f"conflicts with {conflict['reservation_id']}", "updated_at": timestamp}
                self.schemas.validate("reservation", conflicted)
                files[path] = conflicted
                new = self._mutate_locked(task_id, expected_revision=expected_revision, actor=actor, event_type="reservation_conflicted", patch={}, reason="resource reservation conflict", next_action="schedule sequentially or replan", additional_files=files, event_payload={"reservation_id": reservation_id, "conflicts_with": conflict["reservation_id"]}, additional_events=[("reservation_expired", {"reservation_ids": expired})] if expired else None)
                return {"state": new, "reservation": conflicted, "outcome": "conflicted"}
            token = f"reslease-{uuid.uuid4().hex}"
            held_record = {**requested, "status": "held", "lease_owner": actor, "lease_token": token, "acquired_at": timestamp, "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(), "updated_at": timestamp}
            self.schemas.validate("reservation", held_record)
            files[path] = held_record
            new = self._mutate_locked(task_id, expected_revision=expected_revision, actor=actor, event_type="reservation_acquired", patch={}, reason="exclusive scheduler reservation acquired", next_action="start child only through managed adapter", additional_files=files, event_payload={"reservation_id": reservation_id, "loop_id": requested["loop_id"], "lease_token": token}, additional_events=[("reservation_expired", {"reservation_ids": expired})] if expired else None)
            return {"state": new, "reservation": held_record, "outcome": "held"}

    def release_reservation(
        self, task_id: str, expected_revision: int, actor: str, *, reservation_id: str, lease_token: str, reason: str
    ) -> dict[str, Any]:
        with self.lock():
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            path = self._path(task_id) / "reservations" / f"{_safe_runtime_id(reservation_id, 'reservation_id')}.json"
            if not path.exists():
                raise NotFoundError("reservation not found")
            record = read_json(path)
            self.schemas.validate("reservation", record)
            if record["status"] != "held" or record["lease_owner"] != actor or record["lease_token"] != lease_token:
                raise ValidationError("reservation release requires the exact live lease owner and token")
            if not record["expires_at"] or _parse_time(record["expires_at"]) <= datetime.now(timezone.utc):
                expired = {**record, "status": "expired", "lease_owner": None, "lease_token": None, "release_reason": "lease elapsed before release; reconciliation required", "updated_at": now()}
                self.schemas.validate("reservation", expired)
                self._mutate_locked(task_id, expected_revision=expected_revision, actor=actor, event_type="reservation_expired", patch={}, reason="reservation expired before release", next_action="reconcile resource before transfer", additional_files={str(path.relative_to(self._path(task_id))): expired}, event_payload={"reservation_id": reservation_id})
                raise ConflictError("reservation expired; reconcile resource before release or transfer")
            released = {**record, "status": "released", "lease_owner": None, "lease_token": None, "release_reason": reason, "updated_at": now()}
            self.schemas.validate("reservation", released)
            new = self._mutate_locked(task_id, expected_revision=expected_revision, actor=actor, event_type="reservation_released", patch={}, reason="scheduler reservation released after reconciliation", next_action=f"run {state['phase']}", additional_files={str(path.relative_to(self._path(task_id))): released}, event_payload={"reservation_id": reservation_id, "loop_id": record["loop_id"], "reason": reason})
            return {"state": new, "reservation": released}

    def renew_reservation(
        self, task_id: str, expected_revision: int, actor: str, *, reservation_id: str, lease_token: str, lease_seconds: int = 300
    ) -> dict[str, Any]:
        if lease_seconds <= 0 or lease_seconds > 3600:
            raise ValidationError("reservation lease_seconds must be within 1..3600")
        with self.lock():
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            path = self._path(task_id) / "reservations" / f"{_safe_runtime_id(reservation_id, 'reservation_id')}.json"
            if not path.exists():
                raise NotFoundError("reservation not found")
            record = read_json(path)
            self.schemas.validate("reservation", record)
            if record["status"] != "held" or record["lease_owner"] != actor or record["lease_token"] != lease_token:
                raise ConflictError("reservation renewal requires the exact live owner and token")
            if not record["expires_at"] or _parse_time(record["expires_at"]) <= datetime.now(timezone.utc):
                raise ConflictError("reservation expired; reconcile resource before transfer")
            renewed = {**record, "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(), "updated_at": now()}
            self.schemas.validate("reservation", renewed)
            new = self._mutate_locked(task_id, expected_revision=expected_revision, actor=actor, event_type="reservation_renewed", patch={}, reason="scheduler reservation lease renewed", next_action="continue child contract", additional_files={str(path.relative_to(self._path(task_id))): renewed}, event_payload={"reservation_id": reservation_id, "loop_id": record["loop_id"]})
            return {"state": new, "reservation": renewed}

    def recover_expired_reservations(self, task_id: str, actor: str = "taskctl-recovery") -> dict[str, Any] | None:
        """Durably expire stale scheduler reservations from a fresh runtime."""
        with self.lock():
            _, state, _ = self._read_task_unlocked(task_id)
            current = datetime.now(timezone.utc)
            updates: dict[str, dict[str, Any]] = {}
            expired: list[str] = []
            for path, record in self._reservation_records_unlocked(task_id):
                if record["status"] == "held" and record.get("expires_at") and _parse_time(record["expires_at"]) <= current:
                    updated = {
                        **record,
                        "status": "expired",
                        "lease_owner": None,
                        "lease_token": None,
                        "release_reason": "fresh scheduler recovered expired reservation",
                        "updated_at": now(),
                    }
                    self.schemas.validate("reservation", updated)
                    updates[path] = updated
                    expired.append(record["reservation_id"])
            if not expired:
                return None
            new = self._mutate_locked(
                task_id,
                expected_revision=state["revision"],
                actor=actor,
                event_type="reservation_expired",
                patch={},
                reason="fresh scheduler recovered expired reservations",
                next_action="reconcile child resource before transfer",
                additional_files=updates,
                event_payload={"reservation_ids": expired},
            )
            return {"state": new, "expired_reservation_ids": expired}

    def start_child_loop(
        self, task_id: str, expected_revision: int, actor: str, *, loop_id: str, lease_seconds: int = 300
    ) -> dict[str, Any]:
        """Acquire a Child-only lease after its durable reservations are held."""
        if lease_seconds <= 0 or lease_seconds > 3600:
            raise ValidationError("child lease_seconds must be within 1..3600")
        with self.lock():
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            if state["lifecycle_status"] != "active" or state["execution_status"] != "idle":
                raise ValidationError("child scheduling requires an active idle parent checkpoint")
            entry = next((item for item in state["child_loops"] if item.get("loop_id") == loop_id), None)
            if not entry or entry.get("status") != "ready":
                raise ValidationError("only a ready child loop may start")
            if not all(next((item.get("status") == "integration_ready" for item in state["child_loops"] if item.get("loop_id") == dependency), False) for dependency in entry["dependencies"]):
                raise ValidationError("child dependencies are not integration-ready")
            _, contract = self._read_child_contract_unlocked(task_id, loop_id, str(entry["contract_ref"]))
            held_resources = {
                reservation["resource"]
                for _, reservation in self._reservation_records_unlocked(task_id)
                if reservation["loop_id"] == loop_id and reservation["status"] == "held" and reservation["lease_owner"] == actor and reservation["expires_at"] and _parse_time(reservation["expires_at"]) > datetime.now(timezone.utc)
            }
            if not set(contract["write_scope"]).issubset(held_resources):
                raise ValidationError("all declared child write resources require an unexpired held reservation")
            child_path = self._path(task_id) / "loops" / loop_id / "state.json"
            child_state = self._read_child_state(task_id, loop_id)
            self.schemas.validate("task-state", child_state)
            if child_state["execution_status"] not in {"idle", "interrupted"} or child_state["active_lease"] is not None:
                raise ConflictError("child loop already has a live execution lease")
            if child_state["current_action"] is not None:
                raise ConflictError("child loop has an interrupted managed action that must be reconciled before restart")
            token = f"child-lease-{uuid.uuid4().hex}"
            run_id = f"child-run-{uuid.uuid4().hex}"
            expiry = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
            child_state = {**child_state, "revision": child_state["revision"] + 1, "updated_at": now(), "execution_status": "running", "active_lease": {"owner": actor, "token": token, "expires_at": expiry, "run_id": run_id}, "run_id": run_id, "lease_expires_at": expiry}
            child_state, checkpoint_path, checkpoint = self._checkpoint_child_state(task_id, loop_id, child_state, "child lease acquired", "complete child contract")
            children = [({**item, "status": "running"} if item.get("loop_id") == loop_id else item) for item in state["child_loops"]]
            new = self._mutate_locked(task_id, expected_revision=expected_revision, actor=actor, event_type="child_lease_acquired", patch={"child_loops": children}, reason="ready child started with an exact child lease", next_action="run child contract in its isolated backend", additional_files={str(child_path.relative_to(self._path(task_id))): child_state, checkpoint_path: checkpoint}, event_payload={"loop_id": loop_id, "lease_token": token, "run_id": run_id})
            return {"state": new, "child_state": child_state, "lease_token": token}

    def record_child_phase(
        self,
        task_id: str,
        expected_revision: int,
        actor: str,
        *,
        loop_id: str,
        lease_token: str,
        phase: str,
        summary: str,
        outputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist a Plan or Implement result for one leased Child Loop.

        The parent Task revision is the CAS boundary while the child state and
        artifact remain independently durable.  This lets several workers
        make progress concurrently without allowing a worker to write the
        parent's canonical state directly.
        """
        if phase not in {"plan", "implement"}:
            raise ValidationError("child phase recording supports plan and implement only")
        with self.lock():
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            if state["lifecycle_status"] != "active" or state["execution_status"] != "idle":
                raise ValidationError("child phase recording requires an active idle parent checkpoint")
            entry = next((item for item in state["child_loops"] if item.get("loop_id") == loop_id), None)
            if not entry or entry.get("status") != "running":
                raise ValidationError("child phase recording requires a running materialized child")
            child_path = self._path(task_id) / "loops" / _safe_runtime_id(loop_id, "loop_id") / "state.json"
            child_state = self._read_child_state(task_id, loop_id)
            self.schemas.validate("task-state", child_state)
            lease = child_state["active_lease"]
            if child_state["phase"] != phase or not lease or lease["owner"] != actor or lease["token"] != lease_token:
                raise ConflictError("child phase requires the exact active child lease and phase")
            if _parse_time(lease["expires_at"]) <= datetime.now(timezone.utc):
                raise ConflictError("child lease expired; recover before recording a phase")
            if child_state["current_action"] is not None:
                raise ConflictError("cannot record a child phase while a managed action is live")
            self._validate_managed_action_refs(task_id, phase, outputs, loop_id=loop_id)
            contract_ref, _ = self._read_child_contract_unlocked(task_id, loop_id, str(entry["contract_ref"]))
            if phase == "plan" and not isinstance(outputs.get("no_managed_effects"), bool):
                outputs = {**outputs, "no_managed_effects": True}
            artifact = {
                "schema_version": 1,
                "artifact_id": f"{phase}-{loop_id}-{uuid.uuid4().hex[:8]}",
                "task_id": task_id,
                "phase": phase,
                "cycle": child_state["cycle"],
                "created_at": now(),
                "summary": summary,
                "inputs": [contract_ref],
                "outputs": outputs,
                "decision": None,
            }
            self.schemas.validate("phase-result", artifact)
            artifact_path = f"loops/{loop_id}/cycles/{child_state['cycle']:04d}/{'01-plan' if phase == 'plan' else '02-implement'}-result.json"
            artifact_ref = {"artifact_id": artifact["artifact_id"], "path": artifact_path, "content_hash": digest_json(artifact), "revision": child_state["revision"] + 1}
            next_phase = "implement" if phase == "plan" else "review"
            child_state = {
                **child_state,
                "revision": child_state["revision"] + 1,
                "updated_at": now(),
                "phase": next_phase,
                "active_plan_ref": artifact_ref if phase == "plan" else child_state.get("active_plan_ref"),
                "active_implement_ref": artifact_ref if phase == "implement" else child_state.get("active_implement_ref"),
            }
            child_state, checkpoint_path, checkpoint = self._checkpoint_child_state(task_id, loop_id, child_state, f"child {phase} recorded", f"run child {next_phase}")
            new = self._mutate_locked(
                task_id,
                expected_revision=expected_revision,
                actor=actor,
                event_type="child_phase_recorded",
                patch={},
                reason=f"child {phase} recorded",
                next_action=f"run child {next_phase}",
                additional_files={
                    artifact_path: artifact,
                    str(child_path.relative_to(self._path(task_id))): child_state,
                    checkpoint_path: checkpoint,
                },
                event_payload={"loop_id": loop_id, "phase": phase, "artifact_id": artifact["artifact_id"]},
            )
            return {"state": new, "child_state": child_state, "artifact": artifact, "artifact_ref": artifact_ref}

    def record_child_failure(
        self,
        task_id: str,
        expected_revision: int,
        actor: str,
        *,
        loop_id: str,
        lease_token: str,
        phase: str,
        error_code: str,
        resume_phase: str = "plan",
    ) -> dict[str, Any]:
        """Stop one failed child without replaying an uncertain action."""
        if resume_phase not in {"plan", "implement", "review"}:
            raise ValidationError("child failure resume_phase is invalid")
        safe_error = _safe_runtime_id(error_code.replace(".", "-"), "child error code")
        with self.lock():
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            entry = next((item for item in state["child_loops"] if item.get("loop_id") == loop_id), None)
            if not entry or entry.get("status") != "running":
                raise ValidationError("child failure requires a running materialized child")
            child_path = self._path(task_id) / "loops" / _safe_runtime_id(loop_id, "loop_id") / "state.json"
            child_state = self._read_child_state(task_id, loop_id)
            self.schemas.validate("task-state", child_state)
            lease = child_state["active_lease"]
            if not lease or lease["owner"] != actor or lease["token"] != lease_token:
                raise ConflictError("child failure requires the exact active child lease")
            if _parse_time(lease["expires_at"]) <= datetime.now(timezone.utc):
                raise ConflictError("child lease expired; recover before recording failure")
            child_state = {
                **child_state,
                "revision": child_state["revision"] + 1,
                "updated_at": now(),
                "phase": resume_phase,
                "phase_kind": "standard",
                "cycle": child_state["cycle"] + 1 if resume_phase == "plan" else child_state["cycle"],
                "execution_status": "interrupted",
                "active_lease": None,
                "run_id": None,
                "lease_expires_at": None,
                "active_plan_ref": None if resume_phase == "plan" else child_state.get("active_plan_ref"),
                "active_implement_ref": None if resume_phase == "plan" else child_state.get("active_implement_ref"),
                "last_error": f"child {phase} worker failed: {safe_error}; reconcile before retry",
            }
            child_state, checkpoint_path, checkpoint = self._checkpoint_child_state(task_id, loop_id, child_state, "child worker failed", "reconcile or retry child")
            children = [({**item, "status": "ready"} if item.get("loop_id") == loop_id else item) for item in state["child_loops"]]
            new = self._mutate_locked(
                task_id,
                expected_revision=expected_revision,
                actor=actor,
                event_type="child_worker_failed",
                patch={"child_loops": children},
                reason="child worker failed; no automatic effect replay",
                next_action="reconcile or retry failed child",
                additional_files={str(child_path.relative_to(self._path(task_id))): child_state, checkpoint_path: checkpoint},
                event_payload={"loop_id": loop_id, "phase": phase, "resume_phase": resume_phase, "error_code": safe_error},
            )
            return {"state": new, "child_state": child_state}

    def renew_child_lease(
        self, task_id: str, expected_revision: int, actor: str, *, loop_id: str, lease_token: str, lease_seconds: int = 300
    ) -> dict[str, Any]:
        if lease_seconds <= 0 or lease_seconds > 3600:
            raise ValidationError("child lease_seconds must be within 1..3600")
        with self.lock():
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            child_path = self._path(task_id) / "loops" / _safe_runtime_id(loop_id, "loop_id") / "state.json"
            child_state = self._read_child_state(task_id, loop_id)
            self.schemas.validate("task-state", child_state)
            lease = child_state["active_lease"]
            if not lease or child_state["execution_status"] != "running" or lease["owner"] != actor or lease["token"] != lease_token:
                raise ConflictError("child lease token or owner does not match")
            if _parse_time(lease["expires_at"]) <= datetime.now(timezone.utc):
                raise ConflictError("child lease expired; recover before retry")
            expiry = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
            child_state = {**child_state, "revision": child_state["revision"] + 1, "updated_at": now(), "active_lease": {**lease, "expires_at": expiry}, "lease_expires_at": expiry}
            child_state, checkpoint_path, checkpoint = self._checkpoint_child_state(task_id, loop_id, child_state, "child lease renewed", "continue child contract")
            new = self._mutate_locked(task_id, expected_revision=expected_revision, actor=actor, event_type="child_lease_renewed", patch={}, reason="child lease renewed", next_action="continue child contract", additional_files={str(child_path.relative_to(self._path(task_id))): child_state, checkpoint_path: checkpoint}, event_payload={"loop_id": loop_id, "lease_token": lease_token})
            return {"state": new, "child_state": child_state}

    def release_child_lease(
        self, task_id: str, expected_revision: int, actor: str, *, loop_id: str, lease_token: str, reason: str
    ) -> dict[str, Any]:
        with self.lock():
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            child_path = self._path(task_id) / "loops" / _safe_runtime_id(loop_id, "loop_id") / "state.json"
            child_state = self._read_child_state(task_id, loop_id)
            self.schemas.validate("task-state", child_state)
            lease = child_state["active_lease"]
            if not lease or lease["owner"] != actor or lease["token"] != lease_token:
                raise ConflictError("child lease token or owner does not match")
            child_state = {**child_state, "revision": child_state["revision"] + 1, "updated_at": now(), "execution_status": "idle", "active_lease": None, "run_id": None, "lease_expires_at": None}
            child_state, checkpoint_path, checkpoint = self._checkpoint_child_state(task_id, loop_id, child_state, "child lease released", "resume child or reconcile")
            children = [({**item, "status": "ready"} if item.get("loop_id") == loop_id and item.get("status") == "running" else item) for item in state["child_loops"]]
            new = self._mutate_locked(task_id, expected_revision=expected_revision, actor=actor, event_type="child_lease_released", patch={"child_loops": children}, reason="child stopped at a safe checkpoint", next_action="resume child or reconcile", additional_files={str(child_path.relative_to(self._path(task_id))): child_state, checkpoint_path: checkpoint}, event_payload={"loop_id": loop_id, "reason": reason})
            return {"state": new, "child_state": child_state}

    def recover_expired_child_leases(self, task_id: str, actor: str = "taskctl-recovery") -> dict[str, Any] | None:
        """Fresh-process recovery for all elapsed Child leases in one transaction."""
        with self.lock():
            _, state, _ = self._read_task_unlocked(task_id)
            files: dict[str, dict[str, Any]] = {}
            expired: list[str] = []
            children: list[dict[str, Any]] = []
            current = datetime.now(timezone.utc)
            for item in state["child_loops"]:
                loop_id = str(item["loop_id"])
                child_path = self._path(task_id) / "loops" / loop_id / "state.json"
                child_state = self._read_child_state(task_id, loop_id)
                self.schemas.validate("task-state", child_state)
                lease = child_state["active_lease"]
                if lease and _parse_time(lease["expires_at"]) <= current:
                    child_state = {**child_state, "revision": child_state["revision"] + 1, "updated_at": now(), "execution_status": "interrupted", "active_lease": None, "run_id": None, "lease_expires_at": None, "last_error": "child lease expired; fresh-process reconciliation is required"}
                    child_state, checkpoint_path, checkpoint = self._checkpoint_child_state(task_id, loop_id, child_state, "child lease expired", "reconcile then resume child")
                    files[str(child_path.relative_to(self._path(task_id)))] = child_state
                    files[checkpoint_path] = checkpoint
                    expired.append(loop_id)
                    children.append({**item, "status": "ready"} if item.get("status") == "running" else item)
                else:
                    children.append(item)
            if not expired:
                return None
            new = self._mutate_locked(task_id, expected_revision=state["revision"], actor=actor, event_type="child_lease_expired", patch={"child_loops": children}, reason="child lease expired; reconciliation required before restart", next_action="reconcile affected child loops", additional_files=files, event_payload={"loop_ids": expired})
            return {"state": new, "expired_loop_ids": expired}

    def _validate_child_managed_action_refs(self, task_id: str, loop_id: str, entry: dict[str, Any], outputs: dict[str, Any]) -> None:
        refs = outputs.get("managed_action_refs", [])
        if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref for ref in refs):
            raise ValidationError("child managed_action_refs must be a list of completed action IDs")
        if entry.get("worktree_ref") and not refs:
            raise ValidationError("a worktree child result requires completed managed action durability evidence")
        if not entry.get("worktree_ref") and not refs and outputs.get("no_managed_effects") is not True:
            raise ValidationError("a non-writing child result must explicitly declare no_managed_effects")
        seen: set[str] = set()
        commit_heads: set[str] = set()
        for action_id in refs:
            _safe_runtime_id(action_id, "managed action ID")
            if action_id in seen:
                raise ValidationError("child managed_action_refs must not repeat an action")
            seen.add(action_id)
            path = self._path(task_id) / "actions" / f"{action_id}.json"
            if not path.exists():
                raise ValidationError("child result references an unknown managed action")
            record = read_json(path)
            self.schemas.validate("action-record", record)
            if record["status"] != "completed" or not record["after_evidence_ref"] or record["action"]["loop_id"] != loop_id:
                raise ValidationError("child result references action without completed loop-bound postflight evidence")
            if entry.get("worktree_ref") and record["action"]["kind"] == "git.local" and record["action"].get("command", [None])[0] == "commit":
                evidence = read_json(self._path(task_id) / record["after_evidence_ref"])
                self.schemas.validate("probe-evidence", evidence)
                head = evidence["facts"].get("after_head")
                if isinstance(head, str) and re.fullmatch(r"[a-f0-9]{40,64}", head):
                    commit_heads.add(head)
        if entry.get("worktree_ref"):
            durability = outputs.get("durability")
            if not isinstance(durability, dict) or durability.get("kind") != "local_commit" or durability.get("commit") not in commit_heads:
                raise ValidationError("worktree child result must bind a completed managed commit postflight head as durability evidence")

    def record_child_result(
        self, task_id: str, expected_revision: int, actor: str, *, loop_id: str, lease_token: str, outputs: dict[str, Any]
    ) -> dict[str, Any]:
        """Persist a reviewed Child result; it never completes the Root Task."""
        with self.lock():
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            entry = next((item for item in state["child_loops"] if item.get("loop_id") == loop_id), None)
            if not entry or entry.get("status") != "running":
                raise ValidationError("child result requires a running materialized child")
            child_state_path = self._path(task_id) / "loops" / loop_id / "state.json"
            child_state = self._read_child_state(task_id, loop_id)
            self.schemas.validate("task-state", child_state)
            child_lease = child_state["active_lease"]
            if not child_lease or child_lease["owner"] != actor or child_lease["token"] != lease_token:
                raise ConflictError("child result requires the exact active child lease")
            if _parse_time(child_lease["expires_at"]) <= datetime.now(timezone.utc):
                raise ConflictError("child lease expired; recover before recording a result")
            self._validate_child_managed_action_refs(task_id, loop_id, entry, outputs)
            contract_ref, contract = self._read_child_contract_unlocked(task_id, loop_id, str(entry["contract_ref"]))
            if outputs.get("decision") != "completed" or not self._review_completion_is_valid({"acceptance_criteria": contract["acceptance_criteria"]}, outputs):
                raise ValidationError("child result requires completed child review with acceptance and validation evidence")
            child_cycle = int(child_state["cycle"])
            artifact = {"schema_version": 1, "artifact_id": f"child-review-{loop_id}-c{child_cycle}-{uuid.uuid4().hex[:8]}", "task_id": task_id, "phase": "review", "cycle": child_cycle, "created_at": now(), "summary": str(outputs.get("summary", f"child {loop_id} reviewed")), "inputs": [contract_ref], "outputs": outputs, "decision": "completed"}
            self.schemas.validate("phase-result", artifact)
            result_path = f"loops/{loop_id}/cycles/{child_cycle:04d}/03-review-result.json"
            result_ref = {"path": result_path, "artifact_id": artifact["artifact_id"], "content_hash": digest_json(artifact)}
            child_state = {**child_state, "revision": child_state["revision"] + 1, "updated_at": now(), "lifecycle_status": "completed", "execution_status": "idle", "active_lease": None, "run_id": None, "lease_expires_at": None, "phase": "review", "latest_review_result_ref": result_ref, "latest_review_decision_ref": result_ref}
            child_state, checkpoint_path, checkpoint = self._checkpoint_child_state(task_id, loop_id, child_state, "child review completed", "await integration review")
            files: dict[str, dict[str, Any]] = {result_path: artifact, str(child_state_path.relative_to(self._path(task_id))): child_state, checkpoint_path: checkpoint}
            released_ids: list[str] = []
            for reservation_path, reservation in self._reservation_records_unlocked(task_id):
                if reservation["loop_id"] == loop_id and reservation["status"] == "held":
                    released = {**reservation, "status": "released", "lease_owner": None, "lease_token": None, "release_reason": "child review completed", "updated_at": now()}
                    self.schemas.validate("reservation", released)
                    files[reservation_path] = released
                    released_ids.append(reservation["reservation_id"])
            children = [({**item, "status": "integration_ready", "result_ref": result_ref} if item.get("loop_id") == loop_id else item) for item in state["child_loops"]]
            ready_ids = {str(item["loop_id"]) for item in children if item.get("status") == "integration_ready"}
            children = [
                ({**item, "status": "ready"}
                 if item.get("status") == "created" and set(item.get("dependencies", [])).issubset(ready_ids)
                 else item)
                for item in children
            ]
            all_ready = all(item.get("status") == "integration_ready" for item in children)
            patch: dict[str, Any] = {"child_loops": children}
            if all_ready:
                patch.update({"phase": "review", "phase_kind": "integration"})
            new = self._mutate_locked(task_id, expected_revision=expected_revision, actor=actor, event_type="child_result_recorded", patch=patch, reason="child review marked integration-ready", next_action="run integration review" if all_ready else "schedule independent ready child", additional_files=files, event_payload={"loop_id": loop_id, "result_ref": result_ref, "released_reservation_ids": released_ids}, additional_events=[("reservation_released", {"reservation_ids": released_ids, "reason": "child review completed"})] if released_ids else None)
            return {"state": new, "artifact": artifact, "result_ref": result_ref}

    def record_integration_outcome(
        self,
        task_id: str,
        expected_revision: int,
        actor: str,
        *,
        outputs: dict[str, Any],
        decision: str,
    ) -> dict[str, Any]:
        """Persist a non-completing integration review for durable recovery.

        Integration Review is a real root phase, not an exception-only
        callback.  A failed validation re-plans from a clean root graph; a
        Human question keeps the reviewed child graph and resumes integration
        after the answer is recorded.
        """
        if decision not in {"needs_plan", "needs_human_input"}:
            raise ValidationError("integration outcome must be needs_plan or needs_human_input")
        with self.lock():
            _, state, context = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            if not state["child_loops"] or state["phase_kind"] != "integration" or state["phase"] != "review":
                raise ValidationError("integration outcome requires a materialized parallel root in integration phase")
            if state["lifecycle_status"] != "active" or state["execution_status"] != "idle" or state["current_action"] is not None:
                raise ValidationError("integration outcome requires an active idle parent checkpoint")
            self._validate_managed_action_refs(task_id, "review", outputs)
            artifact = {
                "schema_version": 1,
                "artifact_id": f"integration-review-{state['cycle']:02d}-{uuid.uuid4().hex[:8]}",
                "task_id": task_id,
                "phase": "review",
                "cycle": state["cycle"],
                "created_at": now(),
                "summary": str(outputs.get("summary", "integration review requires another cycle")),
                "inputs": [str(item["result_ref"]["path"]) for item in state["child_loops"] if isinstance(item.get("result_ref"), dict)],
                "outputs": outputs,
                "decision": decision,
            }
            self.schemas.validate("phase-result", artifact)
            artifact_path = f"cycles/{state['cycle']:04d}/03-integration-review-{artifact['artifact_id']}.json"
            artifact_ref = {"artifact_id": artifact["artifact_id"], "path": artifact_path, "content_hash": digest_json(artifact), "revision": state["revision"] + 1}
            files: dict[str, dict[str, Any]] = {artifact_path: artifact}
            patch: dict[str, Any] = {"latest_review_result_ref": artifact_ref}
            if decision == "needs_plan":
                patch.update({
                    "lifecycle_status": "active",
                    "execution_status": "idle",
                    "phase": "plan",
                    "phase_kind": "standard",
                    "cycle": state["cycle"] + 1,
                    "active_lease": None,
                    "run_id": None,
                    "lease_expires_at": None,
                    "active_plan_ref": None,
                    "active_parallelization_plan_ref": None,
                    "active_implement_ref": None,
                    "child_loops": [],
                    "blocking_reasons": [str(item) for item in outputs.get("blocking_issues", []) if isinstance(item, str)],
                })
                next_action = "run plan after integration review failure"
            else:
                raw_question = outputs.get("human_question")
                if not isinstance(raw_question, dict):
                    raise ValidationError("integration needs_human_input requires outputs.human_question")
                question = self._normalize_question(task_id, raw_question)
                question_path = f"human-input/questions/{question['question_id']}.json"
                question_ref = {"artifact_id": question["question_id"], "artifact_type": "human_question", "ref": question_path}
                files[question_path] = question
                context = {**context, "revision": state["revision"] + 1, "open_questions": [*context["open_questions"], question_path], "human_question_refs": [*context["human_question_refs"], question_ref], "updated_at": now()}
                self.schemas.validate("stable-context", context)
                files["stable-context.json"] = context
                patch.update({
                    "lifecycle_status": "waiting_human",
                    "execution_status": "idle",
                    "active_lease": None,
                    "run_id": None,
                    "lease_expires_at": None,
                    "waiting_question_ids": [*state["waiting_question_ids"], question["question_id"]],
                    "pending_human_questions": [*state["pending_human_questions"], question_ref],
                })
                next_action = "answer integration review question"
            new = self._mutate_locked(
                task_id,
                expected_revision=expected_revision,
                actor=actor,
                event_type="integration_review_recorded",
                patch=patch,
                reason=f"integration review recorded: {decision}",
                next_action=next_action,
                additional_files=files,
                event_payload={"artifact_id": artifact["artifact_id"], "decision": decision},
            )
            return {"state": new, "artifact": artifact}

    def record_integration_review(
        self, task_id: str, expected_revision: int, actor: str, *, outputs: dict[str, Any]
    ) -> dict[str, Any]:
        """The only parallel operation allowed to complete a materialized Root."""
        with self.lock():
            definition, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            if not state["child_loops"] or state["phase_kind"] != "integration" or state["phase"] != "review":
                raise ValidationError("integration review requires a materialized parallel root in integration phase")
            if state["lifecycle_status"] != "active" or state["execution_status"] != "idle":
                raise ValidationError("integration review requires an active idle parent checkpoint")
            children = {str(item["loop_id"]): item for item in state["child_loops"]}
            accepted = outputs.get("accepted_children")
            if not isinstance(accepted, list) or set(accepted) != set(children) or any(item.get("status") != "integration_ready" for item in children.values()):
                raise ValidationError("integration review must explicitly accept every integration-ready child")
            if not self._review_completion_is_valid(definition, outputs):
                raise ValidationError("integration completion requires root acceptance and validation evidence")
            if not isinstance(outputs.get("integration_validation"), list) or not outputs["integration_validation"]:
                raise ValidationError("integration review requires combined validation evidence")
            integrated_outputs = outputs.get("integrated_outputs")
            worktree_children = {loop_id: item for loop_id, item in children.items() if item.get("worktree_ref")}
            if not isinstance(integrated_outputs, list) or {item.get("loop_id") for item in integrated_outputs if isinstance(item, dict)} != set(worktree_children):
                raise ValidationError("integration review must provide one verified integrated output for every worktree child")
            for output in integrated_outputs:
                if not isinstance(output, dict) or not isinstance(output.get("child_commit"), str) or not isinstance(output.get("integration_action_id"), str):
                    raise ValidationError("integration output is missing child commit or managed integration action")
                loop_id = str(output["loop_id"])
                result_ref = children[loop_id].get("result_ref")
                if not isinstance(result_ref, dict):
                    raise RecoveryRequiredError("worktree child has no durable reviewed result")
                child_result = read_json(self._path(task_id) / str(result_ref["path"]))
                self.schemas.validate("phase-result", child_result)
                durability = child_result["outputs"].get("durability")
                if not isinstance(durability, dict) or durability.get("commit") != output["child_commit"]:
                    raise ValidationError("integration output does not match the child durable local commit")
                action_path = self._path(task_id) / "actions" / f"{_safe_runtime_id(output['integration_action_id'], 'integration action ID')}.json"
                if not action_path.exists():
                    raise ValidationError("integration output references an unknown managed action")
                action = read_json(action_path)
                self.schemas.validate("action-record", action)
                command = action["action"].get("command") or []
                if action["status"] != "completed" or action["action"]["loop_id"] != "root" or action["action"]["kind"] != "git.local" or not command or command[0] not in {"cherry-pick", "apply"}:
                    raise ValidationError("integration output requires a completed root managed cherry-pick or patch apply action")
                if command[0] == "cherry-pick" and output["child_commit"] not in command:
                    raise ValidationError("managed integration action does not bind the declared child commit")
                evidence = read_json(self._path(task_id) / str(action["after_evidence_ref"]))
                self.schemas.validate("probe-evidence", evidence)
                if evidence["result"] != "pass" or evidence["facts"].get("after_dirty_paths") != []:
                    raise ValidationError("integration action lacks a clean passing postflight Git probe")
            cleanup_actions = outputs.get("cleanup_actions")
            if not isinstance(cleanup_actions, list) or {item.get("loop_id") for item in cleanup_actions if isinstance(item, dict)} != set(worktree_children):
                raise ValidationError("integration review must prove managed cleanup for every child worktree and branch")
            used_cleanup_action_ids: set[str] = set()
            for cleanup in cleanup_actions:
                if not isinstance(cleanup, dict):
                    raise ValidationError("integration cleanup entry must be typed")
                loop_id = str(cleanup["loop_id"])
                remove_id = cleanup.get("worktree_remove_action_id")
                branch_id = cleanup.get("branch_delete_action_id")
                if not isinstance(remove_id, str) or not isinstance(branch_id, str) or remove_id == branch_id:
                    raise ValidationError("integration cleanup requires distinct managed worktree and branch actions")
                if remove_id in used_cleanup_action_ids or branch_id in used_cleanup_action_ids:
                    raise ValidationError("one managed cleanup action cannot satisfy multiple child worktrees")
                used_cleanup_action_ids.update({remove_id, branch_id})
                worktree_ref = worktree_children[loop_id]["worktree_ref"]
                worktree = read_json(self._path(task_id) / str(worktree_ref["path"]))
                self.schemas.validate("worktree", worktree)
                def cleanup_action(action_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
                    action_path = self._path(task_id) / "actions" / f"{_safe_runtime_id(action_id, 'cleanup action ID')}.json"
                    if not action_path.exists():
                        raise ValidationError("integration cleanup references an unknown managed action")
                    record = read_json(action_path)
                    self.schemas.validate("action-record", record)
                    if record["status"] != "completed" or record["action"]["loop_id"] != "root" or record["action"]["kind"] != "git.local" or not record["after_evidence_ref"]:
                        raise ValidationError("integration cleanup action must be a completed root managed local-Git action")
                    evidence = read_json(self._path(task_id) / str(record["after_evidence_ref"]))
                    self.schemas.validate("probe-evidence", evidence)
                    if evidence["result"] != "pass" or evidence["facts"].get("after_dirty_paths") != []:
                        raise ValidationError("integration cleanup action lacks a clean passing postflight probe")
                    return record, evidence
                remove_record, remove_evidence = cleanup_action(remove_id)
                remove_command = remove_record["action"].get("command") or []
                expected_remove = ["worktree", "remove", "--force", worktree["worktree_path"]]
                if remove_command != expected_remove:
                    raise ValidationError("integration cleanup worktree command does not exactly match the canonical child worktree")
                if remove_evidence["facts"].get("cleanup_before_repository_probe") != "pass" or remove_evidence["facts"].get("cleanup_before_dirty_paths") != []:
                    raise ValidationError("integration cleanup lacks a clean identity-bound child-worktree preflight probe")
                if str(Path(worktree["worktree_path"]).resolve()) in set(remove_evidence["facts"].get("after_worktree_paths", [])):
                    raise ValidationError("integration cleanup postflight still reports the child worktree")
                branch_record, branch_evidence = cleanup_action(branch_id)
                if (branch_record["action"].get("command") or []) != ["branch", "-D", worktree["branch"]]:
                    raise ValidationError("integration cleanup branch command does not exactly match the canonical child branch")
                if worktree["branch"] in set(branch_evidence["facts"].get("after_branches", [])):
                    raise ValidationError("integration cleanup postflight still reports the child branch")
            artifact = {"schema_version": 1, "artifact_id": f"integration-review-{uuid.uuid4().hex[:8]}", "task_id": task_id, "phase": "review", "cycle": state["cycle"], "created_at": now(), "summary": str(outputs.get("summary", "parallel integration review completed")), "inputs": [str(item["result_ref"]["path"]) for item in children.values()], "outputs": outputs, "decision": "completed"}
            self.schemas.validate("phase-result", artifact)
            artifact_path = f"cycles/{state['cycle']:04d}/03-integration-review-result.json"
            decision = {"schema_version": 1, "task_id": task_id, "artifact_id": artifact["artifact_id"], "decision": "completed", "accepted_children": accepted, "integrated_outputs": integrated_outputs, "cleanup_actions": cleanup_actions, "acceptance_criteria": outputs["acceptance_criteria"], "validation_evidence": outputs["validation_evidence"], "integration_validation": outputs["integration_validation"], "blocking_issues": outputs["blocking_issues"], "created_at": now()}
            decision_path = f"cycles/{state['cycle']:04d}/03-integration-review-decision.json"
            result_ref = {"path": artifact_path, "artifact_id": artifact["artifact_id"], "content_hash": digest_json(artifact), "revision": state["revision"] + 1}
            decision_ref = {"path": decision_path, "content_hash": digest_json(decision), "revision": state["revision"] + 1}
            new = self._mutate_locked(task_id, expected_revision=expected_revision, actor=actor, event_type="integration_review_recorded", patch={"lifecycle_status": "completed", "execution_status": "idle", "latest_review_result_ref": result_ref, "latest_review_decision_ref": decision_ref}, reason="integration review completed all child work", next_action="task completed", additional_files={artifact_path: artifact, decision_path: decision}, event_payload={"accepted_children": accepted, "artifact_id": artifact["artifact_id"]})
            return {"state": new, "artifact": artifact, "decision": decision}

    def record_git_durability_observation(
        self, task_id: str, expected_revision: int, actor: str, observation: dict[str, Any]
    ) -> dict[str, Any]:
        """Persist a read-only Git relation probe; it never performs remote I/O."""
        with self.lock():
            definition, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            self.schemas.validate("remote-state", observation)
            if observation["task_id"] != task_id or not definition["target_repository"] or Path(observation["repository_identity"]).resolve() != Path(definition["target_repository"]).resolve():
                raise ValidationError("Git durability observation must identify this task's canonical repository")
            status = observation["status"]
            sync = {"synced": "clean", "ahead": "push_pending", "diverged": "conflicted", "behind": "conflicted", "local_only": "unavailable", "unavailable": "unavailable"}[status]
            durability = "local_commit" if observation.get("artifact_commit") else "none"
            path = f"evidence/git-durability-{uuid.uuid4().hex}.json"
            new = self._mutate_locked(task_id, expected_revision=expected_revision, actor=actor, event_type="git_durability_observed", patch={"sync_status": sync, "target_artifact_durability": durability}, reason="read-only local Git durability relation observed", next_action="reconcile divergence" if sync == "conflicted" else f"run {state['phase']}", additional_files={path: observation}, event_payload={"observation_ref": path, "relation": status, "local_head": observation["local_head"], "remote_head": observation["remote_head"]})
            return {"state": new, "observation_ref": path}

    def record_git_durability_reconciliation(
        self,
        task_id: str,
        expected_revision: int,
        actor: str,
        *,
        outcome: str,
        rationale: str,
    ) -> dict[str, Any]:
        """Record the only safe v1 response to local/remote divergence.

        v1 deliberately has no managed remote fetch/push/rebase surface.
        A diverged artifact therefore cannot be silently continued or
        overwritten.  The canonical outcome is a blocked replan boundary;
        a Human or a future explicitly managed reconciliation can derive a
        fresh task from it, but this task cannot issue further effects.
        """
        with self.lock():
            _, state, _ = self._read_task_unlocked(task_id)
            if state["revision"] != expected_revision:
                raise ConflictError(f"expected revision {expected_revision} but found {state['revision']}")
            if state["sync_status"] != "conflicted":
                raise ValidationError("Git durability reconciliation requires a canonical conflicted state")
            if outcome != "replan_required" or not rationale.strip():
                raise ValidationError("v1 Git durability reconciliation requires the explicit replan_required outcome and rationale")
            evidence_ref, evidence = self._probe_record(
                f"git-durability-reconcile-{uuid.uuid4().hex}",
                "reconcile",
                actor,
                {
                    "result": "pass",
                    "reconcile_outcome": "partial_requires_repair",
                    "facts": {"outcome": outcome, "rationale": rationale.strip(), "sync_status": state["sync_status"]},
                    "redaction_summary": {},
                },
            )
            new = self._mutate_locked(
                task_id,
                expected_revision=expected_revision,
                actor=actor,
                event_type="git_durability_reconciled",
                patch={
                    "lifecycle_status": "blocked",
                    "execution_status": "idle",
                    "active_lease": None,
                    "run_id": None,
                    "lease_expires_at": None,
                    "phase": "plan",
                    "last_error": "Git durability divergence recorded; create a fresh replan boundary before any effect",
                },
                reason="Git durability divergence requires a new plan boundary",
                next_action="create a fresh task or obtain a Human-approved reconciliation plan",
                additional_files={evidence_ref: evidence},
                event_payload={"outcome": outcome, "evidence_ref": evidence_ref},
            )
            return {"state": new, "evidence_ref": evidence_ref}

    def diagnose(self, task_id: str) -> dict[str, Any]:
        """Inspect canonical recovery signals without proposing side effects.

        The diagnostic result is intentionally a summary: it contains IDs,
        statuses, revisions, and safe relative references, never arbitrary
        file contents or process output.  Expired transaction markers are
        recovered by the store lock before the snapshot is read, so a fresh
        process sees the same durable view as the normal taskctl boundary.
        """
        with self.lock():
            definition, state, context = self._read_task_unlocked(task_id)
            task_path = self._path(task_id)
            checkpoint_path = task_path / "checkpoints" / "latest.json"
            checkpoint = read_json(checkpoint_path) if checkpoint_path.is_file() else {}
            events_path = task_path / "events.jsonl"
            events: list[dict[str, Any]] = []
            if events_path.is_file():
                for line in read_text_nofollow(events_path).splitlines():
                    if line.strip():
                        value = json.loads(line)
                        self.schemas.validate("event", value)
                        events.append(value)

            runtime_diagnostics: list[dict[str, Any]] = []
            diagnostics_path = task_path / "diagnostics.jsonl"
            if diagnostics_path.is_file():
                for line in read_text_nofollow(diagnostics_path).splitlines():
                    if not line.strip():
                        continue
                    value = json.loads(line)
                    self.schemas.validate("runtime-diagnostic", value)
                    if value.get("task_id") != task_id:
                        raise ValidationError("runtime diagnostic task_id does not match its task directory")
                    runtime_diagnostics.append({
                        key: value.get(key)
                        for key in (
                            "diagnostic_id", "category", "phase", "cycle", "provider",
                            "action_id", "error_path", "actual_type", "retryable", "recorded_at",
                        )
                    } | {"message": value.get("message")})

            issues: list[dict[str, Any]] = []
            if not checkpoint:
                issues.append({"code": "checkpoint_missing", "next_action": "rebuild or recover the latest checkpoint"})
            else:
                if checkpoint.get("revision") != state["revision"] or checkpoint.get("state_digest") != digest_json(state):
                    issues.append({"code": "checkpoint_state_mismatch", "next_action": "reconcile the canonical checkpoint before resuming"})
            if not events or events[-1].get("revision") != state["revision"]:
                issues.append({"code": "event_state_revision_mismatch", "next_action": "run recovery before any new mutation"})
            if state.get("execution_status") in {"interrupted", "pausing"} or state.get("current_action") is not None:
                action_id = state.get("current_action", {}).get("action_id") if isinstance(state.get("current_action"), dict) else None
                issues.append({"code": "action_reconciliation_required", "action_id": action_id, "next_action": "reconcile the started action; never retry it implicitly"})
            if state.get("sync_status") == "conflicted":
                issues.append({"code": "git_durability_conflicted", "next_action": "create a new plan boundary; remote repair is not supported by v1"})
            if state.get("pending_human_questions"):
                issues.append({"code": "human_input_pending", "question_count": len(state["pending_human_questions"]), "next_action": "obtain the exact Human answer before continuing"})

            child_summaries: list[dict[str, Any]] = []
            for entry in state.get("child_loops", []):
                loop_id = str(entry.get("loop_id"))
                child_path = task_path / "loops" / loop_id / "state.json"
                child_state = self._read_child_state(task_id, loop_id) if child_path.is_file() else {}
                if child_state:
                    self.schemas.validate("task-state", child_state)
                child_summary = {
                    "loop_id": loop_id,
                    "status": entry.get("status"),
                    "dependencies": list(entry.get("dependencies", [])),
                    "child_revision": child_state.get("revision"),
                    "execution_status": child_state.get("execution_status"),
                    "lifecycle_status": child_state.get("lifecycle_status"),
                    "active_lease": bool(child_state.get("active_lease")),
                    "current_action_id": child_state.get("current_action", {}).get("action_id") if isinstance(child_state.get("current_action"), dict) else None,
                }
                child_summaries.append(child_summary)
                if child_state.get("execution_status") == "interrupted" or child_state.get("current_action") is not None:
                    issues.append({"code": "child_reconciliation_required", "loop_id": loop_id, "next_action": "recover the child lease or reconcile its action"})

            reservations: list[dict[str, Any]] = []
            reservation_dir = task_path / "reservations"
            for reservation_path in sorted(reservation_dir.glob("res-*.json")) if reservation_dir.is_dir() else []:
                reservation = read_json(reservation_path)
                self.schemas.validate("reservation", reservation)
                reservations.append({key: reservation.get(key) for key in ("reservation_id", "loop_id", "resource", "mode", "status", "lease_owner", "expires_at", "release_reason")})
                if reservation.get("status") in {"expired", "conflicted"}:
                    issues.append({"code": f"reservation_{reservation['status']}", "reservation_id": reservation["reservation_id"], "loop_id": reservation["loop_id"], "next_action": "reconcile the resource before requesting a replacement"})

            actions: list[dict[str, Any]] = []
            action_dir = task_path / "actions"
            for action_path in sorted(action_dir.glob("*.json")) if action_dir.is_dir() else []:
                action_record = read_json(action_path)
                self.schemas.validate("action-record", action_record)
                action = action_record["action"]
                actions.append({"action_id": action["action_id"], "kind": action["kind"], "status": action_record["status"], "loop_id": action["loop_id"], "grant_id": action_record.get("grant_id"), "before_evidence_ref": action_record.get("before_evidence_ref"), "after_evidence_ref": action_record.get("after_evidence_ref")})

            durability: list[dict[str, Any]] = []
            evidence_dir = task_path / "evidence"
            for evidence_path in sorted(evidence_dir.glob("git-durability-*.json")) if evidence_dir.is_dir() else []:
                observation = read_json(evidence_path)
                self.schemas.validate("remote-state", observation)
                durability.append({key: observation.get(key) for key in ("status", "branch", "local_head", "remote_head", "artifact_commit")})

            event_summary = [{"event_id": event["event_id"], "type": event["type"], "revision": event["revision"]} for event in events[-50:]]
            diagnostic_counts: dict[str, int] = {}
            for diagnostic in runtime_diagnostics:
                category = str(diagnostic.get("category"))
                diagnostic_counts[category] = diagnostic_counts.get(category, 0) + 1
            return {
                "schema_version": 1,
                "task_id": task_id,
                "title": definition["title"],
                "state": {key: state.get(key) for key in ("revision", "lifecycle_status", "execution_status", "phase", "phase_kind", "cycle", "sync_status", "last_error", "current_action")},
                "checkpoint": {key: checkpoint.get(key) for key in ("checkpoint_id", "revision", "phase", "cycle", "state_digest")},
                "event_count": len(events),
                "recent_events": event_summary,
                "children": child_summaries,
                "reservations": reservations,
                "actions": actions,
                "durability": durability,
                "runtime_diagnostics": runtime_diagnostics[-50:],
                "runtime_diagnostic_count": len(runtime_diagnostics),
                "runtime_diagnostic_category_counts": dict(sorted(diagnostic_counts.items())),
                "pending_human_question_count": len(state.get("pending_human_questions", [])),
                "issues": issues,
                "repair_required": bool(issues),
                "canonical_refs": {
                    "task": f"tasks/{task_id}",
                    "checkpoint": f"tasks/{task_id}/checkpoints/latest.json",
                    "events": f"tasks/{task_id}/events.jsonl",
                    "diagnostics": f"tasks/{task_id}/diagnostics.jsonl",
                },
                "context_revision": context.get("revision"),
            }

    def repair_plan(self, task_id: str) -> dict[str, Any]:
        """Derive a non-mutating repair/reconciliation proposal.

        v1 deliberately does not provide a generic repair apply command.  A
        plan is useful for a Human or a later exact managed action, but
        guessing how to rewrite a canonical file or remote repository would
        violate the fail-closed boundary.
        """
        diagnosis = self.diagnose(task_id)
        issues = diagnosis["issues"]
        if not issues:
            outcome = "no_repair_required"
            actions: list[dict[str, Any]] = []
        elif any(item["code"] == "git_durability_conflicted" for item in issues):
            outcome = "replan_required"
            actions = [{"kind": "git-durability-reconcile", "task_id": task_id, "requires": ["fresh durability observation", "Human or new plan boundary"]}]
        elif any(item["code"] == "human_input_pending" for item in issues):
            outcome = "human_required"
            actions = [{"kind": "answer-human-question", "task_id": task_id, "requires": ["exact persisted question answer"]}]
        else:
            outcome = "reconciliation_required"
            actions = [{"kind": "reconcile-managed-action", "task_id": task_id, "action_id": item.get("action_id"), "loop_id": item.get("loop_id")} for item in issues if item["code"] in {"action_reconciliation_required", "child_reconciliation_required"}]
            if not actions:
                actions = [{"kind": "fresh-process-recovery", "task_id": task_id, "requires": ["canonical transaction/checkpoint review"]}]
        proposal = {"schema_version": 1, "task_id": task_id, "outcome": outcome, "apply_supported": False, "actions": actions, "diagnosis": diagnosis}
        proposal["plan_hash"] = digest_json(proposal)
        return proposal

    def list(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        with self.lock():
            return self.list_unlocked(include_archived=include_archived)

    def render_all(self) -> None:
        items = self.list_unlocked()
        registry = {"schema_version": 1, "generated_at": now(), "tasks": items}
        self.schemas.validate("registry", registry)
        atomic_write_json(self.root / "registry.json", registry)
        lines = ["# nous tasks", ""] + ([f"- `{item['task_id']}` — {item['title']} ({item['lifecycle_status']} / {self._read_task_unlocked(item['task_id'])[1]['execution_status']} / {item['phase']} / cycle {item['cycle']})" for item in items] or ["No tasks."])
        atomic_write_text(self.root / "STATUS.md", "\n".join(lines) + "\n")
        for item in items:
            task = self._path(item["task_id"])
            definition, state, _ = self._read_task_unlocked(item["task_id"])
            atomic_write_text(task / "task.md", f"# {definition['title']}\n\n{definition['objective']}\n")
            atomic_write_text(task / "summary.md", f"# {definition['title']}\n\nState: {state['lifecycle_status']} / {state['execution_status']}\nPhase: {state['phase']} ({state['phase_kind']}, cycle {state['cycle']})\n")
            atomic_write_text(task / "resume.md", f"# Resume\n\nTask: `{item['task_id']}`\nNext: {state['phase']} cycle {state['cycle']}\n")
            knowledge = self._read_knowledge_unlocked(item["task_id"])
            knowledge_lines = [
                "<!-- generated by taskctl; do not edit -->",
                f"# Knowledge for {item['task_id']}",
                "",
                "## Questions",
            ]
            if knowledge["questions"]:
                knowledge_lines.extend(
                    f"- `{record['question_id']}` [{record['status']}] {record['question']}"
                    for record in knowledge["questions"]
                )
            else:
                knowledge_lines.append("- None")
            knowledge_lines.extend(["", "## Active claims"])
            superseded_claims = {identifier for record in knowledge["claims"] for identifier in record.get("supersedes", [])}
            active_claims = [record for record in knowledge["claims"] if record["lifecycle_status"] == "active" and record["claim_id"] not in superseded_claims]
            if active_claims:
                knowledge_lines.extend(
                    f"- `{record['claim_id']}` ({record['epistemic_status']}) {record['claim']}"
                    for record in active_claims
                )
            else:
                knowledge_lines.append("- None")
            atomic_write_text(task / "knowledge.md", "\n".join(knowledge_lines) + "\n")
            source_lines = ["<!-- generated by taskctl; do not edit -->", f"# Sources for {item['task_id']}", ""]
            if knowledge["sources"]:
                source_lines.extend(
                    f"- `{record['source_id']}` {record['title']} — {record['locator']}"
                    for record in knowledge["sources"]
                )
            else:
                source_lines.append("- None")
            atomic_write_text(task / "sources.md", "\n".join(source_lines) + "\n")

    def list_unlocked(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in sorted(self.root.iterdir()):
            if path.is_dir() and not path.name.startswith(".") and (path / "state.json").is_file():
                definition, state, _ = self._read_task_unlocked(path.name)
                if state["visibility"] == "archived" and not include_archived:
                    continue
                items.append({"task_id": path.name, "title": definition["title"], "lifecycle_status": state["lifecycle_status"], "phase": state["phase"], "cycle": state["cycle"], "updated_at": state["updated_at"]})
        return items
