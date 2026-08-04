from fastapi import APIRouter, Request
from ..middleware.rate_limit import limiter
from ..schemas.portfolio import StanceRequest, StanceResponse
from ..services.stance import compute_stance
from ..config import settings

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

@router.post("/stance", response_model=StanceResponse)
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def get_stance(request: Request, body: StanceRequest):
    result = compute_stance(
        entry_price=body.entry_price,
        current_price=body.current_price,
        trade_type=body.trade_type,
        contract=body.contract,
    )
    return StanceResponse(**result)
