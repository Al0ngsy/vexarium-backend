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

    # --- LLM (DeepSeek-compatible OpenAI endpoint) ---
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"

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

    # --- Featured symbols (Pro-preview) ---
    # Symbols that show the Pro AI analysis free as a conversion teaser.
    # The AI result is cached per-symbol-per-day, so this is cheap to serve.
    featured_symbols: str = "AAPL,MSFT,TSLA,SPY,NVDA,AMZN,GOOGL,META"

    @property
    def cors_origin_list(self) -> list[str]:
        """Parse `cors_origins` into a list, splitting on commas."""
        if not self.cors_origins:
            return []
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def featured_symbol_list(self) -> list[str]:
        """Parse `featured_symbols` into an uppercase list."""
        return [s.strip().upper() for s in self.featured_symbols.split(",") if s.strip()]

    @model_validator(mode="after")
    def _check_production_jwt(self):
        if self.vexarium_env == "production" and self.jwt_secret == "change-me-in-production":
            raise ValueError("jwt_secret must be set in production")
        return self


settings = Settings()