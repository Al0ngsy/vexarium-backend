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
            from ..services.cache import _get_redis

            r = _get_redis()
            if r:
                await r.ping()
                deps["redis"] = "ok"
        except Exception:
            deps["redis"] = "down"
    if settings.database_url:
        deps["database"] = "unknown"
        try:
            import sqlalchemy

            engine = sqlalchemy.create_engine(settings.database_url)
            with engine.connect() as conn:
                conn.execute(sqlalchemy.text("SELECT 1"))
            deps["database"] = "ok"
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
