"""Finnhub free-tier enrichment: insider transactions, earnings, peers.

Every fetcher returns [] on any failure (no key, network error, non-200,
malformed payload) so the widgets degrade to "no data" instead of erroring.
Each kind is cached 12h under `finnhub:{SYMBOL}:{kind}` (insider filings and
earnings change slowly; peers rarely).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import httpx

from ..config import settings
from ..logging import get_logger
from .cache import CACHE_TTL_FINNHUB, cache_get, cache_set, finnhub_key, run_coro

logger = get_logger("finnhub")
_FINNHUB_URL = "https://finnhub.io/api/v1"
_TIMEOUT = 10.0


def _get(path: str, params: dict) -> dict | list | None:
    """GET a Finnhub endpoint; None on no key / any failure."""
    if not settings.finnhub_api_key:
        logger.debug("finnhub get skipped path=%s (no api key)", path)
        return None
    t0 = time.monotonic()
    # Never log the token: log the request params minus the credential.
    safe_params = {k: v for k, v in params.items() if k != "token"}
    try:
        resp = httpx.get(
            f"{_FINNHUB_URL}/{path}",
            params={**params, "token": settings.finnhub_api_key},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        ms = int((time.monotonic() - t0) * 1000)
        status = getattr(resp, "status_code", None)
        logger.info("finnhub get done path=%s status=%s duration_ms=%d params=%s",
                    path, status, ms, safe_params)
        return resp.json()
    except Exception:
        ms = int((time.monotonic() - t0) * 1000)
        logger.debug("finnhub get failed path=%s duration_ms=%d params=%s", path, ms, safe_params)
        return None


def _cached(symbol: str, kind: str, fetch) -> list:
    key = finnhub_key(symbol, kind)
    cached = run_coro(cache_get(key))
    if cached is not None:
        logger.debug("finnhub cached payload symbol=%s kind=%s count=%d", symbol, kind, len(cached))
        return cached
    data = fetch()
    if data:
        run_coro(cache_set(key, data, ttl=CACHE_TTL_FINNHUB))
    logger.debug("finnhub fetch done symbol=%s kind=%s count=%d", symbol, kind, len(data or []))
    return data or []


def get_insider_transactions(symbol: str) -> list[dict]:
    """Recent insider trades: name, shares, holding change, filing date."""

    def fetch():
        data = _get("stock/insider-transactions", {"symbol": symbol.upper()})
        if not isinstance(data, dict):
            return []
        rows = []
        for t in data.get("data", [])[:20]:
            rows.append({
                "name": t.get("name", ""),
                "shares": t.get("share") or 0,
                "change": t.get("change") or 0,
                "filing_date": t.get("filingDate", ""),
            })
        return rows

    return _cached(symbol, "insider", fetch)


def get_earnings_history(symbol: str) -> list[dict]:
    """Recent quarterly earnings: period, estimate, actual, surprise %."""

    def fetch():
        data = _get("stock/earnings", {"symbol": symbol.upper()})
        if not isinstance(data, list):
            return []
        rows = []
        for e in data[:8]:
            rows.append({
                "period": e.get("period", ""),
                "estimate": e.get("estimate"),
                "actual": e.get("actual"),
                "surprise_pct": e.get("surprisePercent"),
            })
        return rows

    return _cached(symbol, "earnings", fetch)


def get_peers(symbol: str) -> list[str]:
    """Comparable-company tickers."""

    def fetch():
        data = _get("stock/peers", {"symbol": symbol.upper()})
        if isinstance(data, list):
            return [s for s in data if isinstance(s, str) and s]
        return []

    return _cached(symbol, "peers", fetch)


def get_finnhub_bundle(symbol: str) -> dict:
    """One payload for the FE widgets; each kind cached independently."""
    bundle = {
        "insider": get_insider_transactions(symbol),
        "earnings": get_earnings_history(symbol),
        "peers": get_peers(symbol),
    }
    logger.debug("finnhub bundle built symbol=%s insider=%d earnings=%d peers=%d",
                 symbol, len(bundle["insider"]), len(bundle["earnings"]), len(bundle["peers"]))
    return bundle


def _norm_market(n: dict) -> dict:
    """Normalize a Finnhub general-news item to the shared article shape."""
    ts = n.get("datetime")
    created = (
        datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        if isinstance(ts, (int, float)) and ts
        else ""
    )
    return {
        "id": str(n.get("id")) if n.get("id") is not None else None,
        "headline": n.get("headline", ""),
        "source": n.get("source", ""),
        "url": n.get("url", ""),
        "summary": (n.get("summary") or "")[:400],
        "created_at": created,
        "symbols": [s.strip() for s in (n.get("related") or "").split(",") if s.strip()][:5],
    }


def get_market_news(limit: int = 6) -> list[dict]:
    """Broad market headlines (Finnhub `news?category=general`), cached 12h
    under a global key (not symbol-scoped). Empty on no key / any failure."""
    def fetch():
        data = _get("news", {"category": "general"})
        if not isinstance(data, list):
            return []
        return [_norm_market(n) for n in data[:limit] if isinstance(n, dict)]

    return _cached("GLOBAL", "market-news", fetch)


def get_company_news(symbol: str, limit: int = 8) -> list[dict]:
    """Symbol-scoped news (Finnhub `/company-news`), cached 12h per symbol.

    Second source for the stock feed (Alpaca + Google News is the first) so
    the widget isn't a single outlet. Same normalized shape as market news.
    """
    def fetch():
        to = datetime.now(timezone.utc).date()
        frm = to - timedelta(days=7)
        data = _get(
            "company-news",
            {"symbol": symbol.upper(), "from": frm.isoformat(), "to": to.isoformat()},
        )
        if not isinstance(data, list):
            return []
        return [_norm_market(n) for n in data[: limit * 2] if isinstance(n, dict)]

    return _cached(symbol, "company-news", fetch)
