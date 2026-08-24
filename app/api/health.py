"""Health-check endpoint."""
import json

from fastapi import APIRouter, Response

from ..config import settings
from ..logging import get_logger

router = APIRouter()
logger = get_logger("health")


@router.get("/")
async def health() -> dict[str, str]:
    """Liveness probe."""
    logger.debug("health ok")
    return {"status": "ok"}


@router.get("/ready")
async def ready():
    """Readiness probe: liveness of dependencies (Redis, DB) if configured."""
    deps = {"api": "ok"}
    if settings.redis_url:
        deps["redis"] = "unknown"
        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(settings.redis_url, decode_responses=True)
            try:
                await client.ping()
                deps["redis"] = "ok"
            finally:
                await client.aclose()
        except Exception:
            deps["redis"] = "down"
    if settings.database_url:
        deps["database"] = "unknown"
        try:
            from ..db import async_engine_for_url
            eng = async_engine_for_url(settings.database_url)
            try:
                from sqlalchemy import text
                async with eng.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                deps["database"] = "ok"
            finally:
                await eng.dispose()
        except Exception:
            deps["database"] = "down"
    all_ok = all(v == "ok" for v in deps.values())
    logger.info("ready redis=%s database=%s status=%s", deps.get("redis"), deps.get("database"), "ok" if all_ok else "degraded")
    if not all_ok:
        logger.warning("ready degraded: %s", {k: v for k, v in deps.items() if v != "ok"})
    return Response(
        content=json.dumps(
            {"status": "ok" if all_ok else "degraded", "dependencies": deps}
        ),
        status_code=200 if all_ok else 503,
        media_type="application/json",
    )
