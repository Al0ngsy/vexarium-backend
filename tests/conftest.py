"""Shared test fixtures.

The caching layer uses a process-wide in-memory TTLCache. Since many tests
reuse the same symbols (e.g. "AAPL"), a value cached by one test would
short-circuit a later test that expects to exercise the network path. Clear
the in-memory cache around every test to keep tests order-independent.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.services import cache as cache_module


@pytest.fixture(autouse=True)
def _test_env():
    # Keep tier-gating tests deterministic regardless of a local .env that may set
    # DEV_FORCE_PRO=true for manual dev preview. Tests always exercise the real gate.
    settings.dev_force_pro = False
    yield
    settings.dev_force_pro = False


@pytest.fixture(autouse=True)
def _clear_cache():
    cache_module._ttl_cache.clear()
    yield
    cache_module._ttl_cache.clear()
