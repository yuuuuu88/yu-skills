from pathlib import Path

import pytest

from nous_pilot.context import build_repository_context
from nous_pilot.intake import normalize_request
from nous_pilot.models import ContractError


def test_normalize_request_requires_existing_repository(tmp_path: Path) -> None:
    with pytest.raises(ContractError):
        normalize_request({"request_id": "r1", "objective": "x", "repository_root": str(tmp_path / "missing")})


def test_context_hashes_allowed_files_and_excludes_runtime(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    (tmp_path / ".nous").mkdir()
    (tmp_path / ".nous" / "secret.json").write_text("runtime", encoding="utf-8")
    context = build_repository_context(tmp_path)
    assert [item.path for item in context.files] == ["README.md"]
    assert context.source_of_truth_paths == ("README.md",)
