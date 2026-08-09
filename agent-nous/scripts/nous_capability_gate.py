from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from agent_nous.errors import ValidationError


def evaluate_gate(report: Mapping[str, Any], *, min_ncs: float, min_reliability: float, max_regressions: int = 0, holdout: Mapping[str, Any] | None = None, require_holdout: bool = False) -> dict[str, Any]:
    failures: list[str] = []
    ncs=float(report.get("ncs", -1))
    reliability=float(report.get("task_reliability", -1))
    if report.get("outcome") != "passed": failures.append("report_outcome_not_passed")
    if ncs < min_ncs: failures.append("ncs_below_threshold")
    if reliability < min_reliability: failures.append("reliability_below_threshold")
    if report.get("hard_gate_failures"): failures.append("hard_gate_failures_present")
    regressions=report.get("comparison", {}).get("regressions", []) if isinstance(report.get("comparison"), Mapping) else []
    if len(regressions) > max_regressions: failures.append("baseline_regression_limit_exceeded")
    if require_holdout and holdout is None: failures.append("holdout_report_required")
    if holdout is not None and (holdout.get("outcome") != "passed" or holdout.get("hard_gate_failures")): failures.append("holdout_not_passed")
    return {"schema_version":1,"passed":not failures,"failures":failures,"thresholds":{"min_ncs":min_ncs,"min_reliability":min_reliability,"max_regressions":max_regressions,"require_holdout":require_holdout},"observed":{"ncs":ncs,"task_reliability":reliability,"regressions":regressions}}


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description="Fail-closed Nous capability release gate")
    parser.add_argument("--report", required=True)
    parser.add_argument("--baseline")
    parser.add_argument("--holdout-report")
    parser.add_argument("--config")
    parser.add_argument("--min-ncs", type=float)
    parser.add_argument("--min-reliability", type=float)
    parser.add_argument("--max-regressions", type=int, default=0)
    parser.add_argument("--require-holdout", action="store_true")
    args=parser.parse_args(argv)
    config={}
    if args.config:
        config=json.loads(Path(args.config).read_text(encoding="utf-8"))
    min_ncs=float(args.min_ncs if args.min_ncs is not None else config.get("min_ncs",70))
    min_reliability=float(args.min_reliability if args.min_reliability is not None else config.get("min_reliability",1.0))
    max_regressions=int(config.get("max_regressions",args.max_regressions))
    report=json.loads(Path(args.report).read_text(encoding="utf-8"))
    holdout=json.loads(Path(args.holdout_report).read_text(encoding="utf-8")) if args.holdout_report else None
    result=evaluate_gate(report,min_ncs=min_ncs,min_reliability=min_reliability,max_regressions=max_regressions,holdout=holdout,require_holdout=args.require_holdout or bool(config.get("require_holdout",False)))
    print(json.dumps(result,ensure_ascii=False,sort_keys=True))
    return 0 if result["passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
