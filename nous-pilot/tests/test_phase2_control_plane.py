from pathlib import Path

import pytest

from nous_pilot.capabilities import CapabilityError, CapabilityNegotiator, CostLedger, CostLimitError, ProviderCapabilities, ProviderPolicy, RetryController, RetryPolicy, RetryableProviderError
from nous_pilot.child_loops import ChildLoop, ChildLoopDAG, IntegrationReview
from nous_pilot.evaluation import QualityReport, SubagentUtilizationReport
from nous_pilot.handoff import StructuredHandoff
from nous_pilot.models import ContractError
from nous_pilot.verification_profiles import default_profiles


def test_capability_negotiation_retry_cost_and_profiles() -> None:
    capabilities = ProviderCapabilities("fixture", frozenset({"plan", "implement"}), workspace=True)
    assert CapabilityNegotiator().negotiate(capabilities, ProviderPolicy(required_roles=frozenset({"plan"}))).accepted
    with pytest.raises(CapabilityError):
        CapabilityNegotiator().negotiate(capabilities, ProviderPolicy(required_roles=frozenset({"review"})))
    state = {"attempts": 0}
    def operation() -> str:
        state["attempts"] += 1
        if state["attempts"] < 2:
            raise RetryableProviderError("temporary")
        return "ok"
    assert RetryController(RetryPolicy(max_attempts=2)).run(operation) == "ok"
    with pytest.raises(CostLimitError):
        CostLedger(limit_usd=1).record("fixture", 2)
    assert "unit" in default_profiles().names()


def test_child_loop_waves_and_integration_conflicts() -> None:
    dag = ChildLoopDAG()
    dag.add(ChildLoop("research"))
    dag.add(ChildLoop("implement", ("research",), write_scope=("src/a.py",)))
    dag.add(ChildLoop("docs", ("research",), write_scope=("docs/a.md",)))
    assert dag.waves() == (("research",), ("docs", "implement"))
    assert not IntegrationReview.from_write_sets({"a": ("x",), "b": ("x",)}).accepted


def test_handoff_and_metrics_reject_secret_and_measure_roles() -> None:
    with pytest.raises(ContractError):
        StructuredHandoff("h1", "plan", "implement", "digest", (), {"api_key": "never"})
    report = SubagentUtilizationReport.from_calls(({"role": "plan"}, {"role": "review"}, {"role": "review"}), ("plan", "implement", "review"))
    assert report.missing_roles == ("implement",)
    assert QualityReport.from_evidence((), ({"status": "passed"},)).acceptance_rate == 1.0
