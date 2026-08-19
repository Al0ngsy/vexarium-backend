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
│   │                         #   cache, auth, stripe_service, company_info
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
cd <vexarium-backend checkout>
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
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | — / https://opencode.ai/zen/go/v1 / muse-spark-1.2-contributor | AI analysis provider (OpenAI-compatible, OpenCode Go subscription). Usage limits: https://opencode.ai/docs/go/#usage-limits |
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
docker compose up --build    # api + postgres + redis
docker compose config        # validate
```

## CI

GitHub Actions (`.github/workflows/ci.yml`): backend `pytest` + frontend `yarn check`/`build` on push/PR. Deploy workflow is a placeholder until hosting is configured.

## Deploying the Backend

### Option A — Render (fastest to start, free tier)
1. Push the repo to GitHub (`Al0ngsy/vexarium-backend`).
2. Render dashboard → **New → Web Service** → connect the repo (root = repo root).
3. **Build command:** `pip install -e .`
4. **Start command:** `uvicorn app.main:app --host 0.0.0.0 --port 8000`
5. Add the env vars from `.env.example` (Alpaca keys, `JWT_SECRET`, etc.). Set `VEXARIUM_ENV=production`.
6. Deploy. A free web service spins down when idle — fine for a demo; upgrade or move to Hetzner for real traffic.

> **Note:** Render auto-deploys on push to `main`. Env-var changes do **not** auto-redeploy — trigger a manual deploy after changing env vars.

### Option B — Hetzner VPS + Docker (recommended once you have paying users, ~€4/mo)
On a fresh Hetzner Cloud CX22 (Ubuntu):

```bash
# install docker + compose on the server
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # then re-login

# clone and run the stack
git clone https://github.com/Al0ngsy/vexarium-backend.git && cd vexarium-backend
cp .env.example .env    # fill real secrets; VEXARIUM_ENV=production
docker compose up -d --build            # api :8000, postgres, redis
```

Then put **Caddy** in front for TLS + reverse-proxy:

```
# Caddyfile
api.vexarium.com {
    reverse_proxy localhost:8000
}
```

> `docker compose up` requires the Docker daemon to reach the registry (the base image pull). If Docker Desktop's network is broken on your dev machine, build/run on the VPS instead.

### Production checklist
- [ ] Set a strong `JWT_SECRET` and `VEXARIUM_ENV=production` (startup refuses the placeholder secret)
- [ ] Real Stripe keys + webhook secret; set the webhook endpoint to `https://api.…/api/v1/billing/webhook`
- [ ] Real Sentry DSN (optional)
- [ ] Point `CORS_ORIGINS` at the deployed frontend domain, not `localhost`
- [ ] Set `REDIS_URL` + `DATABASE_URL` if you want Redis caching / Postgres persistence (repo layer is in-memory until the Postgres repo is wired)
- [ ] Add real deploy hooks to `.github/workflows/deploy.yml` (Render webhook or SSH-to-Hetzner)

---

**⚠️ Disclaimer:** VEXARIUM provides mathematical indicator computations and educational analysis only. Nothing here is financial or investment advice. Trading involves significant risk. See the frontend legal pages.
