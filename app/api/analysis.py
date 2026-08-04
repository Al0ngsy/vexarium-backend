from fastapi import APIRouter, HTTPException, Request, Depends

from datetime import datetime, timezone
import logging

from ..middleware.rate_limit import limiter
from ..middleware.validation import validate_symbol
from ..schemas.analysis import (
    AnalysisRequest,
    AnalysisResponse,
    IndicatorResult,
    IndicatorSeries,
    IndicatorPoint,
    OverallVerdict,
    PricePoint,
)
from ..services.alpaca_client import AlpacaClient, AlpacaError
from ..services.indicator_engine import create_default_engine, create_pro_engine
from ..services.chart_series import build_price_series, compute_series_for, indicator_kind
from ..middleware.tier_gating import require_tier
from ..services.verdicts import aggregate
from ..config import settings

router = APIRouter(prefix="/analysis", tags=["analysis"])

logger = logging.getLogger("vexarium.api.analysis")


def _build_series_payload(df, indicator_results):
    """Compute price_series + indicator_series for charting."""
    price_series = build_price_series(df)
    indicator_series = []
    for r in indicator_results:
        series = compute_series_for(df, r.name)
        pts = [
            {"t": ts, "v": v}
            for ts, v in zip(
                [str(x)[:10] for x in df.tail(120)["timestamp"]],
                series[-120:] if series else [],
            )
        ]
        pts = [p for p in pts if p["v"] is not None]
        indicator_series.append(
            IndicatorSeries(
                name=r.name,
                kind=indicator_kind(r.name),
                points=[IndicatorPoint(**p) for p in pts],
            )
        )
    return [PricePoint(**p) for p in price_series], indicator_series


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
        price_series, indicator_series = _build_series_payload(df, indicator_results)
        return AnalysisResponse(
            symbol=sym,
            asset_type=body.asset_type,
            current_price=current_price,
            analyzed_at=datetime.now(timezone.utc).isoformat(),
            overall=OverallVerdict(
                overall_verdict=overall["overall_verdict"],
                score=overall["score"],
                indicator_count=overall["indicator_count"],
                breakdown=[IndicatorResult(**r) for r in overall["breakdown"]],
            ),
            # compute_all returns IndicatorResult dataclass objects; convert via to_dict
            indicators=[IndicatorResult(**r.to_dict()) for r in indicator_results],
            price_series=price_series,
            indicator_series=indicator_series,
        )
    except AlpacaError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        logger.error("Analysis failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error")


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
        price_series, indicator_series = _build_series_payload(df, indicator_results)
        return AnalysisResponse(
            symbol=sym,
            asset_type=body.asset_type,
            current_price=current_price,
            analyzed_at=datetime.now(timezone.utc).isoformat(),
            overall=OverallVerdict(
                overall_verdict=overall["overall_verdict"],
                score=overall["score"],
                indicator_count=overall["indicator_count"],
                breakdown=[IndicatorResult(**r) for r in overall["breakdown"]],
            ),
            indicators=[IndicatorResult(**r.to_dict()) for r in indicator_results],
            price_series=price_series,
            indicator_series=indicator_series,
        )
    except AlpacaError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        logger.error("Analysis failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error")
