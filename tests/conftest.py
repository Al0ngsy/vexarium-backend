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
    # Tests must stay hermetic: never hit Postgres/Redis (which a local .env may
    # configure). Force empty URLs so the in-memory stores are used.
    settings.database_url = ""
    settings.redis_url = ""
    # Keep billing tests hermetic: never hit the live Stripe API from tests.
    settings.stripe_secret_key = ""
    settings.stripe_price_id = ""
    settings.stripe_webhook_secret = ""
    # Data-provider keys must not leak from a local .env into tests — a live
    # Twelve Data/Finnhub key would make get_stock_bars/finnhub tests do real
    # network calls. Tests patch keys explicitly when they exercise those paths.
    settings.twelvedata_api_key = ""
    settings.finnhub_api_key = ""
    yield
    settings.dev_force_pro = False


@pytest.fixture(autouse=True)
def _clear_cache():
    cache_module._ttl_cache.clear()
    # Drop the lazy Redis client too — tests swap settings.redis_url and patch
    # aioredis.from_url per case; a cached client would leak between tests.
    cache_module._redis_client = None
    cache_module._redis_url = ""
    yield
    cache_module._ttl_cache.clear()
    cache_module._redis_client = None
    cache_module._redis_url = ""


@pytest.fixture(autouse=True)
def _reset_user_store():
    # The user store is a process-wide singleton; reset it so tests are isolated.
    from app.repositories.users import reset_user_store
    from app.api.trades import reset_trade_repo
    reset_user_store()
    reset_trade_repo()
    yield
    reset_user_store()
    reset_trade_repo()
