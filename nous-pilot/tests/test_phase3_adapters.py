import pytest

from nous_pilot.adapters import BrowserVerificationAdapter, CIEventAdapter, DraftPRAdapter, PolicyDenied, ReadOnlyIssueAdapter, RemoteGitAdapter, RemoteGitPolicy
from nous_pilot.mcp import MCPAuthContext, MCPHTTPGateway, MCPHostAdapter, MCPPolicy, MCPToolSpec


def test_issue_ci_pr_and_remote_policy_adapters() -> None:
    issue = ReadOnlyIssueAdapter().intake({"issue_id": "I-1", "title": "Improve", "body": "details"})
    assert issue.title == "Improve"
    event = CIEventAdapter().from_mapping({"event_id": "ci-1", "status": "completed", "conclusion": "failure"})
    assert event.findings[0].severity == "major"
    proposal = DraftPRAdapter().build("task-1", "Draft", "body", ("src/a.py",), ("e1",))
    assert proposal.requires_human_approval
    with pytest.raises(PolicyDenied):
        RemoteGitAdapter(RemoteGitPolicy()).execute("push")
    assert BrowserVerificationAdapter().verify("https://example.com").status == "blocked"


def test_mcp_policy_requires_scope_and_oauth_for_http() -> None:
    host = MCPHostAdapter(MCPPolicy(frozenset({"echo"})))
    host.register(MCPToolSpec("echo", lambda arguments: {"echo": arguments["value"]}))
    auth = MCPAuthContext("user-1", frozenset({"nous:invoke"}))
    assert host.invoke("echo", {"value": "ok"}, auth)["echo"] == "ok"
    gateway = MCPHTTPGateway(host)
    oauth_auth = MCPAuthContext("user-1", frozenset({"nous:invoke"}), oauth_verified=True)
    assert gateway.handle({"tool": "echo", "arguments": {"value": "http"}}, oauth_auth)["echo"] == "http"
    with pytest.raises(PolicyDenied):
        host.invoke("echo", {"value": "ok"}, auth, transport="http")
