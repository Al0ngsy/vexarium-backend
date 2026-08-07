from fastapi import APIRouter, Request, Query
from ..middleware.rate_limit import limiter
from ..config import settings
from ..services.company_info import _yahoo_search_quotes
import logging

router = APIRouter(prefix="/assets", tags=["assets"])
logger = logging.getLogger("vexarium.api.assets")

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
    except Exception:
        logger.error("Failed to load assets", exc_info=True)


def _detect_type(name: str, symbol: str) -> str:
    n = name.upper()
    if "ETF" in n or "TRUST" in n or "FUND" in n:
        return "etf"
    return "stock"


@router.get("/search")
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def search_assets(request: Request, q: str = Query("", max_length=60)):
    q_raw = q.strip()
    if not q_raw:
        return {"assets": []}
    q = q_raw.upper()
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
    for a in _assets_cache:
        if a["symbol"].upper() == q:
            _add(a)
            break
    yahoo = _yahoo_search(q_raw, limit=8)
    for a in yahoo:
        if a["symbol"].upper() == q:
            _add(a)

    # 2. Yahoo results next — relevance-ordered by Yahoo, so the primary
    #    listing of a foreign company (RHM.DE / XETRA) ranks above the OTC ADR
    #    (RNMBY) that only Alpaca knows. "Rheinmetall" -> RHM.DE, then RNMBY.
    for a in yahoo:
        _add(a)

    # 3. Alpaca symbol prefix matches.
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
    return {"assets": results[:20]}
