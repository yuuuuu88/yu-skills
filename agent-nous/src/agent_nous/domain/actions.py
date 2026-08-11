from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Literal

from ..io import digest_json

ActionKind = Literal[
    "filesystem.read",
    "filesystem.write",
    "process.run",
    "git.local",
    "git.remote",
    "network",
    "credential",
    "paid",
]
Risk = Literal["green", "yellow", "red"]


@dataclass(frozen=True)
class ActionDescriptor:
    action_id: str
    task_id: str
    kind: ActionKind
    repository_root: str | None = None
    targets: tuple[str, ...] = ()
    command: tuple[str, ...] | None = None
    network_destinations: tuple[str, ...] = ()
    estimated_cost_usd: float | None = None
    approval_id: str | None = None
    declared_risk: Risk | None = None
    loop_id: str = "root"
    expected_state_revision: int | None = None
    request_id: str | None = None
    idempotency_key: str | None = None
    expected_effect: str | None = None
    plan_ref: str | None = None
    plan_content_hash: str | None = None
    environment: str = "local"
    reservation_ids: tuple[str, ...] = ()
    # Child effects name the semantic resources they intend to touch.  The
    # TaskStore independently derives the same set from concrete targets and
    # refuses any mismatch before issuing an Execution Grant.
    resource_ids: tuple[str, ...] = ()
    # Ephemeral fencing credential used only while a managed child action is
    # admitted.  It is intentionally excluded from both the action digest and
    # canonical action record; lease tokens must never become durable evidence.
    lease_token: str | None = None
    # Optional typed policy for a disposable validation process.  Runtime
    # environment values are never put here; the engine stores only the
    # redacted public representation returned by ValidationSpec.public().
    validation: dict[str, object] | None = None

    def digest_payload(self) -> dict[str, object]:
        value = asdict(self)
        value.pop("approval_id", None)
        value.pop("declared_risk", None)
        # Transport/CAS metadata is deliberately not Human approval identity.
        # An approval remains bound to the immutable semantic action while a
        # fresh process observes a newer Task State revision before starting.
        value.pop("expected_state_revision", None)
        value.pop("request_id", None)
        value.pop("lease_token", None)
        # Preserve the v1 digest for actions that do not carry a validation
        # policy.  The optional field was added after durable action records
        # already existed.
        if value.get("validation") is None:
            value.pop("validation", None)
        return value

    @property
    def digest(self) -> str:
        return digest_json(self.digest_payload())

    def canonical_record(self, risk: Risk) -> dict[str, object]:
        """Secret-free descriptor persisted by the taskctl action boundary."""
        return {
            "schema_version": 1,
            "action_id": self.action_id,
            "task_id": self.task_id,
            "kind": self.kind,
            "risk": risk,
            "repository_root": self.repository_root,
            "targets": list(self.targets),
            "command": list(self.command) if self.command else None,
            "network_destinations": list(self.network_destinations),
            "estimated_cost_usd": self.estimated_cost_usd,
            "approval_id": self.approval_id,
            "digest": self.digest,
            "loop_id": self.loop_id,
            "expected_state_revision": self.expected_state_revision,
            "request_id": self.request_id,
            "idempotency_key": self.idempotency_key,
            "expected_effect": self.expected_effect,
            "plan_ref": self.plan_ref,
            "plan_content_hash": self.plan_content_hash,
            "environment": self.environment,
            "reservation_ids": list(self.reservation_ids),
            "resource_ids": list(self.resource_ids),
            **({"validation": self.validation} if self.validation is not None else {}),
        }


@dataclass(frozen=True)
class Approval:
    approval_id: str
    task_id: str
    action_digest: str
    status: Literal["requested", "active", "denied", "revoked", "expired", "exhausted"]
    max_count: int = 1
    consumed_count: int = 0
    expires_at: datetime | None = None

    def is_active(self, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        expiry = self.expires_at
        if expiry and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return (
            self.status == "active"
            and self.consumed_count < self.max_count
            and (expiry is None or expiry > current)
        )


@dataclass(frozen=True)
class PolicyDecision:
    outcome: Literal["grant", "deny", "approval_required", "unsupported"]
    risk: Risk
    reason: str
    action_digest: str
    grant_id: str | None = None
    constraints: dict[str, object] = field(default_factory=dict)
