from __future__ import annotations

"""Portable execution metrics kept separate from Nous Capability Score."""

import statistics
from typing import Any, Iterable, Mapping

from ..domain import secret_summary
from ..errors import ValidationError

OPERATIONAL_METRICS = (
    "elapsed_ms", "cycles", "actions", "tool_calls", "human_gates",
    "no_op_cycles", "resume_count", "estimated_cost_usd",
)


def normalize_operational_metrics(value: Mapping[str, Any] | None) -> dict[str, float]:
    if value is None:
        return {name: 0.0 for name in OPERATIONAL_METRICS}
    if not isinstance(value, Mapping):
        raise ValidationError("operational metrics must be an object")
    unknown = sorted(set(value) - set(OPERATIONAL_METRICS))
    if unknown:
        raise ValidationError(f"operational metrics contain unknown fields: {', '.join(str(item) for item in unknown)}")
    result: dict[str, float] = {}
    for name in OPERATIONAL_METRICS:
        raw = value.get(name, 0)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw < 0:
            raise ValidationError(f"operational metric {name} must be a non-negative number")
        result[name] = round(float(raw), 3)
    for item in result.values():
        if secret_summary(str(item)):
            raise ValidationError("operational metrics contain a secret-like value")
    return result


def aggregate_operational_metrics(values: Iterable[Mapping[str, Any] | None]) -> dict[str, Any]:
    normalized = [normalize_operational_metrics(value) for value in values]
    if not normalized:
        normalized = [normalize_operational_metrics(None)]
    return {
        "trial_count": len(normalized),
        "mean": {name: round(statistics.fmean(item[name] for item in normalized), 3) for name in OPERATIONAL_METRICS},
        "total": {name: round(sum(item[name] for item in normalized), 3) for name in OPERATIONAL_METRICS},
        "max": {name: round(max(item[name] for item in normalized), 3) for name in OPERATIONAL_METRICS},
    }


def compare_operational_metrics(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    base = baseline.get("mean", baseline)
    current = candidate.get("mean", candidate)
    left = normalize_operational_metrics(base if isinstance(base, Mapping) else None)
    right = normalize_operational_metrics(current if isinstance(current, Mapping) else None)
    return {"schema_version": 1, "mean_deltas": {name: round(right[name] - left[name], 3) for name in OPERATIONAL_METRICS}}
