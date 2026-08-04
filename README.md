# VEXARIUM Backend

Trading signal and options analysis API — **informational only, not financial advice.**

FastAPI + Alpaca market data + technical indicators + options Greeks/strategy analysis, with JWT auth, Stripe subscriptions (scaffold), tier-based feature gating, caching, rate limiting, and a Postgres-ready repository layer.

## Features

- **Technical analysis** — 5 free indicators (RSI, SMA/EMA, MACD, Bollinger Bands, Stochastic) with per-indicator verdicts (strong_buy → strong_sell) + overall verdict; 5 Pro indicators (ATR, ADX, OBV, VWAP, Ichimoku) behind tier gating.
- **Options analysis** — option chain, Greeks (delta/gamma/theta/vega/rho), payoff timeline, breakeven, and beginner-friendly strategy cards (Long Call, Cash-Secured Put, Covered Call, Short Put, Bull Call Spread) with P/L curves.
- **Portfolio stance** — deterministic HOLD / TAKE PROFIT / CUT LOSS advice for every trade type (stock, ETF, index, option — options include theta-decay logic).
- **Auth** — JWT (HS256) register/login, bcrypt password hashing, tier claims.
- **Billing** — Stripe checkout + webhook scaffolding (upgrade wiring lives in `stripe_service.handle_webhook`).
- **Production** — Redis-backed caching with in-memory fallback, slowapi rate limiting, request logging, Sentry hook, `/health` + `/health/ready` probes, Docker, GitHub Actions CI.

## Tech Stack

Python 3.11+ · FastAPI · Uvicorn · pandas + pandas-ta-remake · alpaca-py · SQLAlchemy · pydantic-settings · slowapi · python-jose + passlib/bcrypt · stripe · redis · cachetools · pytest

## Project Structure

```
backend/
├── app/
│   ├── main.py               # FastAPI app, CORS, rate limiter, Sentry, router mounting
│   ├── config.py             # pydantic-settings; env-driven (see .env.example)
│   ├── api/                  # routers: health, auth, analysis, options, strategies,
│   │                         #   portfolio, trades, billing
│   ├── services/             # alpaca_client, indicator_engine, verdicts, options_analyzer,
│   │                         #   strategy_engine, stance, ai_analyzer, news_service,
│   │                         #   cache, auth, stripe_service, worker
│   ├── middleware/           # rate_limit, validation, tier_gating, logging
│   ├── schemas/              # pydantic response/request models
│   ├── models/               # SQLAlchemy models (User, Trade)
│   └── repositories/         # TradeRepository protocol + in-memory impl (Postgres stub)
├── tests/                    # 17 test files, ~130 tests
├── Dockerfile
├── .env.example
└── pyproject.toml            # uv-managed
```

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
cd backend
uv venv .venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env   # fill in your keys
```

### Environment variables (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` | — | Alpaca market-data credentials (paper keys work) |
| `ALPACA_PAPER` | `true` | Use paper-trading endpoints |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | — / deepseek / deepseek-chat | AI analysis provider (OpenAI-compatible) |
| `JWT_SECRET` | `change-me-in-production` | **Must be set in production** (startup fails if `VEXARIUM_ENV=production` + placeholder) |
| `JWT_EXPIRY_HOURS` | `24` | Access-token lifetime |
| `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` | — | Stripe billing |
| `SENTRY_DSN` | — | Error tracking (optional) |
| `REDIS_URL` | — | Cache; empty = in-memory fallback |
| `DATABASE_URL` | — | Postgres (optional; repo layer is in-memory until wired) |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated allowlist |
| `RATE_LIMIT_FREE` / `RATE_LIMIT_PRO` | `30` / `200` | Requests per minute per IP |
| `TAKE_PROFIT_THRESHOLD` / `CUT_LOSS_THRESHOLD` | `0.10` / `-0.08` | Stance engine thresholds |

> **Note:** do not put inline comments on the same line as values in `.env` — pydantic-settings reads the whole line as the value.

## Run

```bash
uvicorn app.main:app --reload          # dev, http://localhost:8000
```

Interactive docs: http://localhost:8000/docs (OpenAPI).

## Test

```bash
pytest tests/ -v
```

> If this repo is used inside the Hermes agent environment, run `env -u PYTHONPATH .venv/bin/python -m pytest tests/ -v` — the session's `PYTHONPATH` shadows the project venv.

## API Overview

All routes under `/api/v1`:

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health`, `/health/ready` | — | Liveness / readiness (deps status) |
| POST | `/auth/register` | — | Create account (bcrypt + JWT) |
| POST | `/auth/login` | — | Login |
| GET | `/auth/me?token=` | token | Current user |
| POST | `/analysis` | — | 5-indicator analysis + overall verdict |
| POST | `/analysis/extended` | Pro token | 10-indicator analysis |
| GET | `/options/{symbol}/chain` | — | Option contracts for a window |
| GET | `/options/{symbol}/payoff` | — | Greeks + payoff timeline for a contract |
| GET | `/options/{symbol}/strategies` | — | Ranked strategy cards |
| POST | `/portfolio/stance` | — | HOLD / TAKE PROFIT / CUT LOSS |
| GET/POST/DELETE | `/trades` | token | Saved trades CRUD (ownership-scoped) |
| POST | `/billing/checkout` | token | Stripe checkout session |
| POST | `/billing/webhook` | Stripe | Subscription events |

## Docker

```bash
docker compose up --build    # api + worker + postgres + redis
docker compose config        # validate
```

## CI

GitHub Actions (`.github/workflows/ci.yml`): backend `pytest` + frontend `check`/`build` on push/PR. Deploy workflow is a placeholder until hosting is configured.

---

**⚠️ Disclaimer:** VEXARIUM provides mathematical indicator computations and educational analysis only. Nothing here is financial or investment advice. Trading involves significant risk. See the frontend legal pages.
