"""Docs freshness gate: docs/API.md must match the OpenAPI schema and the
living docs must not contain known-stale tokens. Runs docs/scripts/docs_check.py
in the docs repo (checked out next to backend). Skipped when docs/ is absent
so the suite stays hermetic elsewhere.
"""
import subprocess
import sys
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[2] / "docs"

pytestmark = pytest.mark.skipif(
    not (DOCS / "scripts" / "docs_check.py").exists(),
    reason="docs repo not checked out next to backend",
)


def test_docs_are_current():
    r = subprocess.run(
        [sys.executable, str(DOCS / "scripts" / "docs_check.py")],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, (
        f"Docs are stale — run: cd backend && .venv/bin/python "
        f"../docs/scripts/generate_api_md.py\n{r.stdout}{r.stderr}"
    )
