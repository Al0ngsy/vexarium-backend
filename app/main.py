"""VEXARIUM FastAPI application entrypoint."""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.health import router as health_router
from app.api.analysis import router as analysis_router
from app.api.portfolio import router as portfolio_router
from app.api.options import router as options_router
from app.api.strategies import router as strategies_router
from app.config import settings
from app.middleware.logging import request_logging_middleware
from app.middleware.rate_limit import get_limiter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

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

# --- Rate limiting ---
limiter = get_limiter()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Request logging ---
app.middleware("http")(request_logging_middleware)

# All API routes under /api/v1 prefix
app.include_router(health_router, prefix="/api/v1/health", tags=["health"])
app.include_router(analysis_router, prefix="/api/v1")
app.include_router(portfolio_router, prefix="/api/v1")
app.include_router(options_router, prefix="/api/v1")
app.include_router(strategies_router, prefix="/api/v1")


@app.get("/health")
async def health_root() -> dict[str, str]:
    """Root-level liveness probe (no prefix)."""
    return {"status": "ok"}
