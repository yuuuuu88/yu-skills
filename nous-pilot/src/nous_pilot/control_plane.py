from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .intake import normalize_request
from .models import DevelopmentRequest
from .runtime import NousRuntimeClient, RuntimeCommandError, RuntimeResponse


@dataclass(frozen=True)
class PilotRun:
    request: DevelopmentRequest
    task_id: str
    created: RuntimeResponse
    execution: RuntimeResponse | None = None


class PilotControlPlane:
    """Coordinate intake and the durable local agent-nous Root Task boundary."""

    def __init__(self, runtime: NousRuntimeClient) -> None:
        self.runtime = runtime

    @staticmethod
    def _task_id(response: RuntimeResponse) -> str:
        result = response.payload.get("result")
        candidate = result.get("task_id") if isinstance(result, dict) else response.payload.get("task_id")
        if not isinstance(candidate, str) or not candidate:
            raise RuntimeCommandError("agent-nous create response did not contain a task_id")
        return candidate

    def create(self, request_value: Mapping[str, Any], *, run_id: str) -> PilotRun:
        request = normalize_request(request_value)
        created = self.runtime.create_root_task(request, run_id=run_id)
        return PilotRun(request, self._task_id(created), created)

    def status(self, *, include_archived: bool = False) -> RuntimeResponse:
        return self.runtime.status(include_archived=include_archived)

    def show(self, task_id: str) -> RuntimeResponse:
        return self.runtime.show(task_id)

    def run(self, request_value: Mapping[str, Any], *, run_id: str, engine_config: Path) -> PilotRun:
        created = self.create(request_value, run_id=run_id)
        execution = self.runtime.engine_run(created.task_id, engine_config=engine_config)
        return PilotRun(created.request, created.task_id, created.created, execution)
