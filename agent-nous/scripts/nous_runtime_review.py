from __future__ import annotations
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
files = [
    'tests/test_observability.py',
    'tests/test_resources.py',
    'tests/test_mcp.py',
    'tests/test_trace_context.py',
    'tests/test_secrets.py',
]
raise SystemExit(pytest.main(['-q', '-p', 'no:cacheprovider', *files]))
