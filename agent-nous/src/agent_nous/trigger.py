from __future__ import annotations


def should_start_managed_workflow(request: str, *, explicit: bool = False) -> bool:
    """Small, deterministic trigger boundary used by the Eval gate.

    The runtime does not infer a managed Task for a one-off request.  Implicit
    activation needs a clear request for a durable multi-phase workflow.
    """
    if explicit:
        return bool(request.strip())
    normalized = request.casefold()
    required = ("plan", "implement", "review")
    return all(token in normalized for token in required) and any(
        token in normalized for token in ("resume", "durable", "managed", "workflow")
    )
