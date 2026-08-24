import re

import httpx
from fastapi import APIRouter, Request, Query

from ..config import settings
from ..logging import get_logger
from ..middleware.logging import get_request_id as _rid
from ..middleware.rate_limit import limiter
from ..services.company_info import (
    _HEADERS,
    _MAIN_EXCHANGE_RANK,
    _OTC_EXCHANGE_CODES,
    _yahoo_search_quotes,
)

router = APIRouter(prefix="/assets", tags=["assets"])
logger = get_logger("assets")

_assets_cache: list[dict] = []
_assets_loaded = False


def _yahoo_search(q: str, limit: int = 8) -> list[dict]:
    """Search results from Yahoo (keyless) — surfaces main listings (RHM.DE /
    XETRA, SIE.DE, …) and OTC ADRs that Alpaca's US-equity universe doesn't
    include. Returns [] on any failure (never raises) so search degrades
    gracefully. The HTTP fetch + cache lives in company_info._yahoo_search_quotes.
    """
    quotes = _yahoo_search_quotes(q, limit=limit * 2)
    out: list[dict] = []
    seen_names: set[str] = set()
    for qt in quotes:
        symbol = qt.get("symbol") or ""
        if not symbol:
            continue
        name = qt.get("name") or qt.get("shortname") or symbol
        exch_code = (qt.get("exch_code") or "").upper()
        # Skip OTC/Pink-Sheet listings — the Alpaca side surfaces the US ADR
        # (RNMBY); and skip ADR-variant listings (e.g. RHMB.SG "Rheinmetall AG
        # (ADRs)"). Keep the primary listing per company name.
        if exch_code in ("OQX", "OQB", "PNK", "OTC", "PINKSHEETS", "STU"):
            continue
        if "(ADR" in name.upper():
            continue
        key = name.strip().lower()
        if not key or key in seen_names:
            continue
        seen_names.add(key)
        out.append({
            "symbol": symbol,
            "name": name,
            "exchange": qt.get("exchange") or "",
            "asset_type": _detect_type(name, symbol),
        })
        if len(out) >= limit:
            break
    return out


def _load_assets():
    global _assets_cache, _assets_loaded
    if _assets_loaded:
        return
    try:
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import GetAssetsRequest
        client = TradingClient(settings.alpaca_api_key, settings.alpaca_secret_key, paper=settings.alpaca_paper)
        req = GetAssetsRequest(status="active", asset_class="us_equity")
        assets = client.get_all_assets(req)
        _assets_cache = [
            {
                "symbol": a.symbol,
                "name": a.name,
                # Alpaca returns enum strings like "AssetExchange.NASDAQ" /
                # "AssetExchange.OTC" — strip the prefix for display.
                "exchange": str(getattr(a, "exchange", "")).replace("AssetExchange.", ""),
                "asset_type": _detect_type(a.name, a.symbol),
            }
            for a in assets
        ]
        _assets_loaded = True
        logger.debug("assets universe loaded count=%d", len(_assets_cache))
    except Exception:
        logger.error("Failed to load assets", exc_info=True)


def _detect_type(name: str, symbol: str) -> str:
    n = name.upper()
    if "ETF" in n or "TRUST" in n or "FUND" in n:
        return "etf"
    return "stock"


# --- German WKN fallback (A1JX52, ETF146, …) --------------------------------
# WKNs are 5-6 char German instrument ids. Yahoo search doesn't index them, so
# resolve WKN -> fund name via wallstreet-online (keyless HTML), then run the
# existing Yahoo name search on that name and prefer German listings.


def _wso_fund_name(q: str) -> str:
    """WKN -> fund name via the wallstreet-online search page. '' on failure."""
    try:
        with httpx.Client(headers=_HEADERS, timeout=12.0, follow_redirects=True) as client:
            resp = client.get(f"https://www.wallstreet-online.de/suche?q={q}")
            resp.raise_for_status()
            html = resp.text
        m = re.search(r"Kursdetails\s+([^<]+)", html)
        return m.group(1).strip() if m else ""
    except Exception:  # noqa: BLE001
        logger.debug("wallstreet-online lookup failed for %s", q, exc_info=True)
        return ""


def _wkn_search(q: str, limit: int = 4) -> list[dict]:
    """German WKN -> ticker listings, German exchanges ranked first."""
    name = _wso_fund_name(q)
    if not name:
        return []
    # Yahoo search chokes on the share-class tail wso appends
    # ("… UCITS ETF Distributing" / "… DR - USD (C)") — cut back to the fund
    # name. ponytail: dist-vs-acc share class not matched — same fund family
    # either way, German listing ranked first.
    name = re.split(r"\s+UCITS ETF\b", name, maxsplit=1)[0] + " UCITS ETF" if "UCITS ETF" in name else name
    brand = name.split()[0].upper()
    out: list[dict] = []
    seen: set[str] = set()
    for qt in _yahoo_search_quotes(name, limit=limit * 3):
        symbol = qt.get("symbol") or ""
        if not symbol or symbol.upper() in seen:
            continue
        if (qt.get("exch_code") or "").upper() in _OTC_EXCHANGE_CODES:
            continue
        if "(ADR" in (qt.get("name") or "").upper():
            continue
        # Relevance: Yahoo fuzzy-matches similar funds ("Lyxor MSCI World" for
        # an "Amundi MSCI World" WKN) — require the brand word to match.
        if brand and brand not in (qt.get("name") or qt.get("shortname") or "").upper():
            continue
        seen.add(symbol.upper())
        out.append(
            {
                "symbol": symbol,
                "name": qt.get("name") or qt.get("shortname") or symbol,
                "exchange": qt.get("exchange") or "",
                "exch_code": qt.get("exch_code") or "",
                "asset_type": _detect_type(qt.get("name") or symbol, symbol),
            }
        )
    out.sort(
        key=lambda a: min(
            _MAIN_EXCHANGE_RANK.get(ex, 9)
            for ex in ((a.get("exch_code") or "").upper(), (a["exchange"] or "").upper())
        )
    )
    return out[:limit]


@router.get("/search")
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def search_assets(request: Request, q: str = Query("", max_length=60)):
    q_raw = q.strip()
    if not q_raw:
        logger.debug("rid=%s assets search q=<empty> done results=0", _rid(request))
        return {"assets": []}
    q = q_raw.upper()
    logger.info("rid=%s assets search q=%s", _rid(request), q)
    _load_assets()
    results = []
    seen = set()

    def _add(a: dict) -> None:
        s = a["symbol"].upper()
        if s in seen:
            return
        seen.add(s)
        results.append(a)

    # 1. Exact symbol match always surfaces first (Alpaca, then Yahoo).
    n_before = len(results)
    for a in _assets_cache:
        if a["symbol"].upper() == q:
            _add(a)
            break
    yahoo = _yahoo_search(q_raw, limit=8)
    for a in yahoo:
        if a["symbol"].upper() == q:
            _add(a)
    logger.debug("rid=%s assets search q=%s stage=exact matches=%d yahoo=%d", _rid(request), q, len(results) - n_before, len(yahoo))

    # 2. Yahoo results next — relevance-ordered by Yahoo, so the primary
    #    listing of a foreign company (RHM.DE / XETRA) ranks above the OTC ADR
    #    (RNMBY) that only Alpaca knows. "Rheinmetall" -> RHM.DE, then RNMBY.
    n_before = len(results)
    for a in yahoo:
        _add(a)
    logger.debug("rid=%s assets search q=%s stage=yahoo matches=%d", _rid(request), q, len(results) - n_before)

    # 3. Alpaca symbol prefix matches.
    n_before = len(results)
    for a in _assets_cache:
        if len(results) >= 20:
            break
        if a["symbol"].upper().startswith(q):
            _add(a)
    # 4. Company-name substring matches (e.g. "Apple" -> AAPL).
    if len(results) < 20:
        q_lower = q_raw.lower()
        for a in _assets_cache:
            if len(results) >= 20:
                break
            if q_lower in a["name"].lower():
                _add(a)
    logger.debug("rid=%s assets search q=%s stage=alpaca matches=%d", _rid(request), q, len(results) - n_before)
    # 5. German WKN fallback (A1JX52, ETF146, …) — Yahoo doesn't index WKNs;
    #    only try when nothing else matched so a 6-char US ticker is never
    #    misrouted through the WKN path.
    if not results and re.fullmatch(r"[A-Z0-9]{5,6}", q):
        for a in _wkn_search(q):
            _add(a)
        logger.debug("rid=%s assets search q=%s stage=wkn matches=%d", _rid(request), q, len(results))
    logger.info("rid=%s assets search q=%s done results=%d", _rid(request), q, len(results[:20]))
    return {"assets": results[:20]}
