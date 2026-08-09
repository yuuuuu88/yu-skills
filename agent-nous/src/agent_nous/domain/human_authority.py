from __future__ import annotations

"""Verification boundary for approval-bearing Human answers.

Phase agents can propose questions but cannot manufacture a Human approval.
The trusted host configures only an Ed25519 *public* key.  A Human-facing
surface signs the exact answer envelope with its private key; taskctl verifies
the signature and deliberately never persists it.  If the host has not
configured this boundary, approval-bearing answers are unsupported rather
than silently treating an arbitrary actor string as a Human decision.
"""

import base64
import json
from typing import Any

try:  # pragma: no cover - exercised through the public verifier below
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError:  # fail closed in a partial installation
    InvalidSignature = Exception  # type: ignore[assignment,misc]
    serialization = None  # type: ignore[assignment]
    Ed25519PublicKey = object  # type: ignore[assignment,misc]


_APPROVAL_ANSWER_FIELDS = (
    "answer_type",
    "selected_option_id",
    "answer_text",
    "applied_scope",
    "answer_effect",
    "resume_target",
)


def approval_answer_message(task_id: str, question_id: str, answered_by: str, answer: dict[str, Any]) -> bytes:
    """Canonical bytes that the trusted Human surface signs."""
    value = {
        "contract_version": 1,
        "task_id": task_id,
        "question_id": question_id,
        "answered_by": answered_by,
        **{field: answer.get(field) for field in _APPROVAL_ANSWER_FIELDS},
    }
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class HumanApprovalAuthority:
    """Pinned public-key verifier; no private material reaches Task files.

    The trusted host injects this authority when it creates the Task runtime.
    It intentionally does *not* read a process environment variable or a
    Task-controlled file: phase code could choose either at runtime and sign
    its own answer.  The default instance has no key and fails closed.
    """

    def __init__(self, public_key_pem: str | None = None) -> None:
        self._public_key_pem = public_key_pem

    def verify(self, task_id: str, question_id: str, answered_by: str, answer: dict[str, Any], attestation: str | None) -> bool:
        pem = self._public_key_pem
        if not pem or not attestation or serialization is None:
            return False
        try:
            public = serialization.load_pem_public_key(pem.encode("utf-8"))
            if not isinstance(public, Ed25519PublicKey):
                return False
            signature = base64.b64decode(attestation.encode("ascii"), validate=True)
            public.verify(signature, approval_answer_message(task_id, question_id, answered_by, answer))
            return True
        except (ValueError, TypeError, InvalidSignature):
            return False
