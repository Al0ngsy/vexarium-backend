from fastapi import APIRouter, HTTPException
from ..schemas.analysis import AnalysisRequest, AnalysisResponse, IndicatorResult, OverallVerdict
from ..services.alpaca_client import AlpacaClient, AlpacaError
from ..services.indicator_engine import create_default_engine
from ..services.verdicts import aggregate

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.post("", response_model=AnalysisResponse)
async def analyze(request: AnalysisRequest):
    try:
        client = AlpacaClient()
        df = client.get_stock_bars(request.symbol)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data found for symbol: {request.symbol}")
        engine = create_default_engine()
        indicator_results = engine.compute_all(df)
        overall = aggregate(indicator_results)
        current_price = float(df.iloc[-1]["close"]) if not df.empty else None
        return AnalysisResponse(
            symbol=request.symbol,
            asset_type=request.asset_type,
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
