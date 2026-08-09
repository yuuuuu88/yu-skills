from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, TypeVar

from .models import ContractError, Phase


class CapabilityError(ContractError):
    """Raised when a provider cannot satisfy a declared contract."""


class CostLimitError(ContractError):
    """Raised before a provider invocation would exceed its cost budget."""


class RetryableProviderError(RuntimeError):
    """Provider error that may be retried within the declared policy."""


@dataclass(frozen=True)
class ProviderCapabilities:
    provider_id: str
    supports_roles: frozenset[Phase]
    structured_output: bool = True
    tool_use: bool = False
    network: bool = False
    credentials: bool = False
    workspace: bool = False
    cancellation: bool = False

    def supports(self, role: Phase) -> bool:
        return role in self.supports_roles


@dataclass(frozen=True)
class ProviderPolicy:
    required_roles: frozenset[Phase] = frozenset()
    require_structured_output: bool = True
    require_workspace: bool = False
    require_cancellation: bool = False
    allow_network: bool = False
    max_cost_usd: float | None = None


@dataclass(frozen=True)
class NegotiationResult:
    provider_id: str
    accepted: bool
    missing_roles: tuple[Phase, ...] = ()
    missing_capabilities: tuple[str, ...] = ()


class CapabilityNegotiator:
    def negotiate(self, capabilities: ProviderCapabilities, policy: ProviderPolicy) -> NegotiationResult:
        missing_roles = tuple(sorted(policy.required_roles - capabilities.supports_roles))
        missing: list[str] = []
        if policy.require_structured_output and not capabilities.structured_output:
            missing.append("structured_output")
        if policy.require_workspace and not capabilities.workspace:
            missing.append("workspace")
        if policy.require_cancellation and not capabilities.cancellation:
            missing.append("cancellation")
        if not policy.allow_network and capabilities.network:
            missing.append("network must be disabled")
        result = NegotiationResult(capabilities.provider_id, not missing_roles and not missing, missing_roles, tuple(missing))
        if not result.accepted:
            raise CapabilityError(f"provider {capabilities.provider_id} does not satisfy the declared policy")
        return result


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    retryable_errors: frozenset[type[BaseException]] = frozenset({RetryableProviderError, TimeoutError})

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ContractError("max_attempts must be positive")


T = TypeVar("T")


class RetryController:
    def __init__(self, policy: RetryPolicy) -> None:
        self.policy = policy

    def run(self, operation: Callable[[], T]) -> T:
        last_error: BaseException | None = None
        for _ in range(self.policy.max_attempts):
            try:
                return operation()
            except tuple(self.policy.retryable_errors) as exc:
                last_error = exc
        assert last_error is not None
        raise last_error


@dataclass
class CostLedger:
    limit_usd: float | None = None
    spent_usd: float = 0.0
    entries: list[tuple[str, float]] = field(default_factory=list)

    def record(self, provider_id: str, amount_usd: float) -> float:
        if amount_usd < 0:
            raise ContractError("cost cannot be negative")
        projected = self.spent_usd + amount_usd
        if self.limit_usd is not None and projected > self.limit_usd:
            raise CostLimitError("provider cost limit would be exceeded")
        self.spent_usd = projected
        self.entries.append((provider_id, amount_usd))
        return self.spent_usd
