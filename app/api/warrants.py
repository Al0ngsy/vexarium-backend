from fastapi import APIRouter, HTTPException, Query, Request
from typing import Optional

from ..middleware.rate_limit import limiter
from ..schemas.warrants import WarrantsResponse, WarrantSchema, WarrantValueResponse
from ..services.onvista_client import get_warrant_client
from ..config import settings

router = APIRouter(prefix="/warrants", tags=["warrants"])


def _value_at_price(strike: float, premium: float, cover_ratio: float,
                    is_call: bool, target_price: float) -> dict:
    """Warrant intrinsic value at expiry for a given underlying price.

    Warrants settle to ``max(underlying - strike, 0) / cover_ratio`` for calls,
    ``max(strike - underlying, 0) / cover_ratio`` for puts.
    """
    if is_call:
        intrinsic = max(target_price - strike, 0)
    else:
        intrinsic = max(strike - target_price, 0)
    est = intrinsic / cover_ratio if cover_ratio else 0.0
    pl = est - premium
    pl_pct = (pl / premium) if premium else 0.0
    return {
        "estimated_option_price": round(est, 2),
        "estimated_pl": round(pl, 2),
        "pl_pct": round(pl_pct, 4),
    }


@router.get("", response_model=WarrantsResponse)
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def list_warrants(
    request: Request,
    underlying: Optional[str] = Query(None, description="Underlying name/ISIN/WKN to filter by"),
    exercise_right: Optional[str] = Query(None, pattern="^(CALL|PUT)$"),
    limit: int = Query(100, ge=1, le=500),
):
    """List German warrants (Optionsscheine) from onvista.

    Optionally filter by underlying (name/ISIN/WKN) and exercise right.
    """
    client = get_warrant_client()
    warrants = await client.get_warrants(
        underlying=underlying, exercise_right=exercise_right, limit=limit
    )
    return WarrantsResponse(
        underlying=underlying,
        total=len(warrants),
        warrants=[WarrantSchema(**w) for w in warrants],
    )


@router.get("/{wkn}/value", response_model=WarrantValueResponse)
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def warrant_value_at_price(
    request: Request,
    wkn: str,
    target_price: float = Query(..., gt=0),
    strike: float = Query(...),
    premium: float = Query(...),
    cover_ratio: float = Query(1.0, gt=0),
    exercise_right: str = Query("CALL", pattern="^(CALL|PUT)$"),
):
    """Estimate what a warrant is worth if the underlying trades at target_price.

    Uses the warrant's strike, cover ratio and premium (intrinsic value at expiry).
    """
    is_call = exercise_right.upper() == "CALL"
    val = _value_at_price(strike, premium, cover_ratio, is_call, target_price)
    return WarrantValueResponse(
        wkn=wkn.upper(),
        isin="",
        exercise_right=exercise_right.upper(),
        strike=strike,
        cover_ratio=cover_ratio,
        target_price=target_price,
        **val,
    )
