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
    NewsArticle,
    OverallVerdict,
    PricePoint,
    CompanyInfo,
)
from ..services.alpaca_client import AlpacaClient, AlpacaError
from ..services.indicator_engine import create_pro_engine
from ..services.chart_series import build_price_series, compute_series_for, indicator_kind
from ..services.verdicts import aggregate
from ..services.news_service import fetch_news
from ..services.company_info import get_company_info
from ..services.cache import cache_get, cache_set, analysis_key, CACHE_TTL_ANALYSIS
from ..config import settings

router = APIRouter(prefix="/analysis", tags=["analysis"])

logger = logging.getLogger("vexarium.api.analysis")


def _build_response(sym: str, body: AnalysisRequest, df, indicator_results):
    """Compute the full AnalysisResponse payload (indicators, series, news)."""
    client = AlpacaClient()
    overall = aggregate(indicator_results)
    current_price = float(df.iloc[-1]["close"]) if not df.empty else None
    day_change_pct = None
    try:
        snap = client.get_market_snapshot(sym, df)
        day_change_pct = snap.get("day_change_pct")
    except Exception:
        logger.warning("day_change unavailable for %s", sym, exc_info=True)
    price_series, indicator_series = _build_series_payload(df, indicator_results)
    news, news_articles = fetch_news(client, sym)
    # Free, keyless company/ETF profile (Yahoo meta + Wikipedia summary). The
    # whole analysis is cached 24h, so this only runs once per symbol per day.
    company_info = None
    try:
        raw = get_company_info(sym)
        company_info = CompanyInfo(**{k: v for k, v in raw.items() if k in CompanyInfo.model_fields})
    except Exception:
        logger.error("Company info failed for %s", sym, exc_info=True)
        company_info = None
    return AnalysisResponse(
        symbol=sym,
        asset_type=body.asset_type,
        timeframe=body.timeframe,
        current_price=current_price,
        day_change_pct=day_change_pct,
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
        news_sentiment=news,
        news_articles=[NewsArticle.from_article(a) for a in news_articles[:8]],
        company=company_info,
    )


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
        # Daily bars -> computed indicators only change once per day, so the whole
        # analysis result is cached per symbol per day (cheap repeat lookups).
        key = analysis_key(sym, body.timeframe)
        cached = await cache_get(key)
        if cached is not None:
            try:
                return AnalysisResponse.model_validate(cached)
            except Exception:
                pass
        client = AlpacaClient()
        df = client.get_stock_bars(sym, timeframe=body.timeframe)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data found for symbol: {sym}")
        # All indicators are free (no more free/pro indicator split).
        engine = create_pro_engine()
        indicator_results = engine.compute_all(df)
        response = _build_response(sym, body, df, indicator_results)
        await cache_set(key, response.model_dump(mode="json"), ttl=CACHE_TTL_ANALYSIS)
        return response
    except AlpacaError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        logger.error("Analysis failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error")


@router.get("/bars/{symbol}", response_model=list[PricePoint])
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def bars(
    request: Request,
    symbol: str,
    timeframe: str = "1d",
    limit: int = 300,
):
    """OHLC bars for the chart at a selectable resolution.

    timeframe: 1m / 5m / 15m / 1h / 1d / 1w / 1mo. limit caps the number of
    points returned (the widget renders the most recent `limit` bars).
    """
    try:
        sym = validate_symbol(symbol)
        client = AlpacaClient()
        df = client.get_stock_bars(sym, timeframe=timeframe)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data found for symbol: {sym}")
        return build_price_series(df, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except AlpacaError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        logger.error("Bars failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error")
