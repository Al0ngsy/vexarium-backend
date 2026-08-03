from fastapi import APIRouter
from ..schemas.portfolio import StanceRequest, StanceResponse
from ..services.stance import compute_stance

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

@router.post("/stance", response_model=StanceResponse)
async def get_stance(request: StanceRequest):
    result = compute_stance(
        entry_price=request.entry_price,
        current_price=request.current_price,
        trade_type=request.trade_type,
        contract=request.contract,
    )
    return StanceResponse(**result)
