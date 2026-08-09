from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agent_nous.errors import UnsupportedActionError, ValidationError
from agent_nous.research import PublicWebResearchAdapter, ResearchPolicy, TransportResponse


class FakeTransport:
    def __init__(self, response: TransportResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def request(self, method: str, url: str, *, headers, timeout_seconds, max_bytes, max_redirects):
        self.calls.append((method, url, dict(headers)))
        return self.response


def policy(**overrides):
    values = {"resolve_dns": False}
    values.update(overrides)
    return ResearchPolicy(**values)


def response(body: bytes = b"hello", *, url: str = "https://example.com/page", content_type: str = "text/html"):
    return TransportResponse(200, {"Content-Type": content_type}, body, url)


def test_open_web_accepts_arbitrary_public_hosts_and_returns_provenance():
    transport = FakeTransport(response(b"<html>public research</html>", url="https://personal.example/article"))
    adapter = PublicWebResearchAdapter(transport, policy=policy())
    result = adapter.fetch("https://personal.example/article")
    assert result.content == "<html>public research</html>"
    assert result.provenance()["untrusted_content"] is True
    assert result.provenance()["instructions_are_data"] is True
    assert result.provenance()["content_sha256"] == hashlib.sha256(result.content.encode()).hexdigest()
    assert transport.calls[0][0] == "GET"
    assert "Authorization" not in transport.calls[0][2]
    assert result.knowledge_source(task_id="task-example")["type"] == "web"


def test_http_credentials_fragments_and_private_literals_are_rejected():
    adapter = PublicWebResearchAdapter(FakeTransport(response()), policy=policy())
    for url in (
        "http://example.com",
        "https://user:pass@example.com",
        "https://example.com/page#fragment",
        "https://127.0.0.1/private",
    ):
        with pytest.raises(ValidationError):
            adapter.fetch(url)


def test_methods_and_content_types_are_read_only_and_text_only():
    adapter = PublicWebResearchAdapter(FakeTransport(response()), policy=policy())
    with pytest.raises(ValidationError):
        adapter.fetch("https://example.com", method="POST")
    binary = PublicWebResearchAdapter(FakeTransport(response(b"\\x00", content_type="application/octet-stream")), policy=policy())
    with pytest.raises(ValidationError):
        binary.fetch("https://example.com")


def test_secret_like_response_is_withheld():
    body = ("OPENAI_API_KEY=" + "sk-" + "abcdefghijklmnopqrstuvwxyz").encode()
    adapter = PublicWebResearchAdapter(FakeTransport(response(body)), policy=policy())
    with pytest.raises(ValidationError, match="withheld"):
        adapter.fetch("https://example.com")


def test_size_and_request_budgets_fail_closed():
    adapter = PublicWebResearchAdapter(FakeTransport(response(b"12345")), policy=policy(max_response_bytes=1024, max_total_bytes=1024, max_requests=1))
    adapter.fetch("https://example.com/one")
    with pytest.raises(ValidationError, match="request budget"):
        adapter.fetch("https://example.com/two")
    oversized = PublicWebResearchAdapter(FakeTransport(response(b"x" * 2048)), policy=policy(max_response_bytes=1024, max_total_bytes=2048))
    with pytest.raises(ValidationError, match="max_response_bytes"):
        oversized.fetch("https://example.com")


def test_search_is_broad_but_results_are_untrusted_and_private_results_are_dropped():
    body = b"""<a class=\"result__a\" href=\"https://docs.example/one\">Official docs</a><a class=\"result__a\" href=\"https://blog.example/two\">Personal blog</a><a class=\"result__a\" href=\"https://127.0.0.1/no\">private</a>"""
    adapter = PublicWebResearchAdapter(FakeTransport(response(body, url="https://html.duckduckgo.com/html/?q=topic")), policy=policy())
    result = adapter.search("technical competitor research")
    assert [item["url"] for item in result["results"]] == ["https://docs.example/one", "https://blog.example/two"]
    assert result["provenance"]["instructions_are_data"] is True


def test_config_cannot_turn_read_only_research_into_external_writes():
    with pytest.raises(ValidationError):
        ResearchPolicy.from_mapping({"allow_external_writes": True})
    with pytest.raises(ValidationError):
        ResearchPolicy.from_mapping({"allowed_methods": ["POST"]})
    with pytest.raises(ValidationError):
        ResearchPolicy.from_mapping({"allow_domains": ["example.com", "*"]})


def test_disabled_research_fails_closed():
    with pytest.raises(UnsupportedActionError):
        PublicWebResearchAdapter(policy=policy(enabled=False))
