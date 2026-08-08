import asyncio
import json
import threading
from typing import Any, Optional

import redis.asyncio as aioredis
from cachetools import TTLCache

from ..config import settings

_ttl_cache = TTLCache(maxsize=1000, ttl=3600)

# In-memory single-flight locks (fallback when Redis is not configured).
_locks: dict[str, asyncio.Lock] = {}

_redis_client: Optional[aioredis.Redis] = None
_redis_url: str = ""


def _redis() -> Optional[aioredis.Redis]:
    """Lazy shared Redis client — one per Redis URL, reused for the process
    lifetime (redis-py pools connections internally). Rebuilt if the URL
    changes (tests swap settings.redis_url per case)."""
    global _redis_client, _redis_url
    if not settings.redis_url:
        return None
    if _redis_client is None or _redis_url != settings.redis_url:
        _redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        _redis_url = settings.redis_url
    return _redis_client


def run_coro(coro):
    """Run an async cache call whether or not an event loop is active.

    Returns None on failure (callers treat a failed cache op as a miss).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    result: dict = {}

    def _worker():
        try:
            result["value"] = asyncio.run(coro)
        except Exception:
            result["error"] = True

    t = threading.Thread(target=_worker)
    t.start()
    t.join()
    if "error" in result:
        return None
    return result.get("value")


async def lock_acquire(key: str, ttl: int = 120) -> bool:
    """Try to take a distributed single-flight lock (non-blocking).

    Returns True if THIS caller won the lock (and must do the work), False if
    another request already holds it (and this caller should wait for the
    result). Redis SET NX EX; in-memory asyncio.Lock fallback for local dev.
    """
    client = _redis()
    if client is not None:
        try:
            return bool(await client.set(key, "1", nx=True, ex=ttl))
        except Exception:
            return True  # fail-open: if Redis hiccups, let the request proceed
    lock = _locks.setdefault(key, asyncio.Lock())
    try:
        await asyncio.wait_for(lock.acquire(), timeout=0.01)
        return True
    except asyncio.TimeoutError:
        return False


async def lock_held(key: str) -> bool:
    """Is the single-flight lock currently held by someone?"""
    client = _redis()
    if client is not None:
        try:
            return bool(await client.exists(key))
        except Exception:
            return False
    lock = _locks.get(key)
    return bool(lock and lock.locked())


async def lock_release(key: str) -> None:
    """Release a single-flight lock held by this caller."""
    client = _redis()
    if client is not None:
        try:
            await client.delete(key)
        except Exception:
            pass
        return
    lock = _locks.get(key)
    if lock and lock.locked():
        lock.release()


async def cache_get(key: str) -> Optional[Any]:
    client = _redis()
    if client is not None:
        try:
            val = await client.get(key)
            return json.loads(val) if val else None
        except Exception:
            return None
    return _ttl_cache.get(key)


async def cache_set(key: str, value: Any, ttl: int = 3600) -> None:
    client = _redis()
    if client is not None:
        try:
            # default=str: news articles (and other payloads) can contain
            # datetime objects from model_dump() — without this, json.dumps
            # raises and the value is silently never cached (AI/news keys).
            await client.set(key, json.dumps(value, default=str), ex=ttl)
        except Exception:
            pass
        return
    _ttl_cache[key] = value


async def cache_delete(key: str) -> None:
    client = _redis()
    if client is not None:
        try:
            await client.delete(key)
        except Exception:
            pass
        return
    _ttl_cache.pop(key, None)


CACHE_TTL_BARS = 6 * 3600      # daily bars change once per day
CACHE_TTL_QUOTE = 5            # seconds during market hours
CACHE_TTL_NEWS = 30 * 60       # 30 min
CACHE_TTL_AI = 24 * 3600       # AI analysis per symbol per day
CACHE_TTL_ANALYSIS = 24 * 3600 # computed analysis per symbol per day
CACHE_TTL_OPTION_CHAIN = 15    # indicative/delayed options quotes; short TTL


def bars_key(symbol: str, timeframe: str = "1d") -> str:
    return f"bars:{symbol}:{timeframe}"


def quote_key(symbol: str) -> str:
    return f"quote:{symbol}"


def news_key(symbol: str) -> str:
    return f"news:{symbol}"


def option_chain_key(symbol: str) -> str:
    """Option chain market-data snapshot for a symbol (indicative feed).

    Options quotes/trades move intraday but are delayed (free tier), so a short
    TTL is still safe and keeps repeated page loads cheap.
    """
    return f"optchain:{symbol}"


def ai_key(symbol: str) -> str:
    from datetime import date
    return f"ai:{symbol}:{date.today().isoformat()}"


def ai_lock_key(symbol: str) -> str:
    """Single-flight lock so concurrent AI requests for the same symbol wait
    for the in-flight LLM call instead of firing duplicate ones."""
    from datetime import date
    return f"ai_lock:{symbol}:{date.today().isoformat()}"


def analysis_key(symbol: str) -> str:
    """Daily analysis result for a symbol. Indicators are computed from daily bars,
    so the computed result only changes once per day -> cache for the whole day."""
    from datetime import date
    return f"analysis:{symbol}:{date.today().isoformat()}"
