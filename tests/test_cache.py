"""Tests for the caching service (in-memory path).

Redis is not configured (settings.redis_url stays empty), so these tests
exercise the TTLCache in-memory fallback.
"""
from __future__ import annotations

import time

import pytest

from app.services import cache as cache_module
from app.services.cache import (
    cache_delete,
    cache_get,
    cache_set,
    ai_key,
    bars_key,
    news_key,
    quote_key,
)


@pytest.mark.asyncio
async def test_cache_set_get():
    await cache_set("foo", {"bar": 1})
    assert await cache_get("foo") == {"bar": 1}


@pytest.mark.asyncio
async def test_cache_get_missing():
    assert await cache_get("does-not-exist") is None


@pytest.mark.asyncio
async def test_cache_delete():
    await cache_set("foo", 42)
    assert await cache_get("foo") == 42
    await cache_delete("foo")
    assert await cache_get("foo") is None


@pytest.mark.asyncio
async def test_cache_ttl_expiry(monkeypatch):
    # Use a tiny TTL so the entry expires almost immediately.
    from cachetools import TTLCache

    monkeypatch.setattr(cache_module, "_ttl_cache", TTLCache(maxsize=1000, ttl=0.1))
    await cache_set("ephemeral", "value")
    assert await cache_get("ephemeral") == "value"
    time.sleep(0.2)
    assert await cache_get("ephemeral") is None


def test_keys():
    assert bars_key("AAPL") == "bars:AAPL"
    assert quote_key("AAPL") == "quote:AAPL"
    assert news_key("AAPL") == "news:AAPL"
    assert ai_key("AAPL").startswith("ai:AAPL:")
    # ai_key includes today's date
    from datetime import date
    assert ai_key("AAPL") == f"ai:AAPL:{date.today().isoformat()}"
