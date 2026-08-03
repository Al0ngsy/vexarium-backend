"""VEXARIUM FastAPI application entrypoint."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.config import settings

# --- Optional Sentry initialization ---
if settings.sentry_dsn:
    try:
        import sentry_sdk  # type: ignore[import-not-found]

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            traces_sample_rate=0.1,
        )
    except Exception:
        # Sentry is optional; never block startup on init failure.
        pass

app = FastAPI(
    title="VEXARIUM API",
    description="Trading signal and options analysis tool — informational only, not financial advice",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# All API routes under /api/v1 prefix
app.include_router(health_router, prefix="/api/v1/health", tags=["health"])


@app.get("/health")
async def health_root() -> dict[str, str]:
    """Root-level liveness probe (no prefix)."""
    return {"status": "ok"}