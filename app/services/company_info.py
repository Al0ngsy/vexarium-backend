"""Company/ETF profile + fundamentals enrichment (free, keyless sources).

Three free sources, all requiring no API key and all designed for programmatic
use:

1. **Yahoo Finance v10 quoteSummary** — rich fundamentals for stocks AND ETFs:
   sector, industry, market cap, P/E, P/S, P/B, dividend yield, payout ratio,
   revenue/earnings growth, profit margin, ROE/ROA/gross margin, CEO + total
   pay, headquarters, employee count, shares outstanding, next earnings date.
   Requires a session cookie + crumb (obtained programmatically).
2. **Yahoo Finance v8 chart meta** — name, short name, exchange, 52-week
   high/low, currency. Reliable, no auth.
3. **Wikipedia REST summary** — a plain-English one-paragraph description of
   the company/fund. We derive the article title from the Yahoo longName.

Everything is cached (Redis or in-memory TTL) and degrades gracefully: if any
source fails, that field is simply omitted — never a hard error for the caller.
"""
from __future__ import annotations

import logging

import httpx

from .cache import cache_get, cache_set

logger = logging.getLogger("vexarium.company")

# TTL: company name/exchange/52w are effectively static intraday; fundamentals
# update quarterly; the description is static. A long TTL keeps analysis cheap.
CACHE_TTL_COMPANY = 12 * 3600

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
}
_HTTP_TIMEOUT = 12.0


def _company_key(symbol: str) -> str:
    return f"company:{symbol.upper()}"


# ---------------------------------------------------------------------------
# Yahoo quoteSummary (rich fundamentals)
# ---------------------------------------------------------------------------

def _fetch_quote_summary(symbol: str) -> dict:
    """Fetch rich fundamentals from Yahoo quoteSummary (keyless, crumb dance)."""
    modules = (
        "assetProfile,price,summaryDetail,financialData,defaultKeyStatistics,calendarEvents"
    )
    with httpx.Client(headers=_HEADERS, timeout=_HTTP_TIMEOUT) as client:
        # 1. Establish session + grab a crumb.
        client.get("https://fc.yahoo.com")
        crumb_resp = client.get("https://query1.finance.yahoo.com/v1/test/getcrumb")
        crumb_resp.raise_for_status()
        crumb = crumb_resp.text.strip()
        if not crumb:
            raise RuntimeError("no crumb")
        # 2. Fetch the summary.
        url = (
            f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
            f"?modules={modules}&crumb={crumb}"
        )
        resp = client.get(url)
        resp.raise_for_status()
        payload = resp.json()
    result = payload["quoteSummary"]["result"][0]
    ap = result.get("assetProfile", {}) or {}
    pd = result.get("price", {}) or {}
    sd = result.get("summaryDetail", {}) or {}
    fd = result.get("financialData", {}) or {}
    ks = result.get("defaultKeyStatistics", {}) or {}
    ce = result.get("calendarEvents", {}) or {}

    def num(d: dict, k: str):
        v = d.get(k)
        if isinstance(v, dict):
            v = v.get("raw")
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    officers = ap.get("companyOfficers") or []
    earnings_dates = (ce.get("earnings") or {}).get("earningsDate") or []
    next_earnings = None
    if earnings_dates:
        for ed in earnings_dates:
            raw = ed.get("raw") if isinstance(ed, dict) else None
            if raw:
                next_earnings = ed.get("fmt") or ""
                break

    return {
        # Core identity (also present here so we don't depend on v8 for these).
        "name": pd.get("longName") or "",
        "exchange": pd.get("exchangeName") or "",
        "currency": pd.get("currency") or "",
        "sector": ap.get("sector") or "",
        "industry": ap.get("industry") or "",
        "website": ap.get("website") or "",
        "headquarters": _headquarters(ap),
        "employees": num(ap, "fullTimeEmployees"),
        "ceo": (officers[0].get("name") if officers else ""),
        "ceo_title": (officers[0].get("title") if officers else ""),
        "ceo_pay": num(officers[0], "totalPay") if officers else None,
        "market_cap": num(pd, "marketCap"),
        "shares_outstanding": num(ks, "sharesOutstanding"),
        "pe_ratio": num(sd, "trailingPE"),
        "forward_pe": num(sd, "forwardPE"),
        "ps_ratio": num(sd, "priceToSalesTrailing12Months"),
        "pb_ratio": num(sd, "priceToBook"),
        "dividend_yield": num(sd, "dividendYield"),
        "payout_ratio": num(sd, "payoutRatio"),
        "revenue_growth": num(fd, "revenueGrowth"),
        "earnings_growth": num(ks, "earningsQuarterlyGrowth"),
        "profit_margin": num(fd, "profitMargins"),
        "gross_margin": num(fd, "grossMargins"),
        "roe": num(fd, "returnOnEquity"),
        "roa": num(fd, "returnOnAssets"),
        "next_earnings_date": next_earnings or "",
    }


def _headquarters(ap: dict) -> str:
    parts = [ap.get("address1"), ap.get("city"), ap.get("state"), ap.get("zip")]
    return ", ".join([p for p in parts if p])


# ---------------------------------------------------------------------------
# Yahoo v8 chart meta (name / exchange / 52w)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Wikipedia description
# ---------------------------------------------------------------------------

def _fetch_wikipedia_description(name: str, symbol: str = "") -> str:
    """Fetch a plain-English one-paragraph description from Wikipedia REST."""
    title = _wikipedia_title(name, symbol)
    if not title:
        return ""
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
    with httpx.Client(headers=_HEADERS, timeout=_HTTP_TIMEOUT) as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()
    extract = data.get("extract") or ""
    return _clean_description(extract)


# ---------------------------------------------------------------------------
# public entrypoint
# ---------------------------------------------------------------------------

def get_company_info(symbol: str) -> dict:
    """Return company/ETF profile + fundamentals for ``symbol``, cached.

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

    # Rich fundamentals + core identity (name/exchange/currency) via quoteSummary.
    # This is the primary source; if it fails the panel still shows symbol-only.
    try:
        result.update(_fetch_quote_summary(symbol))
    except Exception:
        logger.debug("Yahoo quoteSummary unavailable for %s", symbol)

    # 52-week range + description (best-effort, non-blocking).
    try:
        meta = _fetch_yahoo_meta(symbol)
        # Only fill fields we don't already have from quoteSummary.
        for k in ("name", "short_name", "exchange", "high_52w", "low_52w", "currency"):
            if k not in result or not result.get(k):
                result[k] = meta.get(k)
        name = result.get("name") or symbol
        try:
            desc = _fetch_wikipedia_description(name, symbol)
            if desc:
                result["description"] = desc
        except Exception:
            logger.debug("Wikipedia description unavailable for %s", symbol)
    except Exception:
        logger.debug("Yahoo v8 meta unavailable for %s", symbol)

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
            from urllib.parse import quote

            return quote(curated, safe="()")
    title = n
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
    text = text.replace("\u200b", "")
    if len(text) > 260:
        cut = text.find(". ", 120)
        if cut != -1 and cut < 320:
            text = text[: cut + 1]
        else:
            text = text[:260].rsplit(" ", 1)[0] + "…"
    return text.strip()
