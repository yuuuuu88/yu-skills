from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .models import ContractError, DevelopmentRequest, RiskClass, safe_identifier, string_tuple


def normalize_request(value: Mapping[str, Any]) -> DevelopmentRequest:
    """Turn a request or Issue-shaped mapping into a validated local request."""
    if not isinstance(value, Mapping):
        raise ContractError("request must be an object")
    request_id = safe_identifier(str(value.get("request_id", "")), "request_id")
    objective = value.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        raise ContractError("objective must be a non-empty string")
    repository = value.get("repository_root", value.get("target_repository"))
    if not isinstance(repository, str) or not repository.strip():
        raise ContractError("repository_root is required")
    root = Path(repository).expanduser().resolve()
    risk = value.get("risk_class", "green")
    if not isinstance(risk, str) or risk not in {"green", "yellow", "red"}:
        raise ContractError("risk_class must be green, yellow, or red")
    validation_profile = value.get("validation_profile", "unit")
    if not isinstance(validation_profile, str) or not validation_profile.strip():
        raise ContractError("validation_profile must be a non-empty string")
    base_revision = value.get("base_revision")
    if base_revision is not None and (not isinstance(base_revision, str) or not base_revision.strip()):
        raise ContractError("base_revision must be a non-empty string or null")
    return DevelopmentRequest(
        request_id=request_id,
        objective=objective.strip(),
        repository_root=root,
        constraints=string_tuple(value.get("constraints"), "constraints"),
        non_goals=string_tuple(value.get("non_goals"), "non_goals"),
        risk_class=risk,  # type: ignore[arg-type]
        validation_profile=validation_profile.strip(),
        base_revision=base_revision.strip() if isinstance(base_revision, str) else None,
    )
