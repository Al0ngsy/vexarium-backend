from fastapi import APIRouter, Request
from ..middleware.rate_limit import limiter
from ..schemas.portfolio import StanceRequest, StanceResponse
from ..services.stance import compute_stance
from ..services.alpaca_client import AlpacaClient
from ..config import settings
from ..logging import get_logger
from ..middleware.logging import get_request_id as _rid

router = APIRouter(prefix="/portfolio", tags=["portfolio"])
logger = get_logger("portfolio")

@router.post("/stance", response_model=StanceResponse)
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def get_stance(request: Request, body: StanceRequest):
    # The client stores only entry prices; the server fetches the live quote
    # so the stance is computed on a real price, not a client-supplied guess.
    current_price = body.current_price
    if not current_price or current_price <= 0:
        try:
            quote = AlpacaClient().get_latest_quote(body.symbol)
            current_price = quote.get("last_price") or quote.get("bid") or quote.get("ask")
            logger.debug("rid=%s stance symbol=%s quote_source=live price=%s", _rid(request), body.symbol, current_price)
        except Exception:
            logger.warning("rid=%s stance symbol=%s quote unavailable → price=0", _rid(request), body.symbol)
            current_price = 0.0
    logger.info("rid=%s stance symbol=%s trade_type=%s entry_price=%s current_price=%s", _rid(request), body.symbol, body.trade_type, body.entry_price, current_price)
    result = compute_stance(
        entry_price=body.entry_price,
        current_price=current_price or 0.0,
        trade_type=body.trade_type,
        contract=body.contract,
    )
    logger.info("rid=%s stance symbol=%s done stance=%s pnl_pct=%s", _rid(request), body.symbol, result.get("stance"), result.get("pnl_pct"))
    return StanceResponse(**result)
