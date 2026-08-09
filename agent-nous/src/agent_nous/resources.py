from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from .errors import ValidationError


@dataclass(frozen=True)
class ExecutionResourcePolicy:
    """Portable limits enforceable by the runtime without OS-specific claims."""

    max_concurrent_agents: int = 3
    max_output_bytes: int = 4 * 1024 * 1024
    max_tool_calls: int = 100
    max_wall_seconds: float = 300.0

    def validate(self) -> None:
        if not 1 <= self.max_concurrent_agents <= 32:
            raise ValidationError("max_concurrent_agents must be between 1 and 32")
        if not 1024 <= self.max_output_bytes <= 16 * 1024 * 1024:
            raise ValidationError("max_output_bytes must be between 1024 and 16777216")
        if not 1 <= self.max_tool_calls <= 10000:
            raise ValidationError("max_tool_calls must be between 1 and 10000")
        if not 1 <= self.max_wall_seconds <= 86400:
            raise ValidationError("max_wall_seconds must be between 1 and 86400")


@dataclass(frozen=True)
class ResourceUsage:
    active_agents: int
    output_bytes: int
    tool_calls: int
    elapsed_seconds: float


class ResourceBudget:
    """Thread-safe bounded budget for provider adapters and parallel workers."""

    def __init__(self, policy: ExecutionResourcePolicy | None = None) -> None:
        self.policy = policy or ExecutionResourcePolicy()
        self.policy.validate()
        self._lock = threading.Lock()
        self._active_agents = 0
        self._output_bytes = 0
        self._tool_calls = 0
        self._started = time.monotonic()

    def _check_locked(self) -> None:
        if time.monotonic() - self._started > self.policy.max_wall_seconds:
            raise ValidationError("execution resource wall-time budget exceeded")
        if self._tool_calls > self.policy.max_tool_calls:
            raise ValidationError("execution resource tool-call budget exceeded")
        if self._output_bytes > self.policy.max_output_bytes:
            raise ValidationError("execution resource output budget exceeded")

    @contextmanager
    def agent(self) -> Iterator[None]:
        with self._lock:
            self._check_locked()
            if self._active_agents >= self.policy.max_concurrent_agents:
                raise ValidationError("execution resource concurrency budget exceeded")
            self._active_agents += 1
        try:
            yield
        finally:
            with self._lock:
                self._active_agents -= 1

    def record_output(self, size: int) -> ResourceUsage:
        if not isinstance(size, int) or size < 0:
            raise ValidationError("output size must be a nonnegative integer")
        with self._lock:
            self._output_bytes += size
            self._check_locked()
            return self.usage()

    def record_tool_call(self) -> ResourceUsage:
        with self._lock:
            self._tool_calls += 1
            self._check_locked()
            return self.usage()

    def usage(self) -> ResourceUsage:
        return ResourceUsage(self._active_agents, self._output_bytes, self._tool_calls, time.monotonic() - self._started)
