from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pytest
from agent_nous.errors import ValidationError
from agent_nous.mcp import McpAuthorizationConfig, McpConnector, McpTool

class Transport:
    def __init__(self): self.requests=[]
    def send(self, request, bearer_token):
        self.requests.append((dict(request), bearer_token))
        return {"jsonrpc":"2.0","id":request["id"],"result":{"ok":True}}

def auth():
    return McpAuthorizationConfig("https://auth.example/authorize","https://auth.example/token","https://mcp.example/resource","client","https://app.example/callback",("mcp",))

def test_pkce_and_resource_indicator_are_required():
    url, verifier = auth().authorization_url(state="state", code_verifier="a"*43)
    assert "code_challenge_method=S256" in url and "resource=https%3A%2F%2Fmcp.example%2Fresource" in url
    assert verifier == "a"*43

def test_connector_validates_schema_and_does_not_put_token_in_json_rpc_body():
    transport=Transport(); connector=McpConnector(auth(), transport)
    connector.register_tool(McpTool("echo", {"type":"object","properties":{"value":{"type":"string"}},"required":["value"]}))
    response=connector.call_tool("echo", {"value":"ok"}, "bearer-value")
    assert response["result"]["ok"] is True
    request, received=transport.requests[0]
    assert received == "bearer-value" and "bearer-value" not in str(request)
    with pytest.raises(ValidationError): connector.call_tool("echo", {"value":"ok","access_token":"x"}, "bearer-value")
