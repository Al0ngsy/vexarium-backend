"""onvista warrant (Optionsschein) data client.

Alpaca does not expose German warrants (Optionsscheine). This client pulls the
warrant list from onvista's public JSON feed (no API key). onvista is an
*unofficial*, scraping-style source: the Next.js build token is discovered at
runtime from the live page and the feed may change or rate-limit. We therefore
cache aggressively and degrade gracefully (return empty list / cached data on
failure) so the app never crashes because of onvista.

Data fields (per warrant): wkn, isin, official_name, underlying (name/isin),
exercise_right (CALL/PUT), exercise_style, strike (strikeAbs),
strike_pct_from_underlying (differenceStrikePct), maturity (dateMaturity),
cover_ratio, leverage, omega (gearingAsk), implied_volatility
(impliedVolatilityAsk), spread_pct (spreadAskPct), issuer, bid, ask.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional

import httpx

from ..config import settings
from .cache import cache_get, cache_set

logger = logging.getLogger("vexarium.onvista")

# Cache keys
_WARRANT_LIST_KEY = "onvista:warrants:list"
_BUILD_TOKEN_KEY = "onvista:build_token"
_CACHE_TTL = settings.onvista_cache_ttl


class OnvistaError(Exception):
    pass


class WarrantClient:
    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self._http = client or httpx.AsyncClient(
            timeout=20.0,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
                "Accept": "application/json, text/html, */*",
            },
        )
        self._own_client = client is None

    async def aclose(self):
        if self._own_client:
            await self._http.aclose()

    async def _get_build_token(self) -> Optional[str]:
        """Discover the Next.js build token from the live onvista page."""
        cached = await cache_get(_BUILD_TOKEN_KEY)
        if cached:
            return cached
        try:
            resp = await self._http.get(f"{settings.onvista_base_url}/derivate/Optionsscheine")
            resp.raise_for_status()
            html = resp.text
            m = re.search(r"/_next/data/([A-Za-z0-9\-_]+)/", html) or \
                re.search(r'buildId["\']?\s*[:=]\s*["\']([A-Za-z0-9\-_]+)["\']', html)
            token = m.group(1) if m else None
            if token:
                await cache_set(_BUILD_TOKEN_KEY, token, ttl=_CACHE_TTL)
                return token
            logger.warning("Could not discover onvista build token")
        except Exception as e:
            logger.error("onvista build-token lookup failed: %s", e)
        return None

    async def get_warrants(self, underlying: Optional[str] = None,
                           exercise_right: Optional[str] = None,
                           limit: int = 100) -> list[dict]:
        """Return the warrant list, optionally filtered by underlying.

        ``underlying`` matches the underlying *name* (case-insensitive substring)
        or ISIN. ``exercise_right`` is 'CALL' or 'PUT'. Returns up to ``limit``.
        Cached to avoid hammering onvista; empty list on failure (graceful).
        """
        cached = await cache_get(_WARRANT_LIST_KEY)
        if isinstance(cached, list) and cached:
            return self._filter(cached, underlying, exercise_right)[:limit]
        try:
            token = await self._get_build_token()
            if not token:
                return []
            url = (f"{settings.onvista_base_url}/_next/data/{token}/derivate/"
                   f"Optionsscheine.json")
            resp = await self._http.get(url)
            resp.raise_for_status()
            payload = resp.json()
            lst = (payload.get("pageProps", {}).get("data", {}) or {}).get("list", [])
            normalized = [self._normalize(w) for w in lst if isinstance(w, dict)]
            await cache_set(_WARRANT_LIST_KEY, normalized, ttl=_CACHE_TTL)
            return self._filter(normalized, underlying, exercise_right)[:limit]
        except Exception as e:
            logger.error("onvista warrant fetch failed: %s", e)
            return []

    @staticmethod
    def _normalize(w: dict) -> dict:
        instr = w.get("instrument", {}) or {}
        underlying = w.get("instrumentUnderlying", {}) or {}
        quote = w.get("quote", {}) or {}
        bid = quote.get("bid")
        ask = quote.get("ask")
        return {
            "wkn": instr.get("wkn", ""),
            "isin": instr.get("isin", ""),
            "name": w.get("officialName") or w.get("shortName", ""),
            "underlying": underlying.get("name", ""),
            "underlying_isin": underlying.get("isin", ""),
            "underlying_wkn": underlying.get("wkn", ""),
            "exercise_right": w.get("nameExerciseRight", ""),
            "exercise_style": w.get("nameExerciseStyle", ""),
            "strike": w.get("strikeAbs"),
            "strike_pct": w.get("differenceStrikePct"),
            "maturity": _clean_date(w.get("dateMaturity")),
            "cover_ratio": w.get("coverRatio"),
            "leverage": w.get("leverage"),
            "omega": w.get("gearingAsk"),
            "implied_volatility": w.get("impliedVolatilityAsk"),
            "spread_pct": w.get("spreadAskPct"),
            "issuer": (w.get("issuer", {}) or {}).get("name", ""),
            "bid": float(bid) if isinstance(bid, (int, float)) else None,
            "ask": float(ask) if isinstance(ask, (int, float)) else None,
            "premium": w.get("premiumAsk"),
        }

    @staticmethod
    def _filter(warrants: list[dict], underlying: Optional[str],
                exercise_right: Optional[str]) -> list[dict]:
        out = []
        u = (underlying or "").strip().upper()
        er = (exercise_right or "").strip().upper()
        for w in warrants:
            if u:
                name = (w.get("underlying") or "").upper()
                isin = (w.get("underlying_isin") or "").upper()
                if u not in name and u not in isin and u not in (w.get("underlying_wkn") or "").upper():
                    continue
            if er and w.get("exercise_right", "").upper() != er:
                continue
            out.append(w)
        return out


def _clean_date(dt_str: Optional[str]) -> Optional[str]:
    """Normalize an ISO datetime to YYYY-MM-DD."""
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00")).date().isoformat()
    except (ValueError, TypeError):
        return str(dt_str)[:10]


_client: Optional[WarrantClient] = None


def get_warrant_client() -> WarrantClient:
    global _client
    if _client is None:
        _client = WarrantClient()
    return _client


def reset_warrant_client() -> None:
    global _client
    _client = None
