"""Application settings via pydantic-settings.

Loads from environment variables and/or a local .env file. Instantiate
`settings = Settings()` at module level so importers can do
`from app.config import settings`.
"""
from __future__ import annotations

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for VEXARIUM backend."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Alpaca ---
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_paper: bool = True

    # --- LLM (OpenCode Zen — OpenAI-compatible endpoint, free tier) ---
    llm_base_url: str = "https://opencode.ai/zen/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-v4-flash-free"
    # Comma-separated free fallback models, tried in order when the primary
    # fails (rate limit, outage). All are OpenCode Zen free-tier IDs.
    llm_fallback_models: str = (
        "big-pickle,mimo-v2.5-free,ling-3.0-tiny-free,laguna-s-2.1-free,"
        "longcat-2.0-free,north-mini-code-free,nemotron-3-ultra-free"
    )
    # Paid terminal fallback, appended after the free chain in
    # ai_analyzer._model_chain(). Empty string disables it (a rate-limited
    # free tier then surfaces as "temporarily unavailable").
    llm_paid_fallback: str = "deepseek-v4-flash"

    # --- Finnhub (real-time intraday bars; free tier, 60 calls/min) ---
    # Empty key → intraday bars keep using Alpaca (15-min delayed) + Yahoo.
    finnhub_api_key: str = ""

    # --- CORS ---
    cors_origins: str = "http://localhost:5173"

    # --- Infra ---
    redis_url: str = ""
    sentry_dsn: str = ""
    database_url: str = ""

    # --- Stripe ---
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_id: str = ""  # Pro subscription price id from the Stripe dashboard
    # Frontend URLs for the Stripe Checkout redirect (override per environment).
    stripe_success_url: str = "http://localhost:5173/pricing?success=1"
    stripe_cancel_url: str = "http://localhost:5173/pricing?cancelled=1"

    # --- Auth ---
    jwt_secret: str = "change-me-in-production"
    jwt_expiry_hours: int = 24

    # --- Environment ---
    vexarium_env: str = "development"
    dev_force_pro: bool = False  # dev-only: allow Pro-tier access for everyone

    # --- Trading thresholds ---
    take_profit_threshold: float = 0.10
    cut_loss_threshold: float = -0.08

    # --- Rate limits ---
    rate_limit_free: int = 30
    rate_limit_pro: int = 200
    # AI is free for everyone: tight per-IP limit (the result is cached 24h
    # per symbol, so the limit only throttles abuse, not legit use).
    rate_limit_ai: int = 10

    @property
    def cors_origin_list(self) -> list[str]:
        """Parse `cors_origins` into a list, splitting on commas."""
        if not self.cors_origins:
            return []
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @model_validator(mode="after")
    def _check_production_jwt(self):
        if self.vexarium_env == "production" and self.jwt_secret == "change-me-in-production":
            raise ValueError("jwt_secret must be set in production")
        return self


settings = Settings()