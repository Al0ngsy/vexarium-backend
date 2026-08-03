from fastapi import APIRouter, HTTPException, Request, Depends

from ..middleware.rate_limit import limiter
from ..middleware.validation import validate_symbol
from ..schemas.analysis import AnalysisRequest, AnalysisResponse, IndicatorResult, OverallVerdict
from ..services.alpaca_client import AlpacaClient, AlpacaError
from ..services.indicator_engine import create_default_engine, create_pro_engine
from ..middleware.tier_gating import require_tier
from ..services.verdicts import aggregate
from ..config import settings

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.post("", response_model=AnalysisResponse)
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def analyze(request: Request, body: AnalysisRequest):
    try:
        sym = validate_symbol(body.symbol)
        client = AlpacaClient()
        df = client.get_stock_bars(sym)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data found for symbol: {sym}")
        engine = create_default_engine()
        indicator_results = engine.compute_all(df)
        overall = aggregate(indicator_results)
        current_price = float(df.iloc[-1]["close"]) if not df.empty else None
        return AnalysisResponse(
            symbol=sym,
            asset_type=body.asset_type,
            current_price=current_price,
            overall=OverallVerdict(
                overall_verdict=overall["overall_verdict"],
                score=overall["score"],
                indicator_count=overall["indicator_count"],
                breakdown=[IndicatorResult(**r) for r in overall["breakdown"]],
            ),
            # compute_all returns IndicatorResult dataclass objects; convert via to_dict
            indicators=[IndicatorResult(**r.to_dict()) for r in indicator_results],
        )
    except AlpacaError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal error: {e}")


@router.post("/extended", response_model=AnalysisResponse)
@limiter.limit(f"{settings.rate_limit_pro}/minute")
async def analyze_extended(request: Request, body: AnalysisRequest, _: str = Depends(require_tier("pro"))):
    try:
        sym = validate_symbol(body.symbol)
        client = AlpacaClient()
        df = client.get_stock_bars(sym)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data found for symbol: {sym}")
        engine = create_pro_engine()
        indicator_results = engine.compute_all(df)
        overall = aggregate(indicator_results)
        current_price = float(df.iloc[-1]["close"]) if not df.empty else None
        return AnalysisResponse(
            symbol=sym,
            asset_type=body.asset_type,
            current_price=current_price,
            overall=OverallVerdict(
                overall_verdict=overall["overall_verdict"],
                score=overall["score"],
                indicator_count=overall["indicator_count"],
                breakdown=[IndicatorResult(**r) for r in overall["breakdown"]],
            ),
            indicators=[IndicatorResult(**r.to_dict()) for r in indicator_results],
        )
    except AlpacaError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal error: {e}")
