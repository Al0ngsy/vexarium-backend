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

import time

import httpx

from ..logging import get_logger
from .cache import cache_get, cache_set, run_coro

logger = get_logger("company")

# TTL: company name/exchange/52w are effectively static intraday; fundamentals
# update quarterly; the description is static. A long TTL keeps analysis cheap.
CACHE_TTL_COMPANY = 12 * 3600

_HEADERS = {
    # Windows Chrome UA: Yahoo rate-limits/429s the macOS UA from datacenter
    # IPs (Render). The Windows UA is accepted and returns full data.
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
}
_HTTP_TIMEOUT = 12.0

# Wikipedia REST requires a descriptive UA per Wikimedia's User-Agent policy
# (browser-like/generic UAs get 403 from cloud IPs). Deliberately NOT the
# Chrome UA above.
_WIKI_HEADERS = {
    "User-Agent": "Vexarium/1.0 (stock analysis tool; https://vexarium.pages.dev)"
}


def _company_key(symbol: str) -> str:
    # v2: adds OTC-ADR -> main-listing resolution; bump on schema changes so
    # stale Redis entries (cached without the new fields) are not served.
    return f"company:v2:{symbol.upper()}"


# ---------------------------------------------------------------------------
# Yahoo quoteSummary (rich fundamentals)
# ---------------------------------------------------------------------------

def _fetch_quote_summary(symbol: str) -> dict:
    """Fetch rich fundamentals from Yahoo quoteSummary (keyless, crumb dance).

    Tries query1 then query2 (Yahoo sometimes blocks one host from datacenter
    IPs), with a fresh crumb per attempt.
    """
    modules = (
        "assetProfile,price,summaryDetail,financialData,defaultKeyStatistics,calendarEvents"
    )
    last_err: Exception | None = None
    for host in ("query1", "query2"):
        try:
            with httpx.Client(headers=_HEADERS, timeout=_HTTP_TIMEOUT) as client:
                # 1. Establish session + grab a crumb.
                client.get("https://fc.yahoo.com")
                crumb_resp = client.get(f"https://{host}.finance.yahoo.com/v1/test/getcrumb")
                crumb_resp.raise_for_status()
                crumb = crumb_resp.text.strip()
                if not crumb:
                    raise RuntimeError("no crumb")
                # 2. Fetch the summary.
                url = (
                    f"https://{host}.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
                    f"?modules={modules}&crumb={crumb}"
                )
                resp = client.get(url)
                resp.raise_for_status()
                payload = resp.json()
            result = payload["quoteSummary"]["result"][0]
            logger.debug("quoteSummary fetched symbol=%s host=%s", symbol, host)
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.debug("quoteSummary host failed symbol=%s host=%s", symbol, host)
            continue
    else:
        raise RuntimeError(f"quoteSummary failed on all hosts: {last_err}")
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
    logger.debug("yahoo meta fetch symbol=%s", symbol)
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
    logger.debug("wikipedia fetch symbol=%s title=%s", symbol, title)
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
    with httpx.Client(headers=_WIKI_HEADERS, timeout=_HTTP_TIMEOUT) as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()
    extract = data.get("extract") or ""
    return _clean_description(extract)


# ---------------------------------------------------------------------------
# stockanalysis.com fundamentals fallback (keyless HTML scrape)
# ---------------------------------------------------------------------------

def _fetch_stockanalysis_fundamentals(symbol: str) -> dict:
    """Fetch fundamentals from stockanalysis.com (keyless HTML scrape).

    Used when Yahoo quoteSummary is unavailable (e.g. datacenter IP blocked).
    Returns a dict with the same field names as the Yahoo path, so the rest of
    the pipeline is source-agnostic. Never raises on partial data.
    """
    import re

    base = f"https://stockanalysis.com/stocks/{symbol.lower()}/"
    result: dict = {}

    logger.debug("stockanalysis fetch symbol=%s", symbol)
    with httpx.Client(headers=_HEADERS, timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
        # 1. Main page: market cap, revenue, dividend, 52-week range, beta.
        resp = client.get(base)
        resp.raise_for_status()
        html = resp.text

        # Link rows: <a ... class="dothref text-default">Label</a><!--]--></td><td ...>Value
        rows = re.findall(
            r'<a href="[^"]*" class="dothref text-default">([^<]+)</a><!--\]--></td>'
            r'<td[^>]*>([^<]+)<',
            html,
        )
        data: dict[str, str] = {}
        for label, value in rows:
            data[label.strip().lower()] = value.strip()

        # Plain rows: <td ...>Label</td><td ...>Value
        plain = re.findall(
            r'<td class="whitespace-nowrap py-\[1px\] px-0\.5 xs:px-1 sm:py-2">([^<]+)</td>'
            r'<td class="whitespace-nowrap py-\[1px\] px-0\.5 text-left[^>]*>([^<]+)<',
            html,
        )
        for label, value in plain:
            data[label.strip().lower()] = value.strip()

        # 2. Statistics page: P/E, margins, ROE, growth.
        try:
            resp2 = client.get(base + "statistics/")
            resp2.raise_for_status()
            html2 = resp2.text
            # <span>Label</span> ... <td title="35.677">35.68</td>
            stat_rows = re.findall(
                r'<span><!---->([^<]+)<!----></span>.*?<td[^>]*title="([^"]*)"[^>]*>',
                html2,
                re.DOTALL,
            )
            for label, value in stat_rows:
                data[label.strip().lower()] = value.strip()
        except Exception:
            logger.debug("stockanalysis statistics page failed symbol=%s", symbol)

    def num(label: str) -> float | None:
        v = data.get(label)
        if not v:
            return None
        v = v.replace(",", "").replace("$", "").strip()
        mult = 1.0
        if v.endswith("T"):
            mult, v = 1e12, v[:-1]
        elif v.endswith("B"):
            mult, v = 1e9, v[:-1]
        elif v.endswith("M"):
            mult, v = 1e6, v[:-1]
        elif v.endswith("K"):
            mult, v = 1e3, v[:-1]
        try:
            return round(float(v) * mult, 2)
        except ValueError:
            return None

    def pct(label: str) -> float | None:
        v = data.get(label)
        if not v:
            return None
        try:
            return round(float(v.replace("%", "").strip()) / 100.0, 4)
        except ValueError:
            return None

    name = data.get("company name") or data.get("name")
    if name:
        result["name"] = name
    mcap = num("market cap")
    if mcap:
        result["market_cap"] = mcap
    pe = num("pe ratio")
    if pe:
        result["pe_ratio"] = pe
    fpe = num("forward pe")
    if fpe:
        result["forward_pe"] = fpe
    ps = num("ps ratio")
    if ps:
        result["ps_ratio"] = ps
    pb = num("pb ratio")
    if pb:
        result["pb_ratio"] = pb
    dy = pct("dividend yield")
    if dy is not None:
        result["dividend_yield"] = dy
    pr = pct("payout ratio")
    if pr is not None:
        result["payout_ratio"] = pr
    pm = pct("profit margin")
    if pm is not None:
        result["profit_margin"] = pm
    gm = pct("gross margin")
    if gm is not None:
        result["gross_margin"] = gm
    roe = pct("return on equity")
    if roe is not None:
        result["roe"] = roe
    roa = pct("return on assets")
    if roa is not None:
        result["roa"] = roa
    rev = num("revenue (ttm)")
    if rev:
        result["revenue_ttm"] = rev
    shares = num("shares outstanding")
    if shares:
        result["shares_outstanding"] = shares
    ed = data.get("earnings date")
    if ed:
        result["next_earnings_date"] = ed
    # 52-week range: "202.16 - 344.57"
    rng = data.get("52-week range")
    if rng:
        parts = re.findall(r"[\d.]+", rng)
        if len(parts) >= 2:
            lo, hi = float(parts[0]), float(parts[1])
            result["low_52w"] = round(lo, 2)
            result["high_52w"] = round(hi, 2)
    return result


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
        cached = run_coro(cache_get(key))
        if cached:
            return cached
    except Exception:
        pass

    t0 = time.monotonic()
    result: dict = {"symbol": symbol}

    # Rich fundamentals + core identity (name/exchange/currency) via quoteSummary.
    # This is the primary source; if it fails the panel still shows symbol-only.
    source = "quote_summary"
    try:
        result.update(_fetch_quote_summary(symbol))
    except Exception:
        logger.warning("yahoo quoteSummary unavailable for %s, trying stockanalysis.com", symbol)
        # Fallback: stockanalysis.com fundamentals (keyless HTML scrape).
        source = "stockanalysis"
        try:
            result.update(_fetch_stockanalysis_fundamentals(symbol))
        except Exception:
            logger.warning("stockanalysis.com fundamentals unavailable for %s", symbol)

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

    # OTC/foreign ADRs (RNMBY, SMERY, …): map to the primary home-exchange
    # listing (RHM.DE / XETRA, ENR.DE / XETRA) so the UI can offer a switch.
    # Runs after the v8 meta fill below so the company name is available even
    # when quoteSummary returns an empty longName (common for OTC symbols).
    if "OTC" in (result.get("exchange") or "").upper():
        try:
            main_listing = find_main_listing(symbol, result.get("name") or "")
            if main_listing:
                result["main_listing"] = main_listing
        except Exception:
            logger.debug("main-listing resolution failed for %s", symbol)

    try:
        run_coro(cache_set(key, result, ttl=CACHE_TTL_COMPANY))
    except Exception:
        pass
    ms = int((time.monotonic() - t0) * 1000)
    logger.info("company info done symbol=%s source=%s fields=%d duration_ms=%d",
                symbol, source, len(result), ms)
    return result


def _num(v) -> float | None:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Yahoo search (keyless) — shared by assets search + ADR main-listing lookup
# ---------------------------------------------------------------------------

_YAHOO_SEARCH_CACHE_TTL = 60  # seconds


def _yahoo_search_quotes(query: str, limit: int = 10) -> list[dict]:
    """Keyless Yahoo Finance search -> raw quote dicts.

    Cached briefly (keystroke-debounced queries shouldn't hammer Yahoo).
    Returns [] on any failure — never raises.
    """
    key = f"ysearch:{query.lower()}"
    try:
        cached = run_coro(cache_get(key))
        if cached:
            return cached
    except Exception:
        pass

    last_err: Exception | None = None
    for host in ("query1", "query2"):
        try:
            logger.debug("yahoo search fetch q=%s host=%s", query, host)
            url = f"https://{host}.finance.yahoo.com/v1/finance/search"
            with httpx.Client(headers=_HEADERS, timeout=_HTTP_TIMEOUT) as client:
                resp = client.get(
                    url, params={"q": query, "quotesCount": limit, "newsCount": 0}
                )
                resp.raise_for_status()
                quotes = resp.json().get("quotes") or []
            result = [
                {
                    "symbol": q.get("symbol") or "",
                    "name": q.get("longname") or q.get("shortname") or "",
                    "shortname": q.get("shortname") or "",
                    "exchange": q.get("exchDisp") or q.get("exchange") or "",
                    "exch_code": q.get("exchange") or "",
                    "quoteType": q.get("quoteType") or "",
                }
                for q in quotes
            ]
            try:
                run_coro(cache_set(key, result, ttl=_YAHOO_SEARCH_CACHE_TTL))
            except Exception:
                pass
            logger.debug("yahoo search done q=%s hits=%d", query, len(result))
            return result
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue
    logger.warning("yahoo search unavailable q=%s error=%s", query, last_err)
    return []


# ---------------------------------------------------------------------------
# ADR -> primary listing resolution (RNMBY -> RHM.DE / XETRA)
# ---------------------------------------------------------------------------

# Exchange preference for choosing the "main listing" of a foreign company.
# XETRA (GER) is the primary German exchange; FRA/Frankfurt is the secondary.
_MAIN_EXCHANGE_RANK = {
    "GER": 0, "XETRA": 0,
    "FRA": 1, "FRANKFURT": 1,
    "LSE": 2, "LON": 2, "PAR": 2, "EPA": 2, "MIL": 3, "TYO": 3, "TLO": 3,
}
_OTC_EXCHANGE_CODES = {"OQX", "OQB", "PNK", "OTC", "PINKSHEETS", "STU", "CXE"}


def find_main_listing(symbol: str, name: str) -> dict | None:
    """Map an OTC ADR to its primary home-exchange listing.

    E.g. ``RNMBY`` (Rheinmetall AG ADR, OTC) -> ``RHM.DE`` (XETRA) and
    ``SMERY`` (Siemens Energy ADR) -> ``ENR.DE`` (XETRA). Uses keyless Yahoo
    search on the company name, preferring the main German exchange, then
    Frankfurt, then any non-OTC listing of the same company.

    Returns ``{"symbol", "name", "exchange"}`` or None.
    """
    if not name:
        return None
    quotes = _yahoo_search_quotes(name, limit=10)
    if not quotes:
        return None
    candidates: list[dict] = []
    for q in quotes:
        sym = (q.get("symbol") or "").upper()
        if not sym or sym == symbol.upper():
            continue
        if q.get("quoteType") not in ("EQUITY", "ETF"):
            continue
        exch_code = (q.get("exch_code") or "").upper()
        if exch_code in _OTC_EXCHANGE_CODES:
            continue
        # Same company? Match on the Yahoo long/short name.
        qname = (q.get("name") or q.get("shortname") or "").upper()
        n = name.upper()
        if not (n in qname or qname in n or _name_tokens_overlap(n, qname)):
            continue
        candidates.append(q)
    if not candidates:
        return None
    candidates.sort(
        key=lambda q: _MAIN_EXCHANGE_RANK.get((q.get("exch_code") or "").upper(), 99)
    )
    best = candidates[0]
    return {
        "symbol": best["symbol"],
        "name": best.get("name") or best.get("shortname") or "",
        "exchange": best.get("exchange") or "",
    }


def _name_tokens_overlap(a: str, b: str) -> bool:
    """Do two company names share a meaningful token (e.g. 'RHEINMETALL')?"""
    import re

    stop = {"AG", "SE", "PLC", "INC", "CORP", "LTD", "CO", "NV", "SA", "ADR"}
    ta = {t for t in re.split(r"[\s,./&()-]+", a) if t and t not in stop}
    tb = {t for t in re.split(r"[\s,./&()-]+", b) if t and t not in stop}
    return bool(ta & tb)


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
