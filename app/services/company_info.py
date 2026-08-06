"""Company/ETF profile enrichment (free, keyless sources).

Two free sources, both requiring no API key and both designed for programmatic
use:

1. **Yahoo Finance v8 chart meta** — returns longName, shortName, exchange,
   52-week high/low for stocks AND ETFs. Reliable, no auth.
2. **Wikipedia REST summary** — a plain-English one-paragraph description of
   the company/fund. We derive the article title from the Yahoo longName.

Everything is cached (Redis or in-memory TTL) and degrades gracefully: if either
source fails, that field is simply omitted — never a hard error for the caller.
"""
from __future__ import annotations

import logging

import httpx

from .cache import cache_get, cache_set

logger = logging.getLogger("vexarium.company")

# TTL: company name/exchange/52w are effectively static intraday; the
# description is static. A long TTL keeps repeated analysis cheap.
CACHE_TTL_COMPANY = 24 * 3600

_HEADERS = {"User-Agent": "Vexarium/0.1 (trading-analysis tool)"}
_HTTP_TIMEOUT = 12.0


def _company_key(symbol: str) -> str:
    return f"company:{symbol.upper()}"


def _fetch_yahoo_meta(symbol: str) -> dict:
    """Fetch company meta from Yahoo Finance v8 chart endpoint (keyless)."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=1d"
    with httpx.Client(headers=_HEADERS, timeout=_HTTP_TIMEOUT) as client:
        resp = client.get(url)
        resp.raise_for_status()
        payload = resp.json()
    meta = payload["chart"]["result"][0]["meta"]
    return {
        "name": meta.get("longName") or meta.get("shortName") or symbol,
        "short_name": meta.get("shortName") or "",
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName") or "",
        "high_52w": _num(meta.get("fiftyTwoWeekHigh")),
        "low_52w": _num(meta.get("fiftyTwoWeekLow")),
        "currency": meta.get("currency") or "",
    }


def _fetch_wikipedia_description(name: str, symbol: str = "") -> str:
    """Fetch a plain-English one-paragraph description from Wikipedia REST."""
    # Normalize the company name to a Wikipedia article title.
    title = _wikipedia_title(name, symbol)
    if not title:
        return ""
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
    with httpx.Client(headers=_HEADERS, timeout=_HTTP_TIMEOUT) as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()
    extract = data.get("extract") or ""
    # Keep it reasonably short for a card, drop trailing citation artifacts.
    return _clean_description(extract)


def get_company_info(symbol: str) -> dict:
    """Return company/ETF profile info for ``symbol``, cached.

    Never raises. On any failure the missing fields are omitted so the UI can
    degrade gracefully. Always returns ``{"symbol": ...}`` at minimum.
    """
    symbol = symbol.upper()
    key = _company_key(symbol)
    try:
        cached = _run_coro_safe(cache_get(key))
        if cached:
            return cached
    except Exception:
        pass

    result: dict = {"symbol": symbol}
    try:
        meta = _fetch_yahoo_meta(symbol)
        result.update(meta)
        # Derive a Wikipedia title from the company name.
        name = meta.get("name") or symbol
        try:
            desc = _fetch_wikipedia_description(name, symbol)
            if desc:
                result["description"] = desc
        except Exception:
            logger.debug("Wikipedia description unavailable for %s", symbol)
    except Exception:
        logger.error("Yahoo meta unavailable for %s", symbol, exc_info=True)

    try:
        _run_coro_safe(cache_set(key, result, ttl=CACHE_TTL_COMPANY))
    except Exception:
        pass
    return result


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _run_coro_safe(coro):
    """Run a small async cache call whether or not a loop is active."""
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    result = {}

    def _worker():
        try:
            result["value"] = asyncio.run(coro)
        except Exception:
            result["error"] = True

    import threading
    t = threading.Thread(target=_worker)
    t.start()
    t.join()
    if "error" in result:
        raise RuntimeError("cache call failed")
    return result.get("value")


def _num(v) -> float | None:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


# Curated fallback: symbol -> exact Wikipedia article title for tricky cases
# where the derived title wouldn't hit the fund's own article (e.g. SPY).
SYMBOL_WIKI_TITLES = {
    "SPY": "SPDR_S&P_500_ETF_Trust",
    "QQQ": "Invesco_QQQ",
    "DIA": "SPDR_Dow_Jones_Industrial_Average_ETF",
    "IWM": "iShares_Russell_2000",
    "GLD": "SPDR_Gold_Shares",
    "VOO": "Vanguard_S&P_500_ETF",
    "VTI": "Vanguard_Total_Stock_Market_ETF",
    "ARKK": "ARK_Innovation",
    "TQQQ": "ProShares_UltraPro_QQQ",
    "EFA": "iShares_MSCI_EAFE",
    "EEM": "iShares_MSCI_Emerging_Markets",
    "HYG": "iShares_iBoxx_High_Yield_Corporate_Bond",
    "LQD": "iShares_iBoxx_Investment_Grade_Corporate_Bond",
    "TLT": "iShares_20%2B_Year_Treasury_Bond_ETF",
}


def _wikipedia_title(name: str, symbol: str = "") -> str:
    """Map a company/fund name (and optionally symbol) to a Wikipedia title."""
    n = name.strip()
    if not n:
        return ""
    if symbol:
        curated = SYMBOL_WIKI_TITLES.get(symbol.upper())
        if curated:
            # quote() URL-encodes & and + for us.
            from urllib.parse import quote
            return quote(curated, safe="()")
    title = n
    # ETF issuer prefixes to drop so we hit the fund's own article.
    for prefix in ("State Street SPDR ", "iShares ", "Invesco ", "Vanguard ",
                   "SPDR ", "ProShares ", "ARK ", "First Trust "):
        if title.startswith(prefix):
            title = title[len(prefix):]
            break
    title = title.replace(" ", "_")
    from urllib.parse import quote
    return quote(title, safe="()")


def _clean_description(text: str) -> str:
    """Trim a Wikipedia extract to a clean ~1-2 sentence summary."""
    if not text:
        return ""
    # Wikipedia summaries sometimes have parenthetical pronunciation or refs.
    text = text.replace("\u200b", "")
    # Cut at the first sentence end (~200 chars) to keep the card compact.
    if len(text) > 260:
        cut = text.find(". ", 120)
        if cut != -1 and cut < 320:
            text = text[: cut + 1]
        else:
            text = text[:260].rsplit(" ", 1)[0] + "…"
    return text.strip()
