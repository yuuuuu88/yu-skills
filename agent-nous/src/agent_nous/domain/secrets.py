from __future__ import annotations

"""Small, deterministic persistence-time secret gate.

This is deliberately conservative.  It does not attempt to identify every
possible credential; instead it blocks the high-confidence forms that must
never enter canonical Task files and returns a value-free summary suitable for
an audit event.  A caller must never include the original matching text in an
exception, event, or diagnostic.
"""

import re
from collections import Counter
from typing import Any


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----")),
    ("openai_token", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("authorization_header", re.compile(r"(?i)\bauthorization\s*:\s*(?:bearer|basic)\s+\S+")),
    # Keep named assignment detection high-confidence.  Runtime expressions
    ("credential_assignment", re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?key|secret|token|password)[ \t]*[:=][ \t]*"
        r"(?!(?:str|bytes|int|float|bool|Path|dict|list|tuple|set|os|context|lease|metadata|state|action|grant|request)(?:\b|[_\[]))"
        r"[^ \t\r\n]{8,}"
    )),
)


def secret_summary(value: Any) -> dict[str, object]:
    """Return a value-free high-confidence secret summary.

    The return value is empty when the candidate contains no detected secret.
    It contains detector names and counts only: no match, prefix, hash, or
    reversible representation is retained.
    """

    found: Counter[str] = Counter()

    def scan(candidate: Any) -> None:
        if isinstance(candidate, str):
            for kind, pattern in _PATTERNS:
                count = len(pattern.findall(candidate))
                if count:
                    found[kind] += count
        elif isinstance(candidate, dict):
            for key, nested in candidate.items():
                # Keys are metadata too; scan them because an accidental
                # credential-bearing URL can otherwise be put into a key.
                scan(str(key))
                scan(nested)
        elif isinstance(candidate, (list, tuple, set)):
            for nested in candidate:
                scan(nested)

    scan(value)
    if not found:
        return {}
    return {
        "detector_ids": sorted(found),
        "counts": {kind: found[kind] for kind in sorted(found)},
    }
