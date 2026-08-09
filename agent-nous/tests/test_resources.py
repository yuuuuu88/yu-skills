from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pytest
from agent_nous.errors import ValidationError
from agent_nous.resources import ExecutionResourcePolicy, ResourceBudget

def test_resource_budget_enforces_output_and_concurrency():
    budget=ResourceBudget(ExecutionResourcePolicy(max_concurrent_agents=1,max_output_bytes=1024,max_tool_calls=2,max_wall_seconds=30))
    with budget.agent():
        with pytest.raises(ValidationError):
            with budget.agent(): pass
        budget.record_output(100)
    with pytest.raises(ValidationError): budget.record_output(1000)

def test_durability_profile_is_explicit():
    from agent_nous.durability import DurabilityProfile
    assert DurabilityProfile("local").capabilities["multi_host"] is False
    assert DurabilityProfile("shared_fs", "host-a").capabilities["multi_host"] is True
