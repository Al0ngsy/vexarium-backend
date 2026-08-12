"""Finnhub free-tier enrichment: insider transactions, earnings, peers.

Every fetcher returns [] on any failure (no key, network error, non-200,
malformed payload) so the widgets degrade to "no data" instead of erroring.
Each kind is cached 12h under `finnhub:{SYMBOL}:{kind}` (insider filings and
earnings change slowly; peers rarely).
"""
from __future__ import annotations

import logging

import httpx

from ..config import settings
from .cache import CACHE_TTL_FINNHUB, cache_get, cache_set, finnhub_key, run_coro

logger = logging.getLogger("vexarium.finnhub")
_FINNHUB_URL = "https://finnhub.io/api/v1"
_TIMEOUT = 10.0


def _get(path: str, params: dict) -> dict | list | None:
    """GET a Finnhub endpoint; None on no key / any failure."""
    if not settings.finnhub_api_key:
        return None
    try:
        resp = httpx.get(
            f"{_FINNHUB_URL}/{path}",
            params={**params, "token": settings.finnhub_api_key},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        logger.debug("Finnhub %s failed", path)
        return None


def _cached(symbol: str, kind: str, fetch) -> list:
    key = finnhub_key(symbol, kind)
    cached = run_coro(cache_get(key))
    if cached is not None:
        return cached
    data = fetch()
    if data:
        run_coro(cache_set(key, data, ttl=CACHE_TTL_FINNHUB))
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
    return {
        "insider": get_insider_transactions(symbol),
        "earnings": get_earnings_history(symbol),
        "peers": get_peers(symbol),
    }
