import json
from typing import Any, Optional
from cachetools import TTLCache

from ..config import settings

_ttl_cache = TTLCache(maxsize=1000, ttl=3600)
_redis = None


def _get_redis():
    global _redis
    if not settings.redis_url:
        return None
    if _redis is None:
        import redis.asyncio as aioredis
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def cache_get(key: str) -> Optional[Any]:
    r = _get_redis()
    if r:
        try:
            val = await r.get(key)
            return json.loads(val) if val else None
        except Exception:
            return None
    return _ttl_cache.get(key)


async def cache_set(key: str, value: Any, ttl: int = 3600) -> None:
    r = _get_redis()
    if r:
        try:
            await r.set(key, json.dumps(value), ex=ttl)
        except Exception:
            pass
        return
    _ttl_cache[key] = value


async def cache_delete(key: str) -> None:
    r = _get_redis()
    if r:
        try:
            await r.delete(key)
        except Exception:
            pass
        return
    _ttl_cache.pop(key, None)


CACHE_TTL_BARS = 6 * 3600      # daily bars change once per day
CACHE_TTL_QUOTE = 5            # seconds during market hours
CACHE_TTL_NEWS = 30 * 60       # 30 min
CACHE_TTL_AI = 24 * 3600       # AI analysis per symbol per day


def bars_key(symbol: str) -> str:
    return f"bars:{symbol}"


def quote_key(symbol: str) -> str:
    return f"quote:{symbol}"


def news_key(symbol: str) -> str:
    return f"news:{symbol}"


def ai_key(symbol: str) -> str:
    from datetime import date
    return f"ai:{symbol}:{date.today().isoformat()}"
