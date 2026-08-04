from fastapi import APIRouter, Request, Query
from ..middleware.rate_limit import limiter
from ..config import settings
import logging

router = APIRouter(prefix="/assets", tags=["assets"])
logger = logging.getLogger("vexarium.api.assets")

_assets_cache: list[dict] = []
_assets_loaded = False


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
                "exchange": str(getattr(a, "exchange", "")),
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
async def search_assets(request: Request, q: str = Query("", max_length=10)):
    q = q.strip().upper()
    if not q:
        return {"assets": []}
    _load_assets()
    results = []
    for a in _assets_cache:
        if a["symbol"].startswith(q):
            results.append(a)
            if len(results) >= 20:
                break
    return {"assets": results}
