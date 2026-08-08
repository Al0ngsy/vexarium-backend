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
    ai_lock_key,
    bars_key,
    news_key,
    quote_key,
)


@pytest.mark.asyncio
async def test_cache_set_get():
    await cache_set("foo", {"bar": 1})
    assert await cache_get("foo") == {"bar": 1}


@pytest.mark.asyncio
async def test_cache_set_with_datetime(monkeypatch):
    """Payloads with datetime objects (news articles from model_dump) must
    serialize in the Redis path — previously json.dumps raised inside the
    swallowed except, so the value was silently never cached (AI/news keys)."""
    from datetime import datetime, timezone

    captured = {}

    class FakeRedis:
        async def set(self, key, value, ex=None):
            captured["key"], captured["value"] = key, value

        async def aclose(self):
            pass

    monkeypatch.setattr(cache_module.settings, "redis_url", "redis://fake")
    import redis.asyncio as aioredis

    monkeypatch.setattr(aioredis, "from_url", lambda *a, **k: FakeRedis())

    payload = {
        "headline": "AAPL surges",
        "published_at": datetime(2026, 8, 6, tzinfo=timezone.utc),
    }
    await cache_set("ai:AAPL:2026-08-06", payload, ttl=86400)
    assert captured["key"] == "ai:AAPL:2026-08-06"
    assert captured["value"].startswith('{"headline": "AAPL surges"')
    assert captured["value"].endswith("}")  # serialized without raising


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


@pytest.mark.asyncio
async def test_lock_acquire_release_redis(monkeypatch):
    """Single-flight lock: first caller wins, second is blocked until release."""
    state = {"lock": None}

    class FakeRedis:
        async def set(self, key, value, ex=None, nx=False):
            if nx:
                if state["lock"] is not None:
                    return False
                state["lock"] = key
                return True
            return True

        async def exists(self, key):
            return 1 if state["lock"] is not None else 0

        async def delete(self, key):
            state["lock"] = None

        async def aclose(self):
            pass

    monkeypatch.setattr(cache_module.settings, "redis_url", "redis://fake")
    import redis.asyncio as aioredis

    monkeypatch.setattr(aioredis, "from_url", lambda *a, **k: FakeRedis())

    key = "ai_lock:AAPL:2026-08-06"
    assert await cache_module.lock_acquire(key, ttl=180) is True
    assert await cache_module.lock_acquire(key, ttl=180) is False  # held
    assert await cache_module.lock_held(key) is True
    await cache_module.lock_release(key)
    assert await cache_module.lock_held(key) is False
    assert await cache_module.lock_acquire(key, ttl=180) is True  # free again


@pytest.mark.asyncio
async def test_lock_in_memory_fallback():
    """Without Redis, the in-memory asyncio lock still serializes."""
    import asyncio

    key = "ai_lock:TEST:inmemory"
    assert await cache_module.lock_acquire(key) is True
    assert await cache_module.lock_acquire(key) is False
    assert await cache_module.lock_held(key) is True
    await cache_module.lock_release(key)
    assert await cache_module.lock_held(key) is False


def test_keys():
    assert bars_key("AAPL") == "bars:AAPL:1d"
    assert quote_key("AAPL") == "quote:AAPL"
    assert news_key("AAPL") == "news:AAPL"
    assert ai_key("AAPL").startswith("ai:AAPL:")
    # ai_key includes today's date
    from datetime import date
    assert ai_key("AAPL") == f"ai:AAPL:{date.today().isoformat()}"
    assert ai_lock_key("AAPL").startswith("ai_lock:AAPL:")
