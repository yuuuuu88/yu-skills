from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .adapters import PolicyDenied
from .models import ContractError, safe_identifier


@dataclass(frozen=True)
class MCPToolSpec:
    name: str
    handler: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    mutating: bool = False

    def __post_init__(self) -> None:
        safe_identifier(self.name, "tool_name")


@dataclass(frozen=True)
class MCPAuthContext:
    subject: str
    scopes: frozenset[str]
    oauth_verified: bool = False


@dataclass(frozen=True)
class MCPPolicy:
    allowed_tools: frozenset[str]
    required_scope: str = "nous:invoke"
    require_oauth_for_http: bool = True
    allow_mutations: bool = False


class MCPHostAdapter:
    """Policy host for local MCP-style tools; it does not open a network listener."""

    def __init__(self, policy: MCPPolicy) -> None:
        self.policy = policy
        self._tools: dict[str, MCPToolSpec] = {}

    def register(self, tool: MCPToolSpec) -> None:
        if tool.name in self._tools:
            raise ContractError(f"duplicate MCP tool: {tool.name}")
        self._tools[tool.name] = tool

    def invoke(self, name: str, arguments: Mapping[str, Any], auth: MCPAuthContext, *, transport: str = "local") -> Mapping[str, Any]:
        if name not in self.policy.allowed_tools or name not in self._tools:
            raise PolicyDenied(f"MCP tool is not allowed: {name}")
        if self.policy.required_scope not in auth.scopes:
            raise PolicyDenied("MCP scope is missing")
        if transport == "http" and self.policy.require_oauth_for_http and not auth.oauth_verified:
            raise PolicyDenied("HTTP MCP invocation requires verified OAuth")
        tool = self._tools[name]
        if tool.mutating and not self.policy.allow_mutations:
            raise PolicyDenied("MCP mutation is disabled by policy")
        return dict(tool.handler(arguments))


class MCPHTTPGateway:
    """Translate an already-authenticated HTTP request into the policy host.

    The gateway deliberately does not bind a socket; deployment owns the HTTP
    server, TLS, and OAuth verifier and passes the verified auth context here.
    """

    def __init__(self, host: MCPHostAdapter) -> None:
        self.host = host

    def handle(self, request: Mapping[str, Any], auth: MCPAuthContext) -> Mapping[str, Any]:
        name = request.get("tool")
        arguments = request.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, Mapping):
            raise ContractError("MCP HTTP request requires tool and object arguments")
        return self.host.invoke(name, arguments, auth, transport="http")
