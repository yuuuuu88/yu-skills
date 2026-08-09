from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .models import ContractError, safe_identifier


_SECRET_WORDS = ("secret", "password", "token", "api_key", "private_key", "credential")


def _assert_safe(value: Any, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key).lower()
            if any(word in key_text for word in _SECRET_WORDS):
                raise ContractError(f"secret-like handoff field is not allowed: {path}.{key}")
            _assert_safe(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_safe(item, f"{path}[{index}]")


@dataclass(frozen=True)
class StructuredHandoff:
    handoff_id: str
    producer_role: str
    consumer_role: str
    input_digest: str
    scope: tuple[str, ...]
    outputs: Mapping[str, Any]
    assumptions: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        safe_identifier(self.handoff_id, "handoff_id")
        if not self.producer_role.strip() or not self.consumer_role.strip() or not self.input_digest.strip():
            raise ContractError("handoff roles and input digest are required")
        _assert_safe(self.outputs)

    def as_dict(self) -> dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "producer_role": self.producer_role,
            "consumer_role": self.consumer_role,
            "input_digest": self.input_digest,
            "scope": list(self.scope),
            "outputs": dict(self.outputs),
            "assumptions": list(self.assumptions),
            "unresolved": list(self.unresolved),
        }
