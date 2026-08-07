from fastapi import APIRouter, Request
from ..middleware.rate_limit import limiter
from ..schemas.portfolio import StanceRequest, StanceResponse
from ..services.stance import compute_stance
from ..services.alpaca_client import AlpacaClient
from ..config import settings

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

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
        except Exception:
            current_price = 0.0
    result = compute_stance(
        entry_price=body.entry_price,
        current_price=current_price or 0.0,
        trade_type=body.trade_type,
        contract=body.contract,
    )
    return StanceResponse(**result)
