"""Health-check endpoint."""
import json

from fastapi import APIRouter, Response

from ..config import settings

router = APIRouter()


@router.get("/")
async def health() -> dict[str, str]:
    """Liveness probe."""
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
            # Use asyncpg directly (the URL may be the async asyncpg driver form,
            # which a sync sqlalchemy engine can't consume).
            from sqlalchemy.ext.asyncio import create_async_engine
            url = settings.database_url
            if url.startswith("postgresql://") or url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql+asyncpg://", 1)
                url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
            eng = create_async_engine(url)
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
    return Response(
        content=json.dumps(
            {"status": "ok" if all_ok else "degraded", "dependencies": deps}
        ),
        status_code=200 if all_ok else 503,
        media_type="application/json",
    )
