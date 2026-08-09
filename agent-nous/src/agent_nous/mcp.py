from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

from jsonschema import Draft202012Validator

from .errors import ValidationError


_SAFE_VERIFIER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
_FORBIDDEN_TOKEN_KEYS = {"access_token", "authorization", "bearer_token", "client_secret", "refresh_token"}


def _https_url(value: str, label: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.fragment:
        raise ValidationError(f"{label} must be an https URL without a fragment")
    return value


@dataclass(frozen=True)
class McpAuthorizationConfig:
    authorization_endpoint: str
    token_endpoint: str
    resource: str
    client_id: str
    redirect_uri: str
    scopes: tuple[str, ...] = ()

    def validate(self) -> None:
        _https_url(self.authorization_endpoint, "authorization_endpoint")
        _https_url(self.token_endpoint, "token_endpoint")
        _https_url(self.resource, "resource")
        _https_url(self.redirect_uri, "redirect_uri")
        if not self.client_id or any(char in self.client_id for char in "\r\n"):
            raise ValidationError("client_id must be a nonempty header-safe value")
        if any(not isinstance(scope, str) or not scope.strip() for scope in self.scopes):
            raise ValidationError("scopes must contain nonempty strings")

    def authorization_url(self, *, state: str, code_verifier: str | None = None) -> tuple[str, str]:
        self.validate()
        if not state or any(char in state for char in "\r\n"):
            raise ValidationError("OAuth state must be nonempty and header-safe")
        verifier = code_verifier or secrets.token_urlsafe(32)
        if not _SAFE_VERIFIER.fullmatch(verifier):
            raise ValidationError("PKCE code_verifier must be 43-128 RFC7636 characters")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": self.resource,
        }
        if self.scopes:
            params["scope"] = " ".join(self.scopes)
        parsed = urlparse(self.authorization_endpoint)
        existing = dict(parse_qsl(parsed.query, keep_blank_values=True))
        existing.update(params)
        return urlunparse(parsed._replace(query=urlencode(existing))), verifier


@dataclass(frozen=True)
class McpTool:
    name: str
    input_schema: Mapping[str, Any]
    description: str = ""
    read_only: bool = False
    destructive: bool = False

    def validate(self, arguments: Mapping[str, Any]) -> None:
        if not self.name or any(char in self.name for char in "\r\n"):
            raise ValidationError("MCP tool name must be nonempty and header-safe")
        if self.destructive and self.read_only:
            raise ValidationError("MCP tool cannot be both destructive and read_only")
        try:
            Draft202012Validator.check_schema(dict(self.input_schema))
        except Exception as exc:
            raise ValidationError("MCP tool input_schema is invalid") from exc
        if not isinstance(arguments, Mapping):
            raise ValidationError("MCP tool arguments must be an object")
        if any(key in _FORBIDDEN_TOKEN_KEYS for key in arguments):
            raise ValidationError("MCP tool arguments must not contain bearer or client credentials")
        validator = Draft202012Validator(dict(self.input_schema))
        errors = sorted(validator.iter_errors(dict(arguments)), key=lambda error: list(error.path))
        if errors:
            raise ValidationError(f"MCP tool arguments failed schema validation: {errors[0].message}")


class McpTransport(Protocol):
    def send(self, request: Mapping[str, Any], bearer_token: str) -> Mapping[str, Any]:
        ...


@dataclass
class McpConnector:
    """Policy-first MCP connector with an injected, separately governed transport.

    The core never performs network I/O. A host adapter supplies ``transport``;
    the bearer token is passed only to that adapter and is never placed in the
    JSON-RPC request or canonical result.
    """

    authorization: McpAuthorizationConfig
    transport: McpTransport
    tools: dict[str, McpTool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.authorization.validate()

    def register_tool(self, tool: McpTool) -> None:
        tool.validate({}) if tool.input_schema.get("type") == "object" and not tool.input_schema.get("required") else None
        if tool.name in self.tools:
            raise ValidationError(f"duplicate MCP tool: {tool.name}")
        self.tools[tool.name] = tool

    def call_tool(self, name: str, arguments: Mapping[str, Any], bearer_token: str, *, request_id: str | int = 1) -> Mapping[str, Any]:
        if not bearer_token or any(char in bearer_token for char in "\r\n"):
            raise ValidationError("MCP bearer token is required at the transport boundary")
        tool = self.tools.get(name)
        if tool is None:
            raise ValidationError(f"unknown MCP tool: {name}")
        tool.validate(arguments)
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": dict(arguments)},
        }
        response = self.transport.send(request, bearer_token)
        if not isinstance(response, Mapping) or response.get("jsonrpc") != "2.0":
            raise ValidationError("MCP transport returned an invalid JSON-RPC response")
        return dict(response)
