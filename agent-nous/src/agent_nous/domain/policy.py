from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Iterable

from .actions import ActionDescriptor, Approval, PolicyDecision, Risk
from ..errors import ValidationError


class PolicyGateway:
    """Single fail-closed policy implementation for every managed adapter."""

    def __init__(
        self,
        *,
        allowed_processes: Iterable[tuple[str, ...]] = (),
        allow_local_git: bool = True,
    ) -> None:
        normalized: list[tuple[str, ...]] = []
        for item in allowed_processes:
            prefix = tuple(item)
            if not prefix or any(not isinstance(part, str) or not part for part in prefix):
                raise ValidationError("process allowlist entries must contain nonempty command arguments")
            normalized.append(prefix)
        self.allowed_processes = tuple(normalized)
        self.allow_local_git = allow_local_git

    @staticmethod
    def _inside(root: str | None, target: str) -> bool:
        if not root:
            return False
        root_path = Path(root).resolve()
        target_path = (root_path / target).resolve() if not os.path.isabs(target) else Path(target).resolve()
        return target_path == root_path or root_path in target_path.parents

    def assess_risk(self, action: ActionDescriptor) -> tuple[Risk, str]:
        if action.kind in {"git.remote", "network", "credential", "paid"}:
            return "red", f"{action.kind} is an external or sensitive effect"
        if action.kind.startswith("filesystem"):
            if not action.targets or any(
                not self._inside(action.repository_root, target) for target in action.targets
            ):
                return "red", "filesystem target is outside the declared repository root"
            return "green", "filesystem targets are contained in the declared root"
        if action.kind == "process.run":
            if not action.repository_root:
                return "red", "process action has no declared repository root"
            if not action.targets or any(not self._inside(action.repository_root, target) for target in action.targets):
                return "red", "process action has no declared in-scope resources"
            command = action.command or ()
            if not any(command[: len(prefix)] == prefix for prefix in self.allowed_processes):
                return "red", "process command is not in the executor allowlist"
            if any(part == "-c" for part in command):
                return "red", "arbitrary process payload is not a managed operation"
            return "yellow", "allowlisted process still requires sandbox constraints"
        if action.kind == "git.local":
            if not action.repository_root:
                return "red", "local Git action has no declared repository root"
            if not action.targets or any(not self._inside(action.repository_root, target) for target in action.targets):
                return "red", "local Git action has no declared in-scope resources"
            if not self.allow_local_git:
                return "red", "local Git adapter is disabled"
            command = action.command or ()
            if any(part in {"push", "fetch", "pull"} for part in command):
                return "red", "remote Git operation is not a local Git action"
            if command[:2] == ("worktree", "remove") or command[:2] in {("branch", "--delete"), ("branch", "-d"), ("branch", "-D")}:
                return "red", "local Git cleanup is destructive and requires an exact approval"
            return "yellow", "local Git metadata changes are managed and auditable"
        return "red", "unknown action kind"

    def evaluate(
        self, action: ActionDescriptor, approvals: Iterable[Approval] = ()
    ) -> PolicyDecision:
        risk, reason = self.assess_risk(action)
        # There is deliberately no managed egress, credential, paid, or remote Git
        # adapter in v1.  Approval cannot turn an unbrokered surface into a guarantee.
        if action.kind in {"git.remote", "network", "credential", "paid"}:
            return PolicyDecision("unsupported", risk, f"unsupported managed surface: {action.kind}", action.digest)
        if action.kind.startswith("filesystem") and "outside" in reason:
            return PolicyDecision("deny", risk, reason, action.digest)
        if action.kind == "process.run" and ("not in" in reason or "no declared" in reason or "arbitrary" in reason):
            return PolicyDecision("deny", risk, reason, action.digest)
        if action.kind in {"process.run", "git.local"} and "no declared repository root" in reason:
            return PolicyDecision("deny", risk, reason, action.digest)
        if action.kind == "git.local" and (
            "disabled" in reason or "remote Git" in reason
        ):
            return PolicyDecision("deny", risk, reason, action.digest)
        if action.declared_risk and action.declared_risk != risk:
            return PolicyDecision(
                "deny", risk, "declared risk does not match computed risk", action.digest
            )
        if risk == "red":
            if action.kind in {"network", "credential", "paid"} and not action.approval_id:
                return PolicyDecision("approval_required", risk, reason, action.digest)
            matching = next(
                (
                    approval
                    for approval in approvals
                    if approval.approval_id == action.approval_id
                    and approval.task_id == action.task_id
                    and approval.action_digest == action.digest
                    and approval.is_active()
                ),
                None,
            )
            if not matching:
                return PolicyDecision("approval_required", risk, reason, action.digest)
        grant_id = f"grant-{uuid.uuid4().hex}"
        return PolicyDecision(
            "grant",
            risk,
            reason,
            action.digest,
            grant_id,
            {
                "repository_root": action.repository_root,
                "network": False,
                "targets": list(action.targets),
            },
        )
